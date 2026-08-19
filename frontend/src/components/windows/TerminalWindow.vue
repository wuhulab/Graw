<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#1e1e1e;">
    <div class="toolbar">
      <span style="color:#0a3d7a;">{{ $t('terminal.title') }}{{ container ? ' · ' + $t('terminal.inContainer', { name: container }) : '' }} · {{ statusText }}</span>
      <button class="btn" style="margin-left:auto;" @click="reconnect">{{ $t('terminal.reconnect') }}</button>
      <button class="btn" @click="clear">{{ $t('terminal.clear') }}</button>
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

// autoCommand：连接建立后自动执行/输入到终端的命令字符串（例如 Foxcode 启动命令）
const props = defineProps({ cwd: String, container: String, autoCommand: String })
const { t } = useI18n()

const termEl = ref(null)
const statusText = ref(t('terminal.notConnected'))
let term = null
let fit = null
let ws = null
let resizeObserver = null
let alive = false
let reconnectTimer = null
let backoff = 500
let autoSent = false
let autoTimer = null

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
