/* settings.js — 面板本地偏好设置（与账号无关的桌面级开关）。

  业务背景：这些是「这台浏览器/这层界面」的偏好——任务栏显示文字、界面语言、
  是否开启统一面板兼容、是否隐藏 Foxcode 快捷方式。它们不跟登录账号走，
  只存在本机 localStorage 里，所以换个浏览器/清缓存就会回到默认值。

  关键状态（defaults）：
    showTaskbarText / taskbarTextOnly —— 任务栏的展示方式
    locale     —— 界面语言（与 locales/index.js 的 LANGUAGES 对应）
    unifiedPanel —— 统一面板兼容：开启后每个窗口绑定打开时的节点
    hideFoxcode  —— 是否隐藏桌面上的 Foxcode 快捷方式
    panelMode    —— 标准面板模式：启用后界面切换为 1Panel 式侧边栏布局；
                    首次访问按设备推断（手机/窄屏默认启用），用户选择单独持久化
    panelTabs    —— 面板模式多标签缓存：开=标签页保留窗口状态，关=单页切换
    desktopPreload —— 类桌面模式预加载：进入面板、数据就绪后空闲分批预取全部应用代码
    shortcutFontSize / shortcutLabelColor / shortcutLabelStroke ——
        桌面图标下方文字的样式：字号(px)、颜色(#RRGGBB)、是否加黑色描边
    bracketEnabled / bracketLang —— 语言括号提示：
        古代语言（古埃及语、苏美尔语、阿卡德语…）的语言包约定所有文案写成
        `本语言书写形式 (中文原文/拉丁转写)`。这两个开关控制该括号注释是否展示、
        以及展示括号内的哪种内容，由 locales/index.js 的 postTranslation 钩子生效。
    overviewDecimals —— 系统概览数值精度：开启后环形统计中心百分比保留两位小数
        （如 31.25%），关闭则取整（31%）。

  用法：任意组件直接读 settings 的响应式字段即可；修改会自动被 watch 落盘，
  无需手动保存。
*/
import { reactive, watch } from 'vue'   // 响应式设置 + watch 自动持久化到 localStorage

const STORAGE_KEY = 'graw_settings'     // 本地存储键名：设置变化时把整份偏好写回这里
// 界面模式偏好单独存一个键：'panel'（标准面板）/ 'desktop'（类桌面）。
// 单独存的原因：旧版本可能已经把 panelMode:false 写进整份设置快照，无法区分
// 「用户明确选了桌面模式」和「旧默认值」；用独立键可在升级后正确套用手机默认值。
const PANEL_MODE_KEY = 'graw_panel_mode'

// 默认偏好值：首次使用（无缓存）时从这里起
const defaults = {
  showTaskbarText: true,   // 任务栏显示详细文字（默认开）
  taskbarTextOnly: false,  // 任务栏仅文字、隐藏图标（默认关）
  // 界面语言（locale code，与 locales/index.js 中 LANGUAGES 对应）
  locale: 'zh-CN',
  // 统一面板兼容：开启后每个应用窗口绑定打开时对应的节点，聚焦窗口即操作该节点
  unifiedPanel: false,
  // 隐藏桌面上的 Foxcode 快捷方式
  hideFoxcode: false,
  // 标准面板模式：开启后界面切换为 1Panel 式侧边栏布局（替代桌面/窗口系统）
  panelMode: false,
  // 面板模式多标签缓存：开启后切换应用保留窗口状态（标签页），关闭则为单页切换
  panelTabs: true,
  // 类桌面模式预加载：面板启动、数据就绪后，空闲时段分批预取全部应用窗口代码，
  // 使之后打开任意应用都命中本地缓存、瞬时渲染（面板模式为单页切换，不参与预加载）
  desktopPreload: true,
  // 桌面图标下方文字的样式：字号（px，8-24）/ 颜色 / 是否加黑边描边
  shortcutFontSize: 12,
  shortcutLabelColor: '#ffffff',
  shortcutLabelStroke: false,
  // 语言括号提示：是否展示「(中文原文/拉丁转写)」括号注释
  bracketEnabled: true,
  // 括号内展示的内容：both=中文+转写（默认）/ zh=仅中文原文 / latin=仅拉丁转写
  bracketLang: 'both',
  // 系统概览数值精度：true=环形统计中心百分比保留两位小数（31.25%），false=取整（31%）
  overviewDecimals: true,
}

// 设备是否更适合「传统服务器面板」布局：
// 手机/平板窄屏默认用标准面板模式（侧边栏 + 单页），避免桌面窗口在手机上难以操作。
// 用户之后手动切换的偏好会写入 localStorage，后续启动以本地偏好为准。
function prefersServerPanel() {
  try {
    const ua = navigator.userAgent || ''
    const mobileUA = /Android|iPhone|iPad|iPod|Mobile|HarmonyOS/i.test(ua)
    const narrow = window.matchMedia
      ? window.matchMedia('(max-width: 767px)').matches
      : window.innerWidth < 768
    // iPadOS 桌面版 UA 伪装成 Macintosh，用触摸点数补充判定
    const iPad = /Macintosh/i.test(ua) && (navigator.maxTouchPoints || 0) > 1
    return !!(mobileUA || narrow || iPad)
  } catch {
    return false
  }
}

// 读取用户明确保存过的界面模式偏好（没有则返回 null）
function readPanelModePref() {
  try {
    const v = localStorage.getItem(PANEL_MODE_KEY)
    return v === 'panel' || v === 'desktop' ? v : null
  } catch {
    return null
  }
}

// 保存界面模式偏好：之后无论屏幕宽度如何变化，都以用户选择为准
function writePanelModePref(panelMode) {
  try {
    localStorage.setItem(PANEL_MODE_KEY, panelMode ? 'panel' : 'desktop')
  } catch (e) {
    // 隐私模式 / 存储被禁用：本次界面仍可正常使用，只是偏好无法持久化
  }
}

// 决定初始界面模式：优先用户明确偏好 → 否则按设备类型推断（手机默认标准面板）
function initialPanelMode() {
  const pref = readPanelModePref()
  if (pref) return pref === 'panel'
  const inferred = prefersServerPanel()
  writePanelModePref(inferred)   // 把推断结果作为本机默认偏好存下来
  return inferred
}

// 启动时读取本地偏好；没有缓存 / 内容损坏时回退默认值
function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      // 从未保存过偏好：按设备类型决定默认界面（手机默认面板模式，桌面默认桌面模式）
      const initial = { ...defaults, panelMode: initialPanelMode() }
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(initial)) } catch (e) { /* 隐私模式写入失败可忽略 */ }
      return initial
    }
    const parsed = JSON.parse(raw)
    const merged = { ...defaults, ...parsed }    // 与默认值合并：新增偏好项时旧缓存也能正常读出
    // 界面模式以独立偏好键为准：旧快照里的 panelMode 不再代表用户选择
    merged.panelMode = initialPanelMode()
    return merged
  } catch {
    return { ...defaults, panelMode: initialPanelMode() }   // 存储损坏/被禁用：宁可丢偏好也不能让应用启动失败
  }
}

// --- 对外暴露：全站唯一的设置单例（修改任意字段即触发 watch 落盘） ---
export const settings = reactive(load())

// 监听全部偏好字段，任一变化就整体写回 localStorage（即改即存，无需手动保存）
watch(
  () => ({
    showTaskbarText: settings.showTaskbarText,
    taskbarTextOnly: settings.taskbarTextOnly,
    locale: settings.locale,
    unifiedPanel: settings.unifiedPanel,
    hideFoxcode: settings.hideFoxcode,
    panelMode: settings.panelMode,
    panelTabs: settings.panelTabs,
    desktopPreload: settings.desktopPreload,
    shortcutFontSize: settings.shortcutFontSize,
    shortcutLabelColor: settings.shortcutLabelColor,
    shortcutLabelStroke: settings.shortcutLabelStroke,
    bracketEnabled: settings.bracketEnabled,
    bracketLang: settings.bracketLang,
    overviewDecimals: settings.overviewDecimals,
  }),
  (val) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(val))   // 整份快照覆盖写，保持存储结构与 defaults 对齐
  },
  { deep: true }
)

// 界面模式偏好单独落盘：用户在设置里切换「面板 / 桌面」后立即生效，
// 后续启动不再受当前屏幕宽度影响（手机/平板横竖屏切换也保持用户选择）。
watch(() => settings.panelMode, (val) => writePanelModePref(!!val))
