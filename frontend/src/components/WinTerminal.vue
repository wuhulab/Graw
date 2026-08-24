<template>
  <div ref="termRef" class="terminal-wrap"></div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const termRef = ref(null)
let term = null
let ws = null
let fitAddon = null

onMounted(() => {
  term = new Terminal({
    fontFamily: 'Consolas, "Courier New", monospace',
    fontSize: 14,
    theme: { background: '#1e1e1e', foreground: '#d4d4d4' },
    cursorBlink: true,
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termRef.value)
  fitAddon.fit()

  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/terminal/ws`)

  ws.onopen = () => {}
  ws.onmessage = (ev) => {
    term.write(ev.data)
  }
  ws.onclose = () => {
    term.write('\r\n[连接已关闭]\r\n')
  }

  term.onData((data) => {
    if (ws && ws.readyState === 1) ws.send(data)
  })

  // Use ResizeObserver for container resize
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(() => {
      try { fitAddon.fit() } catch (e) {}
    })
    ro.observe(termRef.value)
    termRef.value.__ro = ro
  }
})

onBeforeUnmount(() => {
  if (ws) ws.close()
  if (term) term.dispose()
  if (termRef.value && termRef.value.__ro) {
    termRef.value.__ro.disconnect()
  }
})
</script>

<style scoped>
.terminal-wrap {
  width: 100%;
  height: 100%;
  background: #1e1e1e;
}
</style>
