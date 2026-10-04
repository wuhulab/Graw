<!-- Graw 桌面环境根组件：类 macOS 的「类桌面操作系统」界面。
     登录前显示 Login 视图；登录后渲染桌面（动态壁纸 + 快捷方式 + 右侧监控卡片）、
     窗口系统（独立窗口组件，支持拖拽 / 最小化 / 最大化）、Dock 式任务栏与开始菜单。
     核心状态：登录态 auth、已打开窗口列表 openWindows、当前聚焦窗口、管理节点（多机）、
     统一面板兼容（免费）、ShunX 安全入口与网页防篡改告警。
     窗口按 shortcuts 清单打开各自功能组件；多节点经 X-Graw-Node 透传（见 api.js）。
     打开 / 聚焦窗口即同步请求目标节点，避免切换主机后首个请求打到旧节点。 -->

<template>
  <Login v-if="!loggedIn" @login="onLoggedIn" />
  <!-- 标准面板模式（设置 → 面板模式 开启）：1Panel 式侧边栏布局替代桌面/窗口系统。
       窗口打开/聚焦逻辑与桌面共用（openWindows/activeWindowId），仅外壳不同。
       50 个 openXxx 事件经 winEvents 透传给 PanelLayout 内容区的 WindowContent。 -->
  <PanelLayout
    v-else-if="panelModeOn"
    :windows="openWindows"
    :active-id="activeWindowId"
    :user="auth.user"
    :menu-shortcuts="visibleShortcuts"
    :host-name="hostBadgeText"
    :host-remote="hostBadgeRemote"
    :show-tabs="settings.panelTabs"
    :content-events="winEvents"
    @open="onPanelOpen"
    @focus="focusWindow"
    @close="handleCloseWindow"
    @dirty="onPanelDirty"
    @logout="doLogout"
  />
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
          @contextmenu.prevent="openShortcutMenu($event, sc)"
        >
          <div class="icon"><component :is="sc.icon" :size="32" /></div>
          <div class="label" :style="shortcutLabelStyle" :title="sc.titleKey ? $t(sc.titleKey) : sc.label">{{ sc.titleKey ? $t(sc.titleKey) : sc.label }}</div>
        </div>
      </div>

      <!-- 快捷方式右键菜单：隐藏 / 固定到任务栏（在设置里可恢复 / 取消固定） -->
      <div
        v-if="shortcutMenu.show"
        class="shortcut-menu"
        :style="{ left: shortcutMenu.x + 'px', top: shortcutMenu.y + 'px' }"
        @click.stop="shortcutMenu.show = false"
      >
        <template v-if="shortcutMenu.sc">
          <button class="shortcut-menu-item" @click="hideScFromMenu">
            <EyeOff :size="14" /> {{ $t('desktop.hideShortcut') }}
          </button>
          <button
            v-if="desktopPrefs.pinnedKeys.includes(shortcutMenu.sc.key)"
            class="shortcut-menu-item"
            @click="togglePinSc(false)"
          >
            <PinOff :size="14" /> {{ $t('desktop.unpinShortcut') }}
          </button>
          <button v-else class="shortcut-menu-item" @click="togglePinSc(true)">
            <Pin :size="14" /> {{ $t('desktop.pinShortcut') }}
          </button>
        </template>
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

    <!-- Windows：窗口外壳 + 统一的内容组件（约 50 个 openXxx 事件由 winEvents 提供，
         经 WindowFrame 作用域插槽 contentAttrs 透传给 WindowContent 的动态组件） -->
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
      v-bind="winEvents"
      @dirty="(v) => setWinDirty(w.id, v)"
    >
      <template #default="{ contentAttrs }">
        <WindowContent :window="w" :content-events="{ ...contentAttrs, onClose: () => handleCloseWindow(w.id) }" />
      </template>
    </WindowFrame>

    <!-- Dock -->
    <div class="taskbar">
      <div class="start-button" title="Launchpad" @click.stop="toggleStartMenu"><LayoutGrid :size="22" /></div>
      <div v-if="startMenuOpen" class="start-menu" @click.stop>
        <!-- 社区 Star 支持提示：前往 GitHub 点亮 Star 后，填写 GitHub 用户名在线验证，
             验证通过写入本地标记（localStorage）后不再显示。纯前端展示逻辑，不影响任何功能。 -->
        <div v-if="!starTipDone" class="star-tip">
          <div class="star-tip-text">
            <Star :size="13" class="star-tip-icon" />
            <span>{{ $t('app.starTip.text') }}</span>
          </div>
          <div class="star-tip-actions">
            <button class="star-tip-btn" @click="starTipOpenRepo">
              <Github :size="12" /> {{ $t('app.starTip.openGithub') }}
            </button>
            <button v-if="!starTipVerifyOpen" class="star-tip-btn secondary" @click="starTipVerifyOpen = true">
              {{ $t('app.starTip.verify') }}
            </button>
          </div>
          <div v-if="starTipVerifyOpen" class="star-tip-verify">
            <input
              v-model="starTipUsername"
              class="star-tip-input"
              :placeholder="$t('app.starTip.placeholder')"
              :disabled="starTipVerifying"
              @keyup.enter="verifyStarTip"
            />
            <button class="star-tip-btn" :disabled="starTipVerifying" @click="verifyStarTip">
              {{ starTipVerifying ? $t('app.starTip.verifying') : $t('app.starTip.verify') }}
            </button>
          </div>
          <div v-if="starTipMsg" class="star-tip-msg" :class="{ ok: starTipMsgOk }">{{ starTipMsg }}</div>
        </div>
        <div class="start-header">
          <div style="font-weight:700;">{{ auth.user?.username }}</div>
          <div style="font-size:11px;color:#6e6e73;">{{ $t(auth.user?.role === 'admin' ? 'app.admin' : 'app.normalUser') }}</div>
        </div>
        <div class="start-list">
          <button v-if="isFullAdmin()" class="start-item" @click="openUsers(); startMenuOpen = false"><UserCircle2 :size="16" /> {{ $t('app.accountManage') }}</button>
          <button class="start-item" @click="openChangePwd(); startMenuOpen = false"><UserCircle2 :size="16" /> {{ $t('app.changePassword') }}</button>
          <button class="start-item" @click="openSettings(); startMenuOpen = false"><Settings :size="16" /> {{ $t('app.settings') }}</button>
          <button class="start-item" @click="reportIssue(); startMenuOpen = false"><Bug :size="16" /> {{ $t('app.reportIssue') }}</button>
          <!-- 关于外链：源码仓库 / 捐赠（置于退出登录上方，文案复用 settings.about 既有 i18n 键） -->
          <button class="start-item" @click="openExternal(SOURCE_REPO_URL); startMenuOpen = false"><Github :size="16" /> {{ $t('settings.about.githubSource') }}</button>
          <button class="start-item" @click="openExternal(DONATE_URL); startMenuOpen = false"><Heart :size="16" /> {{ $t('settings.about.donate') }}</button>
          <button class="start-item danger" @click="doLogout"><LogOut :size="16" /> {{ $t('app.logout') }}</button>
        </div>
      </div>
      <div class="task-items">
        <!-- 固定到任务栏的快捷方式（不随窗口开关，点击即打开应用） -->
        <div
          v-for="ps in pinnedShortcuts"
          :key="'pin-' + ps.key"
          class="task-item pinned"
          :class="{ 'window-open': pinnedWindowOpen(ps.key) }"
          :title="ps.titleKey ? $t(ps.titleKey) : ps.label"
          @click="openPinned(ps)"
        >
          <span class="icon"><component :is="ps.icon" :size="20" /></span>
        </div>
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

  <!-- 全局快捷搜索（Ctrl+K / Spotlight）：搜索功能/节点/站点/容器并直达 -->
  <CommandPalette v-if="loggedIn" ref="paletteRef" :app-items="paletteItems" @open="onPaletteOpen" />

  <!-- 模块权限提示条：受限管理员点开「无权限模块」时不隐藏入口，只提示无权限 -->
  <div v-if="permNotice" class="perm-notice">{{ permNotice }}</div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, markRaw, watch, defineAsyncComponent } from 'vue'
import { useI18n } from 'vue-i18n'
import WindowFrame from './components/WindowFrame.vue'
import WindowContent from './components/WindowContent.vue'
import PanelLayout from './components/PanelLayout.vue'
import PanelHome from './components/PanelHome.vue'
// 功能窗口组件：统一从「按需加载」注册表命名导入（见 components/windows/lazyWindows.js）。
// 每个窗口会被 Vite 切成独立 chunk，只在真正打开时才下载/执行——首屏不再需要解析
// 全部功能代码（含 ECharts / xterm / markdown-it 等重依赖），这是面板启动提速的关键。
// 使用方式与静态导入完全一致：component: markRaw(XxxWindow)。
// 注：模板中直接渲染的基础组件（Login / PanelLayout / PanelHome / WindowContent /
// 告警层 / CommandPalette）仍保持静态导入，它们属于首屏必需，不能延迟。
import {
  DockerWindow, ProcessWindow, FilesWindow, RecycleBinWindow, TerminalWindow,
  SitesWindow, SiteEditWindow, DatabaseWindow, EditorWindow, MediaWindow,
  UserWindow, ChangePasswordWindow, FrpWindow, LogsWindow, SettingsWindow,
  ContainerLogsWindow, ContainerDetailWindow, ContainerStatsWindow, ContainerEditWindow,
  DockerConfigEditorWindow, AppStoreWindow, AppStoreInstallWindow, AppStoreComposeEditorWindow,
  AppStoreInstallLogWindow, AppStoreReadmeWindow, FirewallRuleFormWindow, BackupTaskFormWindow,
  BackupRemoteFormWindow, DatabaseManageWindow, DatabaseCreateWindow, TamperFormWindow,
  SiteMaintenanceWindow, AppStoreConfigWindow, CronTaskFormWindow, NotifyChannelFormWindow,
  NotifyRuleFormWindow, FrpProxyFormWindow, GitDeployFormWindow, LogCollectFormWindow,
  ServiceMonitorFormWindow, UptimeFormWindow, FtpUserFormWindow, WafAclFormWindow,
  SslUploadWindow, SslLeFormWindow, SshKeyGenWindow, SshKeyImportWindow, SshKeyDeployWindow,
  TasksWindow, ShunxSecurityWindow, UISettingsWindow, ConnectionFormWindow, NetStorageWindow,
  NetStorageBrowseWindow, NetStorageFormWindow, RuntimeWindow, RuntimeCreateWindow, DisksWindow,
  MonitoringWindow, CertWindow, WebStatsWindow, RewriteWindow, SiteOptsWindow,
  MetricsHistoryWindow, RollbackWindow, BatchWindow, GitDeployWindow, ReportWindow,
  PortForwardWindow, PortForwardFormWindow, ImageScanWindow, SlowQueryWindow, FtpUsersWindow,
  PhpVersionsWindow, SessionsWindow,
  preloadWindows,   // 空闲预加载：桌面模式下分批预取全部窗口 chunk（打开即渲染）
} from './components/windows/lazyWindows.js'
import ShunXSetup from './components/ShunXSetup.vue'
import TamperAlert from './components/TamperAlert.vue'
import InstallCheckAlert from './components/InstallCheckAlert.vue'
import CommandPalette from './components/CommandPalette.vue'
import Login from './views/Login.vue'
import { shunxApi, systemApi } from './api'
import { auth, clearAuth, isAdmin, isFullAdmin, hasPerm } from './store/auth'
import { uiState, loadUi, loadUiEffective } from './store/ui'
import { settings } from './store/settings'
import { desktopPrefs, bindUser as bindDesktopUser, hideShortcut, pinShortcut, unpinShortcut } from './store/desktopPrefs'
import { systemState, startMetrics, stopMetrics } from './store/systemMetrics'
import { startDocker, stopDocker } from './store/docker'
import { nodes as nodesStore, refreshNodes } from './store/nodes'
import { setRequestNode } from './store/requestNode'
import { tamperState, startTamper, stopTamper } from './store/tamper'
import { Archive, Container, Settings, Folder, Trash2, Terminal, FileText, Image as ImageIcon, Film, LogOut, LayoutGrid, UserCircle2, Globe, Database, Lock, ScrollText, Shield, ShieldAlert, ShieldCheck, Store, BookOpen, ListChecks, Cpu, HardDrive, Palette, Radio, Cloud, Activity, BarChart3, FileCode2, History, MonitorSmartphone, Unlink, UserCheck, Wrench, Settings2, ServerCog, Bug, Pin, PinOff, EyeOff, Clock, BellRing, Gauge, KeyRound, FileUp, Send, Home, Github, Heart, Star } from 'lucide-vue-next'   // 图标库：Lucide 矢量图标组件（桌面 / 窗口 / 按钮使用；Star 用于 Launchpad 社区支持提示）

// 桌面「系统概览」三张卡片：按需加载（异步组件）。
// RingCard / MonitorCard 依赖 ECharts（体积大），且只在「类桌面」形态的首屏渲染——
// 「标准面板模式」完全不使用它们。改为异步组件后 ECharts 不再进入入口 chunk，
// 面板模式的启动因此不必再下载/解析图表库。
const RingCard = defineAsyncComponent(() => import('./components/cards/RingCard.vue'))
const MonitorCard = defineAsyncComponent(() => import('./components/cards/MonitorCard.vue'))
const InfoNotesCard = defineAsyncComponent(() => import('./components/cards/InfoNotesCard.vue'))

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
// perm：对应后端 require_perm(<模块>) 的模块 key（受限管理员按此门控入口）；
//       省略 = 不受模块限制（只读监控 / 面板自身功能，仍是 adminOnly）。
// 显示名 / 门控三处共用：桌面图标、面板模式侧边栏、Ctrl+K 全局搜索（均取 visibleShortcuts）。
const shortcuts = ref([
  // remoteCap：host（缺省）可在远端节点使用；local 为面板自身管理项，远端节点隐藏
  { key: 'sites', label: '网站', titleKey: 'app.shortcut.sites', icon: markRaw(Globe), component: markRaw(SitesWindow), w: 900, h: 560, adminOnly: true, perm: 'sites', remoteCap: 'local' },
  { key: 'database', label: '数据库', titleKey: 'app.shortcut.database', icon: markRaw(Database), component: markRaw(DatabaseWindow), w: 860, h: 540, adminOnly: true, perm: 'database', remoteCap: 'local' },
  // 计划任务已合并进「任务」应用，桌面不再单独保留
  // { key: 'cron', label: '计划任务', titleKey: 'app.shortcut.cron', icon: markRaw(Clock), component: markRaw(CronWindow), w: 800, h: 520, adminOnly: true, perm: 'cron', remoteCap: 'local' },
  // 防火墙已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'firewall', label: '防火墙', titleKey: 'app.shortcut.firewall', icon: markRaw(Shield), component: markRaw(FirewallWindow), w: 800, h: 540, adminOnly: true, perm: 'firewall' },
  { key: 'frp', label: 'Frp内网穿透', titleKey: 'app.shortcut.frp', icon: markRaw(Radio), component: markRaw(FrpWindow), w: 900, h: 600, adminOnly: true, perm: 'frp', remoteCap: 'local' },
  // SSL 已合并进「网站」应用的 SSL证书 标签页，桌面不再单独保留
  // { key: 'ssl', label: 'SSL', titleKey: 'app.shortcut.ssl', icon: markRaw(Lock), component: markRaw(SSLWindow), w: 820, h: 520, adminOnly: true, perm: 'sites', remoteCap: 'local' },
  { key: 'logs', label: '日志', titleKey: 'app.shortcut.logs', icon: markRaw(ScrollText), component: markRaw(LogsWindow), w: 900, h: 560, adminOnly: true, perm: 'logs' },
  // 审计日志已合并进「日志」应用的审计日志标签页，桌面不再单独保留
  // { key: 'auditlog', label: '审计日志', titleKey: 'app.shortcut.auditlog', icon: markRaw(ScrollText), component: markRaw(AuditLogWindow), w: 900, h: 560, adminOnly: true, perm: 'logs', remoteCap: 'local' },
  { key: 'docker', label: 'Docker', titleKey: 'app.shortcut.docker', icon: markRaw(Container), component: markRaw(DockerWindow), w: 820, h: 520, adminOnly: true, perm: 'docker' },
  // Docker 数据卷已合并进「Docker」应用的数据卷视图，桌面不再单独保留
  // { key: 'dockervolumes', label: 'Docker卷', titleKey: 'app.shortcut.dockervolumes', icon: markRaw(DatabaseBackup), component: markRaw(DockerVolumesWindow), w: 860, h: 540, adminOnly: true, perm: 'docker' },
  // 容器资源与端口编辑（CPU/内存/环境变量/端口映射，管理员专属）
  // 已从桌面隐藏，仅保留 Docker 容器右键「编辑」入口（openContainerEdit）
  // { key: 'containeredit', label: '容器编辑', titleKey: 'app.shortcut.containeredit', icon: markRaw(Settings2), component: markRaw(ContainerEditWindow), w: 760, h: 660, adminOnly: true, perm: 'docker' },
  { key: 'appstore', label: '应用商店', titleKey: 'app.shortcut.appstore', icon: markRaw(Store), component: markRaw(AppStoreWindow), w: 920, h: 580, adminOnly: true, perm: 'appstore', remoteCap: 'local' },
  // 任务 = 计划任务 + 任务中心 合并
  { key: 'tasks', label: '任务', titleKey: 'app.shortcut.tasks', icon: markRaw(ListChecks), component: markRaw(TasksWindow), w: 900, h: 560, adminOnly: true, perm: 'appstore', remoteCap: 'local' },
  // ShunX保护机制 = 防火墙 + 应用防火墙 + 网页防篡改 + 数据库保护 + 系统体检 + 面板备份 + 备份中心 + 通知中心 + SSH密钥 合并
  // 聚合窗口：任一相关模块在授权范围内即可打开（窗口内无权限的标签页由后端 403 兜底）
  { key: 'shunxprotection', label: 'ShunX保护机制', titleKey: 'app.shortcut.shunxprotection', icon: markRaw(ShieldCheck), component: markRaw(ShunxSecurityWindow), w: 980, h: 620, adminOnly: true, perm: ['firewall', 'sites', 'tamper', 'backup', 'notify', 'healthcheck'] },
  // 下述应用已合并进「ShunX保护机制」，桌面不再单独保留
  // { key: 'protection', label: 'Graw数据库保护机制', titleKey: 'app.shortcut.protection', icon: markRaw(ShieldCheck), component: markRaw(ProtectionWindow), w: 860, h: 560, adminOnly: true, perm: 'firewall', remoteCap: 'local' },
  // { key: 'tamper', label: 'ShunX网页防篡改', titleKey: 'app.shortcut.tamper', icon: markRaw(ShieldAlert), component: markRaw(TamperWindow), w: 920, h: 580, adminOnly: true, perm: 'tamper', remoteCap: 'local' },
  // { key: 'waf', label: '应用防火墙', titleKey: 'app.shortcut.waf', icon: markRaw(ShieldBan), component: markRaw(WafWindow), w: 980, h: 620, adminOnly: true, perm: 'sites', remoteCap: 'local' },
  { key: 'runtime', label: '运行环境', titleKey: 'app.shortcut.runtime', icon: markRaw(Cpu), component: markRaw(RuntimeWindow), w: 900, h: 560, adminOnly: true, perm: 'docker', remoteCap: 'local' },
  { key: 'process', label: '进程管理', titleKey: 'app.shortcut.process', icon: markRaw(Settings), component: markRaw(ProcessWindow), w: 780, h: 520, adminOnly: true, perm: 'process' },
  { key: 'files', label: '文件管理', titleKey: 'app.shortcut.files', icon: markRaw(Folder), component: markRaw(FilesWindow), w: 820, h: 540, adminOnly: true, perm: 'files' },
  { key: 'recycle', label: '回收站', titleKey: 'app.shortcut.recycle', icon: markRaw(Trash2), component: markRaw(RecycleBinWindow), w: 760, h: 480, adminOnly: true, perm: 'files' },
  { key: 'netstorage', label: '网络储存', titleKey: 'app.shortcut.netstorage', icon: markRaw(Cloud), component: markRaw(NetStorageWindow), w: 860, h: 540, adminOnly: true, perm: 'netstorage', remoteCap: 'local' },
  // 界面设置影响全局展示，属面板自身功能：始终要求完整管理员（无 perm + fullAdminOnly）
  { key: 'uisettings', label: '界面设置', titleKey: 'app.shortcut.uisettings', icon: markRaw(Palette), component: markRaw(UISettingsWindow), w: 520, h: 540, adminOnly: true, fullAdminOnly: true, remoteCap: 'local' },
  { key: 'disks', label: '磁盘管理', titleKey: 'app.shortcut.disks', icon: markRaw(HardDrive), component: markRaw(DisksWindow), w: 900, h: 560, adminOnly: true, perm: 'disks' },
  // 备份中心已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'backup', label: '备份中心', titleKey: 'app.shortcut.backup', icon: markRaw(DatabaseBackup), component: markRaw(BackupWindow), w: 920, h: 580, adminOnly: true, perm: 'backup', remoteCap: 'local' },
  // 通知中心已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'notify', label: '通知中心', titleKey: 'app.shortcut.notify', icon: markRaw(BellRing), component: markRaw(NotifyWindow), w: 860, h: 560, adminOnly: true, perm: 'notify', remoteCap: 'local' },
  // 站点监控 + 服务监控 合并为「监控」（只读监控数据，不受模块限制）
  { key: 'monitoring', label: '监控', titleKey: 'app.shortcut.monitoring', icon: markRaw(Activity), component: markRaw(MonitoringWindow), w: 920, h: 580, adminOnly: true, remoteCap: 'local' },
  // { key: 'uptime', label: '站点监控', titleKey: 'app.shortcut.uptime', icon: markRaw(Activity), component: markRaw(UptimeWindow), w: 860, h: 560, adminOnly: true, perm: 'notify', remoteCap: 'local' },
  { key: 'webstats', label: '访问统计', titleKey: 'app.shortcut.webstats', icon: markRaw(BarChart3), component: markRaw(WebStatsWindow), w: 980, h: 640, adminOnly: true, perm: 'sites', remoteCap: 'local' },
  { key: 'rewrite', label: '伪静态规则', titleKey: 'app.shortcut.rewrite', icon: markRaw(FileCode2), component: markRaw(RewriteWindow), w: 780, h: 560, adminOnly: true, perm: 'sites', remoteCap: 'local' },
  { key: 'siteopts', label: '防盗链缓存', titleKey: 'app.shortcut.siteopts', icon: markRaw(Unlink), component: markRaw(SiteOptsWindow), w: 860, h: 600, adminOnly: true, perm: 'sites', remoteCap: 'local' },
  // 历史监控为只读指标数据，不受模块限制
  { key: 'metricshistory', label: '历史监控', titleKey: 'app.shortcut.metricshistory', icon: markRaw(History), component: markRaw(MetricsHistoryWindow), w: 980, h: 640, adminOnly: true, remoteCap: 'local' },
  // 服务监控已合并进「监控」应用，桌面不再单独保留
  // { key: 'svcmonitor', label: '服务监控', titleKey: 'app.shortcut.svcmonitor', icon: markRaw(Server), component: markRaw(ServiceMonitorWindow), w: 920, h: 560, adminOnly: true, perm: 'svcmonitor' },
  // SSH 密钥已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'sshkeys', label: 'SSH 密钥', titleKey: 'app.shortcut.sshkeys', icon: markRaw(KeyRound), component: markRaw(SSHKeysWindow), w: 880, h: 540, adminOnly: true, remoteCap: 'local' },
  { key: 'certcheck', label: '证书到期', titleKey: 'app.shortcut.certcheck', icon: markRaw(Lock), component: markRaw(CertWindow), w: 820, h: 540, adminOnly: true, perm: 'notify', remoteCap: 'local' },
  // 系统体检已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'healthcheck', label: '系统体检', titleKey: 'app.shortcut.healthcheck', icon: markRaw(Stethoscope), component: markRaw(HealthCheckWindow), w: 820, h: 600, adminOnly: true, perm: 'healthcheck', remoteCap: 'local' },
  { key: 'ftpusers', label: 'FTP用户', titleKey: 'app.shortcut.ftpusers', icon: markRaw(UserCheck), component: markRaw(FtpUsersWindow), w: 860, h: 560, adminOnly: true, perm: 'ftpusers', remoteCap: 'local' },
  // PHP 多版本管理：探测系统 PHP/FPM + 站点 PHP 版本关联（仅管理员）
  { key: 'phpversions', label: 'PHP版本', titleKey: 'app.shortcut.phpversions', icon: markRaw(ServerCog), component: markRaw(PhpVersionsWindow), w: 900, h: 580, adminOnly: true, perm: 'sites', remoteCap: 'local' },
  // 面板备份已合并进「ShunX保护机制」应用，桌面不再单独保留
  // { key: 'panelbackup', label: '面板备份', titleKey: 'app.shortcut.panelbackup', icon: markRaw(Archive), component: markRaw(PanelBackupWindow), w: 860, h: 540, adminOnly: true, remoteCap: 'local' },
  // 系统更新已从桌面移除（面板自身更新入口走其他渠道）
  // { key: 'update', label: '系统更新', titleKey: 'app.shortcut.update', icon: markRaw(RefreshCw), component: markRaw(UpdateWindow), w: 640, h: 420, adminOnly: true, remoteCap: 'local' },
  // 登录日志已合并进「日志」应用的登录日志标签页，桌面不再单独保留
  // { key: 'loginlog', label: '登录日志', titleKey: 'app.shortcut.loginlog', icon: markRaw(Fingerprint), component: markRaw(LoginLogWindow), w: 900, h: 560, adminOnly: false, perm: 'loginlog', remoteCap: 'local' },
  // 会话管理：在线会话列表、踢出单设备、强制全部下线（普通用户仅管理自己的会话）
  { key: 'sessions', label: '会话管理', titleKey: 'app.shortcut.sessions', icon: markRaw(MonitorSmartphone), component: markRaw(SessionsWindow), w: 900, h: 560, adminOnly: false, remoteCap: 'local' },
  { key: 'terminal', label: '终端', titleKey: 'app.shortcut.terminal', icon: markRaw(Terminal), component: markRaw(TerminalWindow), w: 780, h: 460, adminOnly: true, perm: 'terminal' },
  // Foxcode：双击打开终端并自动输入 foxcode 命令启动（同终端模块权限）
  { key: 'foxcode', label: 'Foxcode', icon: markRaw(Terminal), component: markRaw(TerminalWindow), w: 780, h: 460, adminOnly: true, perm: 'terminal', props: { autoCommand: 'foxcode' } },
  // 配置回滚：站点/防火墙配置的写前快照 + 一键恢复（管理员）
  { key: 'rollback', label: '配置回滚', titleKey: 'app.shortcut.rollback', icon: markRaw(History), component: markRaw(RollbackWindow), w: 980, h: 580, adminOnly: true, perm: 'backup' },
  // 批量操作中心：多节点批量命令 / 批量容器启停（管理员）
  { key: 'batch', label: '批量操作', titleKey: 'app.shortcut.batch', icon: markRaw(ServerCog), component: markRaw(BatchWindow), w: 1000, h: 620, adminOnly: true, perm: 'batch' },
  // 站点 Git 自动部署：绑定仓库 + Webhook 自动发布（管理员，面板自身管理项）
  { key: 'gitdeploy', label: 'Git 部署', titleKey: 'app.shortcut.gitdeploy', icon: markRaw(FileCode2), component: markRaw(GitDeployWindow), w: 960, h: 600, adminOnly: true, perm: 'gitdeploy', remoteCap: 'local' },
  // 巡检报告：每日/手动生成系统健康汇总并推送（管理员）
  { key: 'report', label: '巡检报告', titleKey: 'app.shortcut.report', icon: markRaw(BarChart3), component: markRaw(ReportWindow), w: 960, h: 600, adminOnly: true, perm: 'report' },
  // SSH 端口转发：本地直连远程节点服务（管理员，面向远程节点的隧道）
  { key: 'portforward', label: '端口转发', titleKey: 'app.shortcut.portforward', icon: markRaw(Unlink), component: markRaw(PortForwardWindow), w: 880, h: 580, adminOnly: true, perm: 'portforward' },
  // 镜像漏洞扫描：本地 advisory 比对（管理员）
  { key: 'imgsafety', label: '镜像扫描', titleKey: 'app.shortcut.imgsafety', icon: markRaw(ShieldCheck), component: markRaw(ImageScanWindow), w: 980, h: 600, adminOnly: true, perm: 'docker' },
  // MySQL 慢查询分析：慢日志 TOP N 与建议（管理员）
  { key: 'slowquery', label: '慢查询分析', titleKey: 'app.shortcut.slowquery', icon: markRaw(Activity), component: markRaw(SlowQueryWindow), w: 1000, h: 620, adminOnly: true, perm: 'database' }
])

// 桌面快捷方式：管理员可见全部，普通用户仅可见非管理功能。
// 远端节点下：未配置 Agent 时隐藏 local 类（面板自身管理项）应用，避免误操作本机；
// 已配置 Agent 时 local 类经 Agent 代理在子节点可用，正常显示。
// --- 统一入口门控：管理员身份 / 隐藏偏好 / 远端 local 类 ---
// 真正的权限边界在后端 require_perm。按产品约定：**不按模块隐藏入口**——受限管理员
// 也能看到全部功能入口（保持界面一致、便于知晓系统有哪些能力），点开无权限的模块时
// 由 openWindow() 提示「无此权限」而不打开窗口，避免出现空白窗口。
// 桌面、面板模式侧边栏、Ctrl+K 全局搜索共用 `visibleShortcuts`，因此三处门控天然一致。
// 模块权限判定：perm 支持单 key 或 key 数组（任一命中即放行，如聚合窗口）。
function allowsPerm(perm) {
  if (!perm) return true
  return hasPerm(...(Array.isArray(perm) ? perm : [perm]))
}

function canAccess(s) {
  if (s.adminOnly && !isAdmin()) return false
  // fullAdminOnly：面板自身安全边界（如用户管理 / 界面设置），受限管理员不可见
  if (s.fullAdminOnly && !isFullAdmin()) return false
  return true
}

// 模块权限提示：受限管理员点开无权限模块时短暂显示（2.5s 自动消失）
const permNotice = ref('')
let permNoticeTimer = null
function showPermNotice() {
  permNotice.value = t('common.noModulePerm')
  if (permNoticeTimer) clearTimeout(permNoticeTimer)
  permNoticeTimer = setTimeout(() => { permNotice.value = '' }, 2500)
}

// --- 快捷方式可见性：管理员 / 隐藏 Foxcode / 远端节点 local 类 / 用户隐藏
//     （不按模块权限隐藏：受限管理员可见全部模块入口，点开无权限时提示） ---
const visibleShortcuts = computed(() => shortcuts.value.filter(s =>
  canAccess(s) &&
  !(s.key === 'foxcode' && settings.hideFoxcode) &&
  !desktopPrefs.hiddenKeys.includes(s.key) &&
  !(isCurrentHostRemote.value && !currentHostAgentReady.value && s.remoteCap === 'local')
))

// --- 桌面快捷方式右键菜单：隐藏 / 固定到任务栏 ---
const shortcutMenu = ref({ show: false, x: 0, y: 0, sc: null })

// 桌面图标下方文字的样式（设置 → 面板 里可调）：字号 / 颜色 / 黑边描边。
// 黑边开启时用 8 向 text-shadow 模拟描边，否则保持默认柔和投影。
const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/
const shortcutLabelStyle = computed(() => {
  const size = Number(settings.shortcutFontSize)
  const fontSize = Number.isFinite(size) && size >= 8 && size <= 24 ? Math.round(size) : 12
  const color = HEX_COLOR_RE.test(settings.shortcutLabelColor) ? settings.shortcutLabelColor : '#ffffff'
  const textShadow = settings.shortcutLabelStroke
    ? '-1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000, 0 -1px 0 #000, 0 1px 0 #000, -1px 0 0 #000, 1px 0 0 #000'
    : '0 1px 2px rgba(0,0,0,0.45)'
  return { fontSize: fontSize + 'px', color, textShadow }
})

function openShortcutMenu(e, sc) {
  // 菜单定位：限制在视口内，避免贴边被截断
  const menuW = 160
  const menuH = 76
  shortcutMenu.value = {
    show: true,
    x: Math.min(e.clientX, window.innerWidth - menuW - 8),
    y: Math.min(e.clientY, window.innerHeight - menuH - 8),
    sc,
  }
}

// 隐藏当前右键的应用（桌面移除，可在「设置 → 桌面」里恢复）
function hideScFromMenu() {
  const sc = shortcutMenu.value.sc
  if (!sc) return
  hideShortcut(sc.key)
  // 若该应用窗口正开着，不强制关闭（只隐藏入口），并收窄为桌面右键菜单已处理
  shortcutMenu.value.show = false
}

// 固定 / 取消固定当前右键的应用（任务栏常驻入口）
function togglePinSc(pin) {
  const sc = shortcutMenu.value.sc
  if (!sc) return
  if (pin) {
    pinShortcut(sc.key)
  } else {
    unpinShortcut(sc.key)
  }
  shortcutMenu.value.show = false
}

// 固定的应用在任务栏的渲染集：按固定顺序匹配 shortcuts 定义
const pinnedShortcuts = computed(() => {
  const byKey = {}
  shortcuts.value.forEach(s => { byKey[s.key] = s })
  return desktopPrefs.pinnedKeys.map(k => byKey[k]).filter(Boolean)
})

// 固定应用是否已有窗口打开（任务栏高亮提示）
function pinnedWindowOpen(key) {
  return openWindows.value.some(w => w.key === key)
}

// 点击任务栏固定图标：未打开则打开；已打开则聚焦（若已聚焦则最小化，与其他窗口一致）
function openPinned(ps) {
  const existing = openWindows.value.find(w => w.key === ps.key)
  if (existing) {
    if (existing.minimized) {
      existing.minimized = false
      focusWindow(existing.id)
    } else if (activeWindowId.value === existing.id) {
      existing.minimized = true
    } else {
      focusWindow(existing.id)
    }
    return
  }
  if (ps.key === 'foxcode') {
    openFoxcode()
  } else {
    openWindow(ps.key)
  }
}

// 登录后校准桌面偏好作用域：切换用户时按「仅当前用户」开关读取对应偏好
watch(() => auth.user?.username, (name) => {
  if (name) bindDesktopUser()
})

// --- 窗口系统状态：选中项、已开窗口、聚焦窗口、开始菜单 ---
const selected = ref(null)
const openWindows = ref([])
const activeWindowId = ref(null)
const startMenuOpen = ref(false)

// 统一面板兼容的实际生效值：跟随界面设置开关（完全免费，无付费门控）。
const unifiedPanelOn = computed(() => !!settings.unifiedPanel)

// 标准面板模式：设置里开启后界面切换为 1Panel 式侧边栏布局（本地偏好，即改即生效）
const panelModeOn = computed(() => !!settings.panelMode)

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
// 「设置」窗口 →「界面设置」入口：复用桌面快捷方式门控（adminOnly + 远程能力）。
function openUiSettings() { openWindow('uisettings') }
function openTasks() { openWindow('tasks') }

// 报告问题：跳转到项目 GitHub Issues 新建页（新窗口，noopener 防钓鱼）
function reportIssue() {
  window.open('https://github.com/wuhulab/Graw/issues/new', '_blank', 'noopener')
}

// ---- 关于外链（源码仓库 / 捐赠）----
// 集中常量便于后续更换地址；点击后新标签页打开，异常仅提示不中断桌面
const SOURCE_REPO_URL = 'https://github.com/wuhulab/Graw'   // 源码仓库
const DONATE_URL = 'https://afdian.com/a/shunianssy'        // 捐赠（爱发电）

/**
 * 新标签页打开外部链接
 * @param {string} url - 目标地址
 */
function openExternal(url) {
  try {
    window.open(url, '_blank', 'noopener')
  } catch (e) {
    // 浏览器拦截弹窗等异常：仅提示，不影响桌面其余操作
    console.error('[app] 打开外链失败:', url, e)
    alert(`${url}`)
  }
}

// ---- Launchpad「社区 Star 支持」提示 ----
// 目标：Graw 为社区推动的开源项目，引导用户前往 GitHub 点亮 Star，并提供
// 「填写 GitHub 用户名 → 在线校验 → 通过后关闭」的轻量闭环。
// 校验原理：GitHub 公开 API `GET /users/{name}/starred`（支持 CORS、免令牌），
// 使用 sort=created&direction=desc 让最新 Star 排在最前，最多查 2 页兜底；
// 命中目标仓库即视为验证通过。
// 状态记忆：验证通过后记录「GitHub 用户名 + 验证日期」；之后每次启动面板时，
// 若当天尚未复查，则静默复查该账号是否仍 Star（当天只查一次）；
// 若已取消 Star（或账号不可查），清除记录并重新显示提示。
// 注意：纯前端展示引导，不参与面板权限判定；接口失败/离线时保持现状，不打扰用户。
const STAR_TIP_STATE_KEY = 'graw.starTip.state'    // 验证状态：{ username, verifiedOn }
const STAR_TIP_LEGACY_KEY = 'graw.starTip.done'    // 旧版布尔标记（已废弃，读取时自动清除以重置状态）
const STAR_TIP_REPO = 'wuhulab/Graw'               // 需要验证 Star 的目标仓库（与 SOURCE_REPO_URL 对应）

// 本地日期字符串（YYYY-MM-DD，按浏览器时区）——用于「当天只复查一次」的判定
function starTipToday() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// 读取本地验证状态：隐私模式 / 存储被禁用 / 内容损坏时一律视为「未验证」，不影响桌面使用
function readStarTipState() {
  try {
    // 旧版实现只存布尔 '1'：读取时直接清除，回到待验证状态（结构升级后的自然重置）
    if (localStorage.getItem(STAR_TIP_LEGACY_KEY) !== null) {
      localStorage.removeItem(STAR_TIP_LEGACY_KEY)
      console.info('[app] 已清除旧版 Star 验证标记，社区支持提示将重新显示')
    }
    const raw = JSON.parse(localStorage.getItem(STAR_TIP_STATE_KEY) || 'null')
    if (raw && typeof raw.username === 'string' && raw.username) {
      return { username: raw.username, verifiedOn: typeof raw.verifiedOn === 'string' ? raw.verifiedOn : '' }
    }
  } catch (e) { /* 存储不可用或内容损坏：视为未验证 */ }
  return null
}

const starTipState = ref(readStarTipState())             // null=未验证；{ username, verifiedOn }=已验证
const starTipDone = computed(() => !!starTipState.value) // 卡片显隐：已有验证记录则隐藏
const starTipVerifyOpen = ref(false)   // 是否展开「用户名 + 验证」输入区
const starTipUsername = ref('')        // 待校验的 GitHub 用户名
const starTipVerifying = ref(false)    // 校验请求进行中（禁用重复点击）
const starTipMsg = ref('')             // 校验结果提示文案
const starTipMsgOk = ref(false)        // 提示是否为成功态（用于绿色样式）

// 写入 / 清除验证状态；存储不可用时仅本次会话生效
function writeStarTipState(next) {
  starTipState.value = next
  try {
    if (next) localStorage.setItem(STAR_TIP_STATE_KEY, JSON.stringify(next))
    else localStorage.removeItem(STAR_TIP_STATE_KEY)
  } catch (e) { /* 忽略：存储不可用不影响功能 */ }
}

// 点击「GitHub 链接」：新标签页打开仓库页并展开验证区，引导用户 Star 后回来验证
function starTipOpenRepo() {
  openExternal(SOURCE_REPO_URL)
  starTipVerifyOpen.value = true
}

// 手动验证通过：记录「用户名 + 今天」，卡片随即隐藏；当天内不再复查
function starTipMarkDone(username) {
  writeStarTipState({ username, verifiedOn: starTipToday() })
}

/**
 * 查询 GitHub 用户是否已 Star 目标仓库（纯查询，不改变界面状态）
 * - 手动验证与启动复查复用同一段逻辑
 * @returns {Promise<'starred'|'not-starred'|'user-not-found'|'error'>}
 *   starred=已 Star；not-starred=确认未 Star；user-not-found=用户不存在；
 *   error=网络异常或接口限流（无法确认，由调用方保守处理）
 */
async function queryGithubStarred(name) {
  const target = STAR_TIP_REPO.toLowerCase()
  try {
    // 用户刚 Star 时目标仓库必然出现在「最近 Star」最前面；查 2 页（200 条）足够兜底
    for (let page = 1; page <= 2; page++) {
      const url = `https://api.github.com/users/${encodeURIComponent(name)}/starred`
        + `?per_page=100&sort=created&direction=desc&page=${page}`
      const res = await fetch(url, { headers: { Accept: 'application/vnd.github.star+json' } })
      if (res.status === 404) return 'user-not-found'
      if (!res.ok) {
        // 403 多为匿名调用限流（60 次/小时）；其余状态码同理视为「无法确认」
        console.warn('[app] GitHub starred 查询失败:', res.status, url)
        return 'error'
      }
      const list = await res.json()
      if (!Array.isArray(list) || list.length === 0) return 'not-starred'
      // star+json 媒体类型下条目为 { starred_at, repo }；不带该头时条目本身即仓库对象，两种都兼容
      if (list.some(it => ((it.repo && it.repo.full_name) || it.full_name || '').toLowerCase() === target)) return 'starred'
      if (list.length < 100) return 'not-starred'  // 不足一页说明已到末尾，无需继续翻页
    }
    return 'not-starred'
  } catch (e) {
    // 网络异常（断网 / 浏览器拦截等）——无法确认
    console.error('[app] Star 查询请求异常:', e)
    return 'error'
  }
}

/**
 * 手动验证：校验输入的用户名是否已 Star 目标仓库
 * - 已 Star → 显示感谢并短暂停顿后关闭卡片
 * - 未 Star / 用户不存在 / 接口异常 → 给出对应提示，保留输入便于重试
 */
async function verifyStarTip() {
  if (starTipVerifying.value) return
  const name = starTipUsername.value.trim().replace(/^@/, '')  // 容忍粘贴「@用户名」
  if (!name) { starTipMsgOk.value = false; starTipMsg.value = t('app.starTip.needUsername'); return }
  starTipVerifying.value = true
  starTipMsg.value = ''
  try {
    const result = await queryGithubStarred(name)
    if (result === 'starred') {
      starTipMsgOk.value = true
      starTipMsg.value = t('app.starTip.thanks')
      console.info('[app] Star 验证通过，感谢支持！')
      setTimeout(() => starTipMarkDone(name), 1000)  // 让用户看到成功提示后再关闭卡片
    } else if (result === 'user-not-found') {
      starTipMsgOk.value = false
      starTipMsg.value = t('app.starTip.userNotFound')
    } else if (result === 'not-starred') {
      starTipMsgOk.value = false
      starTipMsg.value = t('app.starTip.notFound')
    } else {
      starTipMsgOk.value = false
      starTipMsg.value = t('app.starTip.netError')
    }
  } finally {
    starTipVerifying.value = false
  }
}

/**
 * 启动复查：面板启动（登录后）时，若已有验证记录且今天尚未复查，
 * 则静默确认该账号是否仍 Star 目标仓库：
 * - 仍已 Star → 刷新验证日期（当天不再复查），提示保持隐藏
 * - 已取消 Star / 账号不可查 → 清除记录，重新显示提示
 * - 接口异常 → 无法确认，保持现状，下次启动再复查（避免网络不佳时误打扰）
 */
async function autoReverifyStarTip() {
  const state = starTipState.value
  if (!state || !auth.token) return
  if (state.verifiedOn === starTipToday()) return  // 当天已复查过：不再请求
  const result = await queryGithubStarred(state.username)
  if (result === 'starred') {
    // 仍已 Star：记录本次复查日期，今天内不再复查
    writeStarTipState({ username: state.username, verifiedOn: starTipToday() })
    console.info('[app] Star 复查通过（当天不再复查）:', state.username)
  } else if (result === 'not-starred' || result === 'user-not-found') {
    // 已取消 Star（或账号不可查）：清除记录，重新显示社区支持提示
    console.info('[app] 未检测到 Star（已取消或账号不可查），重新显示社区支持提示:', state.username)
    writeStarTipState(null)
  } else {
    // 无法确认：保持现状，下次启动再试
    console.warn('[app] Star 复查无法确认（网络或限流），保持现状，下次启动再试')
  }
}

// 面板启动 / 登录后触发一次复查（未登录或当天已复查时内部直接跳过）
watch(() => auth.token, () => { autoReverifyStarTip() }, { immediate: true })

function doLogout() {
  startMenuOpen.value = false
  clearAuth()
  location.reload()
}

// --- 窗口内容事件映射（winEvents）---
// 桌面模式（WindowFrame 作用域插槽）与面板模式（PanelLayout 内容区）共用的一份
// 「窗口内容 → openXxx」事件清单。onXxx 键等价模板 @xxx；各 openXxx 均为本组件内的
// function 声明（会被提升），因此这里可以安全地直接引用。close/dirty 因需要按窗口
// id 定位，不放进映射（桌面分支逐个绑定，面板分支统一冒泡后按 activeId 处理）。
const winEvents = {
  onOpenTerminal: openTerminalAt,
  onOpenEditor: openEditor,
  onOpenMedia: openMedia,
  onOpenUsers: openUsers,
  onOpenUiSettings: openUiSettings,
  onOpenLogs: openContainerLogs,
  onOpenContainerTerminal: openContainerTerminal,
  onOpenContainerDetails: openContainerDetails,
  onOpenContainerStats: openContainerStats,
  onOpenContainerEdit: openContainerEdit,
  onOpenFiles: openFiles,
  onOpenDockerConfigEditor: openDockerConfigEditor,
  onOpenAppInstall: openAppStoreInstall,
  onOpenComposeEditor: openAppStoreComposeEditor,
  onOpenInstallLog: openAppStoreInstallLog,
  onOpenReadme: openAppStoreReadme,
  onOpenTaskCenter: openTasks,
  onOpenRuntimeCreate: openRuntimeCreate,
  onOpenConnectionForm: openConnectionForm,
  onOpenNetStorageBrowse: openNetStorageBrowse,
  onOpenNetStorageForm: openNetStorageForm,
  onOpenSiteEdit: openSiteEdit,
  onOpenFirewallRuleForm: openFirewallRuleForm,
  onOpenBackupTaskForm: openBackupTaskForm,
  onOpenBackupRemoteForm: openBackupRemoteForm,
  onOpenDatabaseManage: openDatabaseManage,
  onOpenDatabaseCreate: openDatabaseCreate,
  onOpenTamperForm: openTamperForm,
  onOpenSiteMaintenance: openSiteMaintenance,
  onOpenAppStoreConfig: openAppStoreConfig,
  onOpenCronTaskForm: openCronTaskForm,
  onOpenNotifyChannelForm: openNotifyChannelForm,
  onOpenNotifyRuleForm: openNotifyRuleForm,
  onOpenFrpProxyForm: openFrpProxyForm,
  onOpenGitDeployForm: openGitDeployForm,
  onOpenLogCollectForm: openLogCollectForm,
  onOpenServiceMonitorForm: openServiceMonitorForm,
  onOpenUptimeForm: openUptimeForm,
  onOpenFtpUserForm: openFtpUserForm,
  onOpenWafAclForm: openWafAclForm,
  onOpenSslUpload: openSslUpload,
  onOpenSslLeForm: openSslLeForm,
  onOpenSshKeyGen: openSshKeyGen,
  onOpenSshKeyImport: openSshKeyImport,
  onOpenSshKeyDeploy: openSshKeyDeploy,
  onOpenPortForwardForm: openPortForwardForm,
}

// 更新指定窗口的 dirty 标记（编辑器未保存时关闭前提示）
function setWinDirty(id, value) {
  const w = openWindows.value.find((x) => x.id === id)
  if (w) w.dirty = value
}

// 面板模式侧边栏/用户菜单点击：已打开同 key 窗口则聚焦（即切页），否则走 openWindow
// （openWindow 内部已含 adminOnly / remoteCap 两层守卫与统一面板节点绑定）
function onPanelOpen(key) {
  const existing = openWindows.value.find((w) => w.key === key)
  if (existing) {
    if (existing.minimized) existing.minimized = false
    focusWindow(existing.id)
    return
  }
  openWindow(key)
}

// 面板模式内容区窗口 dirty 标记：按 { id, value } 更新
function onPanelDirty({ id, value }) {
  setWinDirty(id, value)
}

function onDocClick(e) {
  // 右键菜单：点击菜单外部（含桌面空白、窗口、任务栏）任意处即关闭。
  // 必须在开始菜单判断之前执行——开始菜单关闭时若提前 return，右键菜单会残留。
  const ctx = e.target.closest('.shortcut-menu')
  if (shortcutMenu.value.show && !ctx) shortcutMenu.value.show = false
  if (!startMenuOpen.value) return
  const btn = e.target.closest('.start-button')
  const menu = e.target.closest('.start-menu')
  if (!btn && !menu && !ctx) startMenuOpen.value = false
}

// --- 全局快捷搜索（CommandPalette）：
// 功能入口取「对当前用户可见」的快捷方式（复用 visibleShortcuts 门控）；
// 站点/容器/节点由 CommandPalette 内部拉取；执行动作统一走 openWindow（含门控）。
const paletteRef = ref(null)
const paletteItems = computed(() =>
  visibleShortcuts.value.map(s => ({ key: s.key, label: s.titleKey ? t(s.titleKey) : (s.label || s.key) }))
)
function onPaletteOpen(key) {
  openWindow(key) // openWindow 内部已做 adminOnly / remoteCap 两层守卫
}
function onPaletteGlobalKey(e) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    paletteRef.value?.toggle()
  } else if (e.key === 'Escape') {
    paletteRef.value?.close()
  }
}

// --- 通用窗口打开：含 adminOnly / remoteCap 双重门控 ---
function openWindow(key) {
  let def = shortcuts.value.find(s => s.key === key)
  if (!def) {
    const extras = {
      users: { label: '账号管理', titleKey: 'app.winTitle.users', icon: markRaw(UserCircle2), component: markRaw(UserWindow), w: 600, h: 460, adminOnly: true, fullAdminOnly: true },
      changepwd: { label: '修改密码', titleKey: 'app.winTitle.changepwd', icon: markRaw(UserCircle2), component: markRaw(ChangePasswordWindow), w: 420, h: 360 },
      settings: { label: '设置', titleKey: 'app.winTitle.settings', icon: markRaw(Settings), component: markRaw(SettingsWindow), w: 520, h: 480 },
      // 面板模式「主页」：系统概览 + 实时监控 + 系统信息/备忘录（非窗口外壳的全屏视图）
      panelhome: { label: '主页', titleKey: 'panel.home', icon: markRaw(Home), component: markRaw(PanelHome), w: 900, h: 620 }
    }
    def = extras[key]
    if (!def) return
  }
  // 统一守卫：无论主快捷方式还是 extras，adminOnly 窗口都要求管理员
  // （后端 API 已有鉴权，此处为前端纵深防御，避免普通用户残留窗口 UI）
  if (def.adminOnly && !isAdmin()) return
  // 完整管理员专属（面板自身安全边界：用户管理 / 更新 / 插件等）
  if (def.fullAdminOnly && !isFullAdmin()) return
  // 模块权限守卫（受限管理员）：入口不再隐藏，点开无权限模块时不打开窗口、
  // 仅提示「无此权限」，避免出现空白窗口。此处覆盖所有入口（桌面图标、面板模式
  // 侧边栏、Ctrl+K 搜索、面板模式事件透传）；后端 require_perm 仍会返回 403 兜底。
  if (!allowsPerm(def.perm)) {
    showPermNotice()
    return
  }
  // 远程能力守卫：未配置 Agent 的远端节点下，local 类（面板自身管理项）应用
  // 禁止打开（后端同一守护返回 403，此处前端提前拦截并提示，避免空白窗口）。
  // 已配置 Agent 时 local 类经 Agent 代理在子节点可用，正常打开。
  if (def.remoteCap === 'local' && isCurrentHostRemote.value && !currentHostAgentReady.value) {
    alert(t('nodes.localOnlyOnRemote'))
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

// 防火墙：点击「新增端口/IP规则」打开独立的规则表单窗口（避免内嵌弹窗误触遮罩丢输入）
function openFirewallRuleForm(payload) {
  const id = ++windowSeq
  // 窗口标题按类型展示（端口规则 / IP 规则）
  const isPort = payload?.mode !== 'ip'
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'firewall-rule-form',
    nodeId: boundNode,
    title: isPort ? t('firewall.addPortRule') : t('firewall.addIpRule'),
    titleKey: isPort ? 'firewall.addPortRule' : 'firewall.addIpRule',
    icon: markRaw(Shield),
    component: markRaw(FirewallRuleFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 440,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 计划任务：新增/编辑「定时任务」的独立表单窗口（mode: regular/standar，task 存在即为编辑）
function openCronTaskForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.task
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'cron-task-form',
    nodeId: boundNode,
    title: isEdit ? '编辑定时任务' : '新建定时任务',
    titleKey: null,
    icon: markRaw(Clock),
    component: markRaw(CronTaskFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 520,
    height: 560,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 通知中心：新增/编辑「通知渠道」的独立表单窗口
function openNotifyChannelForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.channel
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'notify-channel-form',
    nodeId: boundNode,
    title: isEdit ? '编辑通知渠道' : '添加通知渠道',
    titleKey: null,
    icon: markRaw(BellRing),
    component: markRaw(NotifyChannelFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 520,
    height: 560,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 通知中心：新增/编辑「告警规则」的独立表单窗口
function openNotifyRuleForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.rule
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'notify-rule-form',
    nodeId: boundNode,
    title: isEdit ? '编辑告警规则' : '添加告警规则',
    titleKey: null,
    icon: markRaw(Gauge),
    component: markRaw(NotifyRuleFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 480,
    height: 420,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 内网穿透：新增/编辑「代理配置」的独立表单窗口
function openFrpProxyForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.proxy
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'frp-proxy-form',
    nodeId: boundNode,
    title: isEdit ? '编辑代理' : '新增代理',
    titleKey: null,
    icon: markRaw(Radio),
    component: markRaw(FrpProxyFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
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

// Git 部署：新增/编辑「部署绑定」的独立表单窗口（binding.isNew 为创建）
function openGitDeployForm(payload) {
  // 探针日志：排查面板模式下「新建」点击无反应时事件是否到达本函数（输出则链路通）
  console.debug('[panel] openGitDeployForm', payload)
  const id = ++windowSeq
  const isCreate = !!payload?.binding?.isNew
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'git-deploy-form',
    nodeId: boundNode,
    title: isCreate ? '创建部署绑定' : '编辑部署绑定',
    titleKey: null,
    icon: markRaw(FileCode2),
    component: markRaw(GitDeployFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 520,
    height: 560,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 日志中心：添加「日志收集」的独立表单窗口
function openLogCollectForm(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'log-collect-form',
    nodeId: boundNode,
    title: '添加日志收集',
    titleKey: null,
    icon: markRaw(ScrollText),
    component: markRaw(LogCollectFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 420,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 服务监控：新增/编辑「监控项」的独立表单窗口
function openServiceMonitorForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.item
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'service-monitor-form',
    nodeId: boundNode,
    title: isEdit ? '编辑监控项' : '添加监控项',
    titleKey: null,
    icon: markRaw(Activity),
    component: markRaw(ServiceMonitorFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 520,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 站点监控：新增/编辑「监控项」的独立表单窗口
function openUptimeForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.item
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'uptime-form',
    nodeId: boundNode,
    title: isEdit ? '编辑监控项' : '添加监控项',
    titleKey: null,
    icon: markRaw(Activity),
    component: markRaw(UptimeFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 400,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// FTP 用户：新增/编辑「FTP 用户」的独立表单窗口
function openFtpUserForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.user
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'ftp-user-form',
    nodeId: boundNode,
    title: isEdit ? '编辑 FTP 用户' : '添加 FTP 用户',
    titleKey: null,
    icon: markRaw(UserCheck),
    component: markRaw(FtpUserFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 460,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 应用防火墙：新增/编辑「自定义 ACL」的独立表单窗口（编辑结果经 onSaved 写回父窗口）
function openWafAclForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.acl
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'waf-acl-form',
    nodeId: boundNode,
    title: isEdit ? '编辑 ACL' : '新增 ACL',
    titleKey: null,
    icon: markRaw(ShieldCheck),
    component: markRaw(WafAclFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 420,
    height: 440,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// SSL：证书「上传」独立窗口（证书名由多语言键提供）
function openSslUpload(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'ssl-upload',
    nodeId: boundNode,
    title: t('ssl.uploadTitle'),
    titleKey: 'ssl.uploadTitle',
    icon: markRaw(Lock),
    component: markRaw(SslUploadWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 420,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// SSL：证书「Let's Encrypt 申请」独立窗口
function openSslLeForm(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'ssl-le-form',
    nodeId: boundNode,
    title: t('ssl.leTitle'),
    titleKey: 'ssl.leTitle',
    icon: markRaw(Lock),
    component: markRaw(SslLeFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 380,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// SSH 密钥：生成密钥对的独立窗口
function openSshKeyGen(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'ssh-key-gen',
    nodeId: boundNode,
    title: '生成密钥对',
    titleKey: null,
    icon: markRaw(KeyRound),
    component: markRaw(SshKeyGenWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 420,
    height: 360,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// SSH 密钥：导入私钥的独立窗口
function openSshKeyImport(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'ssh-key-import',
    nodeId: boundNode,
    title: '导入私钥',
    titleKey: null,
    icon: markRaw(FileUp),
    component: markRaw(SshKeyImportWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 520,
    height: 560,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// SSH 密钥：部署私钥到节点的独立窗口
function openSshKeyDeploy(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'ssh-key-deploy',
    nodeId: boundNode,
    title: '部署到节点',
    titleKey: null,
    icon: markRaw(Send),
    component: markRaw(SshKeyDeployWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 360,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 备份中心：新建/编辑「备份任务」的独立表单窗口（避免内嵌弹窗误触遮罩丢输入）
function openBackupTaskForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.task
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'backup-task-form',
    nodeId: boundNode,
    title: isEdit ? '编辑备份任务' : '新建备份任务',
    titleKey: null,
    icon: markRaw(Archive),
    component: markRaw(BackupTaskFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 520,
    height: 620,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 备份中心：新建/编辑「远程备份目标（WebDAV）」的独立表单窗口
function openBackupRemoteForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.remote
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'backup-remote-form',
    nodeId: boundNode,
    title: isEdit ? '编辑远程备份目标' : '添加远程备份目标',
    titleKey: null,
    icon: markRaw(Cloud),
    component: markRaw(BackupRemoteFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 480,
    height: 320,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 数据库：点击「管理」打开独立的连接管理控制台窗口（库列表 / 查询，误触不丢查询内容）
function openDatabaseManage(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'database-manage',
    nodeId: boundNode,
    title: t('database.manageTitle', { name: payload?.conn?.name || '' }),
    titleKey: null,
    icon: markRaw(Database),
    component: markRaw(DatabaseManageWindow),
    props: payload ? { ...payload } : {},
    x: 160 + (openWindows.value.length * 30),
    y: 70 + (openWindows.value.length * 25),
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

// 数据库：管理窗口内点「创建数据库」打开的独立表单窗口
function openDatabaseCreate(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'database-create',
    nodeId: boundNode,
    title: t('database.createDBTitle'),
    titleKey: 'database.createDBTitle',
    icon: markRaw(Database),
    component: markRaw(DatabaseCreateWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 90 + (openWindows.value.length * 25),
    width: 380,
    height: 180,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 网页防篡改：点击「添加/编辑」打开独立的防护表单窗口（含长文本域，误触不丢内容）
function openTamperForm(payload) {
  const id = ++windowSeq
  const isEdit = !!payload?.task
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'tamper-form',
    nodeId: boundNode,
    title: isEdit ? t('tamper.formTitleEdit', { name: payload?.task?.site_name || '' }) : t('tamper.formTitle'),
    titleKey: null,
    icon: markRaw(ShieldAlert),
    component: markRaw(TamperFormWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 520,
    height: 640,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 网站：右键「维护模式」打开的独立表单窗口（开关 + 自定义维护页 HTML）
function openSiteMaintenance(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'site-maintenance',
    nodeId: boundNode,
    title: t('sites.maintTitle', { name: payload?.site?.name || '' }),
    titleKey: null,
    icon: markRaw(Wrench),
    component: markRaw(SiteMaintenanceWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 420,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 应用商店：点击「索引地址配置」打开的独立表单窗口
function openAppStoreConfig(payload) {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'appstore-config',
    nodeId: boundNode,
    title: t('appstore.indexConfigTitle'),
    titleKey: 'appstore.indexConfigTitle',
    icon: markRaw(Settings2),
    component: markRaw(AppStoreConfigWindow),
    props: payload ? { ...payload } : {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 460,
    height: 220,
    z: ++zSeq,
    minimized: false,
    maximized: false,
    prev: null
  })
  openWindows.value.push(w)
  activeWindowId.value = id
}

// 端口转发：点击「新建」打开的独立表单窗口（选择节点 + 端口配置，误触不丢内容）
function openPortForwardForm() {
  const id = ++windowSeq
  const boundNode = unifiedPanelOn.value ? nodesStore.currentId : ''
  const w = reactive({
    id,
    key: 'portforward-form',
    nodeId: boundNode,
    title: t('pf.create'),
    titleKey: 'pf.create',
    icon: markRaw(Unlink),
    component: markRaw(PortForwardFormWindow),
    props: {},
    x: 180 + (openWindows.value.length * 30),
    y: 80 + (openWindows.value.length * 25),
    width: 420,
    height: 480,
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
    props: { initialPath: path },   // 与 FilesWindow 的 initialPath prop 对应
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

// 连接池启动/停止：登录后统一建立共享指标 WS（系统概览）与 Docker 实时推送 WS；
// 退出登录时全部停止，避免未登录时持续请求。
function startRealtime() {
  startMetrics()
  // 网页防篡改告警订阅：登录即建立（篡改发生时对在线用户弹窗）
  startTamper()
  // Docker 为模块授权功能：仅在持有 docker 模块权限时订阅实时推送（/api/docker/ws），
  // 否则受限管理员会持续收到 403 并无谓重连。
  if (hasPerm('docker')) startDocker()
  // 节点列表仅完整管理员可见（多节点管理属面板自身安全边界）
  if (isFullAdmin()) refreshNodes()
}
function stopRealtime() {
  stopMetrics()
  stopTamper()
  stopDocker()
}

// --- 类桌面模式「空闲预加载」（设置项 settings.desktopPreload 控制） ---
// 目的：窗口按需加载后，首次打开某应用需现下载其 chunk（慢网下会「点开空白一下」）。
// 桌面模式用户会频繁穿梭各应用，因此在首屏与实时数据就绪后，分批预取全部窗口代码，
// 之后打开任意应用都命中本地缓存、瞬时渲染。
// 约束：
//   - 仅类桌面模式生效——面板模式一次只挂载一个窗口，预取全部属浪费；
//   - 延迟 PRELOAD_DELAY 启动，先让首屏渲染与指标/Docker WS 首帧跑完，不抢关键带宽；
//   - preloadWindows() 自身幂等，重复触发只会复用同一任务。
const PRELOAD_DELAY = 1500   // 进入面板后延迟多久开始预取（毫秒）
let preloadTimer = null      // 待触发的延时器：避免多次触发排队

function maybePreloadWindows() {
  if (preloadTimer) return                    // 已有待触发任务：无需重复排队
  if (!loggedIn.value) return                 // 未登录：不预取（应用均在登录后才可用）
  if (settings.panelMode) return              // 面板模式：不参与预加载
  if (!settings.desktopPreload) return        // 用户已关闭预加载
  preloadTimer = setTimeout(() => {
    preloadTimer = null
    // 二次校验：延迟期间用户可能已退出登录 / 切到面板模式 / 关闭开关
    if (!loggedIn.value || settings.panelMode || !settings.desktopPreload) return
    preloadWindows()
  }, PRELOAD_DELAY)
}

// 开关或界面形态变化时按需补触发（已预取过则由 preloadWindows 幂等短路）
watch(() => [settings.panelMode, settings.desktopPreload], () => maybePreloadWindows())

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
    // 已登录态（如页面刷新）也重新检测安装环境，确保缺失时弹窗提醒
    checkInstallCheck()
    // 加载当前账号生效的动态壁纸 / 环形图（「仅用于这个账号」优先）
    loadUiEffective().catch(() => {})
    // 类桌面模式：首屏与实时数据就绪后，空闲预取全部应用窗口代码（见 maybePreloadWindows）
    maybePreloadWindows()
  }
  updateClock()
  clockTimer = setInterval(updateClock, 1000)
  document.addEventListener('mousedown', onDocClick)
  // 全局快捷搜索：Ctrl/Cmd+K 唤出（浏览器保留常用 Ctrl+K 聚焦地址栏，
  // 通常在输入框内不拦截，这里在面板顶层监听一次即可）
  document.addEventListener('keydown', onPaletteGlobalKey)
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
    loadUiEffective().catch(() => {})
    // 登录成功（含切号后重新登录）：同样在数据就绪后启动空闲预加载
    maybePreloadWindows()
  } else {
    stopRealtime()
  }
})

onUnmounted(() => {
  stopRealtime()
  stopCarousel()
  clearInterval(clockTimer)
  clearTimeout(preloadTimer)   // 卸载时取消待触发的空闲预加载（已启动的预取不受影响）
  document.removeEventListener('mousedown', onDocClick)
  document.removeEventListener('keydown', onPaletteGlobalKey)
})
</script>
