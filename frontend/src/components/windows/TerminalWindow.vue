<!--
  Web 终端窗口（Terminal）

  这个窗口做什么：
    基于 xterm.js 的服务器命令行终端，通过 WebSocket 直连后端执行 shell。
    支持普通登录终端，也支持「进入容器」（传入容器 ID 走 /ws/container）。
    可指定工作目录、连接后自动执行命令（autoCommand，如 Foxcode 启动命令）、
    断线自动重连（指数退避），以及 SGR 鼠标模式（供 vim / tmux / htop 等
    TUI 程序点击交互）。

  用到的后端模块：
    /api/terminal/ws（强制管理员，WebSocket，token 走查询参数）——普通终端；
    /api/terminal/ws/container?container=xx——容器内终端；
    /api/terminal/mouse-capability——查询平台是否支持 TUI 鼠标
    （Windows 10 及更早的 ConPTY 不支持）。终端会话固定绑定打开时的管理节点。

  关键状态：
    term / fit      xterm 实例与 FitAddon 插件
    ws              当前 WebSocket 连接
    termNode        会话绑定的目标节点（打开窗口时由 App.vue 同步）
    mouseOn         SGR 鼠标模式开关
    reconnectTimer / backoff   断线重连与指数退避

  怎么被打开：
    桌面「终端」应用，或文件管理器 / Docker 容器详情里的「打开终端」。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#1e1e1e; overflow:hidden;">
    <div class="toolbar">
      <span style="color:#0a3d7a;">{{ $t('terminal.title') }}{{ container ? ' · ' + $t('terminal.inContainer', { name: container }) : '' }} · {{ statusText }}</span>
      <button class="btn" style="margin-left:auto;" @click="reconnect">{{ $t('terminal.reconnect') }}</button>
      <button class="btn" @click="clear">{{ $t('terminal.clear') }}</button>
      <!-- 鼠标模式开关：开启后向 TUI 与应用写入鼠标启用序列，支持 vim/tmux/ranger/htop 等点击交互 -->
      <button class="btn" :disabled="!mouseSupported"
        :style="mouseSupported && mouseOn ? 'background:#0a3d7a; color:#fff;' : ''"
        :title="!mouseSupported ? (mouseReason || $t('terminal.mouseUnsupported')) : (mouseOn ? $t('terminal.mouseOffHint') : $t('terminal.mouseOnHint'))"
        @click="toggleMouse">{{ $t('terminal.mouse') }}:{{ mouseOn ? $t('terminal.on') : $t('terminal.off') }}</button>
    </div>
    <div ref="termEl" style="flex:1; min-height:0; padding:4px; background:#1e1e1e; overflow:hidden;"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'   // 响应式状态与终端挂载/卸载钩子
import { useI18n } from 'vue-i18n'   // 取 t()，连接状态文案跟随面板语言
import { Terminal } from '@xterm/xterm'   // xterm.js 终端核心
import { FitAddon } from '@xterm/addon-fit'   // 让终端尺寸跟随容器变化的插件
import '@xterm/xterm/css/xterm.css'   // xterm 基础样式
import { auth } from '../../store/auth'   // 登录态：WebSocket 用 token 查询参数鉴权
import { getRequestNode } from '../../store/requestNode'   // 取当前管理节点（会话固定绑定它）
import api from '../../api'   // 默认 axios 实例，用于查询鼠标能力

// autoCommand：连接建立后自动执行/输入到终端的命令字符串（例如 Foxcode 启动命令）
const props = defineProps({ cwd: String, container: String, autoCommand: String })
const { t } = useI18n()

const termEl = ref(null)          // 终端挂载容器 DOM
const statusText = ref(t('terminal.notConnected'))   // 顶栏连接状态文案
// 会话绑定的目标节点：由于 App.vue 在打开窗口时已同步设置请求级节点，
// 此处取到的即为本终端窗口绑定的节点；连接/重连固定使用它，避免切走后串到别的节点。
const termNode = getRequestNode() || ''
// 平台是否支持 TUI 鼠标：Windows 10 及更早的 ConPTY 不支持鼠标输入，
// 需禁用「鼠标」开关并给出原因；Linux / Win11 22H2+ 支持。
const mouseSupported = ref(true)
const mouseReason = ref('')
let term = null            // xterm 实例
let fit = null             // FitAddon 实例
let ws = null              // 当前 WebSocket 连接
let resizeObserver = null  // 监听容器尺寸变化，触发重新 fit
let alive = false          // 组件是否仍存活（卸载后停止重连）
let reconnectTimer = null  // 断线重连的定时器句柄
let backoff = 500          // 重连退避毫秒数，从 500ms 起按 1.5 倍递增
let autoSent = false       // 自动命令是否已发送（防止重复发送）
let autoTimer = null       // 自动命令兜底发送的定时器句柄
// 鼠标模式开关状态（默认关闭，避免干扰普通 shell 与下拉选文本）
const mouseOn = ref(false)

// SGR 扩展鼠标模式启用/停用序列：?1000 启用 X10 鼠标追踪（点击），?1006 使用 SGR 坐标格式（兼容性更好）
const MOUSE_ON_SEQ = '\x1b[?1000h\x1b[?1006h'
const MOUSE_OFF_SEQ = '\x1b[?1000l\x1b[?1006l'

// 切换鼠标模式：同时写入前端 xterm 解析器（让其捕获鼠标事件）与后端/TUI（让应用进入或退出鼠标模式）
function toggleMouse() {
  if (!mouseSupported.value) return
  mouseOn.value = !mouseOn.value
  const seq = mouseOn.value ? MOUSE_ON_SEQ : MOUSE_OFF_SEQ
  try { if (term) term.write(seq) } catch (e) { /* 写入 xterm 解析器失败不阻塞 */ }
  try { if (ws && ws.readyState === 1) ws.send(seq) } catch (e) { /* 连接未就绪时忽略，重连时会重放 */ }
}

function setStatus(s) { statusText.value = s }

// --- 建立 WebSocket 连接（拼装 token/节点/容器参数，挂接各事件回调） ---
function connect() {
  if (ws) { try { ws.close() } catch (e) {} }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'   // https 页面必须用 wss，否则浏览器拒绝
  setStatus(t('terminal.connecting'))
  // 浏览器 WebSocket 无法设置请求头，token 通过查询参数传递；
  // 目标节点（窗口绑定节点）同样经 node 参数下发，使本会话连接该节点而非全局当前节点。
  const qs = []
  if (auth.token) qs.push(`token=${encodeURIComponent(auth.token)}`)
  if (termNode) qs.push(`node=${encodeURIComponent(termNode)}`)
  const qstr = qs.length ? '?' + qs.join('&') : ''
  try {
    if (props.container) {
      // 容器内终端：使用 /ws/container 端点，传入容器 ID
      ws = new WebSocket(`${proto}://${location.host}/api/terminal/ws/container?container=${encodeURIComponent(props.container)}${qstr ? '&' + qstr.slice(1) : ''}`)
    } else {
      ws = new WebSocket(`${proto}://${location.host}/api/terminal/ws${qstr}`)
    }
  } catch (e) {
    scheduleReconnect()
    return
  }
  ws.onopen = () => {
    backoff = 500
    setStatus(t('terminal.connected'))
    autoSent = false
    sendResize()
    // 若用户已开启鼠标模式，连接稳定后重放启用序列，保证重连后仍处于鼠标模式
    if (mouseOn.value) {
      try { if (term) term.write(MOUSE_ON_SEQ) } catch (e) {}
      try { if (ws) ws.send(MOUSE_ON_SEQ) } catch (e) {}
    }
    // 若指定了工作目录，连接稳定后先切换目录
    if (props.cwd) {
      setTimeout(() => {
        if (ws && ws.readyState === 1) {
          const isWinPath = props.cwd.includes('\\') || /^[A-Za-z]:/.test(props.cwd)
          ws.send(isWinPath ? `cd /d "${props.cwd}"\r\n` : `cd "${props.cwd}"\n`)
        }
      }, 600)
    }
    // 自动命令（如 Foxcode）：等待 shell 输出提示符或超时兜底后发送
    scheduleAutoSend()
  }
  ws.onmessage = (e) => {
    if (term) term.write(e.data)
    // 收到 shell 首次输出（提示符出现）后再发送自动命令，连接就绪判断更可靠
    if (props.autoCommand && !autoSent) doAutoSend()
  }
  ws.onclose = () => {
    setStatus(t('terminal.disconnectedShort'))
    clearAutoTimer()
    if (alive) scheduleReconnect()
  }
  ws.onerror = () => { setStatus(t('terminal.error')) }
}

// 自动命令相关：等待 shell 输出提示符后发送；若迟迟无输出，则用定时器兜底发送
function scheduleAutoSend() {
  if (!props.autoCommand || autoSent) return
  clearAutoTimer()
  autoTimer = setTimeout(() => { doAutoSend() }, 1200)
}
function doAutoSend() {
  if (!props.autoCommand || autoSent || !ws || ws.readyState !== 1) return
  autoSent = true
  clearAutoTimer()
  ws.send(props.autoCommand + '\r\n')
}
function clearAutoTimer() {
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
}

// --- 断线重连：用指数退避避免高频重试，最多等 5 秒 ---
function scheduleReconnect() {
  if (reconnectTimer) return   // 已有重连任务在排队，不重复安排
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    if (alive) connect()
  }, backoff)
  backoff = Math.min(backoff * 1.5, 5000)   // 每次翻 1.5 倍，封顶 5000ms
}

// --- 通知后端终端尺寸变化：先本地 fit，再把行列数发过去 ---
function sendResize() {
  if (!fit || !ws || ws.readyState !== 1) return   // 未就绪或没有连接时不发
  try {
    fit.fit()
    const { rows, cols } = term
    ws.send(`\x1bRESIZE:${rows},${cols}`)
  } catch (e) {}
}

// --- 手动重连：重置退避到最小值，立即重连 ---
function reconnect() {
  backoff = 500
  connect()
}
function clear() { term && term.clear() }

onMounted(async () => {
  alive = true
  // 查询平台鼠标能力：Windows 10 及更早的 ConPTY 不支持 TUI 鼠标，
  // 查询失败时按「支持」处理（能力接口属增强信息，不阻塞终端使用）
  try {
    const r = await api.get('/terminal/mouse-capability')
    if (r.data && typeof r.data.supported === 'boolean') {
      mouseSupported.value = r.data.supported
      mouseReason.value = r.data.reason || ''
    }
  } catch (e) {
    console.warn('[terminal] 查询鼠标能力失败，按支持处理:', e)
  }
  await nextTick()   // 等 DOM 渲染出容器后再初始化 xterm
  term = new Terminal({
    fontFamily: 'Consolas, "Courier New", monospace',
    fontSize: 13,
    cursorBlink: true,
    theme: { background: '#1e1e1e', foreground: '#d4d4d4' }
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(termEl.value)
  fit.fit()
  term.onData(data => {
    if (ws && ws.readyState === 1) ws.send(data)   // 键盘输入原样转发给后端 shell
  })
  connect()
  resizeObserver = new ResizeObserver(() => sendResize())   // 容器大小变化时同步后端行列
  resizeObserver.observe(termEl.value)
})

onBeforeUnmount(() => {
  alive = false                     // 先标记已销毁，杜绝卸载后仍触发重连
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  clearAutoTimer()
  resizeObserver && resizeObserver.disconnect()
  if (ws) { try { ws.close() } catch (e) {} }   // 关闭连接并释放后端会话
  if (term) { try { term.dispose() } catch (e) {} }   // 释放 xterm 占用的 DOM 与资源
})
</script>
