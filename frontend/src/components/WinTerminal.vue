<!--
  WinTerminal.vue — Web 终端窗口
  作用：基于 xterm.js 的交互式 Shell。通过 WebSocket（/api/terminal/ws）与后端
        paramiko 建立的 shell 双向通信：用户输入发往后端，后端输出回显到终端。
  数据：连接本身由后端强制管理员鉴权（?token=），这里只负责前端呈现与收发。
  打开方式：桌面快捷方式或开始菜单的「终端」。
-->
<template>
  <div ref="termRef" class="terminal-wrap"></div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'    // Vue 响应式与生命周期
import { Terminal } from '@xterm/xterm'                  // xterm 终端内核
import { FitAddon } from '@xterm/addon-fit'              // 终端自适应容器尺寸插件
import '@xterm/xterm/css/xterm.css'                      // xterm 基础样式

// 终端实例 / WebSocket 连接 / 尺寸自适应插件（均在挂载时才初始化）
const termRef = ref(null)
let term = null
let ws = null
let fitAddon = null

onMounted(() => {
  // 初始化 xterm 终端（深色配色、光标闪烁）
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

  // 终端 WebSocket：与后端 /api/terminal/ws 建立交互式 shell
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/terminal/ws`)

  ws.onopen = () => {}
  ws.onmessage = (ev) => {
    term.write(ev.data)             // 后端输出回显到终端
  }
  ws.onclose = () => {
    term.write('\r\n[连接已关闭]\r\n')
  }

  // 用户输入经终端捕获后发给后端
  term.onData((data) => {
    if (ws && ws.readyState === 1) ws.send(data)   // readyState 1 = OPEN
  })

  // Use ResizeObserver for container resize
  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(() => {
      try { fitAddon.fit() } catch (e) {}    // 容器尺寸变化时重排终端
    })
    ro.observe(termRef.value)
    termRef.value.__ro = ro                   // 暂存引用，卸载时断开
  }
})

onBeforeUnmount(() => {
  if (ws) ws.close()                 // 关闭 shell 连接
  if (term) term.dispose()           // 释放 xterm 实例
  if (termRef.value && termRef.value.__ro) {
    termRef.value.__ro.disconnect()  // 停止监听容器尺寸，避免泄漏
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
