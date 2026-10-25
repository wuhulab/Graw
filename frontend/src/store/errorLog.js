/* errorLog.js — 全局错误日志单例（捕获前端异常 / 接口错误 / 未处理 Promise）。

  业务背景：面板功能多、接口多，出错时用户往往只看到「没反应」或零散提示。
  这里把可捕获的错误集中到一个内存日志里，由 ErrorLogPanel.vue 以浮窗展示：
  可关闭、可复制全部日志、可跳转 GitHub Issues 提交。

  设计约束：
    - 只存内存，不落 localStorage，避免把 token / 请求体等敏感信息长期留在浏览器；
    - 相同错误 3 秒内去重并累计次数，防止轮询接口连续失败时刷屏；
    - 最多保留 MAX_ITEMS 条，超出丢弃最旧的；
    - 所有导出函数内部吞异常，错误收集本身不能影响面板运行。
*/
import { reactive } from 'vue'

const MAX_ITEMS = 80              // 日志上限：够排查问题，又不会占满内存
const DEDUPE_WINDOW_MS = 3000     // 同一错误的去重窗口（毫秒）
const MAX_DETAIL_LEN = 4000       // 单条详情最大长度，避免超长堆栈撑爆界面

// 对外暴露的错误日志状态：visible 控制浮窗显隐，items 为新→旧排列
export const errorLog = reactive({
  visible: false,
  items: [],
})

let seq = 0   // 自增 id，供列表 key 与去重定位

function nowText() {
  const d = new Date()
  const p = (n, len = 2) => String(n).padStart(len, '0')
  return `${p(d.getFullYear(), 4)}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`
}

function clip(text, max = MAX_DETAIL_LEN) {
  const s = String(text ?? '')
  return s.length > max ? s.slice(0, max) + '…' : s
}

/**
 * 记录一条错误并弹出日志浮窗。
 * @param {string} source 来源：api / vue / window / promise / fetch / terminal 等
 * @param {string} message 主错误信息（界面列表直接展示）
 * @param {object} extra 附加信息：detail / url / status / method 等
 */
export function pushError(source, message, extra = {}) {
  try {
    const msg = clip(String(message || '').trim(), 1000)
    if (!msg) return
    const ts = Date.now()
    // 去重：最近几条里若有同来源同文案，只累计次数，避免刷屏
    const dup = errorLog.items.find(
      (it) => it.source === source && it.message === msg && ts - it.lastAt < DEDUPE_WINDOW_MS
    )
    if (dup) {
      dup.count += 1
      dup.lastAt = ts
      dup.time = nowText()
      dup.detail = clip(extra.detail || dup.detail)
      errorLog.visible = true
      return
    }
    errorLog.items.unshift({
      id: ++seq,
      time: nowText(),
      lastAt: ts,
      source: String(source || 'unknown'),
      message: msg,
      detail: clip(extra.detail || ''),
      url: clip(extra.url || '', 500),
      method: clip(extra.method || '', 20),
      status: extra.status || 0,
      count: 1,
    })
    if (errorLog.items.length > MAX_ITEMS) errorLog.items.length = MAX_ITEMS
    errorLog.visible = true
  } catch (e) {
    // 错误收集器自身失败时静默：不能因为记录日志而打断业务
  }
}

// 关闭浮窗（保留日志内容，下次有新错误时仍会重新弹出）
export function dismissErrorLog() {
  errorLog.visible = false
}

// 清空全部日志并关闭浮窗
export function clearErrorLog() {
  errorLog.items.splice(0, errorLog.items.length)
  errorLog.visible = false
}

// 把当前日志整理为可复制 / 可提交 Issue 的纯文本
export function errorLogText() {
  const lines = []
  lines.push('Graw 错误日志')
  lines.push(`导出时间: ${nowText()}`)
  try {
    lines.push(`页面: ${location.origin}${location.pathname}`)
  } catch (e) { /* 非浏览器环境忽略 */ }
  try {
    lines.push(`浏览器: ${navigator.userAgent}`)
  } catch (e) { /* 非浏览器环境忽略 */ }
  lines.push(`面板版本: ${window.__GRAW_VERSION__ || '未知'}`)
  lines.push('')
  if (!errorLog.items.length) {
    lines.push('（暂无错误）')
    return lines.join('\n')
  }
  errorLog.items.forEach((it, i) => {
    lines.push(`#${i + 1} [${it.time}] [${it.source}] ${it.message}${it.count > 1 ? `（重复 ${it.count} 次）` : ''}`)
    if (it.method || it.url) lines.push(`    请求: ${it.method || ''} ${it.url || ''}`.trimEnd())
    if (it.status) lines.push(`    状态码: ${it.status}`)
    if (it.detail) lines.push(`    详情: ${it.detail}`)
    lines.push('')
  })
  return lines.join('\n').trimEnd()
}

// 生成 GitHub 新建 Issue 的地址。
// 注意：不要把完整日志塞进 URL（中文编码后会很快超过浏览器/服务端长度限制），
// 这里只预填一条摘要；完整日志已由「提交 Issue」按钮写入剪贴板，粘贴即可。
export function issueURL() {
  const repo = 'https://github.com/wuhulab/Graw/issues/new'
  const title = encodeURIComponent('[Bug] 面板错误日志')
  const latest = errorLog.items[0]
  let body = '请在下方粘贴错误日志（已复制到剪贴板）。\n'
  try {
    body += `\n页面: ${location.origin}${location.pathname}`
    body += `\n面板版本: ${window.__GRAW_VERSION__ || '未知'}`
  } catch (e) { /* 非浏览器环境忽略 */ }
  if (latest) body += `\n最近错误: [${latest.source}] ${latest.message}`
  // 控制 URL 长度：中文经 encodeURIComponent 后每个字约 9 字符，500 字足够安全
  if (body.length > 500) body = body.slice(0, 500) + '…'
  return `${repo}?title=${title}&body=${encodeURIComponent(body)}`
}
