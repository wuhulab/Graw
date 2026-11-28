<!--
  ToolDock.vue — 右侧「工具」书签 + 可移动 / 可收回的工具浮窗

  业务背景：
    面板需要一个随时可用、不占桌面窗口位置的「工具箱」。点右侧书签弹出浮窗，
    按节点创建标签：
      - 终端：后端常驻 shell（persist + sid），切标签只断开 WebSocket、进程继续跑；
      - 文件管理：轻量文件浏览（列表 / 进入目录 / 上传 / 下载），固定绑定所选节点。
    标签直到手动关闭才移除（文件标签没有后端进程，关闭不需要确认）。

  关键状态：
    open        浮窗是否展开（收起后只留右侧小书签）
    tabs        标签列表 [{ id, kind('terminal'|'files'), node, title, path? }]，
                持久化到 localStorage（按用户名隔离）
    activeId    当前激活标签；只挂载激活标签的内容（终端其余连接断开但进程保留）
    newToolKind 「+」菜单当前选中的工具类型（终端 / 文件管理）
    pos         浮窗左上角坐标；bookY 右侧书签纵向位置（都持久化）

  数据来源：
    nodes store  节点列表（本机 + SSH 子节点），用于「+」里的节点选择；
    /api/terminal/ws     持久终端 WebSocket（persist=1 + sid + node，见 ToolTerminalPane）；
    /api/terminal/persist DELETE 关闭某个终端标签对应的常驻 shell 进程；
    /api/files/*         文件管理（显式 X-Graw-Node 头固定目标节点，见 ToolFilesPane）。

  打开方式：登录后由 App.vue 常驻渲染，所有界面形态（桌面 / 标准面板）共用。
-->
<template>
  <div v-if="dockReady" class="td-root">
    <!-- 收起态：贴在右侧边缘的小书签（可上下拖动，位置记在浏览器本地） -->
    <button v-show="!open" ref="bookEl" class="td-bookmark" :class="{ dragging: bookDragging }"
      :style="{ top: bookY + 'px' }" :title="$t('toolDock.open')"
      @mousedown="startBookDrag" @click="onBookmarkClick">
      <Wrench :size="15" />
      <span class="td-bookmark-label">{{ $t('toolDock.title') }}</span>
    </button>

    <!-- 展开态：可拖动的浮窗 -->
    <div v-if="open" ref="panelEl" class="td-panel" :style="{ left: pos.x + 'px', top: pos.y + 'px' }">
      <header class="td-head" :class="{ dragging }" @mousedown="startDrag">
        <Wrench :size="14" />
        <span class="td-head-title">{{ $t('toolDock.title') }}</span>
        <span class="td-head-sub">{{ $t('toolDock.tabCount', { n: tabs.length }) }}</span>
        <div class="td-head-actions" @mousedown.stop>
          <div class="td-node-wrap">
            <button class="td-iconbtn" :title="$t('toolDock.newTool')" @click="toggleNodeMenu">
              <Plus :size="16" />
            </button>
            <!-- 先选工具类型（终端 / 文件管理），再选节点：点节点即创建对应标签 -->
            <div v-if="nodeMenuOpen" class="td-node-menu" @mousedown.stop>
              <div class="td-kind-row">
                <button v-if="canUseTerminal" class="td-kind" :class="{ active: newToolKind === 'terminal' }"
                  @click="newToolKind = 'terminal'">
                  <TerminalSquare :size="13" />{{ $t('toolDock.toolTerminal') }}
                </button>
                <button v-if="canUseFiles" class="td-kind" :class="{ active: newToolKind === 'files' }"
                  @click="newToolKind = 'files'">
                  <Folder :size="13" />{{ $t('toolDock.toolFiles') }}
                </button>
              </div>
              <div class="td-node-menu-head">{{ $t('toolDock.chooseNode') }}</div>
              <button v-for="n in nodeOptions" :key="n.id" class="td-node-item" @click="createTab(n, newToolKind)">
                <Server :size="13" />
                <span class="td-node-name">{{ n.type === 'local' ? $t('toolDock.local') : (n.name || n.id) }}</span>
                <span v-if="n.type !== 'local'" class="td-node-tag">SSH</span>
              </button>
            </div>
          </div>
          <button class="td-iconbtn" :title="$t('toolDock.collapse')" @click="collapse">
            <Minus :size="16" />
          </button>
        </div>
      </header>

      <!-- 标签栏：图标区分终端 / 文件管理（终端图标同时用颜色表示连接状态） -->
      <div v-if="tabs.length" class="td-tabs">
        <div v-for="tab in tabs" :key="tab.id" class="td-tab" :class="{ active: tab.id === activeId }"
          @click="activeId = tab.id">
          <Folder v-if="tab.kind === 'files'" :size="13" class="td-tab-icon files" />
          <TerminalSquare v-else :size="13" class="td-tab-icon" :class="statusOf(tab.id)" />
          <span class="td-tab-name" :title="tab.kind === 'files' ? tab.title : statusLabel(tab.id)">{{ tab.title }}</span>
          <button class="td-tab-close" :title="$t('toolDock.closeTab')" @click.stop="closeTab(tab.id)">
            <X :size="11" />
          </button>
        </div>
      </div>

      <div class="td-body">
        <!-- 只挂载激活标签：终端切走即断开连接（后端进程与回放缓冲不受影响）；
             文件管理切走会重新挂载，目录路径存在标签里，切回自动恢复 -->
        <ToolTerminalPane v-if="activeTab && activeTab.kind !== 'files'" :key="activeTab.id"
          :sid="activeTab.id" :node-id="activeTab.node"
          @status="(s) => statusMap[activeTab.id] = s" />
        <ToolFilesPane v-else-if="activeTab" :key="activeTab.id"
          :node-id="activeTab.node" :initial-path="activeTab.path || ''"
          @path-change="(p) => { if (activeTab) activeTab.path = p }" />
        <div v-else class="td-empty">
          <Wrench :size="30" />
          <div>{{ $t('toolDock.empty') }}</div>
          <button class="td-empty-btn" @click="toggleNodeMenu">
            <Plus :size="14" />{{ $t('toolDock.newTool') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'                       // 节点名 / 标签标题跟随语言
import { Wrench, Plus, Minus, X, Server, TerminalSquare, Folder } from 'lucide-vue-next'
import ToolTerminalPane from './ToolTerminalPane.vue'
import ToolFilesPane from './ToolFilesPane.vue'
import api from '../api'                                 // DELETE /terminal/persist 结束常驻会话
import { auth, hasPerm, isFullAdmin } from '../store/auth'
import { nodes, refreshNodes } from '../store/nodes'

const { t } = useI18n()

// 工具入口需要的模块权限：终端看 terminal，文件管理看 files（后端同样会校验）
const canUseTerminal = computed(() => hasPerm('terminal'))
const canUseFiles = computed(() => hasPerm('files'))
const dockReady = computed(() => canUseTerminal.value || canUseFiles.value)

// 存储键按用户名隔离：同一浏览器换账号登录不会看到别人的终端标签
const userKey = auth.user?.username || 'anon'
const TABS_KEY = `graw.toolDock.tabs.${userKey}`
const POS_KEY = `graw.toolDock.pos.${userKey}`
const BOOK_KEY = `graw.toolDock.bookmarkY.${userKey}`

// --- 标签状态：从 localStorage 恢复（终端只恢复描述，连接在浮窗打开时重建；
//     文件管理恢复上次目录，切回标签时自动回到该目录） ---
function loadTabs() {
  try {
    const raw = JSON.parse(localStorage.getItem(TABS_KEY) || '{}')
    const list = Array.isArray(raw.tabs) ? raw.tabs : []
    return list
      .filter((x) => x && typeof x.id === 'string' && typeof x.node === 'string')
      .map((x) => {
        const kind = x.kind === 'files' ? 'files' : 'terminal'   // 老数据没有 kind：按终端处理
        const tab = { id: x.id, kind, node: x.node, title: String(x.title || '') }
        if (kind === 'files' && typeof x.path === 'string') tab.path = x.path
        return tab
      })
      .slice(0, 30)   // 防御异常数据：最多恢复 30 个标签
  } catch (e) {
    return []
  }
}
const saved = loadTabs()
const tabs = ref(saved)
const activeId = ref(
  saved.some((x) => x.id === localStorage.getItem(`${TABS_KEY}.active`))
    ? localStorage.getItem(`${TABS_KEY}.active`)
    : (saved[0]?.id || ''),
)
const statusMap = reactive({})   // 终端 id -> 'connecting' | 'connected' | 'reconnecting' | 'idle'

watch(tabs, () => {
  try {
    localStorage.setItem(TABS_KEY, JSON.stringify({ tabs: tabs.value }))
    localStorage.setItem(`${TABS_KEY}.active`, activeId.value || '')
  } catch (e) { /* 隐私模式等写入失败：仅本次生效 */ }
}, { deep: true })
watch(activeId, (v, old) => {
  // 切走的终端标签已断开连接（进程仍留在后端），状态回到灰色待接入
  if (old && statusMap[old] === 'connected') statusMap[old] = 'idle'
  try { localStorage.setItem(`${TABS_KEY}.active`, v || '') } catch (e) {}
})

const activeTab = computed(() => tabs.value.find((x) => x.id === activeId.value) || null)

// 状态颜色：连接成功绿色，连接中 / 重连中黄色（作用在终端图标上）
function statusOf(id) {
  const s = statusMap[id]
  if (s === 'connected') return 'ok'
  if (s === 'connecting' || s === 'reconnecting') return 'warn'
  return ''
}
function statusLabel(id) {
  const s = statusMap[id]
  if (s === 'connecting') return t('toolDock.connecting')
  if (s === 'reconnecting') return t('toolDock.reconnecting')
  if (s === 'connected') return t('toolDock.connected')
  return ''
}

// --- 浮窗开关与拖动 ---
const open = ref(false)
const panelEl = ref(null)
const pos = ref({ x: 0, y: 0 })
const dragging = ref(false)

// 坐标限位：始终留 8px 边距，避免拖动 / 缩放窗口后浮窗跑到屏幕外
function clampPos(x, y) {
  const el = panelEl.value
  const w = el ? el.offsetWidth : 720
  const h = el ? el.offsetHeight : 460
  return {
    x: Math.min(Math.max(8, x), Math.max(8, window.innerWidth - w - 8)),
    y: Math.min(Math.max(8, y), Math.max(8, window.innerHeight - h - 8)),
  }
}
function loadPos() {
  try {
    const p = JSON.parse(localStorage.getItem(POS_KEY) || 'null')
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return p
  } catch (e) { /* 损坏的坐标按默认位置处理 */ }
  return null
}
async function openPanel() {
  // 先按记录 / 默认位置摆好再显示，避免展开瞬间在左上角闪一下；渲染后再用真实尺寸校正
  const savedPos = loadPos()
  if (savedPos) {
    pos.value = clampPos(savedPos.x, savedPos.y)
  } else {
    pos.value = clampPos(window.innerWidth - 720 - 16, 72)
  }
  open.value = true
  nodeMenuOpen.value = false
  await nextTick()
  pos.value = clampPos(pos.value.x, pos.value.y)
}
function collapse() {
  open.value = false
  nodeMenuOpen.value = false
  // 浮窗收起后终端全部断开，状态点清零，重新打开时由新连接重新标记
  Object.keys(statusMap).forEach((k) => { delete statusMap[k] })
}

let dragState = null
function startDrag(e) {
  if (e.button !== 0 || (e.target.closest && e.target.closest('button'))) return
  dragState = { x: e.clientX, y: e.clientY, ox: pos.value.x, oy: pos.value.y }
  dragging.value = true
  document.addEventListener('mousemove', onDragMove)
  document.addEventListener('mouseup', onDragEnd)
  e.preventDefault()
}
function onDragMove(e) {
  if (!dragState) return
  pos.value = clampPos(dragState.ox + e.clientX - dragState.x, dragState.oy + e.clientY - dragState.y)
}
function onDragEnd() {
  if (!dragState) return
  dragState = null
  dragging.value = false
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', onDragEnd)
  try { localStorage.setItem(POS_KEY, JSON.stringify(pos.value)) } catch (e) {}
}

// --- 右侧书签：上下拖动 + 位置记忆（存 localStorage，按用户名隔离） ---
const bookEl = ref(null)
const bookDragging = ref(false)
// 默认放在屏幕约 42% 高处；初始化与窗口缩放时都做限位，避免书签跑出可视区
const bookY = ref(clampBookY(loadBookY()))
let bookDragState = null
let bookMoved = false   // 本次按下是否发生了拖动（拖动松手不当作「点击展开」）

function loadBookY() {
  try {
    const raw = localStorage.getItem(BOOK_KEY)
    // 注意不能直接 Number(raw)：null / 空串会被转成 0，导致书签贴到屏幕最顶部
    if (raw !== null && raw !== '') {
      const v = Number(raw)
      if (Number.isFinite(v) && v >= 0) return v
    }
  } catch (e) { /* 读取失败用默认位置 */ }
  return Math.round(window.innerHeight * 0.42)
}
function clampBookY(y) {
  const h = bookEl.value ? bookEl.value.offsetHeight : 96
  return Math.min(Math.max(8, y), Math.max(8, window.innerHeight - h - 8))
}
function startBookDrag(e) {
  if (e.button !== 0) return
  bookDragState = { y: e.clientY, oy: bookY.value }
  bookMoved = false
  bookDragging.value = true
  document.addEventListener('mousemove', onBookDragMove)
  document.addEventListener('mouseup', onBookDragEnd)
  e.preventDefault()   // 拖动时不要选中页面文字
}
function onBookDragMove(e) {
  if (!bookDragState) return
  const dy = e.clientY - bookDragState.y
  if (Math.abs(dy) > 3) bookMoved = true
  bookY.value = clampBookY(bookDragState.oy + dy)
}
function onBookDragEnd() {
  if (!bookDragState) return
  bookDragState = null
  bookDragging.value = false
  document.removeEventListener('mousemove', onBookDragMove)
  document.removeEventListener('mouseup', onBookDragEnd)
  try { localStorage.setItem(BOOK_KEY, String(bookY.value)) } catch (e) {}
}
function onBookmarkClick() {
  // 拖动松手后的 click 不当作「打开」；原地单击才展开浮窗
  if (bookMoved) { bookMoved = false; return }
  openPanel()
}

// --- 节点选择：本机固定排最前，后面是 SSH 子节点 ---
const nodeMenuOpen = ref(false)
// 「+」菜单里当前选中的工具类型（终端 / 文件管理）；无对应权限时回退到另一种
const newToolKind = ref(canUseTerminal.value ? 'terminal' : 'files')
const nodeOptions = computed(() => {
  const list = nodes.list.filter((n) => n && (n.type === 'local' || n.type === 'ssh'))
  const local = list.find((n) => n.type === 'local') || { id: 'local', name: 'local', type: 'local' }
  return [local, ...list.filter((n) => n.type === 'ssh')]
})
async function toggleNodeMenu() {
  nodeMenuOpen.value = !nodeMenuOpen.value
  if (!nodeMenuOpen.value) return
  // 权限被收窄 / 从上次状态恢复时，保证选中的类型当前是允许的
  if (newToolKind.value === 'terminal' && !canUseTerminal.value) newToolKind.value = 'files'
  if (newToolKind.value === 'files' && !canUseFiles.value) newToolKind.value = 'terminal'
  // 节点列表只有完整管理员能读取；受限管理员维持「本机」一个选项即可
  if (isFullAdmin() && !nodes.loaded) {
    try { await refreshNodes() } catch (e) { /* 拉取失败仍可创建本机标签 */ }
  }
}

// 点击菜单与「+」按钮以外的区域收起节点选择
function onDocMouseDown(e) {
  if (!nodeMenuOpen.value) return
  if (e.target.closest && e.target.closest('.td-node-wrap')) return
  nodeMenuOpen.value = false
}
document.addEventListener('mousedown', onDocMouseDown)

// 窗口尺寸变化时把浮窗与右侧书签都拉回可视区
function onWinResize() {
  if (open.value) pos.value = clampPos(pos.value.x, pos.value.y)
  bookY.value = clampBookY(bookY.value)
}
window.addEventListener('resize', onWinResize)

// --- 新建 / 关闭标签 ---
// sid / id 由浏览器生成并长期复用：终端刷新页面后按同一 sid 接回原来的常驻 shell
function makeSid() {
  try {
    if (crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, '')
  } catch (e) { /* 非安全上下文没有 randomUUID：走下方兜底 */ }
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 10)
}

// 同一节点同类型的标签可能开多个：用「名称 / 名称 2 / 名称 3」区分（取已有最大序号 + 1）；
// 终端与文件管理分开编号，避免「本机终端 + 本机文件」被挤成「本机 2」。
function makeTitle(node, kind, baseName) {
  const escaped = baseName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(`^${escaped}(?: (\\d+))?$`)
  let max = 0
  for (const tab of tabs.value) {
    if (tab.node !== node.id || tab.kind !== kind) continue
    const m = re.exec(tab.title)
    if (m) max = Math.max(max, m[1] ? Number(m[1]) : 1)
  }
  return max === 0 ? baseName : `${baseName} ${max + 1}`
}

function createTab(node, kind) {
  // 兜底：当前类型无权限时换成有权限的那一种
  if (kind === 'files' && !canUseFiles.value) kind = 'terminal'
  if (kind === 'terminal' && !canUseTerminal.value) kind = 'files'
  const id = makeSid()
  const baseName = node.type === 'local' ? t('toolDock.local') : (node.name || node.id)
  const tab = { id, kind, node: node.id, title: makeTitle(node, kind, baseName) }
  if (kind === 'files') tab.path = ''   // 文件管理标签记住上次目录（初始为根目录）
  tabs.value.push(tab)
  activeId.value = id
  nodeMenuOpen.value = false
}

async function closeTab(id) {
  const tab = tabs.value.find((x) => x.id === id)
  if (!tab) return
  if (tab.kind !== 'files') {
    if (!window.confirm(t('toolDock.closeConfirm', { name: tab.title }))) return
    // 先请求后端结束该常驻会话（远端 Agent 节点由主面板转发），再移除标签
    try {
      await api.delete('/terminal/persist', { params: { node: tab.node, sid: tab.id } })
    } catch (e) {
      console.warn('[tool-dock] 结束后端终端会话失败（可能已自行退出）:', e)
    }
  }
  const idx = tabs.value.findIndex((x) => x.id === id)
  if (idx < 0) return
  tabs.value.splice(idx, 1)
  delete statusMap[id]
  if (activeId.value === id) {
    const next = tabs.value[idx] || tabs.value[idx - 1]
    activeId.value = next ? next.id : ''
  }
}

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMouseDown)
  window.removeEventListener('resize', onWinResize)
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', onDragEnd)
  document.removeEventListener('mousemove', onBookDragMove)
  document.removeEventListener('mouseup', onBookDragEnd)
})
</script>

<style scoped>
/* ---- 右侧书签（收起态）---- */
.td-bookmark {
  position: fixed;
  right: 0;
  z-index: 9300;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
  padding: 12px 7px;
  background: var(--g-surface);
  color: var(--g-text);
  border: 1px solid var(--g-border);
  border-right: none;
  border-radius: 12px 0 0 12px;
  box-shadow: -6px 0 18px rgba(0, 0, 0, 0.18);
  cursor: grab;
  touch-action: none;
  transition: background 0.15s ease, padding 0.15s ease;
}
.td-bookmark.dragging { cursor: grabbing; }
.td-bookmark:hover {
  background: var(--g-surface-hover);
  padding-right: 10px;
}
.td-bookmark-label {
  writing-mode: vertical-rl;
  letter-spacing: 3px;
  font-size: 12px;
  font-weight: 600;
}

/* ---- 浮窗（展开态）---- */
.td-panel {
  position: fixed;
  z-index: 9400;
  display: flex;
  flex-direction: column;
  width: min(760px, calc(100vw - 40px));
  height: min(470px, calc(100vh - 110px));
  min-width: 380px;
  min-height: 240px;
  background: var(--g-surface);
  color: var(--g-text);
  border: 1px solid var(--g-border);
  border-radius: 12px;
  box-shadow: var(--g-shadow);
  overflow: hidden;
}
.td-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
  height: 38px;
  padding: 0 8px 0 12px;
  background: var(--g-titlebar-bg);
  border-bottom: 1px solid var(--g-border-soft);
  cursor: grab;
  user-select: none;
  position: relative;
}
.td-head.dragging { cursor: grabbing; }
.td-head-title { font-size: 13px; font-weight: 600; }
.td-head-sub { font-size: 11px; color: var(--g-text-muted); }
.td-head-actions { margin-left: auto; display: flex; align-items: center; gap: 2px; }
.td-node-wrap { position: relative; }
.td-iconbtn {
  width: 26px;
  height: 26px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--g-text);
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.td-iconbtn:hover { background: var(--g-hover); }

/* 节点选择下拉：锚定在「+」按钮下方 */
.td-node-menu {
  position: absolute;
  right: 0;
  top: 32px;
  z-index: 6;
  min-width: 210px;
  max-height: 260px;
  overflow: auto;
  padding: 4px;
  background: var(--g-menu-bg);
  border: 1px solid var(--g-border);
  border-radius: 8px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.28);
}
/* 工具类型选择：终端 / 文件管理（分段按钮） */
.td-kind-row {
  display: flex;
  gap: 4px;
  padding: 4px 4px 6px;
  margin-bottom: 2px;
  border-bottom: 1px solid var(--g-border-soft);
}
.td-kind {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 5px 6px;
  font-size: 12px;
  color: var(--g-text-secondary);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
}
.td-kind:hover { background: var(--g-hover); }
.td-kind.active {
  color: var(--g-text);
  background: var(--g-selected);
  border-color: var(--g-border);
}
.td-node-menu-head {
  padding: 6px 8px;
  font-size: 11px;
  color: var(--g-text-muted);
}
.td-node-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 8px;
  background: transparent;
  color: var(--g-text);
  border: none;
  border-radius: 6px;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.td-node-item:hover { background: var(--g-hover); }
.td-node-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.td-node-tag {
  font-size: 10px;
  color: var(--g-text-muted);
  border: 1px solid var(--g-border);
  border-radius: 4px;
  padding: 0 4px;
}

/* ---- 终端标签栏 ---- */
.td-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
  padding: 6px 8px;
  overflow-x: auto;
  background: var(--g-surface-soft);
  border-bottom: 1px solid var(--g-border-soft);
}
.td-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  padding: 4px 5px 4px 8px;
  font-size: 12px;
  color: var(--g-text-secondary);
  border: 1px solid transparent;
  border-radius: 7px;
  cursor: pointer;
  white-space: nowrap;
}
.td-tab:hover { background: var(--g-hover); }
.td-tab.active {
  background: var(--g-selected);
  border-color: var(--g-border);
  color: var(--g-text);
}
/* 标签图标：终端图标兼作连接状态指示（绿=已连接 / 黄=连接中），文件夹图标标记文件管理 */
.td-tab-icon {
  flex: none;
  color: var(--g-text-faint);
}
.td-tab-icon.ok { color: #3fb950; }
.td-tab-icon.warn { color: #d9a51f; }
.td-tab-icon.files { color: var(--g-primary); }
.td-tab-name {
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.td-tab-close {
  width: 16px;
  height: 16px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--g-text-muted);
  border: none;
  border-radius: 4px;
  cursor: pointer;
}
.td-tab-close:hover { background: rgba(224, 27, 36, 0.16); color: #e01b24; }

/* ---- 内容区 / 空状态 ---- */
.td-body { flex: 1; min-height: 0; overflow: hidden; background: #1e1e1e; }
.td-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  font-size: 12px;
  color: var(--g-text-muted);
  background: var(--g-surface);
}
.td-empty-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  font-size: 12px;
  color: var(--g-on-strong);
  background: var(--g-strong);
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.td-empty-btn:hover { background: var(--g-strong-hover); }

/* 小屏：浮窗贴近全屏，避免拖出可视区 */
@media (max-width: 520px) {
  .td-panel {
    width: calc(100vw - 16px);
    height: calc(100vh - 90px);
  }
}
</style>
