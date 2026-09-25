/* settings.js — 面板本地偏好设置（与账号无关的桌面级开关）。

  业务背景：这些是「这台浏览器/这层界面」的偏好——任务栏显示文字、界面语言、
  是否开启统一面板兼容、是否隐藏 Foxcode 快捷方式。它们不跟登录账号走，
  只存在本机 localStorage 里，所以换个浏览器/清缓存就会回到默认值。

  关键状态（defaults）：
    showTaskbarText / taskbarTextOnly —— 任务栏的展示方式
    locale     —— 界面语言（与 locales/index.js 的 LANGUAGES 对应）
    unifiedPanel —— 统一面板兼容：开启后每个窗口绑定打开时的节点
    hideFoxcode  —— 是否隐藏桌面上的 Foxcode 快捷方式
    panelMode    —— 标准面板模式：启用后界面切换为 1Panel 式侧边栏布局
    panelTabs    —— 面板模式多标签缓存：开=标签页保留窗口状态，关=单页切换
    shortcutFontSize / shortcutLabelColor / shortcutLabelStroke ——
        桌面图标下方文字的样式：字号(px)、颜色(#RRGGBB)、是否加黑色描边
    bracketEnabled / bracketLang —— 语言括号提示：
        古代语言（古埃及语、苏美尔语、阿卡德语…）的语言包约定所有文案写成
        `本语言书写形式 (中文原文/拉丁转写)`。这两个开关控制该括号注释是否展示、
        以及展示括号内的哪种内容，由 locales/index.js 的 postTranslation 钩子生效。

  用法：任意组件直接读 settings 的响应式字段即可；修改会自动被 watch 落盘，
  无需手动保存。
*/
import { reactive, watch } from 'vue'   // 响应式设置 + watch 自动持久化到 localStorage

const STORAGE_KEY = 'graw_settings'     // 本地存储键名：设置变化时把整份偏好写回这里

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
  // 桌面图标下方文字的样式：字号（px，8-24）/ 颜色 / 是否加黑边描边
  shortcutFontSize: 12,
  shortcutLabelColor: '#ffffff',
  shortcutLabelStroke: false,
  // 语言括号提示：是否展示「(中文原文/拉丁转写)」括号注释
  bracketEnabled: true,
  // 括号内展示的内容：both=中文+转写（默认）/ zh=仅中文原文 / latin=仅拉丁转写
  bracketLang: 'both',
}

// 启动时读取本地偏好；没有缓存 / 内容损坏时回退默认值
function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...defaults }              // 从未保存过偏好：直接用默认值
    return { ...defaults, ...JSON.parse(raw) }    // 与默认值合并：新增偏好项时旧缓存也能正常读出
  } catch {
    return { ...defaults }                        // 存储损坏/被禁用：宁可丢偏好也不能让应用启动失败
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
    shortcutFontSize: settings.shortcutFontSize,
    shortcutLabelColor: settings.shortcutLabelColor,
    shortcutLabelStroke: settings.shortcutLabelStroke,
    bracketEnabled: settings.bracketEnabled,
    bracketLang: settings.bracketLang,
  }),
  (val) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(val))   // 整份快照覆盖写，保持存储结构与 defaults 对齐
  },
  { deep: true }
)
