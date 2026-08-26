/* index.js — vue-i18n 实例与语言注册中心。

  业务背景：面板支持 11 种界面语言。这里负责：
   1. 汇总所有语言包（messages），供 createI18n 使用；
   2. 维护支持语言清单 LANGUAGES（设置页下拉用它渲染）；
   3. 提供 setLocale() 切换语言，并把用户选择同步到 settings store 持久化。

  界面语言偏好存在 settings（localStorage），启动时按它初始化；
  非法/未知的 locale 一律回退到简体中文。
*/
import { createI18n } from 'vue-i18n'       // 创建 i18n 实例（Composition API 模式）
import { settings } from '../store/settings' // 读取/写入用户语言偏好（持久化在 localStorage）

// 11 个语言包：结构一致（key 对齐），供 $t() 按当前 locale 取词
import zhCN from './zh-CN'   // 简体中文（源语言，缺失键的回退目标）
import zhTW from './zh-TW'   // 繁体中文
import en from './en'        // 英语
import ja from './ja'        // 日语
import ru from './ru'        // 俄语
import es from './es'        // 西班牙语
import de from './de'        // 德语
import fr from './fr'        // 法语
import pt from './pt'        // 葡萄牙语
import ko from './ko'        // 韩语
import eo from './eo'        // 世界语

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
]

// 语言 code → 语言包 的映射（code 与 LANGUAGES 保持一致）
const messages = {
  'zh-CN': zhCN,
  'zh-TW': zhTW,
  en,
  ja,
  ru,
  es,
  de,
  fr,
  pt,
  ko,
  eo,
}

// 合法性校验：非法 locale 回退到简体中文
function normalizeLocale(code) {
  return LANGUAGES.some((l) => l.code === code) ? code : 'zh-CN'
}

// 创建 i18n 实例：启动语言取用户设置，缺失 key 回退简体中文
const i18n = createI18n({
  legacy: false, // 使用 Composition API 模式
  globalInjection: true, // 模板中可直接使用 $t
  locale: normalizeLocale(settings.locale),
  fallbackLocale: 'zh-CN', // 缺失的键回退到简体中文
  messages,
})

// --- 动作说明：切换界面语言（设置页语言下拉调用） ---
/**
 * 切换界面语言
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
}

// 启动时同步一次 html lang（让浏览器/翻译器一开始就识别当前语言）
if (typeof document !== 'undefined') {
  document.documentElement.setAttribute('lang', i18n.global.locale.value)
}

// --- 对外导出：i18n 实例（main.js 里 app.use(i18n) 安装） ---
export default i18n
