<!--
  Taskbar.vue — 底部任务栏
  作用：仿桌面系统的底部任务栏。左侧「开始」按钮弹出应用菜单；中间列出当前已打开
        的窗口（点击可在最小化 / 激活间切换）；右侧显示实时时钟。
  数据：窗口列表来自 desktop 状态单例；时钟由本地定时器每秒刷新。
  打开方式：由 App.vue 固定在桌面底部渲染，常驻可见。
-->
<template>
  <div class="taskbar">
    <div class="taskbar-start" @click.stop="showStart = !showStart">
      <span><LayoutGrid :size="20" /></span>
    </div>
    <div
      v-for="w in desktop.windows"
      :key="w.id"
      class="taskbar-item"
      :class="{ active: w.active && !w.minimized }"
      @click="toggleWindow(w.id)"
    >
      <span><component :is="iconFor(w.type)" :size="20" /></span>
      <span class="task-title">{{ w.title }}</span>
    </div>
    <div class="taskbar-clock">{{ time }}</div>

    <!-- Start menu overlay -->
    <div v-if="showStart" class="start-menu" @click.stop>
      <div class="start-header">Server Panel</div>
      <div class="start-list">
        <div class="start-item" @click="openApp('docker','Docker 管理',{width:900,height:600})"><Container :size="16" /> Docker 管理</div>
        <div class="start-item" @click="openApp('process','进程管理',{width:900,height:600})"><BarChart3 :size="16" /> 进程管理</div>
        <div class="start-item" @click="openApp('file','文件管理',{width:900,height:600})"><Folder :size="16" /> 文件管理</div>
        <div class="start-item" @click="openApp('terminal','终端',{width:800,height:520})"><Terminal :size="16" /> 终端</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'                                       // Vue 响应式与生命周期
import { desktop } from '../store/desktop.js'                                              // 桌面窗口状态单例
import { LayoutGrid, Container, BarChart3, Folder, Terminal, Package } from 'lucide-vue-next' // 任务栏与菜单图标

const time = ref('')          // 任务栏时钟文本
const showStart = ref(false)  // 开始菜单是否展开
let timer = null              // setInterval 句柄，卸载时清除

// 刷新时钟：中文 24 小时制文本
function updateTime() {
  const now = new Date()
  time.value = now.toLocaleTimeString('zh-CN', { hour12: false })
}

// 任务栏项点击：在「激活前台」与「最小化收起」间切换
function toggleWindow(id) {
  const w = desktop.windows.find(x => x.id === id)
  if (!w) return                         // 窗口已被关闭则忽略
  if (w.minimized || !w.active) {
    desktop.activate(id)                // 已最小化或未激活 → 激活并置于前台
  } else {
    desktop.minimize(id)                // 已是前台窗口 → 收起到任务栏
  }
}

// 根据窗口类型映射对应图标
function iconFor(type) {
  switch (type) {
    case 'docker': return Container
    case 'process': return BarChart3
    case 'file': return Folder
    case 'terminal': return Terminal
    default: return Package             // 未匹配类型时兜底图标
  }
}

// 从开始菜单打开应用：先收起菜单再派发打开请求
function openApp(type, title, opts) {
  showStart.value = false
  desktop.open(type, title, opts)
}

onMounted(() => {
  updateTime()
  timer = setInterval(updateTime, 1000)   // 每秒刷新时钟
  // 点击页面任意处收起开始菜单（菜单自身 @click.stop 阻止冒泡）
  window.addEventListener('click', () => { showStart.value = false })
})
onBeforeUnmount(() => {
  clearInterval(timer)
})
</script>

<style scoped>
.taskbar {
  position: fixed;
  left: 50%;
  transform: translateX(-50%);
  bottom: 12px;
  height: 64px;
  padding: 6px 10px;
  background: rgba(255, 255, 255, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: 22px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.6);
  backdrop-filter: saturate(180%) blur(30px);
  -webkit-backdrop-filter: saturate(180%) blur(30px);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  z-index: 9999;
  width: auto;
  max-width: calc(100% - 40px);
}
.taskbar-start {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: rgba(0, 0, 0, 0.06);
  color: #1d1d1f;
  cursor: pointer;
  border: none;
}
.taskbar-start:hover { background: rgba(0, 0, 0, 0.12); }
.taskbar-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  height: 52px;
  border-radius: 12px;
  background: transparent;
  border: none;
  color: #1d1d1f;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  margin-left: 0;
  max-width: 200px;
  overflow: hidden;
  white-space: nowrap;
  position: relative;
}
.taskbar-item.active {
  background: rgba(0, 0, 0, 0.08);
}
.taskbar-item.active::after {
  content: '';
  position: absolute;
  bottom: 2px;
  left: 50%;
  transform: translateX(-50%);
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #1d1d1f;
}
.taskbar-item:hover {
  background: rgba(0, 0, 0, 0.06);
}
.task-title {
  overflow: hidden;
  text-overflow: ellipsis;
}
.taskbar-clock {
  margin-left: 6px;
  padding: 0 12px;
  height: 52px;
  display: inline-flex;
  align-items: center;
  color: #1d1d1f;
  font-size: 12px;
  border-left: 1px solid rgba(0, 0, 0, 0.1);
}
.start-menu {
  position: absolute;
  bottom: 76px;
  left: 12px;
  width: 240px;
  background: rgba(246, 246, 248, 0.85);
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 14px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.25);
  backdrop-filter: saturate(180%) blur(30px);
  -webkit-backdrop-filter: saturate(180%) blur(30px);
  overflow: hidden;
  z-index: 10000;
}
.start-header {
  color: #1d1d1f;
  padding: 12px 16px 8px;
  font-weight: 600;
  font-size: 13px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.start-list {
  padding: 6px;
}
.start-item {
  padding: 8px 12px;
  font-size: 13px;
  color: #1d1d1f;
  cursor: pointer;
  border-radius: 8px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.start-item:hover {
  background: rgba(0, 0, 0, 0.06);
}
</style>
