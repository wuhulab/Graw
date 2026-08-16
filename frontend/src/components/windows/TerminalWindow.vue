<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#1e1e1e;">
    <div class="toolbar">
      <span style="color:#0a3d7a;">终端{{ container ? ' · 容器内 ' + container : '' }} · {{ statusText }}</span>
      <button class="btn" style="margin-left:auto;" @click="reconnect">重新连接</button>
      <button class="btn" @click="clear">清屏</button>
    </div>
    <div ref="termEl" style="flex:1; min-height:0; padding:4px; background:#1e1e1e;"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { auth } from '../../store/auth'

const props = defineProps({ cwd: String, container: String })

const termEl = ref(null)
const statusText = ref('未连接')
let term = null
let fit = null
let ws = null
let resizeObserver = null
let alive = false
let reconnectTimer = null
let backoff = 500

function setStatus(s) { statusText.value = s }

function connect() {
  if (ws) { try { ws.close() } catch (e) {} }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  setStatus('连接中...')
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
    setStatus('已连接')
    sendResize()
    if (props.cwd) {
      setTimeout(() => {
        if (ws && ws.readyState === 1) {
          const isWinPath = props.cwd.includes('\\') || /^[A-Za-z]:/.test(props.cwd)
          const cmd = isWinPath ? `cd /d "${props.cwd}"\r\n` : `cd "${props.cwd}"\n`
          ws.send(cmd)
        }
      }, 600)
    }
  }
  ws.onmessage = (e) => {
    if (term) term.write(e.data)
  }
  ws.onclose = () => {
    setStatus('已断开')
    if (alive) scheduleReconnect()
  }
  ws.onerror = () => { setStatus('错误') }
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
  resizeObserver && resizeObserver.disconnect()
  if (ws) { try { ws.close() } catch (e) {} }
  if (term) { try { term.dispose() } catch (e) {} }
})
</script>
