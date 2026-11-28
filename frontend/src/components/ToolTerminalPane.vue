<!--
  ToolTerminalPane.vue — 「工具」浮窗里的单个持久化终端

  和 TerminalWindow.vue 的区别：
    - 没有自己的工具栏与「保留持久化终端」勾选框，生命周期由 ToolDock 的标签页管理；
    - 连接固定带 persist=1 + node + sid：后端按 <节点>|shell|sid|<sid> 各开一个
      常驻 shell，同一节点可以并存多个终端；
    - 切到别的标签页 / 收回浮窗只会断开 WebSocket，shell 进程留在后端继续跑，
      重新激活时按同一 sid 接回并回放最近 256KB 输出，直到用户手动关掉标签。

  依赖后端：/api/terminal/ws（WebSocket，token 走查询参数；持久会话控制帧
  {"type":"persist",...} 与心跳 pong 帧都不能写入 xterm）。
-->
<template>
  <div class="ttp-root">
    <!-- xterm 挂载点：阻止浏览器默认右键菜单，改用终端自己的复制/粘贴菜单 -->
    <div ref="hostEl" class="ttp-term" @contextmenu.prevent.stop="openCtxMenu"></div>
    <Teleport to="body">
      <div v-if="ctx.show" ref="ctxEl" class="ttp-ctx" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }"
        @mousedown.stop @contextmenu.prevent.stop>
        <div class="ttp-ctx-item" @mousedown.prevent="copySelection">{{ $t('toolDock.copy') }}</div>
        <div class="ttp-ctx-item" @mousedown.prevent="pasteClipboard">{{ $t('toolDock.paste') }}</div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'   // 终端只在挂载时初始化，卸载即释放
import { Terminal } from '@xterm/xterm'                 // xterm 终端内核
import { FitAddon } from '@xterm/addon-fit'             // 容器尺寸变化时自动重排
import '@xterm/xterm/css/xterm.css'
import { auth } from '../store/auth'                    // WebSocket 用 ?token= 鉴权

const props = defineProps({
  nodeId: { type: String, default: '' },   // 目标节点 id（'local' 或 SSH 节点）；决定会话建在哪台机器
  sid: { type: String, required: true },   // 前端生成的会话标识：同一节点多个终端靠它区分
})
const emit = defineEmits(['status'])       // 把连接状态回传给 ToolDock（标签提示 / 状态点）

// 应用层心跳参数：与 TerminalWindow.vue 保持一致，防止空闲连接被反代/NAT 掐成「半开」
const HEARTBEAT_INTERVAL = 20000
const HEARTBEAT_TIMEOUT = 65000
const HEARTBEAT_PING = '{"type":"ping"}'
const HEARTBEAT_PONG = '{"type":"pong"}'
const CTRL_MAX = 4096                      // 超长文本不按控制帧解析（shell 输出可能以 { 开头）

const hostEl = ref(null)                   // xterm 挂载容器
const ctx = ref({ show: false, x: 0, y: 0 })   // 右键菜单显隐与坐标
const ctxEl = ref(null)

let term = null
let fit = null
let ws = null
let alive = false                          // 组件是否存活（卸载后停止重连）
let reconnectTimer = null
let backoff = 500                          // 重连退避：500ms 起、1.5 倍递增、封顶 5s
let hbTimer = null
let lastAlive = 0                          // 最近一次收到任何后端数据的时间
let resizeObserver = null

// 连接状态外抛（状态码，由 ToolDock 翻译成文案 / 状态点）：
// 'connecting' → 建立连接中；'connected' → 已连接；'reconnecting' → 断开等待重连
function setStatus(code) {
  emit('status', code)
}

// --- 建立 WebSocket：token / persist / sid / node 全部走查询参数 ---
function connect() {
  if (!alive) return
  if (ws) { try { ws.close() } catch (e) {} }
  stopHeartbeat()
  setStatus('connecting')
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const qs = []
  if (auth.token) qs.push('token=' + encodeURIComponent(auth.token))
  qs.push('persist=1')
  qs.push('sid=' + encodeURIComponent(props.sid))
  // 显式带上节点：浮窗里的终端固定绑定创建时选中的节点，不跟随全局「当前主机」
  if (props.nodeId) qs.push('node=' + encodeURIComponent(props.nodeId))
  let sock
  try {
    sock = new WebSocket(`${proto}://${location.host}/api/terminal/ws?${qs.join('&')}`)
  } catch (e) {
    scheduleReconnect()
    return
  }
  ws = sock
  sock.onopen = () => {
    backoff = 500
    lastAlive = Date.now()
    setStatus('connected')
    startHeartbeat()
    sendResize()
  }
  sock.onmessage = (ev) => {
    // 任何后端数据都说明链路可用，先刷新存活时间
    lastAlive = Date.now()
    if (typeof ev.data === 'string') {
      if (ev.data.startsWith(HEARTBEAT_PONG)) return                 // 心跳应答不写入终端
      if (ev.data.length < CTRL_MAX && ev.data.startsWith('{')) {
        let msg = null
        try { msg = JSON.parse(ev.data) } catch (e) { /* 非 JSON：按普通输出处理 */ }
        if (msg && msg.type === 'persist') {
          // 持久会话控制帧：接入前先复位终端，避免回放内容与旧画面叠加
          try { if (term) term.reset() } catch (e) {}
          return
        }
      }
    }
    if (term) term.write(ev.data)
  }
  // 用局部引用识别「已经被新连接取代」的旧连接，避免重复排重连
  sock.onclose = () => {
    if (ws !== sock) return
    stopHeartbeat()
    if (!alive) return   // 组件已卸载（切标签 / 收浮窗）：不再回传状态，避免误标到别的标签
    setStatus('reconnecting')
    scheduleReconnect()
  }
  sock.onerror = () => setStatus('reconnecting')
}

// --- 断线重连：指数退避，避免面板重启 / 网络抖动时高频重试 ---
function scheduleReconnect() {
  if (reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    if (alive) connect()
  }, backoff)
  backoff = Math.min(backoff * 1.5, 5000)
}

// --- 应用层心跳：超过阈值没有任何数据就主动断开，走重连接回同一常驻会话 ---
function startHeartbeat() {
  stopHeartbeat()
  hbTimer = setInterval(() => {
    if (!ws || ws.readyState !== 1) return
    try {
      ws.send(HEARTBEAT_PING)
      if (Date.now() - lastAlive > HEARTBEAT_TIMEOUT) { try { ws.close() } catch (e) {} }
    } catch (e) {
      try { ws.close() } catch (e2) {}
    }
  }, HEARTBEAT_INTERVAL)
}
function stopHeartbeat() {
  if (hbTimer) { clearInterval(hbTimer); hbTimer = null }
}

// --- 尺寸同步：先本地 fit，再把行列数发给后端（后端据此调整 PTY） ---
function sendResize() {
  if (!fit || !ws || ws.readyState !== 1 || !term) return
  try {
    fit.fit()
    ws.send(`\x1bRESIZE:${term.rows},${term.cols}`)
  } catch (e) {}
}

// --- 右键菜单：复制选中 / 粘贴到终端（和终端窗口一致的类本地终端习惯） ---
function openCtxMenu(e) {
  const w = 120
  const h = 76
  ctx.value = {
    show: true,
    x: Math.max(8, Math.min(e.clientX, window.innerWidth - w - 8)),
    y: Math.max(8, Math.min(e.clientY, window.innerHeight - h - 8)),
  }
}
function closeCtx() {
  if (ctx.value.show) ctx.value = { show: false, x: 0, y: 0 }
}
function onDocMouseDown(e) {
  if (ctx.value.show && ctxEl.value && !ctxEl.value.contains(e.target)) closeCtx()
}
async function copySelection() {
  closeCtx()
  const text = term ? term.getSelection() : ''
  if (!text) return
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return
    }
  } catch (e) { /* 剪贴板权限被拒：走下面的兜底 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed; left:-9999px; top:0; opacity:0;'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  } catch (e) { console.warn('[tool-terminal] 复制失败:', e) }
}
async function pasteClipboard() {
  closeCtx()
  try {
    if (!navigator.clipboard || !window.isSecureContext) return
    const text = await navigator.clipboard.readText()
    if (!text || !term) return
    // 剪贴板里的 \n 在终端里表示回车换行，与本地终端行为一致
    term.paste(text.replace(/\r?\n/g, '\r'))
    term.focus()
  } catch (e) { /* 用户拒绝剪贴板权限：静默忽略 */ }
}

onMounted(() => {
  alive = true
  document.addEventListener('mousedown', onDocMouseDown)
  term = new Terminal({
    fontFamily: 'Consolas, "Courier New", monospace',
    fontSize: 13,
    cursorBlink: true,
    theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
  })
  fit = new FitAddon()
  term.loadAddon(fit)
  term.open(hostEl.value)
  fit.fit()
  term.onData((data) => {
    if (ws && ws.readyState === 1) ws.send(data)
  })
  connect()
  resizeObserver = new ResizeObserver(() => sendResize())
  resizeObserver.observe(hostEl.value)
})

onBeforeUnmount(() => {
  // 只断开连接：常驻 shell 留在后端，下次按同一 sid 接回
  alive = false
  document.removeEventListener('mousedown', onDocMouseDown)
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
  stopHeartbeat()
  if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null }
  if (ws) { try { ws.close() } catch (e) {} }
  if (term) { try { term.dispose() } catch (e) {} }
})
</script>

<style scoped>
.ttp-root {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #1e1e1e;
}
.ttp-term {
  flex: 1;
  min-height: 0;
  padding: 4px;
  overflow: hidden;
  background: #1e1e1e;
}
/* 终端右键菜单：深色主题贴合终端，z-index 高于浮窗本身 */
.ttp-ctx {
  position: fixed;
  z-index: 20000;
  min-width: 120px;
  padding: 4px 0;
  background: #2d2d30;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
  user-select: none;
}
.ttp-ctx-item {
  padding: 6px 16px;
  font-size: 13px;
  color: #d4d4d4;
  cursor: pointer;
  white-space: nowrap;
}
.ttp-ctx-item:hover {
  background: var(--g-strong);
  color: var(--g-on-strong);
}
</style>
