<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#1e1e1e;">
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
    <div ref="termEl" style="flex:1; min-height:0; padding:4px; background:#1e1e1e;"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { auth } from '../../store/auth'
import api from '../../api'

// autoCommand：连接建立后自动执行/输入到终端的命令字符串（例如 Foxcode 启动命令）
const props = defineProps({ cwd: String, container: String, autoCommand: String })
const { t } = useI18n()

const termEl = ref(null)
const statusText = ref(t('terminal.notConnected'))
// 平台是否支持 TUI 鼠标：Windows 10 及更早的 ConPTY 不支持鼠标输入，
// 需禁用「鼠标」开关并给出原因；Linux / Win11 22H2+ 支持。
const mouseSupported = ref(true)
const mouseReason = ref('')
let term = null
let fit = null
let ws = null
let resizeObserver = null
let alive = false
let reconnectTimer = null
let backoff = 500
let autoSent = false
let autoTimer = null
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

function connect() {
  if (ws) { try { ws.close() } catch (e) {} }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  setStatus(t('terminal.connecting'))
  // 浏览器 WebSocket 无法设置请求头，token 通过查询参数传递
  const tokenParam = auth.token ? `?token=${encodeURIComponent(auth.token)}` : ''
  try {
    if (props.container) {
      // 容器内终端：使用 /ws/container 端点，传入容器 ID
      ws = new WebSocket(`${proto}://${location.host}/api/terminal/ws/container?container=${encodeURIComponent(props.container)}${tokenParam ? '&' + tokenParam.slice(1) : ''}`)
    } else {
      ws = new WebSocket(`${proto}://${location.host}/api/terminal/ws${tokenParam}`)
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

function scheduleReconnect() {
  if (reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    if (alive) connect()
  }, backoff)
  backoff = Math.min(backoff * 1.5, 5000)
}

function sendResize() {
  if (!fit || !ws || ws.readyState !== 1) return
  try {
    fit.fit()
    const { rows, cols } = term
    ws.send(`\x1bRESIZE:${rows},${cols}`)
  } catch (e) {}
}

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
  await nextTick()
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
    if (ws && ws.readyState === 1) ws.send(data)
  })
  connect()
  resizeObserver = new ResizeObserver(() => sendResize())
  resizeObserver.observe(termEl.value)
})

onBeforeUnmount(() => {
  alive = false
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  clearAutoTimer()
  resizeObserver && resizeObserver.disconnect()
  if (ws) { try { ws.close() } catch (e) {} }
  if (term) { try { term.dispose() } catch (e) {} }
})
</script>
