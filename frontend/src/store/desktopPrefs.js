/* desktopPrefs.js — 应用个性化偏好（收藏 / 隐藏 / 固定到任务栏）。

  业务背景：应用入口默认全部展示。用户可在桌面或标准面板侧边栏**右键**某个应用选择：
    - 收藏：置顶到面板侧边栏的「收藏」分组（仅面板模式）
    - 隐藏：从桌面 / 侧边栏 / 全局搜索移除（可在「设置」里恢复显示）
    - 固定到任务栏：在底部任务栏常驻入口（就算窗口没打开也能点开）
  这些操作受「分账号隔离」开关影响：
    - 关闭（默认）：偏好全局共享
    - 开启：偏好只对当前登录用户生效（key 带用户名后缀），不同用户互不干扰

  存储结构（localStorage 两个 key）：
    - 全局 key：{ perUser: bool, hiddenKeys: [], pinnedKeys: [], favoriteKeys: [] }
      —— perUser 开关与「全局模式」下的收藏/隐藏/固定列表
    - 用户 key（graw_desktop_prefs_u_<username>）：{ hiddenKeys: [], pinnedKeys: [], favoriteKeys: [] }
      —— perUser 开启时当前账号自己的收藏/隐藏/固定列表

  兼容：旧版收藏存在 settings.panelFavorites（graw_settings 快照）里，首次升级时
  自动迁移到当前作用域，避免用户已收藏的应用丢失。

  用法：App.vue 在登录态建立后调用 bindUser()；其余组件直接读 desktopPrefs
  的响应式字段（favoriteKeys / hiddenKeys / pinnedKeys / perUser）即可。
*/
import { reactive, watch } from 'vue'   // 响应式偏好 + watch 自动持久化到 localStorage
import { auth } from './auth'           // 当前登录用户（「分账号隔离」模式的 key 区分）

// 本地存储键：全局 key 与 用户级 key（perUser 开启时用后者存收藏/隐藏/固定列表）
const GLOBAL_KEY = 'graw_desktop_prefs'
const userKey = (username) => `${GLOBAL_KEY}_u_${(username || 'guest')}`
// 旧版偏好快照（settings.js 写入）：仅用于迁移 panelFavorites
const LEGACY_SETTINGS_KEY = 'graw_settings'
// 旧版收藏迁移标记：只迁移一次，避免后续新建账号时重复继承旧列表
const LEGACY_FAV_MIGRATED_KEY = 'graw_favorite_migrated'

// 读取指定 key 的本地存储值（损坏 / 不存在时回退默认）
function loadFrom(key) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

// 当前登录用户名（auth store 就绪后填充）
function currentUsername() {
  return auth.user?.username || ''
}

// 隐藏/固定/收藏列表 当前应读写的存储 key：perUser 开启 → 用户 key，否则全局 key
function scopeKey(perUser = desktopPrefs.perUser) {
  return perUser ? userKey(currentUsername()) : GLOBAL_KEY
}

// 从指定作用域读取三类列表（缺失/类型异常时回退空数组）
function readLists(perUser) {
  const data = loadFrom(scopeKey(perUser))
  return {
    hiddenKeys: Array.isArray(data.hiddenKeys) ? data.hiddenKeys : [],
    pinnedKeys: Array.isArray(data.pinnedKeys) ? data.pinnedKeys : [],
    favoriteKeys: Array.isArray(data.favoriteKeys) ? data.favoriteKeys : [],
  }
}

// 旧版收藏：从 settings 快照里读出 panelFavorites（一次性迁移用）
function legacyFavorites() {
  try {
    const raw = localStorage.getItem(LEGACY_SETTINGS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed?.panelFavorites) ? parsed.panelFavorites : []
  } catch {
    return []
  }
}

// --- 初始作用域：perUser 开关存在全局 key；列表按开关决定从哪个 key 读 ---
const initialPerUser = loadFrom(GLOBAL_KEY).perUser === true
const initialLists = readLists(initialPerUser)
// 迁移旧版收藏：只执行一次；仅当当前作用域从未写过 favoriteKeys 时写入，避免覆盖新数据
function legacyMigrated() {
  try {
    return localStorage.getItem(LEGACY_FAV_MIGRATED_KEY) === '1'
  } catch {
    return true   // 存储不可用则不再尝试迁移
  }
}
if (!legacyMigrated()) {
  if (!Array.isArray(loadFrom(scopeKey(initialPerUser)).favoriteKeys)) {
    const legacy = legacyFavorites()
    if (legacy.length) {
      initialLists.favoriteKeys = legacy
      try {
        const target = { ...loadFrom(scopeKey(initialPerUser)), ...initialLists }
        localStorage.setItem(scopeKey(initialPerUser), JSON.stringify(target))
      } catch {
        // 存储不可用：本次内存里仍能正常显示，仅无法持久化
      }
    }
  }
  try { localStorage.setItem(LEGACY_FAV_MIGRATED_KEY, '1') } catch { /* 忽略写入失败 */ }
}

// --- 对外暴露：应用偏好单例 ---
export const desktopPrefs = reactive({
  perUser: initialPerUser,                          // 分账号隔离 开关（全局存储）
  hiddenKeys: initialLists.hiddenKeys,              // 当前生效作用域的隐藏列表
  pinnedKeys: initialLists.pinnedKeys,              // 当前生效作用域的固定列表
  favoriteKeys: initialLists.favoriteKeys,          // 当前生效作用域的收藏列表（最近的在前）
})

// 重新加载当前作用域的收藏/隐藏/固定列表（登录用户 / perUser 切换后调用）
function reload() {
  Object.assign(desktopPrefs, readLists(desktopPrefs.perUser))
}

// 持久化当前作用域的收藏/隐藏/固定列表
function persistList() {
  try {
    const data = {
      hiddenKeys: desktopPrefs.hiddenKeys,
      pinnedKeys: desktopPrefs.pinnedKeys,
      favoriteKeys: desktopPrefs.favoriteKeys,
    }
    if (desktopPrefs.perUser) {
      localStorage.setItem(scopeKey(true), JSON.stringify(data))
    } else {
      // 合并写全局：保留原 perUser 值，仅更新列表
      const g = loadFrom(GLOBAL_KEY)
      g.hiddenKeys = data.hiddenKeys
      g.pinnedKeys = data.pinnedKeys
      g.favoriteKeys = data.favoriteKeys
      localStorage.setItem(GLOBAL_KEY, JSON.stringify(g))
    }
  } catch {
    // 存储不可用（隐私模式等）时静默失败，不影响界面使用
  }
}

// 持久化 perUser 开关（始终写全局 key）
function persistPerUser() {
  try {
    const g = { ...loadFrom(GLOBAL_KEY), perUser: desktopPrefs.perUser }
    localStorage.setItem(GLOBAL_KEY, JSON.stringify(g))
  } catch {
    // 存储不可用时不阻断 UI
  }
}

// --- 对外操作 ---

// 登录态就绪 / 切换用户后校准：重新读取当前账号的偏好
export function bindUser() {
  reload()
}

// 切换「仅当前用户」开关：保存开关（全局）并重新加载目标作用域列表
export function setPerUser(val) {
  desktopPrefs.perUser = !!val
  persistPerUser()
  reload()
}

export function isHidden(key) {
  return desktopPrefs.hiddenKeys.includes(key)
}

export function hideShortcut(key) {
  if (!desktopPrefs.hiddenKeys.includes(key)) desktopPrefs.hiddenKeys.push(key)
  persistList()
}

export function showShortcut(key) {
  desktopPrefs.hiddenKeys = desktopPrefs.hiddenKeys.filter(k => k !== key)
  persistList()
}

export function isPinned(key) {
  return desktopPrefs.pinnedKeys.includes(key)
}

export function pinShortcut(key) {
  if (!desktopPrefs.pinnedKeys.includes(key)) desktopPrefs.pinnedKeys.push(key)
  persistList()
}

export function unpinShortcut(key) {
  desktopPrefs.pinnedKeys = desktopPrefs.pinnedKeys.filter(k => k !== key)
  persistList()
}

export function isFavorite(key) {
  return desktopPrefs.favoriteKeys.includes(key)
}

// 收藏 / 取消收藏：收藏时放到最前（最近收藏优先），后续由下方 watch 统一落盘
export function toggleFavorite(key) {
  if (!key) return
  if (isFavorite(key)) {
    desktopPrefs.favoriteKeys = desktopPrefs.favoriteKeys.filter(k => k !== key)
  } else {
    desktopPrefs.favoriteKeys = [key, ...desktopPrefs.favoriteKeys.filter(k => k !== key)]
  }
}

// 双保险：perUser 变化时（外部直接赋值，如设置页 v-model）自动落盘 + 重读列表
watch(() => desktopPrefs.perUser, (val) => {
  persistPerUser()
  reload()
})

// 监听收藏/隐藏/固定列表变化自动落盘（即改即存，操作改完数组即持久化）
watch(
  () => [desktopPrefs.hiddenKeys, desktopPrefs.pinnedKeys, desktopPrefs.favoriteKeys],
  () => persistList(),
  { deep: true }
)