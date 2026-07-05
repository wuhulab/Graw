<template>
  <Login v-if="!loggedIn" @login="onLoggedIn" />
  <div v-else class="desktop">
    <div class="desktop-content">
      <!-- Shortcuts -->
      <div class="shortcuts">
        <div
          v-for="sc in shortcuts"
          :key="sc.key"
          class="shortcut"
          :class="{ selected: selected === sc.key }"
          @click="selected = sc.key"
          @dblclick="openWindow(sc.key)"
        >
          <div class="icon"><component :is="sc.icon" :size="32" /></div>
          <div class="label">{{ sc.label }}</div>
        </div>
      </div>

      <!-- Spacer (center) -->
      <div></div>

      <!-- Right cards -->
      <div class="right-cards">
        <RingCard :overview="overview" />
        <MonitorCard />
        <InfoNotesCard />
      </div>
    </div>

    <!-- Windows -->
    <WindowFrame
      v-for="w in openWindows"
      :key="w.id"
      :window="w"
      :active="activeWindowId === w.id"
      @focus="focusWindow(w.id)"
      @close="closeWindow(w.id)"
      @minimize="minimizeWindow(w.id)"
      @maximize="toggleMaximize(w.id)"
      @move="(x, y) => moveWindow(w.id, x, y)"
      @resize="(width, height) => resizeWindow(w.id, width, height)"
    >
      <component :is="w.component" />
    </WindowFrame>

    <!-- Dock -->
    <div class="taskbar">
      <div class="start-button" title="Launchpad"><LayoutGrid :size="22" /></div>
      <div class="task-items">
        <div
          v-for="w in openWindows"
          :key="w.id"
          class="task-item"
          :class="{ active: activeWindowId === w.id && !w.minimized }"
          @click="taskClick(w.id)"
        >
          <span class="icon"><component :is="w.icon" :size="20" /></span>
          <span class="title">{{ w.title }}</span>
        </div>
      </div>
      <div class="user-chip" :title="auth.user?.role === 'admin' ? '管理员' : '用户'" @click="toggleUserMenu">
        <UserCircle2 :size="20" />
        <span class="name">{{ auth.user?.username }}</span>
        <span v-if="auth.user?.role === 'admin'" class="role-badge">管理</span>
      </div>
      <div v-if="userMenuOpen" class="user-menu" @click.stop>
        <div class="meta">
          <div class="nm">{{ auth.user?.username }}</div>
          <div>{{ auth.user?.role === 'admin' ? '管理员' : '普通用户' }}</div>
        </div>
        <button v-if="isAdmin()" class="item" @click="openUsers; userMenuOpen = false">账号管理</button>
        <button class="item" @click="openChangePwd; userMenuOpen = false">修改密码</button>
        <button class="item danger" @click="doLogout">退出登录</button>
      </div>
      <div class="clock">
        <div>{{ clockTime }}</div>
        <div>{{ clockDate }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, shallowRef, markRaw, watch } from 'vue'
import RingCard from './components/cards/RingCard.vue'
import MonitorCard from './components/cards/MonitorCard.vue'
import InfoNotesCard from './components/cards/InfoNotesCard.vue'
import WindowFrame from './components/WindowFrame.vue'
import DockerWindow from './components/windows/DockerWindow.vue'
import ProcessWindow from './components/windows/ProcessWindow.vue'
import FilesWindow from './components/windows/FilesWindow.vue'
import TerminalWindow from './components/windows/TerminalWindow.vue'
import UserWindow from './components/windows/UserWindow.vue'
import ChangePasswordWindow from './components/windows/ChangePasswordWindow.vue'
import Login from './views/Login.vue'
import { systemApi } from './api'
import { auth, clearAuth, isAdmin } from './store/auth'
import { Container, Settings, Folder, Terminal, LayoutGrid, UserCircle2 } from 'lucide-vue-next'

const loggedIn = computed(() => !!auth.token)
function onLoggedIn() { /* 触发响应式重渲染 */ }

const shortcuts = ref([
  { key: 'docker', label: 'Docker', icon: markRaw(Container), component: markRaw(DockerWindow), w: 820, h: 520, adminOnly: false },
  { key: 'process', label: '进程管理', icon: markRaw(Settings), component: markRaw(ProcessWindow), w: 780, h: 520, adminOnly: false },
  { key: 'files', label: '文件管理', icon: markRaw(Folder), component: markRaw(FilesWindow), w: 820, h: 540, adminOnly: false },
  { key: 'terminal', label: '终端', icon: markRaw(Terminal), component: markRaw(TerminalWindow), w: 780, h: 460, adminOnly: false },
  { key: 'changepwd', label: '修改密码', icon: markRaw(UserCircle2), component: markRaw(ChangePasswordWindow), w: 420, h: 360, adminOnly: false }
])
if (isAdmin()) {
  shortcuts.value.push({
    key: 'users', label: '账号管理', icon: markRaw(UserCircle2),
    component: markRaw(UserWindow), w: 600, h: 460, adminOnly: true
  })
}

const selected = ref(null)
const openWindows = ref([])
const activeWindowId = ref(null)
const userMenuOpen = ref(false)
let windowSeq = 0
let zSeq = 100

function toggleUserMenu() { userMenuOpen.value = !userMenuOpen.value }
function openUsers() { openWindow('users') }
function openChangePwd() { openWindow('changepwd') }

function doLogout() {
  userMenuOpen.value = false
  clearAuth()
  location.reload()
}

function onDocClick(e) {
  if (!userMenuOpen.value) return
  const chip = e.target.closest('.user-chip')
  const menu = e.target.closest('.user-menu')
  if (!chip && !menu) userMenuOpen.value = false
}

function openWindow(key) {
  const def = shortcuts.value.find(s => s.key === key)
  if (!def) return
  const id = ++windowSeq
  const w = reactive({
    id,
    key,
    title: def.label,
    icon: def.icon,
    component: def.component,
    x: 140 + (openWindows.value.length * 30),
    y: 60 + (openWindows.value.length * 25),
    width: def.w,
    height: def.h,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

function focusWindow(id) {
  const w = openWindows.value.find(x => x.id === id)
  if (!w) return
  w.z = ++zSeq
  w.minimized = false
  activeWindowId.value = id
}

function closeWindow(id) {
  openWindows.value = openWindows.value.filter(w => w.id !== id)
  if (activeWindowId.value === id) activeWindowId.value = null
}

function minimizeWindow(id) {
  const w = openWindows.value.find(x => x.id === id)
  if (w) w.minimized = true
}

function toggleMaximize(id) {
  const w = openWindows.value.find(x => x.id === id)
  if (!w) return
  if (w.maximized) {
    Object.assign(w, w.prev)
    w.maximized = false
    w.prev = null
  } else {
    w.prev = { x: w.x, y: w.y, width: w.width, height: w.height }
    w.x = 0
    w.y = 0
    w.width = window.innerWidth
    w.height = window.innerHeight - 90
    w.maximized = true
  }
}

function moveWindow(id, x, y) {
  const w = openWindows.value.find(v => v.id === id)
  if (w) { w.x = x; w.y = y }
}

function resizeWindow(id, width, height) {
  const w = openWindows.value.find(v => v.id === id)
  if (w) { w.width = width; w.height = height }
}

function taskClick(id) {
  const w = openWindows.value.find(x => x.id === id)
  if (!w) return
  if (w.minimized) {
    w.minimized = false
    focusWindow(id)
  } else if (activeWindowId.value === id) {
    w.minimized = true
  } else {
    focusWindow(id)
  }
}

// Overview polling
const overview = ref({
  cpu: 0,
  memory: { percent: 0, total: 0, used: 0 },
  storage: { percent: 0, total: 0, used: 0 },
  load: { percent: 0, load1: 0 }
})
let overviewTimer = null
async function refreshOverview() {
  if (!auth.token) return
  try { overview.value = await systemApi.overview() } catch (e) { /* ignore */ }
}

function startOverview() {
  if (overviewTimer) return
  refreshOverview()
  overviewTimer = setInterval(refreshOverview, 2000)
}
function stopOverview() {
  if (overviewTimer) { clearInterval(overviewTimer); overviewTimer = null }
}

// Clock
const clockTime = ref('')
const clockDate = ref('')
let clockTimer = null
function updateClock() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  clockTime.value = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  clockDate.value = `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`
}

onMounted(() => {
  // 仅在已登录时启动业务轮询；登录态变化时通过 watch 启停
  if (loggedIn.value) startOverview()
  updateClock()
  clockTimer = setInterval(updateClock, 1000)
  document.addEventListener('mousedown', onDocClick)
})

// 登录态变化时启停系统概览轮询，避免未登录时持续打 401
watch(loggedIn, (v) => {
  if (v) startOverview()
  else stopOverview()
})

onUnmounted(() => {
  stopOverview()
  clearInterval(clockTimer)
  document.removeEventListener('mousedown', onDocClick)
})
</script>
