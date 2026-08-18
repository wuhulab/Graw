<template>
  <Login v-if="!loggedIn" @login="onLoggedIn" />
  <div v-else class="desktop" :style="desktopBgStyle">
    <div class="desktop-content">
      <!-- Shortcuts -->
      <div class="shortcuts">
        <div
          v-for="sc in visibleShortcuts"
          :key="sc.key"
          class="shortcut"
          :class="{ selected: selected === sc.key }"
          @click="selected = sc.key"
          @dblclick="openWindow(sc.key)"
        >
          <div class="icon"><component :is="sc.icon" :size="32" /></div>
          <div class="label" :title="sc.titleKey ? $t(sc.titleKey) : sc.label">{{ sc.titleKey ? $t(sc.titleKey) : sc.label }}</div>
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
      <component :is="w.component" v-bind="w.props || {}" @close="handleCloseWindow(w.id)" @dirty="(v) => { const ww=openWindows.value.find(x=>x.id===w.id); if(ww) ww.dirty=v }" @openTerminal="openTerminalAt" @openEditor="openEditor" @openMedia="openMedia" @openUsers="openUsers" @openLogs="openContainerLogs" @openContainerTerminal="openContainerTerminal" @openContainerDetails="openContainerDetails" @openFiles="openFiles" @openDockerConfigEditor="openDockerConfigEditor" @openAppInstall="openAppStoreInstall" @openComposeEditor="openAppStoreComposeEditor" @openInstallLog="openAppStoreInstallLog" @openReadme="openAppStoreReadme" @openTaskCenter="openTasks" @openRuntimeCreate="openRuntimeCreate" @openConnectionForm="openConnectionForm" />
    </WindowFrame>

    <!-- Dock -->
    <div class="taskbar">
      <div class="start-button" title="Launchpad" @click.stop="toggleStartMenu"><LayoutGrid :size="22" /></div>
      <div v-if="startMenuOpen" class="start-menu" @click.stop>
        <div class="start-header">
          <div style="font-weight:700;">{{ auth.user?.username }}</div>
          <div style="font-size:11px;color:#6e6e73;">{{ $t(auth.user?.role === 'admin' ? 'app.admin' : 'app.normalUser') }}</div>
        </div>
        <div class="start-list">
          <button v-if="isAdmin()" class="start-item" @click="openUsers(); startMenuOpen = false"><UserCircle2 :size="16" /> {{ $t('app.accountManage') }}</button>
          <button class="start-item" @click="openChangePwd(); startMenuOpen = false"><UserCircle2 :size="16" /> {{ $t('app.changePassword') }}</button>
          <button class="start-item" @click="openSettings(); startMenuOpen = false"><Settings :size="16" /> {{ $t('app.settings') }}</button>
          <button class="start-item danger" @click="doLogout"><LogOut :size="16" /> {{ $t('app.logout') }}</button>
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
          <span v-if="settings.showTaskbarText || settings.taskbarTextOnly" class="title">{{ w.titleKey ? $t(w.titleKey, w.titleArgs) : w.title }}</span>
        </div>
      </div>
      <div class="clock">
        <div v-if="hostBadgeText && isAdmin()" class="host-badge" :class="{ remote: hostBadgeRemote }" :title="hostBadgeRemote ? hostBadgeText : ''">
          <span class="dot"></span>{{ hostBadgeText }}
        </div>
        <div>{{ clockTime }}</div>
        <div>{{ clockDate }}</div>
      </div>
    </div>
  </div>

  <!-- ShunX 网页防篡改告警弹窗：篡改发生时对在线面板用户弹窗 -->
  <TamperAlert v-if="loggedIn && tamperState.alerts.length > 0" />

  <!-- ShunX 安全入口：登录后未配置入口时强制设置，阻止使用面板其他功能 -->
  <ShunXSetup v-if="loggedIn && shunxRequired" @saved="onShunxSaved" />
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
import DatabaseWindow from './components/windows/DatabaseWindow.vue'
import EditorWindow from './components/windows/EditorWindow.vue'
import MediaWindow from './components/windows/MediaWindow.vue'
import UserWindow from './components/windows/UserWindow.vue'
import ChangePasswordWindow from './components/windows/ChangePasswordWindow.vue'
import CronWindow from './components/windows/CronWindow.vue'
import FirewallWindow from './components/windows/FirewallWindow.vue'
import SSLWindow from './components/windows/SSLWindow.vue'
import LogsWindow from './components/windows/LogsWindow.vue'
import SettingsWindow from './components/windows/SettingsWindow.vue'
import ProtectionWindow from './components/windows/ProtectionWindow.vue'
import TamperWindow from './components/windows/TamperWindow.vue'
import ContainerLogsWindow from './components/windows/ContainerLogsWindow.vue'
import ContainerDetailWindow from './components/windows/ContainerDetailWindow.vue'
import DockerConfigEditorWindow from './components/windows/DockerConfigEditorWindow.vue'
import AppStoreWindow from './components/windows/AppStoreWindow.vue'
import AppStoreInstallWindow from './components/windows/AppStoreInstallWindow.vue'
import AppStoreComposeEditorWindow from './components/windows/AppStoreComposeEditorWindow.vue'
import AppStoreInstallLogWindow from './components/windows/AppStoreInstallLogWindow.vue'
import AppStoreReadmeWindow from './components/windows/AppStoreReadmeWindow.vue'
import TaskCenterWindow from './components/windows/TaskCenterWindow.vue'
import UISettingsWindow from './components/windows/UISettingsWindow.vue'
import ConnectionFormWindow from './components/windows/ConnectionFormWindow.vue'
import RuntimeWindow from './components/windows/RuntimeWindow.vue'
import RuntimeCreateWindow from './components/windows/RuntimeCreateWindow.vue'
import DisksWindow from './components/windows/DisksWindow.vue'
import ShunXSetup from './components/ShunXSetup.vue'
import TamperAlert from './components/TamperAlert.vue'
import Login from './views/Login.vue'
import { shunxApi } from './api'
import { auth, clearAuth, isAdmin } from './store/auth'
import { uiState, loadUi } from './store/ui'
import { settings } from './store/settings'
import { systemState, startMetrics, stopMetrics } from './store/systemMetrics'
import { startDocker, stopDocker, refresh as refreshDocker } from './store/docker'
import { nodes as nodesStore, refreshNodes } from './store/nodes'
import { tamperState, startTamper, stopTamper } from './store/tamper'
import { Container, Settings, Folder, Terminal, FileText, Image as ImageIcon, Film, LogOut, LayoutGrid, UserCircle2, Globe, Database, Clock, Shield, Lock, ScrollText, ShieldCheck, ShieldAlert, Store, BookOpen, ListChecks, Cpu, HardDrive, Palette } from 'lucide-vue-next'

const loggedIn = computed(() => !!auth.token)

// 桌面背景样式：与登录页共用同一份界面配置（自定义背景或回退默认 hero.png）
const desktopBgStyle = computed(() => {
  if (uiState.background) {
    return {
      backgroundImage: `url('${uiState.background}')`,
      backgroundSize: 'cover',
      backgroundPosition: 'center',
    }
  }
  return {}
})

// 当前管理主机：用于底栏指示（多机管理），切换后自动响应式更新
const currentHost = computed(() => {
  const cur = nodesStore.list.find((n) => n.id === nodesStore.currentId)
  return cur || null
})
const hostBadgeText = computed(() => {
  if (!currentHost.value) return ''
  return currentHost.value.type === 'ssh' ? `${currentHost.value.name} · ${currentHost.value.user}@${currentHost.value.host}` : currentHost.value.name
})
const hostBadgeRemote = computed(() => !!(currentHost.value && currentHost.value.type === 'ssh'))
function onLoggedIn() {
  // 触发响应式重渲染，并检查是否需要强制设置安全入口
  checkShunxRequired()
}

const shortcuts = ref([
  { key: 'sites', label: '网站', titleKey: 'app.shortcut.sites', icon: markRaw(Globe), component: markRaw(SitesWindow), w: 900, h: 560, adminOnly: true },
  { key: 'database', label: '数据库', titleKey: 'app.shortcut.database', icon: markRaw(Database), component: markRaw(DatabaseWindow), w: 860, h: 540, adminOnly: true },
  { key: 'cron', label: '计划任务', titleKey: 'app.shortcut.cron', icon: markRaw(Clock), component: markRaw(CronWindow), w: 800, h: 520, adminOnly: true },
  { key: 'firewall', label: '防火墙', titleKey: 'app.shortcut.firewall', icon: markRaw(Shield), component: markRaw(FirewallWindow), w: 800, h: 540, adminOnly: true },
  { key: 'ssl', label: 'SSL', titleKey: 'app.shortcut.ssl', icon: markRaw(Lock), component: markRaw(SSLWindow), w: 820, h: 520, adminOnly: true },
  { key: 'logs', label: '日志', titleKey: 'app.shortcut.logs', icon: markRaw(ScrollText), component: markRaw(LogsWindow), w: 900, h: 560, adminOnly: true },
  { key: 'docker', label: 'Docker', titleKey: 'app.shortcut.docker', icon: markRaw(Container), component: markRaw(DockerWindow), w: 820, h: 520, adminOnly: true },
  { key: 'appstore', label: '应用商店', titleKey: 'app.shortcut.appstore', icon: markRaw(Store), component: markRaw(AppStoreWindow), w: 920, h: 580, adminOnly: true },
  { key: 'tasks', label: '任务中心', titleKey: 'app.shortcut.tasks', icon: markRaw(ListChecks), component: markRaw(TaskCenterWindow), w: 900, h: 560, adminOnly: true },
  { key: 'protection', label: 'Graw数据库保护机制', titleKey: 'app.shortcut.protection', icon: markRaw(ShieldCheck), component: markRaw(ProtectionWindow), w: 860, h: 560, adminOnly: true },
  { key: 'tamper', label: 'ShunX网页防篡改', titleKey: 'app.shortcut.tamper', icon: markRaw(ShieldAlert), component: markRaw(TamperWindow), w: 920, h: 580, adminOnly: true },
  { key: 'runtime', label: '运行环境', titleKey: 'app.shortcut.runtime', icon: markRaw(Cpu), component: markRaw(RuntimeWindow), w: 900, h: 560, adminOnly: true },
  { key: 'process', label: '进程管理', titleKey: 'app.shortcut.process', icon: markRaw(Settings), component: markRaw(ProcessWindow), w: 780, h: 520, adminOnly: true },
  { key: 'files', label: '文件管理', titleKey: 'app.shortcut.files', icon: markRaw(Folder), component: markRaw(FilesWindow), w: 820, h: 540, adminOnly: true },
  { key: 'uisettings', label: '界面设置', titleKey: 'app.shortcut.uisettings', icon: markRaw(Palette), component: markRaw(UISettingsWindow), w: 520, h: 540, adminOnly: true },
  { key: 'disks', label: '磁盘管理', titleKey: 'app.shortcut.disks', icon: markRaw(HardDrive), component: markRaw(DisksWindow), w: 900, h: 560, adminOnly: true },
  { key: 'terminal', label: '终端', titleKey: 'app.shortcut.terminal', icon: markRaw(Terminal), component: markRaw(TerminalWindow), w: 780, h: 460, adminOnly: true }
])

// 桌面快捷方式：管理员可见全部，普通用户仅可见非管理功能
const visibleShortcuts = computed(() => shortcuts.value.filter(s => !s.adminOnly || isAdmin()))

const selected = ref(null)
const openWindows = ref([])
const activeWindowId = ref(null)
const startMenuOpen = ref(false)

// ShunX 安全入口：登录后检查是否已配置，未配置则强制设置
const shunxRequired = ref(false)

async function checkShunxRequired() {
  if (!auth.token) return
  try {
    const config = await shunxApi.config()
    if (!config.entry_path) {
      shunxRequired.value = true
    }
  } catch (e) {
    // 接口失败时允许进入面板（兼容旧版后端）
    shunxRequired.value = false
  }
}

function onShunxSaved() {
  shunxRequired.value = false
}
let windowSeq = 0
let zSeq = 100

function toggleStartMenu() { startMenuOpen.value = !startMenuOpen.value }
function openUsers() { openWindow('users') }
function openChangePwd() { openWindow('changepwd') }
function openSettings() { openWindow('settings') }
function openTasks() { openWindow('tasks') }

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
      users: { label: '账号管理', titleKey: 'app.winTitle.users', icon: markRaw(UserCircle2), component: markRaw(UserWindow), w: 600, h: 460, adminOnly: true },
      changepwd: { label: '修改密码', titleKey: 'app.winTitle.changepwd', icon: markRaw(UserCircle2), component: markRaw(ChangePasswordWindow), w: 420, h: 360 },
      settings: { label: '设置', titleKey: 'app.winTitle.settings', icon: markRaw(Settings), component: markRaw(SettingsWindow), w: 520, h: 480 }
    }
    def = extras[key]
    if (!def) return
    if (def.adminOnly && !isAdmin()) return
  }
  const id = ++windowSeq
  const w = reactive({
    id,
    key,
    title: def.label,
    titleKey: def.titleKey,
    titleArgs: def.titleArgs,
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
    titleKey: 'app.shortcut.terminal',
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
    titleKey: 'app.winTitle.editor',
    titleArgs: { name: path.split(/[\\/]/).pop() },
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
    titleKey: type === 'image' ? 'app.winTitle.image' : 'app.winTitle.video',
    titleArgs: { name },
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

function openContainerLogs({ id, name }) {
  const id2 = ++windowSeq
  const w = reactive({
    id: id2,
    key: 'container-logs',
    title: '日志: ' + name,
    titleKey: 'app.winTitle.containerLogs',
    titleArgs: { name },
    icon: markRaw(ScrollText),
    component: markRaw(ContainerLogsWindow),
    props: { id, name },
    x: 140 + (openWindows.value.length * 30),
    y: 60 + (openWindows.value.length * 25),
    width: 760,
    height: 520,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id2
}

// Docker：打开容器内终端（进入容器 shell）
function openContainerTerminal({ id, name }) {
  const id2 = ++windowSeq
  const w = reactive({
    id: id2,
    key: 'terminal',
    title: '容器终端: ' + name,
    titleKey: 'app.winTitle.containerTerminal',
    titleArgs: { name },
    icon: markRaw(Terminal),
    component: markRaw(TerminalWindow),
    props: { container: id },
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
  activeWindowId.value = id2
}

// Docker：打开容器详细信息窗口
function openContainerDetails({ id, name }) {
  const id2 = ++windowSeq
  const w = reactive({
    id: id2,
    key: 'container-details',
    title: '容器详情: ' + name,
    titleKey: 'app.winTitle.containerDetails',
    titleArgs: { name },
    icon: markRaw(Container),
    component: markRaw(ContainerDetailWindow),
    props: { id, name },
    x: 150 + (openWindows.value.length * 30),
    y: 70 + (openWindows.value.length * 25),
    width: 640,
    height: 520,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id2
}

// 打开文件管理窗口（Docker「进入安装目录」等场景，指定初始路径）
function openFiles({ path }) {
  const id2 = ++windowSeq
  const w = reactive({
    id: id2,
    key: 'files',
    title: '文件管理',
    titleKey: 'app.winTitle.files',
    icon: markRaw(Folder),
    component: markRaw(FilesWindow),
    props: { path },
    x: 140 + (openWindows.value.length * 30),
    y: 60 + (openWindows.value.length * 25),
    width: 820,
    height: 540,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id2
}

// Docker：打开 Docker/Podman 引擎配置文件编辑器
function openDockerConfigEditor() {
  const id2 = ++windowSeq
  const w = reactive({
    id: id2,
    key: 'docker-config-editor',
    title: 'Docker 配置文件',
    titleKey: 'app.winTitle.dockerConfig',
    icon: markRaw(FileText),
    component: markRaw(DockerConfigEditorWindow),
    props: {},
    x: 150 + (openWindows.value.length * 30),
    y: 70 + (openWindows.value.length * 25),
    width: 720,
    height: 520,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id2
}

// 应用商店：点击「安装」打开独立的安装配置窗口
function openAppStoreInstall(app) {
  const id = ++windowSeq
  const w = reactive({
    id,
    key: 'appstore-install',
    title: '安装: ' + (app?.name || ''),
    titleKey: 'app.winTitle.appInstall',
    titleArgs: { name: app?.name || '' },
    icon: markRaw(Store),
    component: markRaw(AppStoreInstallWindow),
    props: { app },
    x: 160 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 720,
    height: 660,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 应用商店：点击「编辑 compose」打开独立的 compose 编辑器窗口（与标准文件编辑器一致）
function openAppStoreComposeEditor({ appId, compose }) {
  const id = ++windowSeq
  const w = reactive({
    id,
    key: 'appstore-compose-editor',
    title: '编辑: docker-compose.yml (' + appId + ')',
    titleKey: 'app.winTitle.appComposeEditor',
    titleArgs: { name: appId },
    icon: markRaw(FileText),
    component: markRaw(AppStoreComposeEditorWindow),
    props: { appId, compose },
    x: 180 + (openWindows.value.length * 30),
    y: 100 + (openWindows.value.length * 25),
    width: 720,
    height: 520,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 应用商店：点击「确认安装」打开独立的安装日志窗口（SSE 流式展示）
function openAppStoreInstallLog({ app, request }) {
  const id = ++windowSeq
  const w = reactive({
    id,
    key: 'appstore-install-log',
    title: '安装日志: ' + (app?.name || ''),
    titleKey: 'app.winTitle.appInstallLog',
    titleArgs: { name: app?.name || '' },
    icon: markRaw(ScrollText),
    component: markRaw(AppStoreInstallLogWindow),
    props: { app, request },
    x: 200 + (openWindows.value.length * 30),
    y: 120 + (openWindows.value.length * 25),
    width: 780,
    height: 540,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 应用商店：点击「README」打开独立的 README 展示窗口
function openAppStoreReadme(app) {
  const id = ++windowSeq
  const w = reactive({
    id,
    key: 'appstore-readme',
    title: 'README: ' + (app?.name || ''),
    titleKey: 'app.winTitle.appReadme',
    titleArgs: { name: app?.name || '' },
    icon: markRaw(BookOpen),
    component: markRaw(AppStoreReadmeWindow),
    props: { app },
    x: 180 + (openWindows.value.length * 30),
    y: 100 + (openWindows.value.length * 25),
    width: 800,
    height: 560,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 运行环境：点选运行时时打开独立的新窗口填写配置
function openRuntimeCreate(type) {
  const id = ++windowSeq
  const w = reactive({
    id,
    key: 'runtime-create',
    title: '创建运行环境',
    titleKey: 'app.winTitle.runtimeCreate',
    icon: markRaw(Cpu),
    component: markRaw(RuntimeCreateWindow),
    props: { type },
    x: 200 + (openWindows.value.length * 30),
    y: 120 + (openWindows.value.length * 25),
    width: 760,
    height: 700,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 数据库：添加/编辑连接在新的独立窗口打开表单（不再内嵌在主窗口弹层中）
function openConnectionForm(conn = null) {
  const id = ++windowSeq
  const w = reactive({
    id,
    key: 'conn-form',
    title: conn ? '编辑: ' + (conn.name || '') : '添加数据库连接',
    titleKey: conn ? 'app.winTitle.connEdit' : 'app.winTitle.connAdd',
    titleArgs: conn ? { name: conn.name || '' } : undefined,
    icon: markRaw(Database),
    component: markRaw(ConnectionFormWindow),
    props: { conn },
    x: 200 + (openWindows.value.length * 30),
    y: 120 + (openWindows.value.length * 25),
    width: 460,
    height: 560,
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

// 系统概览：改由共享的「单条 WS」指标推送驱动（见 store/systemMetrics.js），
// 这里仅做一块响应式视图，不再各自开 HTTP 轮询。
const overview = computed(() => systemState.overview)

// 连接池启动/停止：登录后统一建立共享指标 WS，并预启动 Docker 后台轮询；
// 退出登录时全部停止，避免未登录时持续请求。
function startRealtime() {
  startMetrics()
  // 网页防篡改告警订阅：登录即建立（篡改发生时对在线用户弹窗）
  startTamper()
  // Docker 为管理员功能：登录即后台预热并缓存，打开窗口可直接渲染上次数据
  if (isAdmin()) {
    startDocker()
    refreshDocker()
    refreshNodes()
  }
}
function stopRealtime() {
  stopMetrics()
  stopTamper()
  stopDocker()
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
  // ShunX 保护兜底：本地若残留「待改密」登录态，回到登录页走强制改密流程
  if (auth.user?.must_change_password) {
    clearAuth()
    location.href = '/'
    return
  }
  // 加载界面品牌配置（网站名/欢迎语/Logo/背景），失败不阻塞面板使用
  loadUi().catch(() => {})
  // 仅在已登录时启动共享实时数据（指标 WS + Docker 轮询）；登录态变化时通过 watch 启停
  if (loggedIn.value) {
    startRealtime()
    checkShunxRequired()
  }
  updateClock()
  clockTimer = setInterval(updateClock, 1000)
  document.addEventListener('mousedown', onDocClick)
})

// 登录态变化时启停共享实时数据，避免未登录时持续请求
watch(loggedIn, (v) => {
  if (v) startRealtime()
  else stopRealtime()
})

onUnmounted(() => {
  stopRealtime()
  clearInterval(clockTimer)
  document.removeEventListener('mousedown', onDocClick)
})
</script>
