<!-- Graw 桌面环境根组件：类 macOS 的「类桌面操作系统」界面。
     登录前显示 Login 视图；登录后渲染桌面（动态壁纸 + 快捷方式 + 右侧监控卡片）、
     窗口系统（独立窗口组件，支持拖拽 / 最小化 / 最大化）、Dock 式任务栏与开始菜单。
     核心状态：登录态 auth、已打开窗口列表 openWindows、当前聚焦窗口、管理节点（多机）、
     VIP / 统一面板兼容门控、ShunX 安全入口与网页防篡改告警。
     窗口按 shortcuts 清单打开各自功能组件；多节点经 X-Graw-Node 透传（见 api.js）。
     打开 / 聚焦窗口即同步请求目标节点，避免切换主机后首个请求打到旧节点。 -->

<template>
  <Login v-if="!loggedIn" @login="onLoggedIn" />
  <div v-else class="desktop" :style="desktopBgStyle">
    <!-- 动态壁纸层：视频壁纸或图片轮播（置于桌面内容之下） -->
    <div v-if="wallpaperVideo" class="wallpaper-video">
      <video :src="wallpaperVideo" autoplay muted loop playsinline></video>
      <div class="wallpaper-video-mask"></div>
    </div>
    <div v-else-if="carouselImages.length > 1" class="wallpaper-carousel">
      <div
        v-for="(_, i) in carouselImages"
        :key="i"
        class="wallpaper-carousel-slide"
        :class="{ active: i === carouselIndex }"
        :style="{ backgroundImage: `url('${carouselImages[i]}')` }"
      ></div>
      <div class="wallpaper-carousel-mask"></div>
    </div>
    <div class="desktop-content">
      <!-- Shortcuts -->
      <div class="shortcuts">
        <div
          v-for="sc in visibleShortcuts"
          :key="sc.key"
          class="shortcut"
          :class="{ selected: selected === sc.key }"
          @click="onShortcutClick(sc.key)"
          @dblclick="openShortcut(sc.key)"
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
      <component :is="w.component" v-bind="w.props || {}" @close="handleCloseWindow(w.id)" @dirty="(v) => { const ww=openWindows.value.find(x=>x.id===w.id); if(ww) ww.dirty=v }" @openTerminal="openTerminalAt" @openEditor="openEditor" @openMedia="openMedia" @openUsers="openUsers" @openVip="openVip" @openLogs="openContainerLogs" @openContainerTerminal="openContainerTerminal" @openContainerDetails="openContainerDetails" @openContainerStats="openContainerStats" @openContainerEdit="openContainerEdit" @openFiles="openFiles" @openDockerConfigEditor="openDockerConfigEditor" @openAppInstall="openAppStoreInstall" @openComposeEditor="openAppStoreComposeEditor" @openInstallLog="openAppStoreInstallLog" @openReadme="openAppStoreReadme" @openTaskCenter="openTasks" @openRuntimeCreate="openRuntimeCreate" @openConnectionForm="openConnectionForm" @openNetStorageBrowse="openNetStorageBrowse" @openNetStorageForm="openNetStorageForm" @openSiteEdit="openSiteEdit" />
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
          <button class="start-item" @click="reportIssue(); startMenuOpen = false"><Bug :size="16" /> {{ $t('app.reportIssue') }}</button>
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
        <div v-if="hostBadgeText && isAdmin()" class="host-badge" :class="{ remote: hostBadgeRemote }" :title="hostBadgeTitle">
          <span class="dot"></span>{{ hostBadgeText }}
        </div>
        <div>{{ clockTime }}</div>
        <div>{{ clockDate }}</div>
      </div>
    </div>
  </div>

  <!-- ShunX 网页防篡改告警弹窗：篡改发生时对在线面板用户弹窗 -->
  <TamperAlert v-if="loggedIn && tamperState.alerts.length > 0" />

  <!-- 安装环境提醒：未按 README 完整宿主机模式安装、缺少宿主机权限时弹窗 -->
  <InstallCheckAlert v-if="loggedIn && installCheckMissing.length" :missing="installCheckMissing" @close="installCheckMissing = []" />

  <!-- ShunX 安全入口：登录后未配置入口时强制设置，阻止使用面板其他功能 -->
  <ShunXSetup v-if="loggedIn && shunxRequired" @saved="onShunxSaved" />
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, shallowRef, markRaw, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import RingCard from './components/cards/RingCard.vue'
import MonitorCard from './components/cards/MonitorCard.vue'
import InfoNotesCard from './components/cards/InfoNotesCard.vue'
import WindowFrame from './components/WindowFrame.vue'
import DockerWindow from './components/windows/DockerWindow.vue'
import ProcessWindow from './components/windows/ProcessWindow.vue'
import FilesWindow from './components/windows/FilesWindow.vue'
import RecycleBinWindow from './components/windows/RecycleBinWindow.vue'
import TerminalWindow from './components/windows/TerminalWindow.vue'
import SitesWindow from './components/windows/SitesWindow.vue'
import SiteEditWindow from './components/windows/SiteEditWindow.vue'
import DatabaseWindow from './components/windows/DatabaseWindow.vue'
import EditorWindow from './components/windows/EditorWindow.vue'
import MediaWindow from './components/windows/MediaWindow.vue'
import UserWindow from './components/windows/UserWindow.vue'
import ChangePasswordWindow from './components/windows/ChangePasswordWindow.vue'
import VipWindow from './components/windows/VipWindow.vue'
import FrpWindow from './components/windows/FrpWindow.vue'
import LogsWindow from './components/windows/LogsWindow.vue'
import SettingsWindow from './components/windows/SettingsWindow.vue'
import ContainerLogsWindow from './components/windows/ContainerLogsWindow.vue'
import ContainerDetailWindow from './components/windows/ContainerDetailWindow.vue'
import ContainerStatsWindow from './components/windows/ContainerStatsWindow.vue'
import ContainerEditWindow from './components/windows/ContainerEditWindow.vue'
import DockerConfigEditorWindow from './components/windows/DockerConfigEditorWindow.vue'
import AppStoreWindow from './components/windows/AppStoreWindow.vue'
import AppStoreInstallWindow from './components/windows/AppStoreInstallWindow.vue'
import AppStoreComposeEditorWindow from './components/windows/AppStoreComposeEditorWindow.vue'
import AppStoreInstallLogWindow from './components/windows/AppStoreInstallLogWindow.vue'
import AppStoreReadmeWindow from './components/windows/AppStoreReadmeWindow.vue'
import TasksWindow from './components/windows/TasksWindow.vue'
import ShunxSecurityWindow from './components/windows/ShunxSecurityWindow.vue'
import UISettingsWindow from './components/windows/UISettingsWindow.vue'
import ConnectionFormWindow from './components/windows/ConnectionFormWindow.vue'
import NetStorageWindow from './components/windows/NetStorageWindow.vue'
import NetStorageBrowseWindow from './components/windows/NetStorageBrowseWindow.vue'
import NetStorageFormWindow from './components/windows/NetStorageFormWindow.vue'
import RuntimeWindow from './components/windows/RuntimeWindow.vue'
import RuntimeCreateWindow from './components/windows/RuntimeCreateWindow.vue'
import DisksWindow from './components/windows/DisksWindow.vue'
import MonitoringWindow from './components/windows/MonitoringWindow.vue'
import CertWindow from './components/windows/CertWindow.vue'
// 面板备份已合并进「ShunX保护机制」应用（详见 ShunxSecurityWindow）
// import PanelBackupWindow from './components/windows/PanelBackupWindow.vue'
import WebStatsWindow from './components/windows/WebStatsWindow.vue'
import RewriteWindow from './components/windows/RewriteWindow.vue'
import SiteOptsWindow from './components/windows/SiteOptsWindow.vue'
import MetricsHistoryWindow from './components/windows/MetricsHistoryWindow.vue'
// 系统体检已合并进「ShunX保护机制」应用（详见 ShunxSecurityWindow）
// import HealthCheckWindow from './components/windows/HealthCheckWindow.vue'
import FtpUsersWindow from './components/windows/FtpUsersWindow.vue'
import PhpVersionsWindow from './components/windows/PhpVersionsWindow.vue'
import SessionsWindow from './components/windows/SessionsWindow.vue'
import ShunXSetup from './components/ShunXSetup.vue'
import TamperAlert from './components/TamperAlert.vue'
import InstallCheckAlert from './components/InstallCheckAlert.vue'
import Login from './views/Login.vue'
import { shunxApi, systemApi } from './api'
import { auth, clearAuth, isAdmin } from './store/auth'
import { uiState, loadUi, loadUiEffective } from './store/ui'
import { settings } from './store/settings'
import { vip as vipStore, refreshVip } from './store/vip'
import { systemState, startMetrics, stopMetrics } from './store/systemMetrics'
import { startDocker, stopDocker, refresh as refreshDocker } from './store/docker'
import { nodes as nodesStore, refreshNodes } from './store/nodes'
import { setRequestNode } from './store/requestNode'
import { tamperState, startTamper, stopTamper } from './store/tamper'
import { Container, Settings, Folder, Trash2, Terminal, FileText, Image as ImageIcon, Film, LogOut, LayoutGrid, UserCircle2, Globe, Database, Lock, ScrollText, ShieldCheck, Store, BookOpen, ListChecks, Cpu, HardDrive, Palette, Radio, Cloud, Activity, BarChart3, FileCode2, History, MonitorSmartphone, Unlink, UserCheck, Wrench, Settings2, ServerCog, Bug } from 'lucide-vue-next'   // 图标库：Lucide 矢量图标组件（桌面 / 窗口 / 按钮使用）

// --- 桌面根状态：登录态、动态壁纸、底栏主机徽标 ---
const loggedIn = computed(() => !!auth.token)

// 桌面背景样式：与登录页共用同一份界面配置（自定义背景或回退默认 hero.png）
const desktopBgStyle = computed(() => {
  // 有动态壁纸（视频/轮播）时，底层由独立壁纸层渲染，这里给桌面容器一个兜底背景
  if (wallpaperVideo.value || carouselImages.value.length > 1) return {}
  if (uiState.background) {
    return {
      backgroundImage: `url('${uiState.background}')`,
      backgroundSize: 'cover',
      backgroundPosition: 'center',
    }
  }
  return {}
})

// ---- 动态壁纸：视频壁纸 / 多背景图片轮播 ----
// video 模式：全屏 muted 循环视频；image 模式：多张背景按间隔轮播
const wallpaperVideo = computed(() =>
  uiState.background_mode === 'video' && uiState.wallpaper_video ? uiState.wallpaper_video : ''
)
const carouselImages = computed(() =>
  uiState.background_mode === 'image' && Array.isArray(uiState.backgrounds) ? uiState.backgrounds : []
)
const carouselIndex = ref(0)
let carouselTimer = null
function startCarousel() {
  stopCarousel()
  if (carouselImages.value.length <= 1) return
  const interval = Math.max(3, Number(uiState.background_interval) || 8) * 1000
  carouselTimer = setInterval(() => {
    if (carouselImages.value.length <= 1) return
    carouselIndex.value = (carouselIndex.value + 1) % carouselImages.value.length
  }, interval)
}
function stopCarousel() {
  if (carouselTimer) {
    clearInterval(carouselTimer)
    carouselTimer = null
  }
}
watch(carouselImages, (v) => {
  carouselIndex.value = 0
  if (v.length > 1) startCarousel()
  else stopCarousel()
})

// 当前管理主机：用于底栏指示（多机管理），切换后自动响应式更新
const { t } = useI18n()
const currentHost = computed(() => {
  const cur = nodesStore.list.find((n) => n.id === nodesStore.currentId)
  return cur || null
})
const hostBadgeText = computed(() => {
  if (!currentHost.value) return ''
  // 底栏只显示节点名称；完整信息（名称 · 用户@主机 · 管理员）放悬浮提示
  return currentHost.value.name || currentHost.value.id
})
// 底栏主机完整悬浮提示：名称 · user@host · 管理员（本机仅显示名称 · 管理员）
const hostBadgeTitle = computed(() => {
  if (!currentHost.value) return ''
  const name = currentHost.value.name || currentHost.value.id
  if (currentHost.value.type === 'ssh') {
    return `${name} ${currentHost.value.user}@${currentHost.value.host} 管理员`
  }
  return `${name} 管理员`
})
const hostBadgeRemote = computed(() => !!(currentHost.value && currentHost.value.type === 'ssh'))
// 当前管理主机是否为远程（SSH）节点：remoteCap 门控依赖此响应式状态
const isCurrentHostRemote = computed(() => hostBadgeRemote.value)
// 当前远端节点是否已配置 Agent（local 类应用可经 Agent 代理在子节点使用）。
// 未配置 Agent 的裸远端节点，local 类（面板自身管理项）仍应隐藏。
const currentHostAgentReady = computed(() =>
  !!(currentHost.value && currentHost.value.type === 'ssh' && currentHost.value.agent_enabled)
)
// --- 登录后回调：检查 ShunX 安全入口 + 安装完整性 ---
function onLoggedIn() {
  // 触发响应式重渲染，并检查是否需要强制设置安全入口
  checkShunxRequired()
  // 检测安装环境是否完整（未按 README 安装则弹窗提醒重新安装）
  checkInstallCheck()
}

// --- 桌面快捷方式清单：key/图标/窗口组件/尺寸/权限/远端能力 ---
const shortcuts = ref([
  // remoteCap：host（缺省）可在远端节点使用；local 为面板自身管理项，远端节点隐藏
  { key: 'sites', label: '网站', titleKey: 'app.shortcut.sites', icon: markRaw(Globe), component: markRaw(SitesWindow), w: 900, h: 560, adminOnly: true, remoteCap: 'local' },
  { key: 'database', label: '数据库', titleKey: 'app.shortcut.database', icon: markRaw(Database), component: markRaw(DatabaseWindow), w: 860, h: 540, adminOnly: true, remoteCap: 'local' },
  // 计划任务已合并进「任务」应用，桌面不再单独保留
  // { key: 'cron', label: '计划任务', titleKey: 'app.shortcut.cron', icon: markRaw(Clock), component: markRaw(CronWindow), w: 800, h: 520, adminOnly: true, remoteCap: 'local' },
  // 防火墙已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'firewall', label: '防火墙', titleKey: 'app.shortcut.firewall', icon: markRaw(Shield), component: markRaw(FirewallWindow), w: 800, h: 540, adminOnly: true },
  { key: 'frp', label: 'Frp内网穿透', titleKey: 'app.shortcut.frp', icon: markRaw(Radio), component: markRaw(FrpWindow), w: 900, h: 600, adminOnly: true, remoteCap: 'local' },
  // SSL 已合并进「网站」应用的 SSL证书 标签页，桌面不再单独保留
  // { key: 'ssl', label: 'SSL', titleKey: 'app.shortcut.ssl', icon: markRaw(Lock), component: markRaw(SSLWindow), w: 820, h: 520, adminOnly: true, remoteCap: 'local' },
  { key: 'logs', label: '日志', titleKey: 'app.shortcut.logs', icon: markRaw(ScrollText), component: markRaw(LogsWindow), w: 900, h: 560, adminOnly: true },
  // 审计日志已合并进「日志」应用的审计日志标签页，桌面不再单独保留
  // { key: 'auditlog', label: '审计日志', titleKey: 'app.shortcut.auditlog', icon: markRaw(ScrollText), component: markRaw(AuditLogWindow), w: 900, h: 560, adminOnly: true, remoteCap: 'local' },
  { key: 'docker', label: 'Docker', titleKey: 'app.shortcut.docker', icon: markRaw(Container), component: markRaw(DockerWindow), w: 820, h: 520, adminOnly: true },
  // Docker 数据卷已合并进「Docker」应用的数据卷视图，桌面不再单独保留
  // { key: 'dockervolumes', label: 'Docker卷', titleKey: 'app.shortcut.dockervolumes', icon: markRaw(DatabaseBackup), component: markRaw(DockerVolumesWindow), w: 860, h: 540, adminOnly: true },
  // 容器资源与端口编辑（CPU/内存/环境变量/端口映射，管理员专属）
  // 已从桌面隐藏，仅保留 Docker 容器右键「编辑」入口（openContainerEdit）
  // { key: 'containeredit', label: '容器编辑', titleKey: 'app.shortcut.containeredit', icon: markRaw(Settings2), component: markRaw(ContainerEditWindow), w: 760, h: 660, adminOnly: true },
  { key: 'appstore', label: '应用商店', titleKey: 'app.shortcut.appstore', icon: markRaw(Store), component: markRaw(AppStoreWindow), w: 920, h: 580, adminOnly: true, remoteCap: 'local', vip: true },
  // 任务 = 计划任务 + 任务中心 合并
  { key: 'tasks', label: '任务', titleKey: 'app.shortcut.tasks', icon: markRaw(ListChecks), component: markRaw(TasksWindow), w: 900, h: 560, adminOnly: true, remoteCap: 'local' },
  // ShunX保护机制 = 防火墙 + 应用防火墙 + 网页防篡改 + 数据库保护 + 系统体检 + 面板备份 + 备份中心 + 通知中心 + SSH密钥 合并
  { key: 'shunxprotection', label: 'ShunX保护机制', titleKey: 'app.shortcut.shunxprotection', icon: markRaw(ShieldCheck), component: markRaw(ShunxSecurityWindow), w: 980, h: 620, adminOnly: true },
  // 下述应用已合并进「ShunX保护机制」，桌面不再单独保留
  // { key: 'protection', label: 'Graw数据库保护机制', titleKey: 'app.shortcut.protection', icon: markRaw(ShieldCheck), component: markRaw(ProtectionWindow), w: 860, h: 560, adminOnly: true, remoteCap: 'local' },
  // { key: 'tamper', label: 'ShunX网页防篡改', titleKey: 'app.shortcut.tamper', icon: markRaw(ShieldAlert), component: markRaw(TamperWindow), w: 920, h: 580, adminOnly: true, remoteCap: 'local' },
  // { key: 'waf', label: '应用防火墙', titleKey: 'app.shortcut.waf', icon: markRaw(ShieldBan), component: markRaw(WafWindow), w: 980, h: 620, adminOnly: true, remoteCap: 'local' },
  { key: 'runtime', label: '运行环境', titleKey: 'app.shortcut.runtime', icon: markRaw(Cpu), component: markRaw(RuntimeWindow), w: 900, h: 560, adminOnly: true, remoteCap: 'local' },
  { key: 'process', label: '进程管理', titleKey: 'app.shortcut.process', icon: markRaw(Settings), component: markRaw(ProcessWindow), w: 780, h: 520, adminOnly: true },
  { key: 'files', label: '文件管理', titleKey: 'app.shortcut.files', icon: markRaw(Folder), component: markRaw(FilesWindow), w: 820, h: 540, adminOnly: true },
  { key: 'recycle', label: '回收站', titleKey: 'app.shortcut.recycle', icon: markRaw(Trash2), component: markRaw(RecycleBinWindow), w: 760, h: 480, adminOnly: true },
  { key: 'netstorage', label: '网络储存', titleKey: 'app.shortcut.netstorage', icon: markRaw(Cloud), component: markRaw(NetStorageWindow), w: 860, h: 540, adminOnly: true, remoteCap: 'local' },
  { key: 'uisettings', label: '界面设置', titleKey: 'app.shortcut.uisettings', icon: markRaw(Palette), component: markRaw(UISettingsWindow), w: 520, h: 540, adminOnly: true, remoteCap: 'local', vip: true },
  { key: 'disks', label: '磁盘管理', titleKey: 'app.shortcut.disks', icon: markRaw(HardDrive), component: markRaw(DisksWindow), w: 900, h: 560, adminOnly: true },
  // 备份中心已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'backup', label: '备份中心', titleKey: 'app.shortcut.backup', icon: markRaw(DatabaseBackup), component: markRaw(BackupWindow), w: 920, h: 580, adminOnly: true, remoteCap: 'local' },
  // 通知中心已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'notify', label: '通知中心', titleKey: 'app.shortcut.notify', icon: markRaw(BellRing), component: markRaw(NotifyWindow), w: 860, h: 560, adminOnly: true, remoteCap: 'local' },
  // 站点监控 + 服务监控 合并为「监控」
  { key: 'monitoring', label: '监控', titleKey: 'app.shortcut.monitoring', icon: markRaw(Activity), component: markRaw(MonitoringWindow), w: 920, h: 580, adminOnly: true, remoteCap: 'local' },
  // { key: 'uptime', label: '站点监控', titleKey: 'app.shortcut.uptime', icon: markRaw(Activity), component: markRaw(UptimeWindow), w: 860, h: 560, adminOnly: true, remoteCap: 'local' },
  { key: 'webstats', label: '访问统计', titleKey: 'app.shortcut.webstats', icon: markRaw(BarChart3), component: markRaw(WebStatsWindow), w: 980, h: 640, adminOnly: true, remoteCap: 'local' },
  { key: 'rewrite', label: '伪静态规则', titleKey: 'app.shortcut.rewrite', icon: markRaw(FileCode2), component: markRaw(RewriteWindow), w: 780, h: 560, adminOnly: true, remoteCap: 'local' },
  { key: 'siteopts', label: '防盗链缓存', titleKey: 'app.shortcut.siteopts', icon: markRaw(Unlink), component: markRaw(SiteOptsWindow), w: 860, h: 600, adminOnly: true, remoteCap: 'local' },
  { key: 'metricshistory', label: '历史监控', titleKey: 'app.shortcut.metricshistory', icon: markRaw(History), component: markRaw(MetricsHistoryWindow), w: 980, h: 640, adminOnly: true, remoteCap: 'local' },
  // 服务监控已合并进「监控」应用，桌面不再单独保留
  // { key: 'svcmonitor', label: '服务监控', titleKey: 'app.shortcut.svcmonitor', icon: markRaw(Server), component: markRaw(ServiceMonitorWindow), w: 920, h: 560, adminOnly: true },
  // SSH 密钥已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'sshkeys', label: 'SSH 密钥', titleKey: 'app.shortcut.sshkeys', icon: markRaw(KeyRound), component: markRaw(SSHKeysWindow), w: 880, h: 540, adminOnly: true, remoteCap: 'local' },
  { key: 'certcheck', label: '证书到期', titleKey: 'app.shortcut.certcheck', icon: markRaw(Lock), component: markRaw(CertWindow), w: 820, h: 540, adminOnly: true, remoteCap: 'local' },
  // 系统体检已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'healthcheck', label: '系统体检', titleKey: 'app.shortcut.healthcheck', icon: markRaw(Stethoscope), component: markRaw(HealthCheckWindow), w: 820, h: 600, adminOnly: true, remoteCap: 'local' },
  { key: 'ftpusers', label: 'FTP用户', titleKey: 'app.shortcut.ftpusers', icon: markRaw(UserCheck), component: markRaw(FtpUsersWindow), w: 860, h: 560, adminOnly: true, remoteCap: 'local' },
  // PHP 多版本管理：探测系统 PHP/FPM + 站点 PHP 版本关联（仅管理员）
  { key: 'phpversions', label: 'PHP版本', titleKey: 'app.shortcut.phpversions', icon: markRaw(ServerCog), component: markRaw(PhpVersionsWindow), w: 900, h: 580, adminOnly: true, remoteCap: 'local' },
  // 面板备份已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'panelbackup', label: '面板备份', titleKey: 'app.shortcut.panelbackup', icon: markRaw(Archive), component: markRaw(PanelBackupWindow), w: 860, h: 540, adminOnly: true, remoteCap: 'local' },
  // 系统更新已从桌面移除（面板自身更新入口走其他渠道）
  // { key: 'update', label: '系统更新', titleKey: 'app.shortcut.update', icon: markRaw(RefreshCw), component: markRaw(UpdateWindow), w: 640, h: 420, adminOnly: true, remoteCap: 'local' },
  // 登录日志已合并进「日志」应用的登录日志标签页，桌面不再单独保留
  // { key: 'loginlog', label: '登录日志', titleKey: 'app.shortcut.loginlog', icon: markRaw(Fingerprint), component: markRaw(LoginLogWindow), w: 900, h: 560, adminOnly: false, remoteCap: 'local' },
  // 会话管理：在线会话列表、踢出单设备、强制全部下线（普通用户仅管理自己的会话）
  { key: 'sessions', label: '会话管理', titleKey: 'app.shortcut.sessions', icon: markRaw(MonitorSmartphone), component: markRaw(SessionsWindow), w: 900, h: 560, adminOnly: false, remoteCap: 'local' },
  { key: 'terminal', label: '终端', titleKey: 'app.shortcut.terminal', icon: markRaw(Terminal), component: markRaw(TerminalWindow), w: 780, h: 460, adminOnly: true },
  // Foxcode：双击打开终端并自动输入 foxcode 命令启动
  { key: 'foxcode', label: 'Foxcode', icon: markRaw(Terminal), component: markRaw(TerminalWindow), w: 780, h: 460, adminOnly: true, props: { autoCommand: 'foxcode' } }
])

// 桌面快捷方式：管理员可见全部，普通用户仅可见非管理功能。
// 远端节点下：未配置 Agent 时隐藏 local 类（面板自身管理项）应用，避免误操作本机；
// 已配置 Agent 时 local 类经 Agent 代理在子节点可用，正常显示。
// --- 快捷方式可见性：管理员 / 隐藏 Foxcode / 远端节点 local 类门控 ---
const visibleShortcuts = computed(() => shortcuts.value.filter(s =>
  (!s.adminOnly || isAdmin()) &&
  !(s.key === 'foxcode' && settings.hideFoxcode) &&
  !(isCurrentHostRemote.value && !currentHostAgentReady.value && s.remoteCap === 'local')
))

// --- 窗口系统状态：选中项、已开窗口、聚焦窗口、开始菜单 ---
const selected = ref(null)
const openWindows = ref([])
const activeWindowId = ref(null)
const startMenuOpen = ref(false)

// 统一面板兼容的实际生效值：设定开启且为生效 VIP 才启用。
// 未授权（未解锁）时强制视为关闭，避免历史残留值绕过付费锁定。
const unifiedPanelOn = computed(() => settings.unifiedPanel && !!vipStore.vip)

// ShunX 安全入口：登录后检查是否已配置，未配置则强制设置。
// 仅管理员触发（保存入口需要管理员权限）；后端对普通用户已脱敏
// entry_path，普通用户凭 enabled 判断即可。
const shunxRequired = ref(false)

// 安装环境不完整时的缺失项 key 列表（非空则弹窗提醒重新安装）
const installCheckMissing = ref([])

async function checkInstallCheck() {
  if (!auth.token) return
  try {
    const res = await systemApi.installCheck()
    // 仅容器模式下检测到缺失项时才提醒；本机直跑视为完整
    installCheckMissing.value = res.ok ? [] : (res.missing || [])
  } catch (e) {
    // 接口失败时不弹窗（兼容旧版后端）
    installCheckMissing.value = []
  }
}

async function checkShunxRequired() {
  if (!auth.token) return
  try {
    const config = await shunxApi.config()
    const missing = isAdmin() ? !config.entry_path : !config.enabled
    if (missing) {
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

// --- 开始菜单 / 启动器 / 退出登录 ---
function toggleStartMenu() { startMenuOpen.value = !startMenuOpen.value }
function openUsers() { openWindow('users') }
function openChangePwd() { openWindow('changepwd') }
function openSettings() { openWindow('settings') }
function openVip() { openWindow('vip') }
function openTasks() { openWindow('tasks') }

// 报告问题：跳转到项目 GitHub Issues 新建页（新窗口，noopener 防钓鱼）
function reportIssue() {
  window.open('https://github.com/wuhulab/Graw/issues/new', '_blank', 'noopener')
}

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

// --- 通用窗口打开：含 adminOnly / remoteCap / VIP 三重门控 ---
function openWindow(key) {
  let def = shortcuts.value.find(s => s.key === key)
  if (!def) {
    const extras = {
      users: { label: '账号管理', titleKey: 'app.winTitle.users', icon: markRaw(UserCircle2), component: markRaw(UserWindow), w: 600, h: 460, adminOnly: true },
      changepwd: { label: '修改密码', titleKey: 'app.winTitle.changepwd', icon: markRaw(UserCircle2), component: markRaw(ChangePasswordWindow), w: 420, h: 360 },
      settings: { label: '设置', titleKey: 'app.winTitle.settings', icon: markRaw(Settings), component: markRaw(SettingsWindow), w: 520, h: 480 },
      vip: { label: 'VIP', titleKey: 'app.winTitle.vip', icon: markRaw(Lock), component: markRaw(VipWindow), w: 440, h: 400, adminOnly: false, remoteCap: 'local' }
    }
    def = extras[key]
    if (!def) return
  }
  // 统一守卫：无论主快捷方式还是 extras，adminOnly 窗口都要求管理员
  // （后端 API 已有鉴权，此处为前端纵深防御，避免普通用户残留窗口 UI）
  if (def.adminOnly && !isAdmin()) return
  // 远程能力守卫：未配置 Agent 的远端节点下，local 类（面板自身管理项）应用
  // 禁止打开（后端同一守护返回 403，此处前端提前拦截并提示，避免空白窗口）。
  // 已配置 Agent 时 local 类经 Agent 代理在子节点可用，正常打开。
  if (def.remoteCap === 'local' && isCurrentHostRemote.value && !currentHostAgentReady.value) {
    alert(t('nodes.localOnlyOnRemote'))
    return
  }
  // 付费门控：vip 标记的功能（应用商店/界面管理）需生效 VIP。未解锁时拦截并
  // 提示，转入「付费解锁」窗口；加载中（vip.loaded=false）暂不误拦，待状态明确。
  if (def.vip && vipStore.loaded && !vipStore.vip) {
    alert(t('vip.gateMsg'))
    openWindow('vip')
    return
  }
  const id = ++windowSeq
  // 「统一面板兼容」：窗口绑定打开时对应的节点（聚焦该窗口即操作该节点）
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key,
    nodeId: boundNode, // 绑定的目标节点（开启统一面板兼容时有值，否则空=跟随全局）
    title: def.label,
    titleKey: def.titleKey,
    titleArgs: def.titleArgs,
    icon: def.icon,
    component: def.component,
    props: def.props ? { ...def.props } : {},
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
  // 打开即同步绑定请求节点，让新窗口的首个请求立刻作用于其目标节点
  applyActiveRequestNode(id)
}

// 触屏设备判定（手机/平板/DevTools 触摸模拟）：组合多种探测，避免单靠
// matchMedia(pointer:coarse) 在某些 WebView/模拟器里误判为 false，导致
// 单击只选中不打开。移动端没有双击，单击即打开应用；桌面端保持单击选中、双击打开。
const isTouchDevice =
  typeof window !== 'undefined' && (
    ('ontouchstart' in window) ||
    (typeof navigator !== 'undefined' && navigator.maxTouchPoints > 0) ||
    (window.matchMedia && window.matchMedia('(any-pointer: coarse)').matches) ||
    (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) ||
    (window.innerWidth <= 820)
  )
function onShortcutClick(key) {
  if (isTouchDevice) {
    openShortcut(key)
  } else {
    selected.value = key
  }
}

// 桌面快捷方式双击分发：特殊应用（如 Foxcode）走自定义打开逻辑，其余走通用 openWindow
function openShortcut(key) {
  if (key === 'foxcode') {
    openFoxcode()
  } else {
    openWindow(key)
  }
}

// Foxcode：打开终端并自动输入 foxcode 启动命令
// 首次启动（浏览器本地从未记录过）弹窗提示需要安装，之后不再重复提醒
function openFoxcode() {
  // 每次浏览器会话只弹一次（sessionStorage 关页即清空），首次启动提醒安装 foxcode
  if (!sessionStorage.getItem('graw_foxcode_warned')) {
    alert('需要安装 foxcode：pip install foxcode2')
    sessionStorage.setItem('graw_foxcode_warned', '1')
  }
  openWindow('foxcode')
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

// 「网站」应用创建/编辑站点的独立表单窗口（类型选择留在网站窗口内，提交做成独立可移动窗口）
function openSiteEdit(payload) {
  const id = ++windowSeq
  const isEdit = payload?.mode === 'edit'
  const type = isEdit ? (payload?.site?.type || 'static') : (payload?.type || 'static')
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'site-edit',
    nodeId: boundNode, // 绑定当前打开的节点（统一面板兼容）
    titleKey: 'app.winTitle.site',
    title: '站点配置',
    icon: markRaw(Globe),
    component: markRaw(SiteEditWindow),
    props: payload ? { ...payload } : {},
    x: 160 + (openWindows.value.length * 28),
    y: 70 + (openWindows.value.length * 24),
    width: 540,
    height: ['static', 'proxy'].includes(type) ? 430 : 540,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
  applyActiveRequestNode(id)
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

// Docker：打开容器资源图表（CPU / 内存实时曲线）
function openContainerStats({ id, name }) {
  const id2 = ++windowSeq
  const w = reactive({
    id: id2,
    key: 'container-stats',
    title: '资源图表: ' + name,
    titleKey: 'app.winTitle.containerStats',
    titleArgs: { name },
    icon: markRaw(Activity),
    component: markRaw(ContainerStatsWindow),
    props: { id, name },
    x: 150 + (openWindows.value.length * 30),
    y: 70 + (openWindows.value.length * 25),
    width: 760,
    height: 420,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id2
}

// Docker：右键「编辑」跳转到容器编辑窗口（预先指定容器）
function openContainerEdit({ id, name }) {
  const id2 = ++windowSeq
  const w = reactive({
    id: id2,
    key: 'containeredit',
    title: '容器编辑: ' + name,
    titleKey: 'app.winTitle.containerEdit',
    titleArgs: { name },
    icon: markRaw(Settings2),
    component: markRaw(ContainerEditWindow),
    props: { id, name },
    x: 140 + (openWindows.value.length * 30),
    y: 60 + (openWindows.value.length * 25),
    width: 760,
    height: 660,
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

// 网络储存：点击云盘卡片 → 启动「文件管理」，标题为「文件管理：<名称>」
function openNetStorageBrowse({ id, name }) {
  const id2 = ++windowSeq
  const w = reactive({
    id: id2,
    key: 'netstorage-browse',
    title: '文件管理: ' + (name || ''),
    titleKey: 'app.winTitle.netstorageBrowse',
    titleArgs: { name: name || '' },
    icon: markRaw(Folder),
    component: markRaw(NetStorageBrowseWindow),
    props: { conn: { id, name } },
    x: 140 + (openWindows.value.length * 30),
    y: 60 + (openWindows.value.length * 25),
    width: 860,
    height: 560,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id2
}

// 网络储存：可保存时再次编辑；点击「添加/编辑」则打开独立表单窗口（风格同运行环境）
function openNetStorageForm(conn = null) {
  const id = ++windowSeq
  const w = reactive({
    id,
    key: 'netstorage-form',
    title: conn ? '编辑: ' + (conn.name || '') : '添加网络储存',
    titleKey: conn ? 'app.winTitle.netstorageEdit' : 'app.winTitle.netstorageAdd',
    titleArgs: conn ? { name: conn.name || '' } : undefined,
    icon: markRaw(Cloud),
    component: markRaw(NetStorageFormWindow),
    props: { conn },
    x: 180 + (openWindows.value.length * 30),
    y: 100 + (openWindows.value.length * 25),
    width: 560,
    height: 620,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// --- 窗口生命周期：聚焦 / 关闭 / 最小化 / 最大化 / 移动 / 缩放 ---
function focusWindow(id) {
  const w = openWindows.value.find(x => x.id === id)
  if (!w) return
  w.z = ++zSeq
  w.minimized = false
  activeWindowId.value = id
  // 聚焦即同步更新请求节点，避免切换后聚焦旧窗口时请求节点滞后
  applyActiveRequestNode(id)
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
// --- 系统概览 + 实时数据（指标 WS / 防篡改 / Docker）启停 ---
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

// --- 挂载 / 卸载生命周期：登录态兜底、加载 UI、启停实时数据 ---
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
    // 付费功能：启动即加载当前账号 VIP 状态，供「统一面板兼容」/应用商店等门控使用
    refreshVip()
    // 已登录态（如页面刷新）也重新检测安装环境，确保缺失时弹窗提醒
    checkInstallCheck()
    // 加载当前账号生效的动态壁纸 / 环形图（「仅用于这个账号」优先）
    loadUiEffective().catch(() => {})
  }
  updateClock()
  clockTimer = setInterval(updateClock, 1000)
  document.addEventListener('mousedown', onDocClick)
})

// 统一面板兼容：把「当前请求目标节点」设为当前聚焦/打开窗口绑定的节点。
// 桌面无窗口时跟随全局 currentId。此函数在窗口打开/聚焦时同步调用（而非仅靠
// watch 异步触发），避免「切换主机后立刻启动应用」的首个请求仍打到切换前的节点
// （应用名称已显示新节点、实际连接却还是旧节点）。先定义再供 watch 与 open/focus 复用。
// --- 统一面板兼容：把聚焦窗口绑定的目标节点写入请求上下文 ---
function applyActiveRequestNode(id) {
  let node = ''
  if (unifiedPanelOn.value) {
    const w = openWindows.value.find(x => x.id === id)
    node = (w && w.nodeId) || ''
  }
  nodesStore.activeWindowNode = node
  setRequestNode(node)
}

watch(activeWindowId, (id) => {
  applyActiveRequestNode(id)
})

// 登录态变化时启停共享实时数据，避免未登录时持续请求
watch(loggedIn, (v) => {
  if (v) {
    startRealtime()
    // 付费功能：登录后刷新当前账号 VIP 状态，保证门控（应用商店/界面管理）准确
    refreshVip()
    loadUiEffective().catch(() => {})
  } else {
    stopRealtime()
  }
})

onUnmounted(() => {
  stopRealtime()
  stopCarousel()
  clearInterval(clockTimer)
  document.removeEventListener('mousedown', onDocClick)
})
</script>
