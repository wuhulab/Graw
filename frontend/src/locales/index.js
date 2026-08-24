import { createI18n } from 'vue-i18n'
import { settings } from '../store/settings'
import zhCN from './zh-CN'
import zhTW from './zh-TW'
import en from './en'
import ja from './ja'
import ru from './ru'
import es from './es'
import de from './de'
import fr from './fr'
import pt from './pt'
import ko from './ko'
import eo from './eo'

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

const i18n = createI18n({
  legacy: false, // 使用 Composition API 模式
  globalInjection: true, // 模板中可直接使用 $t
  locale: normalizeLocale(settings.locale),
  fallbackLocale: 'zh-CN', // 缺失的键回退到简体中文
  messages,
})

/**
 * 切换界面语言
 * @param {string} code - 语言代码（见 LANGUAGES）
 */
export function setLocale(code) {
  const target = normalizeLocale(code)
  i18n.global.locale.value = target
  settings.locale = target
  // 同步 html 的 lang 属性，利于无障碍与浏览器翻译
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('lang', target)
  }
}

// 启动时同步一次 html lang
if (typeof document !== 'undefined') {
  document.documentElement.setAttribute('lang', i18n.global.locale.value)
}

export default i18n
