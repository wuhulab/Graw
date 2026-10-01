/* index.js — vue-i18n 实例与语言注册中心。

  业务背景：面板支持 28 种界面语言。这里负责：
   1. 汇总所有语言包（messages），供 createI18n 使用；
   2. 维护支持语言清单 LANGUAGES（设置页下拉用它渲染）；
   3. 提供 setLocale() 切换语言，并把用户选择同步到 settings store 持久化。

  界面语言偏好存在 settings（localStorage），启动时按它初始化；
  非法/未知的 locale 一律回退到简体中文。
*/
import { createI18n } from 'vue-i18n'       // 创建 i18n 实例（Composition API 模式）
import { settings } from '../store/settings' // 读取/写入用户语言偏好（持久化在 localStorage）

// 语言包「按需加载」（本轮启动提速的关键改动之一）：
//   28 个语言包合计约 3.4MB 源码——此前全部静态 import，会被整体打进首屏 chunk，
//   用户一进面板就要下载/解析全部 28 种语言（实际一次只用一种），是启动慢的最大单一原因。
//   现改为：只静态内置源语言 zh-CN（同时是 fallbackLocale 的兜底语言，必须内置），
//   其余语言包通过动态 import 注册成独立 chunk，首次用到时才下载：
//     - 启动：main.js 先 await ensureLocale(当前语言) 再挂载，避免「先中文后翻译」闪现；
//     - 切换：setLocale() 内部触发加载，语言包到位后 vue-i18n 自动重新渲染。
import zhCN from './zh-CN'   // 简体中文（源语言，缺失键的回退目标）

// 支持的语言列表（设置页下拉使用）
// code: i18n locale code；name: 该语言下自身的名称
export const LANGUAGES = [
  { code: 'zh-CN', name: '简体中文' },
  { code: 'zh-TW', name: '繁體中文' },
  { code: 'en', name: 'English' },
  { code: 'ja', name: '日本語' },
  { code: 'ru', name: 'Русский' },
  { code: 'es', name: 'Español' },
  { code: 'de', name: 'Deutsch' },
  { code: 'fr', name: 'Français' },
  { code: 'pt', name: 'Português' },
  { code: 'ko', name: '한국어' },
  { code: 'eo', name: 'Esperanto' },
  { code: 'ar', name: 'العربية' },
  { code: 'arz', name: 'مصرى' },
  { code: 'egy', name: '𓂋𓏤𓈖𓆎𓅓𓏏𓊖 (Medu Neter)' },
  { code: 'ga', name: 'Gaeilge' },
  { code: 'vi', name: 'Tiếng Việt' },
  { code: 'el', name: 'Ελληνικά' },
  { code: 'pl', name: 'Polski' },
  { code: 'it', name: 'Italiano' },
  { code: 'la', name: 'Latina' },
  { code: 'bo', name: 'བོད་ཡིག' },
  { code: 'za', name: 'Vahcuengh' },
  { code: 'sux', name: '𒅴𒂠 (Eme-Gir)' },
  { code: 'akk', name: '𒀝𒅗𒁺𒌑 (Akkadû)' },
  { code: 'hit', name: '𒉈𒅆𒇷 (Nešili)' },
  { code: 'phn', name: '𐤃𐤁𐤓𐤉𐤌 (Dabarīm)' },
  { code: 'grc', name: 'Ἀρχαία Ἑλληνική' },
  { code: 'non', name: 'ᚠᚢᚦᚨᚱᚲ (Futhark)' },
]

// 语言 code → 语言包「动态加载器」（code 与 LANGUAGES 保持一致）
// 注意：每个 () => import(...) 都会被 Vite 切成独立 chunk，只有真正用到才下载。
const LOADERS = {
  'zh-TW': () => import('./zh-TW.js'),   // 繁体中文
  en: () => import('./en.js'),           // 英语
  ja: () => import('./ja.js'),           // 日语
  ru: () => import('./ru.js'),           // 俄语
  es: () => import('./es.js'),           // 西班牙语
  de: () => import('./de.js'),           // 德语
  fr: () => import('./fr.js'),           // 法语
  pt: () => import('./pt.js'),           // 葡萄牙语
  ko: () => import('./ko.js'),           // 韩语
  eo: () => import('./eo.js'),           // 世界语
  ar: () => import('./ar.js'),           // 阿拉伯语
  arz: () => import('./arz.js'),         // 埃及语（埃及阿拉伯语）
  egy: () => import('./egy.js'),         // 古埃及语
  ga: () => import('./ga.js'),           // 爱尔兰语
  vi: () => import('./vi.js'),           // 越南文
  el: () => import('./el.js'),           // 希腊文
  pl: () => import('./pl.js'),           // 波兰语
  it: () => import('./it.js'),           // 意大利语
  la: () => import('./la.js'),           // 拉丁语
  bo: () => import('./bo.js'),           // 藏语
  za: () => import('./za.js'),           // 壮语
  sux: () => import('./sux.js'),         // 苏美尔语（楔形文字）
  akk: () => import('./akk.js'),         // 阿卡德语（楔形文字）
  hit: () => import('./hit.js'),         // 赫梯语（楔形文字）
  phn: () => import('./phn.js'),         // 腓尼基语（腓尼基字母）
  grc: () => import('./grc.js'),         // 古希腊语（阿提卡方言）
  non: () => import('./non.js'),         // 古北欧语（卢恩文）
}

// 已注册到 i18n 的语言包：zh-CN 为静态内置，其余在 ensureLocale 首次调用时注入
const messages = { 'zh-CN': zhCN }

// 合法性校验：非法 locale 回退到简体中文
function normalizeLocale(code) {
  return LANGUAGES.some((l) => l.code === code) ? code : 'zh-CN'
}

// --- 语言括号提示（古代语言语言包的书写约定） ---
// 古代语言包（古埃及语、苏美尔语、阿卡德语、赫梯语…）的所有文案统一写成：
//     `本语言书写形式 (中文原文/拉丁转写)`（部分条目只有 `(中文原文)`）
// 这里在翻译结果返回前按用户偏好改写这段括号注释（见 store/settings 的
// bracketEnabled / bracketLang），语言包本身无需改动。
//
// 只对下列「采用该约定的语言包」生效。普通语言里正文自带的括号不能用同一套规则
// 处理 —— 例如中文的 `密码 (至少 6 位)`、日文的 `アカウント (2-32 文字)`、
// 英语的 `Please choose a video file (MP4/WebM)`，误删会破坏原文。
const BRACKET_LOCALES = ['egy', 'sux', 'akk', 'hit', 'phn', 'grc', 'non']

// 行尾括号注释：`…(不含括号的内容)`，允许前后有空白
const BRACKET_TAIL_RE = /\s*\(([^()]*)\)\s*$/
// 中日韩统一表意文字（含扩展 A 区与兼容区）：用于判定括号内是否含中文原文
const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/

/**
 * 按用户偏好改写「括号注释」样式（postTranslation 钩子）。
 * @param {unknown} text - $t() 的原始翻译结果
 * @returns {unknown} 改写后的文案；非字符串、非括号语言包、非括号注释时原样返回
 */
export function applyBracketStyle(text) {
  if (typeof text !== 'string') return text                        // 非字符串（数组/对象消息）：不处理
  if (!BRACKET_LOCALES.includes(i18n.global.locale.value)) return text  // 非括号约定语言包：不处理
  const m = BRACKET_TAIL_RE.exec(text)
  if (!m) return text                                              // 末尾无括号：普通文案
  const inner = m[1]
  if (!CJK_RE.test(inner)) return text                             // 括号内无中文（如 `(my-site)`）：不是注释
  const head = text.slice(0, m.index)                              // 本语言书写形式（去掉括号注释）

  // 用「最后一个斜杠」作分隔符：中文原文里本身可能含 `/`（如 `{running}/{total} 容器`），
  // 而转写部分按约定不含 `/`。再要求斜杠之后不含中文，避免把 `(国家/地区)` 这类
  // 纯中文注释误判成「中文/转写」。
  const sep = inner.lastIndexOf('/')
  const after = sep >= 0 ? inner.slice(sep + 1).trim() : ''
  const hasTranslit = after.length > 0 && !CJK_RE.test(after)
  const zh = hasTranslit ? inner.slice(0, sep).trim() : inner.trim()  // 中文原文
  const latin = hasTranslit ? after : ''                              // 拉丁/英文转写（可能没有）

  if (!settings.bracketEnabled) return head                        // 关闭括号：只保留本语言书写
  if (settings.bracketLang === 'zh') return `${head} (${zh})`       // 仅中文
  // 仅转写：没有转写可显示的条目，直接去掉括号，避免留下空括号
  if (settings.bracketLang === 'latin') return latin ? `${head} (${latin})` : head
  return text                                                      // both：保持原样（中文 + 转写）
}

// 创建 i18n 实例：启动语言取用户设置，缺失 key 回退简体中文
const i18n = createI18n({
  legacy: false, // 使用 Composition API 模式
  globalInjection: true, // 模板中可直接使用 $t
  locale: normalizeLocale(settings.locale),
  fallbackLocale: 'zh-CN', // 缺失的键回退到简体中文
  messages,
  // 翻译结果后处理：动态套用「语言括号提示」偏好。
  // 注意：这里读取了响应式的 settings 与 locale，而 $t() 通常在组件渲染期间调用，
  // 因此修改设置会自动触发所有相关组件重新渲染，无需手动刷新。
  postTranslation: (translated) => applyBracketStyle(translated),
})

// --- 动作说明：按需加载指定语言的文案（语言包懒加载核心） ---
// 已就绪的语言集合（zh-CN 静态内置，无需下载）
const _loadedLocales = new Set(['zh-CN'])
// 正在加载的 Promise（同一语言并发请求只发起一次下载）
const _pendingLocales = new Map()

/**
 * 确保指定语言的文案已注册到 i18n（首次调用时动态下载对应语言包）。
 *
 * @param {string} code 语言代码（非法值按 zh-CN 处理）
 * @returns {Promise<boolean>} true = 文案已就绪；false = 未注册/加载失败（界面保持回退语言）
 */
export async function ensureLocale(code) {
  const target = normalizeLocale(code)   // 非法 code 归一为 zh-CN
  if (_loadedLocales.has(target)) return true
  const loader = LOADERS[target]
  if (!loader) return false              // 未注册的语言包：保持回退语言，不影响面板使用
  if (!_pendingLocales.has(target)) {
    _pendingLocales.set(
      target,
      loader()
        .then((mod) => {
          // 语言包统一 `export default {...}`；兼容命名导出形态（mod.default 兜底为整个模块）
          i18n.global.setLocaleMessage(target, mod.default || mod)
          _loadedLocales.add(target)
          return true
        })
        .catch((e) => {
          // 下载/解析失败（离线、chunk 哈希失效等）：记日志并回退语言，不阻断面板
          console.error('[graw] 加载语言包失败：', target, e)
          return false
        })
        .finally(() => _pendingLocales.delete(target))
    )
  }
  return _pendingLocales.get(target)
}

// --- 动作说明：切换界面语言（设置页语言下拉调用） ---
/**
 * 切换界面语言
 *
 * 语言包按需加载：先切 locale（未加载完的键自动回退 zh-CN），随后异步加载目标语言包，
 * 加载完成由 vue-i18n 自动触发相关组件重新渲染，无需手动刷新。
 *
 * @param {string} code - 语言代码（见 LANGUAGES）
 */
export function setLocale(code) {
  const target = normalizeLocale(code)   // 非法 code 会被归一为 zh-CN
  i18n.global.locale.value = target
  settings.locale = target               // 同步到设置，watch 自动落盘 localStorage
  // 同步 html 的 lang 属性，利于无障碍与浏览器翻译
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('lang', target)
  }
  // 目标语言包尚未内置时触发按需下载（设置页切换 / 启动预加载都会走到这里）
  ensureLocale(target).catch(() => false)
}

// 启动时同步一次 html lang（让浏览器/翻译器一开始就识别当前语言）
if (typeof document !== 'undefined') {
  document.documentElement.setAttribute('lang', i18n.global.locale.value)
}

// --- 对外导出：i18n 实例（main.js 里 app.use(i18n) 安装） ---
export default i18n
