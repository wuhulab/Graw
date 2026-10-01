/* desktopPrefs.js — 桌面快捷方式的个性化偏好（隐藏 / 固定到任务栏）。

  业务背景：桌面快捷方式默认全部展示。用户可在桌面**右键**某个应用选择：
    - 隐藏：从桌面移除（可在「设置 → 桌面」里恢复）
    - 固定到任务栏：在底部任务栏常驻入口（就算窗口没打开也能点开）
  这两个操作受「仅当前用户」开关影响：
    - 关闭（默认）：偏好全局共享
    - 开启：偏好只对当前登录用户生效（key 带用户名后缀），不同用户互不干扰

  存储结构（localStorage 两个 key）：
    - 全局 key：{ perUser: bool, hiddenKeys: [], pinnedKeys: [] }
      —— perUser 开关与「全局模式」下的隐藏/固定列表
    - 用户 key（graw_desktop_prefs_u_<username>）：{ hiddenKeys: [], pinnedKeys: [] }
      —— perUser 开启时当前账号自己的隐藏/固定列表

  用法：App.vue 在登录态建立后调用 bindUser()；其余组件直接读 desktopPrefs
  的响应式字段（hiddenKeys / pinnedKeys / perUser）即可。
*/
import { reactive, watch } from 'vue'   // 响应式偏好 + watch 自动持久化到 localStorage
import { auth } from './auth'           // 当前登录用户（「仅当前用户」模式的 key 区分）

// 本地存储键：全局 key 与 用户级 key（perUser 开启时用后者存隐藏/固定列表）
const GLOBAL_KEY = 'graw_desktop_prefs'
const userKey = (username) => `${GLOBAL_KEY}_u_${(username || 'guest')}`

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

// --- 对外暴露：桌面偏好单例 ---
export const desktopPrefs = reactive({
  perUser: loadFrom(GLOBAL_KEY).perUser === true,  // 仅当前用户 开关（全局存储）
  hiddenKeys: [],                                  // 当前生效作用域的隐藏列表
  pinnedKeys: [],                                  // 当前生效作用域的固定列表
})

// 当前登录用户名（auth store 就绪后填充）
function currentUsername() {
  return auth.user?.username || ''
}

// 隐藏/固定列表 当前应读写的存储 key：perUser 开启 → 用户 key，否则全局 key
function listKey() {
  return desktopPrefs.perUser ? userKey(currentUsername()) : GLOBAL_KEY
}

// 重新加载当前作用域的隐藏/固定列表（登录用户 / perUser 切换后调用）
function reload() {
  const data = listKey() === GLOBAL_KEY
    ? loadFrom(GLOBAL_KEY)
    : loadFrom(userKey(currentUsername()))
  desktopPrefs.hiddenKeys = Array.isArray(data.hiddenKeys) ? data.hiddenKeys : []
  desktopPrefs.pinnedKeys = Array.isArray(data.pinnedKeys) ? data.pinnedKeys : []
}

// 持久化当前作用域的隐藏/固定列表
function persistList() {
  try {
    const data = { hiddenKeys: desktopPrefs.hiddenKeys, pinnedKeys: desktopPrefs.pinnedKeys }
    if (desktopPrefs.perUser) {
      localStorage.setItem(userKey(currentUsername()), JSON.stringify(data))
    } else {
      // 合并写全局：保留原 perUser 值，仅更新列表
      const g = loadFrom(GLOBAL_KEY)
      g.hiddenKeys = data.hiddenKeys
      g.pinnedKeys = data.pinnedKeys
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

// 双保险：perUser 变化时（外部直接赋值，如设置页 v-model）自动落盘 + 重读列表
watch(() => desktopPrefs.perUser, (val) => {
  persistPerUser()
  reload()
})

// 监听隐藏/固定列表变化自动落盘（即改即存，隐藏/pin 等操作改完数组即持久化）
watch(
  () => [desktopPrefs.hiddenKeys, desktopPrefs.pinnedKeys],
  () => persistList(),
  { deep: true }
)