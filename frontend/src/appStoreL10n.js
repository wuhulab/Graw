// appStoreL10n.js - 应用商店索引本地化辅助函数
//
// 后端 /api/appstore/index 返回的每个应用（app）可能带 translations 字段，
// 其内容由仓库 app-store/apps/<id>/i18n.<locale>.yml 生成，结构形如：
//   translations: {
//     en: { name, description, category, ports: {"5700": "Web UI"}, env: {...} },
//     ja: { ... },
//     eo: { ... }
//   }
// 本模块负责把「当前界面语言 locale」映射到 translations 的子键，
// 并提供统一的字段读取函数：无对应语言翻译时回退到 data.yml 的默认中文内容。
//
// 注意：索引数据来自远程（index_url 可指向任意源），属不可信输入，
// 所有读取都做了类型/空值防御，避免脏数据导致前端崩溃。

// 前端界面语言 → translations 子键 的映射（仅 en/ja/eo 提供翻译，
// 其余语言（zh-TW/ru/es/de/fr/pt/ko）暂未提供应用级翻译，直接回退默认中文）
const LOCALE_TO_TRANS_KEY = {
  en: 'en',
  ja: 'ja',
  eo: 'eo',
}

/**
 * 根据当前界面语言返回 app.translations 中对应的翻译对象（无则返回空对象）。
 * @param {object} app - 索引中的应用条目
 * @param {string} locale - 当前界面语言代码（如 'zh-CN' / 'en' / 'ja'）
 * @returns {object} 对应语言的翻译片段（可能为空对象）
 */
export function appTrans(app, locale) {
  const key = LOCALE_TO_TRANS_KEY[locale]
  const tr = app && typeof app === 'object' && app.translations && typeof app.translations === 'object'
    ? app.translations[key]
    : null
  // 防御：翻译片段必须是纯对象才可用
  return tr && typeof tr === 'object' ? tr : {}
}

/**
 * 读取应用本地化名称（无翻译时返回空串，由调用方回退到默认名称）。
 * @param {object} app - 索引中的应用条目
 * @param {string} locale - 当前界面语言代码
 * @returns {string} 本地化名称（可能为空串）
 */
export function localizedName(app, locale) {
  const name = appTrans(app, locale).name
  return typeof name === 'string' && name.trim() ? name : ''
}

/**
 * 读取应用本地化描述（无翻译时返回空串，由调用方回退到默认描述）。
 * @param {object} app - 索引中的应用条目
 * @param {string} locale - 当前界面语言代码
 * @returns {string} 本地化描述（可能为空串）
 */
export function localizedDescription(app, locale) {
  const desc = appTrans(app, locale).description
  return typeof desc === 'string' && desc.trim() ? desc : ''
}

/**
 * 读取某个容器端口的本地化用途说明（如 "Web UI" / "Web 画面"）。
 * @param {object} app - 索引中的应用条目
 * @param {string|number} container - 容器端口号
 * @param {string} locale - 当前界面语言代码
 * @returns {string} 本地化端口说明（可能为空串）
 */
export function localizedPortLabel(app, container, locale) {
  const ports = appTrans(app, locale).ports
  if (!ports || typeof ports !== 'object') return ''
  const label = ports[String(container)]
  return typeof label === 'string' && label.trim() ? label : ''
}
