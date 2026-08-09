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
      @close="handleCloseWindow(w.id)"
      @minimize="minimizeWindow(w.id)"
      @maximize="toggleMaximize(w.id)"
      @move="(x, y) => moveWindow(w.id, x, y)"
      @resize="(width, height) => resizeWindow(w.id, width, height)"
    >
      <component :is="w.component" v-bind="w.props || {}" @close="handleCloseWindow(w.id)" @dirty="(v) => { const ww=openWindows.value.find(x=>x.id===w.id); if(ww) ww.dirty=v }" @openTerminal="openTerminalAt" @openEditor="openEditor" @openMedia="openMedia" @openUsers="openUsers" />
    </WindowFrame>

    <!-- Dock -->
    <div class="taskbar">
      <div class="start-button" title="Launchpad" @click.stop="toggleStartMenu"><LayoutGrid :size="22" /></div>
      <div v-if="startMenuOpen" class="start-menu" @click.stop>
        <div class="start-header">
          <div style="font-weight:700;">{{ auth.user?.username }}</div>
          <div style="font-size:11px;color:#6e6e73;">{{ auth.user?.role === 'admin' ? '管理员' : '普通用户' }}</div>
        </div>
        <div class="start-list">
          <button v-if="isAdmin()" class="start-item" @click="openUsers(); startMenuOpen = false"><UserCircle2 :size="16" /> 账号管理</button>
          <button class="start-item" @click="openChangePwd(); startMenuOpen = false"><UserCircle2 :size="16" /> 修改密码</button>
          <button class="start-item" @click="openSettings(); startMenuOpen = false"><Settings :size="16" /> 设置</button>
          <button class="start-item danger" @click="doLogout"><LogOut :size="16" /> 退出登录</button>
        </div>
      </div>
      <div class="task-items">
        <div
          v-for="w in openWindows"
          :key="w.id"
          class="task-item"
          :class="{ active: activeWindowId === w.id && !w.minimized, 'icon-only': settings.taskbarTextOnly, 'no-text': !settings.showTaskbarText && !settings.taskbarTextOnly }"
          @click="taskClick(w.id)"
        >
          <span v-if="!settings.taskbarTextOnly" class="icon"><component :is="w.icon" :size="20" /></span>
          <span v-if="settings.showTaskbarText || settings.taskbarTextOnly" class="title">{{ w.title }}</span>
        </div>
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
import SitesWindow from './components/windows/SitesWindow.vue'
import EditorWindow from './components/windows/EditorWindow.vue'
import MediaWindow from './components/windows/MediaWindow.vue'
import UserWindow from './components/windows/UserWindow.vue'
import ChangePasswordWindow from './components/windows/ChangePasswordWindow.vue'
import SettingsWindow from './components/windows/SettingsWindow.vue'
import Login from './views/Login.vue'
import { systemApi } from './api'
import { auth, clearAuth, isAdmin } from './store/auth'
import { settings } from './store/settings'
import { Container, Settings, Folder, Terminal, FileText, Image as ImageIcon, Film, LogOut, LayoutGrid, UserCircle2, Globe } from 'lucide-vue-next'

const loggedIn = computed(() => !!auth.token)
function onLoggedIn() { /* 触发响应式重渲染 */ }

const shortcuts = ref([
  { key: 'sites', label: '网站', icon: markRaw(Globe), component: markRaw(SitesWindow), w: 900, h: 560, adminOnly: false },
  { key: 'docker', label: 'Docker', icon: markRaw(Container), component: markRaw(DockerWindow), w: 820, h: 520, adminOnly: false },
  { key: 'process', label: '进程管理', icon: markRaw(Settings), component: markRaw(ProcessWindow), w: 780, h: 520, adminOnly: false },
  { key: 'files', label: '文件管理', icon: markRaw(Folder), component: markRaw(FilesWindow), w: 820, h: 540, adminOnly: false },
  { key: 'terminal', label: '终端', icon: markRaw(Terminal), component: markRaw(TerminalWindow), w: 780, h: 460, adminOnly: false }
])

const selected = ref(null)
const openWindows = ref([])
const activeWindowId = ref(null)
const startMenuOpen = ref(false)
let windowSeq = 0
let zSeq = 100

function toggleStartMenu() { startMenuOpen.value = !startMenuOpen.value }
function openUsers() { openWindow('users') }
function openChangePwd() { openWindow('changepwd') }
function openSettings() { openWindow('settings') }

function doLogout() {
  startMenuOpen.value = false
  clearAuth()
  location.reload()
}

function onDocClick(e) {
  if (!startMenuOpen.value) return
  const btn = e.target.closest('.start-button')
  const menu = e.target.closest('.start-menu')
  if (!btn && !menu) startMenuOpen.value = false
}

function openWindow(key) {
  let def = shortcuts.value.find(s => s.key === key)
  if (!def) {
    const extras = {
      users: { label: '账号管理', icon: markRaw(UserCircle2), component: markRaw(UserWindow), w: 600, h: 460 },
      changepwd: { label: '修改密码', icon: markRaw(UserCircle2), component: markRaw(ChangePasswordWindow), w: 420, h: 360 },
      settings: { label: '设置', icon: markRaw(Settings), component: markRaw(SettingsWindow), w: 520, h: 480 }
    }
    def = extras[key]
    if (!def) return
    if (key === 'users' && !isAdmin()) return
  }
  const id = ++windowSeq
  const w = reactive({
    id,
    key,
    title: def.label,
    icon: def.icon,
    component: def.component,
    props: {},
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

function openTerminalAt(cwd) {
  const id = ++windowSeq
  const w = reactive({
    id,
    key: 'terminal',
    title: '终端',
    icon: markRaw(Terminal),
    component: markRaw(TerminalWindow),
    props: { cwd },
    x: 140 + (openWindows.value.length * 30),
    y: 60 + (openWindows.value.length * 25),
    width: 780,
    height: 460,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

function openEditor({ path, content }) {
  const id = ++windowSeq
  const w = reactive({
    id,
    key: 'editor',
    title: '编辑: ' + path.split(/[\\/]/).pop(),
    icon: markRaw(FileText),
    component: markRaw(EditorWindow),
    props: { path, content },
    dirty: false,
    x: 140 + (openWindows.value.length * 30),
    y: 60 + (openWindows.value.length * 25),
    width: 780,
    height: 520,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

function openMedia({ path, name, type }) {
  const id = ++windowSeq
  const title = (type === 'image' ? '图片' : '视频') + ': ' + name
  const w = reactive({
    id,
    key: 'media',
    title,
    icon: markRaw(type === 'image' ? ImageIcon : Film),
    component: markRaw(MediaWindow),
    props: { path, name, type },
    x: 140 + (openWindows.value.length * 30),
    y: 60 + (openWindows.value.length * 25),
    width: 780,
    height: 520,
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

function handleCloseWindow(id) {
  const w = openWindows.value.find(x => x.id === id)
  if (w?.key === 'editor' && w.dirty) {
    if (!confirm('文件已修改，是否关闭？')) return
  }
  closeWindow(id)
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
