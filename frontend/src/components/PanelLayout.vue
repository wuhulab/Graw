<!--
  PanelLayout.vue — 标准面板前端模式外壳（1Panel 风格侧边栏布局）

  作用：替代工具栏式「类桌面」界面。布局 = 顶栏 + 左侧分组菜单 + 可选标签栏 + 右侧内容区。
        内容区一次只挂载「激活窗口」的组件：多标签模式下用 KeepAlive 缓存失活窗口的组件
        实例（DOM 卸载、状态/WS 连接保留，切回即恢复）；单页切换模式下直接销毁重建。
  数据：windows（App.vue openWindows 响应式数组）、activeId（激活窗口 id）、
        menuShortcuts（当前用户可见的桌面快捷方式，用于分组菜单）、hostName（主机徽标）。
  事件（emits）：open(key)   —— 点侧边栏菜单 / 用户菜单，App 侧复用 openWindow 的门控逻辑
                 focus(id)   —— 点标签，App 侧复用 focusWindow（同步 activeWindowId + 请求节点）
                 close(id)   —— 关闭标签/内容（App 侧走 handleCloseWindow，含 dirty 确认）
                 dirty/attrs —— 其余约 50 个 openXxx 事件经 $attrs 透传给 WindowContent
  （注：本组件不声明 openXxx，inheritAttrs:false 后统一由内容区 WindowContent 显式接收）
-->
<template>
  <div class="pnl-root">
    <!-- 顶栏：汉堡 + 当前标题 + 主机徽标 + 用户菜单 -->
    <header class="pnl-topbar">
      <button class="pnl-iconbtn" :title="$t('panel.toggleSidebar')" @click="toggleSidebar">
        <Menu :size="18" />
      </button>
      <div class="pnl-topbar-title" title="Graw">Graw</div>
      <div class="pnl-topbar-right">
        <span v-if="hostName && isAdmin()" class="pnl-host" :class="{ remote: hostRemote }" :title="hostName">
          <span class="pnl-host-dot"></span>{{ hostName }}
        </span>
        <div class="pnl-usermenu" @click.stop>
          <button class="pnl-iconbtn" :title="userName" @click="userMenuOpen = !userMenuOpen">
            <UserCircle2 :size="20" />
          </button>
          <Transition name="pnl-drop">
            <div v-if="userMenuOpen" class="pnl-dropdown">
              <div class="pnl-drop-head">
                <div class="pnl-drop-user">{{ userName }}</div>
                <div class="pnl-drop-role">{{ userRole }}</div>
              </div>
              <button v-if="isAdmin()" class="pnl-drop-item" @click="menuAction('users')">
                <UserCircle2 :size="14" /> {{ $t('app.accountManage') }}
              </button>
              <button class="pnl-drop-item" @click="menuAction('changepwd')">
                <KeyRound :size="14" /> {{ $t('app.changePassword') }}
              </button>
              <button class="pnl-drop-item" @click="menuAction('settings')">
                <Settings :size="14" /> {{ $t('app.settings') }}
              </button>
              <button v-if="isAdmin()" class="pnl-drop-item" @click="menuAction('uisettings')">
                <Palette :size="14" /> {{ $t('app.shortcut.uisettings') }}
              </button>
              <button class="pnl-drop-item danger" @click="onLogout">
                <LogOut :size="14" /> {{ $t('app.logout') }}
              </button>
            </div>
          </Transition>
        </div>
      </div>
    </header>

    <div class="pnl-body">
      <!-- 侧边栏（宽屏） / 抽屉（移动端） -->
      <Transition name="pnl-slide">
        <aside v-show="sidebarVisible" class="pnl-sidebar" :class="[{ 'pnl-hide-text': collapsed && !isMobile }, { 'pnl-drawer': isMobile }]">
          <!-- 品牌区仅保留顶部栏（汉堡前的 Graw 标识），这里不再重复 Logo -->

          <!-- 快速搜索：按名称/标题实时过滤菜单项 -->
          <div v-if="!(collapsed && !isMobile)" class="pnl-search">
            <Search :size="14" class="pnl-search-icon" />
            <input v-model.trim="search" :placeholder="$t('panel.searchPlaceholder')" spellcheck="false" />
            <button v-if="search" class="pnl-search-clear" @click="search = ''"><X :size="12" /></button>
          </div>

          <!-- 主页固定入口：系统概览 + 实时监控 + 系统信息/备忘录 -->
          <button
            class="pnl-item pnl-home-entry"
            :class="{ active: currentMenuKey === 'panelhome' }"
            :title="$t('panel.home')"
            @click="menuAction('panelhome')"
          >
            <span class="pnl-item-icon"><Home :size="16" /></span>
            <span v-if="!(collapsed && !isMobile)" class="pnl-item-label">{{ $t('panel.home') }}</span>
          </button>

          <!-- 分组菜单 -->
          <nav class="pnl-menu">
            <div v-for="g in groups" :key="g.key" class="pnl-group" :class="{ active: expandedSet.has(g.key) || searching }">
              <button class="pnl-group-head" :title="$t(g.titleKey)" @click.stop="toggleGroup(g.key)">
                <span class="pnl-group-title">{{ $t(g.titleKey) }}</span>
                <ChevronDown :size="14" class="pnl-chev" :class="{ open: expandedSet.has(g.key) || searching }" />
              </button>
              <div v-show="expandedSet.has(g.key) || searching" class="pnl-group-items">
                <button
                  v-for="it in g.items"
                  :key="it.key"
                  class="pnl-item"
                  :class="{ active: it.key === currentMenuKey }"
                  :title="itemName(it)"
                  @click="menuAction(it.key)"
                >
                  <span class="pnl-item-icon"><component :is="it.icon" :size="16" /></span>
                  <span v-if="!(collapsed && !isMobile)" class="pnl-item-label">{{ itemName(it) }}</span>
                </button>
              </div>
            </div>
          </nav>
        </aside>
      </Transition>

      <!-- 移动端抽屉遮罩 -->
      <Transition name="pnl-fade">
        <div v-if="isMobile && drawerOpen" class="pnl-mask" @click="drawerOpen = false"></div>
      </Transition>

      <!-- 主内容：标签栏 + 内容区 -->
      <main class="pnl-main">
        <!-- 标签栏（多标签模式） -->
        <div v-if="showTabs && windows.length" class="pnl-tabs">
          <div
            v-for="w in windows"
            :key="w.id"
            class="pnl-tab"
            :class="{ active: w.id === activeId }"
            :title="tabTitle(w)"
            @click="emit('focus', w.id)"
          >
            <span class="pnl-tab-icon"><component :is="w.icon" :size="14" /></span>
            <span class="pnl-tab-title">{{ tabTitle(w) }}</span>
            <button class="pnl-tab-close" :title="$t('panel.closeTab')" @click.stop="emit('close', w.id)"><X :size="12" /></button>
          </div>
        </div>

        <!-- 内容区：仅挂载激活窗口 -->
        <div class="pnl-content">
          <template v-if="activeWindow">
            <!-- 多标签模式：KeepAlive 缓存失活窗口实例（DOM 卸载、状态保留），限制缓存数量防膨胀 -->
            <KeepAlive v-if="showTabs" :max="20">
              <WindowContent
                :key="'w' + activeWindow.id"
                :window="activeWindow"
                v-bind="$attrs"
                @close="onContentClose"
                @dirty="onContentDirty"
              />
            </KeepAlive>
            <!-- 单页切换模式：直接渲染（切换即销毁重建，不缓存） -->
            <WindowContent
              v-else
              :key="'s' + activeWindow.id"
              :window="activeWindow"
              v-bind="$attrs"
              @close="onContentClose"
              @dirty="onContentDirty"
            />
          </template>
          <div v-else class="pnl-empty">
            <LayoutGrid :size="40" class="pnl-empty-icon" />
            <div class="pnl-empty-text">{{ $t('panel.emptyHint') }}</div>
            <div class="pnl-empty-sub">{{ $t('panel.emptySub') }}</div>
          </div>
        </div>
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, markRaw, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Menu, X, Search, LogOut, Settings, UserCircle2, KeyRound, Palette,
  ChevronDown, LayoutGrid, Home,
} from 'lucide-vue-next'
import WindowContent from './WindowContent.vue'
import { isAdmin } from '../store/auth'

// 主窗口面板模式：菜单分组定义（组标题 i18n key + 成员 key 清单）。
// 成员 key 与 App.vue shortcuts/extras（openWindow）保持一致，缺失的项自动跳过。
const MENU_GROUPS = [
  { key: 'monitor', titleKey: 'panel.menu.monitor', items: ['monitoring', 'metricshistory', 'webstats'] },
  { key: 'site', titleKey: 'panel.menu.site', items: ['sites', 'rewrite', 'siteopts', 'certcheck', 'gitdeploy'] },
  { key: 'database', titleKey: 'panel.menu.database', items: ['database'] },
  { key: 'container', titleKey: 'panel.menu.container', items: ['docker', 'runtime', 'process', 'imgsafety', 'slowquery'] },
  { key: 'file', titleKey: 'panel.menu.file', items: ['files', 'recycle', 'netstorage', 'ftpusers'] },
  { key: 'network', titleKey: 'panel.menu.network', items: ['frp', 'portforward'] },
  { key: 'security', titleKey: 'panel.menu.security', items: ['shunxprotection', 'sessions'] },
  { key: 'task', titleKey: 'panel.menu.task', items: ['tasks', 'batch', 'report'] },
  { key: 'store', titleKey: 'panel.menu.store', items: ['appstore'] },
  { key: 'system', titleKey: 'panel.menu.system', items: ['logs', 'disks', 'phpversions', 'terminal', 'foxcode', 'rollback', 'uisettings', 'settings', 'users', 'changepwd'] },
]

// 不在桌面 shortcuts 里的内部门栏入口（对应 App.vue openWindow 的 extras key）
const EXTRA_DEFS = {
  settings: { key: 'settings', label: '设置', titleKey: 'app.winTitle.settings', icon: markRaw(Settings) },
  users: { key: 'users', label: '账号管理', titleKey: 'app.winTitle.users', icon: markRaw(UserCircle2), adminOnly: true },
  changepwd: { key: 'changepwd', label: '修改密码', titleKey: 'app.winTitle.changepwd', icon: markRaw(UserCircle2) },
}

const props = defineProps({
  windows: { type: Array, default: () => [] },            // 已打开窗口列表（App.vue openWindows）
  activeId: { type: [Number, String], default: null },        // 激活窗口 id（无窗口时为 null）
  user: { type: Object, default: null },                  // 当前登录用户（auth.user）
  menuShortcuts: { type: Array, default: () => [] },      // 当前用户可见的桌面快捷方式清单
  hostName: { type: String, default: '' },                // 当前管理主机名称（空则不显示徽标）
  hostRemote: { type: Boolean, default: false },          // 当前主机是否为远程（SSH）节点
  showTabs: { type: Boolean, default: true },             // 多标签缓存模式（false = 单页切换）
})
const emit = defineEmits(['open', 'focus', 'close', 'dirty', 'logout'])
// 不把 attrs 落到根元素：openXxx 事件全部由内容区 WindowContent 显式接收后再转给具体窗口组件
defineOptions({ inheritAttrs: false })

const { t } = useI18n()

// ---- 顶栏/用户信息 ----
const userName = computed(() => props.user?.username || 'admin')
const userRole = computed(() => {
  const isAdm = isAdmin()
  return isAdm ? t('app.admin') : t('app.normalUser')
})
const userMenuOpen = ref(false)

// 当前激活窗口（内容区渲染对象）；顶栏标题始终固定为品牌名 Graw，不随窗口变化
const activeWindow = computed(() => props.windows.find((x) => x.id === props.activeId) || null)

// 标签标题
function tabTitle(w) {
  if (!w) return ''
  if (w.titleKey) return t(w.titleKey, w.titleArgs)
  return w.title || w.key || ''
}

// 关闭内容区窗口：冒泡给 App 的 handleCloseWindow（含 editor dirty 确认），
// 并把关闭目标窗口的 id 一并带出
function onContentClose() {
  if (!activeWindow.value) return
  emit('close', activeWindow.value.id)
}

// 内容区窗口 dirty 标记：冒泡给 App，按 id 更新对应窗口（编辑器未保存提示用）
function onContentDirty(value) {
  if (!activeWindow.value) return
  emit('dirty', { id: activeWindow.value.id, value })
}

// ---- 侧边栏（分组菜单 + 搜索 + 折叠/抽屉） ----
// UI 折叠状态本地持久化：侧边栏折叠（汉堡）与分组展开/收起的记录存 localStorage，
// 刷新/重进面板模式后保持用户上次的折叠习惯。
const PNL_UI_KEY = 'graw_panel_ui'   // 本地存储键：{ collapsed, expanded: [...] }
function loadPanelUi() {
  try {
    const raw = localStorage.getItem(PNL_UI_KEY)
    return raw ? JSON.parse(raw) : null
  } catch (e) {
    // 存储损坏/不可用时回退默认（全部展开、侧边栏不折叠）
    return null
  }
}
const savedPanelUi = loadPanelUi()

const collapsed = ref(!!(savedPanelUi && savedPanelUi.collapsed))  // 宽屏折叠为窄图标条
const drawerOpen = ref(false)                                       // 移动端抽屉展开
const isMobile = ref(false)                                         // 是否进入移动端布局（<768px）
const search = ref('')                                              // 菜单过滤关键词
const searching = computed(() => search.value.trim().length > 0)

// 组展开状态：优先恢复上次记忆，首次使用默认全展开
const expandedSet = reactive(
  new Set(
    savedPanelUi && Array.isArray(savedPanelUi.expanded) && savedPanelUi.expanded.length
      ? savedPanelUi.expanded
      : MENU_GROUPS.map((g) => g.key)
  )
)

// 折叠/组展开变化即落盘（deep 监听覆盖 Set 的增删）
watch(
  () => ({ collapsed: collapsed.value, expanded: Array.from(expandedSet) }),
  (v) => {
    try {
      localStorage.setItem(PNL_UI_KEY, JSON.stringify(v))
    } catch (e) {
      // 存储不可用（如隐私模式）时忽略，仅影响刷新后的 UI 记忆
    }
  },
  { deep: true }
)

// 移动端判定：窗口宽度 < 768 视为移动端（抽屉模式）
function updateViewport() {
  const wasMobile = isMobile.value
  isMobile.value = window.innerWidth < 768
  if (!wasMobile && isMobile.value) drawerOpen.value = false // 进入移动端时默认收起抽屉
  if (wasMobile && !isMobile.value) drawerOpen.value = false
}
onMounted(() => {
  updateViewport()
  window.addEventListener('resize', updateViewport)
})
onUnmounted(() => window.removeEventListener('resize', updateViewport))

// 汉堡按钮：宽屏切换折叠；移动端切换抽屉
function toggleSidebar() {
  if (isMobile.value) {
    drawerOpen.value = !drawerOpen.value
  } else {
    collapsed.value = !collapsed.value
  }
}

// 侧边栏可见性：移动端由抽屉控制，宽屏由折叠控制（折叠后仍显示窄图标条）
const sidebarVisible = computed(() => (isMobile.value ? drawerOpen.value : true))

// 菜单项解析：优先使用可见快捷方式（含图标/门控），否则回退内部门栏入口
function menuItem(key) {
  return props.menuShortcuts.find((s) => s.key === key) || EXTRA_DEFS[key]
}

// 显示名：titleKey 走 i18n，缺省回退 label/key
function itemName(it) {
  if (!it) return ''
  return it.titleKey ? t(it.titleKey) : (it.label || it.key)
}

// 组折叠状态：展开/收起由 expandedSet 控制（见上方持久化部分），此处仅提供切换动作
function toggleGroup(key) {
  if (expandedSet.has(key)) expandedSet.delete(key)
  else expandedSet.add(key)
}

// 分组渲染：过滤掉无可见项的组；搜索时按名称/标题过滤成员并自动展开
const groups = computed(() => {
  const q = search.value.trim().toLowerCase()
  return MENU_GROUPS
    .map((g) => {
      let items = g.items.map(menuItem).filter(Boolean).filter((it) => !it.adminOnly || isAdmin())
      if (q) items = items.filter((it) => itemName(it).toLowerCase().includes(q))
      return { ...g, items }
    })
    .filter((g) => g.items.length)
})

// 当前菜单高亮：激活窗口对应 key 的菜单项；无窗口时不高亮
const currentMenuKey = computed(() => (activeWindow.value ? activeWindow.value.key : ''))

// 菜单动作：统一冒泡给 App（App 侧 openWindow 内含 adminOnly/remoteCap/VIP 守卫），
// 移动端点选后自动收起抽屉；点用户菜单项时关闭下拉
function menuAction(key) {
  userMenuOpen.value = false
  if (isMobile.value) drawerOpen.value = false
  emit('open', key)
}

// 退出登录：冒泡给 App 执行 doLogout
function onLogout() {
  userMenuOpen.value = false
  emit('logout')
}

// 点击侧边栏/页面任意处关闭用户下拉菜单
function onDocClick(e) {
  if (userMenuOpen.value && !e.target.closest('.pnl-usermenu')) userMenuOpen.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocClick))
onUnmounted(() => document.removeEventListener('mousedown', onDocClick))

// 搜索变化时自动展开全部组（保证命中项可见）
watch(searching, (v) => {
  if (v) MENU_GROUPS.forEach((g) => expandedSet.add(g.key))
})
</script>

<style scoped>
/* ---- 根布局：全屏 flex 纵向（顶栏 + 主体） ---- */
.pnl-root {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100%;
  background: #f5f6f8;
  overflow: hidden;
}

/* ---- 顶栏 ---- */
.pnl-topbar {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 48px;
  padding: 0 12px;
  background: #fff;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
  flex-shrink: 0;
  z-index: 20;
}
.pnl-iconbtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  color: #1d1d1f;
  border-radius: 8px;
  cursor: pointer;
}
.pnl-iconbtn:hover { background: rgba(0, 0, 0, 0.05); }
.pnl-topbar-title {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  font-weight: 600;
  color: #1d1d1f;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pnl-topbar-right { display: flex; align-items: center; gap: 10px; }
.pnl-host {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 180px;
  padding: 3px 10px;
  font-size: 11px;
  color: #0a84ff;
  background: rgba(10, 132, 255, 0.08);
  border: 1px solid rgba(10, 132, 255, 0.25);
  border-radius: 999px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pnl-host.remote { color: #7a3ce8; background: rgba(122, 60, 232, 0.08); border-color: rgba(122, 60, 232, 0.25); }
.pnl-host-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #0a84ff;
  flex-shrink: 0;
}
.pnl-host.remote .pnl-host-dot { background: #7a3ce8; }

/* ---- 用户下拉菜单 ---- */
.pnl-usermenu { position: relative; }
.pnl-dropdown {
  position: absolute;
  right: 0;
  top: 40px;
  min-width: 180px;
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 6px;
  z-index: 100;
}
.pnl-drop-head {
  padding: 8px 10px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  margin-bottom: 4px;
}
.pnl-drop-user { font-size: 13px; font-weight: 600; color: #1d1d1f; }
.pnl-drop-role { font-size: 11px; color: #8e8e93; }
.pnl-drop-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  background: transparent;
  font-size: 12px;
  color: #1d1d1f;
  border-radius: 7px;
  cursor: pointer;
  text-align: left;
}
.pnl-drop-item:hover { background: rgba(10, 132, 255, 0.08); color: #0a84ff; }
.pnl-drop-item.danger:hover { background: rgba(229, 72, 77, 0.08); color: #e5484d; }
.pnl-drop-enter-active, .pnl-drop-leave-active { transition: opacity 0.15s, transform 0.15s; }
.pnl-drop-enter-from, .pnl-drop-leave-to { opacity: 0; transform: translateY(-4px); }

/* ---- 主体：侧边栏 + 内容 ---- */
.pnl-body {
  display: flex;
  flex: 1;
  min-height: 0;
  position: relative;
}

/* ---- 侧边栏 ---- */
.pnl-sidebar {
  display: flex;
  flex-direction: column;
  width: 240px;
  background: #fafbfc;
  border-right: 1px solid rgba(0, 0, 0, 0.07);
  flex-shrink: 0;
  transition: width 0.2s ease, transform 0.25s ease;
  overflow-x: hidden;
  position: relative;
  z-index: 30;
}
.pnl-sidebar.pnl-hide-text { width: 64px; }

/* 搜索框 */
.pnl-search {
  position: relative;
  margin: 12px 12px 8px;
  flex-shrink: 0;
}
.pnl-search input {
  width: 100%;
  padding: 7px 26px 7px 30px;
  font-size: 12px;
  font-family: inherit;
  color: #1d1d1f;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 8px;
  outline: none;
  background: #fff;
  box-sizing: border-box;
}
.pnl-search input:focus { border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10, 132, 255, 0.15); }
.pnl-search-icon {
  position: absolute;
  left: 9px;
  top: 50%;
  transform: translateY(-50%);
  color: #8e8e93;
  pointer-events: none;
}
.pnl-search-clear {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  background: rgba(0, 0, 0, 0.06);
  color: #6e6e73;
  border-radius: 50%;
  cursor: pointer;
}
.pnl-search-clear:hover { background: rgba(0, 0, 0, 0.12); }

/* 菜单 */
.pnl-menu {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 8px 12px;
}
.pnl-group { margin-bottom: 2px; }
.pnl-group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  background: transparent;
  font-size: 11px;
  font-weight: 600;
  color: #8e8e93;
  border-radius: 7px;
  cursor: pointer;
  text-align: left;
}
.pnl-group-head:hover { background: rgba(0, 0, 0, 0.04); color: #1d1d1f; }
.pnl-group-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pnl-chev { transition: transform 0.2s; flex-shrink: 0; }
.pnl-chev.open { transform: rotate(180deg); }
.pnl-group-items { padding: 2px 0; }
.pnl-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  background: transparent;
  font-size: 13px;
  color: #3a3a3c;
  border-radius: 8px;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
}
.pnl-item:hover { background: rgba(10, 132, 255, 0.08); color: #0a84ff; }
.pnl-item.active {
  background: #0a84ff;
  color: #fff;
  font-weight: 500;
}
.pnl-item-icon { display: inline-flex; flex-shrink: 0; }
.pnl-item-label { overflow: hidden; text-overflow: ellipsis; }

/* 主页固定入口：与分组菜单之间留出分隔 */
.pnl-home-entry {
  margin: 2px 8px 8px;
  width: calc(100% - 16px);
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  border-radius: 8px;
}

/* 移动端抽屉 */
.pnl-sidebar.pnl-drawer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  height: 100%;
  box-shadow: 8px 0 24px rgba(0, 0, 0, 0.12);
  z-index: 40;
}
.pnl-mask {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 35;
}
.pnl-slide-enter-active, .pnl-slide-leave-active { transition: transform 0.25s ease; }
.pnl-slide-enter-from, .pnl-slide-leave-to { transform: translateX(-100%); }
.pnl-fade-enter-active, .pnl-fade-leave-active { transition: opacity 0.2s; }
.pnl-fade-enter-from, .pnl-fade-leave-to { opacity: 0; }

/* ---- 主内容 ---- */
.pnl-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #f5f6f8;
}

/* 标签栏 */
.pnl-tabs {
  display: flex;
  align-items: stretch;
  gap: 2px;
  padding: 6px 10px 0;
  background: #f5f6f8;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  overflow-x: auto;
  overflow-y: hidden;
  flex-shrink: 0;
  scrollbar-width: thin;
}
.pnl-tab {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  max-width: 180px;
  padding: 7px 6px 7px 12px;
  background: transparent;
  color: #6e6e73;
  border-radius: 9px 9px 0 0;
  border: 1px solid transparent;
  cursor: pointer;
  font-size: 12px;
  white-space: nowrap;
  flex-shrink: 0;
}
.pnl-tab:hover { background: rgba(0, 0, 0, 0.04); }
.pnl-tab.active {
  background: #fff;
  color: #0a84ff;
  border-color: rgba(0, 0, 0, 0.06);
  border-bottom-color: transparent;
  font-weight: 500;
}
.pnl-tab-icon { display: inline-flex; flex-shrink: 0; }
.pnl-tab-title { overflow: hidden; text-overflow: ellipsis; }
.pnl-tab-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: none;
  background: transparent;
  color: inherit;
  border-radius: 50%;
  cursor: pointer;
  flex-shrink: 0;
}
.pnl-tab-close:hover { background: rgba(0, 0, 0, 0.1); color: #e5484d; }

/* 内容区 */
.pnl-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
}
.pnl-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  background: #f5f6f8;
}
.pnl-empty-icon { color: #c7c7cc; }
.pnl-empty-text { font-size: 14px; font-weight: 600; color: #6e6e73; }
.pnl-empty-sub { font-size: 12px; color: #aeaeb2; }

/* ---- 窄屏（移动端）---- */
@media (max-width: 767px) {
  .pnl-sidebar { width: 220px; }
  .pnl-host { display: none; }
}
</style>