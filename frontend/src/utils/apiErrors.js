/**
 * apiErrors.js - 统一 API 错误信息格式化
 *
 * 将 axios / fetch 抛出的错误转换为用户可读的提示文案，
 * 解决此前直接展示原始 e.message（如 "timeout of 60000ms exceeded"、
 * "Network Error"、"Request failed with status code 500"）导致
 * 用户无法理解问题所在的问题。
 *
 * 分类优先级：
 *  1. 后端明确返回的 detail（FastAPI 规范，兼容字符串/数组/对象三种形态）
 *  2. 请求超时（axios ECONNABORTED / fetch AbortError / message 含 timeout）
 *  3. 网络层失败（无 HTTP 响应：Network Error / Failed to fetch 等）
 *  4. HTTP 状态码语义（401/403/404/413/5xx）
 *  5. 兜底：原始错误信息
 *
 * 用法：
 *   import { getApiErrorMessage } from '../utils/apiErrors'
 *   catch (e) { alert(t('files.accessFailed', { error: getApiErrorMessage(e, t) })) }
 */

/** 从后端响应中提取 FastAPI 风格的 detail 字段（字符串 / 数组 / 对象）。 */
export function extractApiDetail(err) {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail.trim()
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI 422 校验错误：[{ loc, msg, type }, ...]
    return detail.map(d => (d && typeof d === 'object' && d.msg) ? d.msg : JSON.stringify(d)).join('；')
  }
  if (detail && typeof detail === 'object') {
    return detail.msg || detail.message || detail.error || JSON.stringify(detail)
  }
  return ''
}

/**
 * 把任意错误对象转换为友好的提示文案。
 * @param {*} err axios 错误 / fetch 错误 / 手动 throw 的 Error
 * @param {Function} t vue-i18n 的 t 函数（必须传，用于多语言文案）
 * @returns {string}
 */
export function getApiErrorMessage(err, t) {
  if (!err) return ''

  // 1. 后端明确返回的 detail：优先展示（具体原因比状态码分类更准确）。
  //    FastAPI 默认 500 的 "Internal Server Error" 属于占位文案，跳过按状态码提示。
  const detail = extractApiDetail(err)
  if (detail && detail !== 'Internal Server Error') return detail

  const msg = err.message || ''
  // 2. 请求超时：axios 超时（ECONNABORTED / "timeout of 60000ms exceeded"）、
  //    fetch 中止（AbortError / "The user aborted a request."）
  const isTimeout =
    err.code === 'ECONNABORTED' ||
    err.name === 'AbortError' ||
    /timeout of \d+ms exceeded/i.test(msg) ||
    msg === 'The user aborted a request.'
  if (isTimeout) {
    const seconds = Math.round((err.config?.timeout || 60000) / 1000)
    return t('files.errTimeout', { seconds })
  }

  // 3. 网络层失败：没有任何 HTTP 响应（后端未启动 / 网络中断 / 被代理拦截）
  if (!err.response) {
    return t('files.errNetwork')
  }

  // 4. 按 HTTP 状态码给出语义提示（detail 为空时）
  const status = err.response.status
  if (status === 401) return t('files.errUnauthorized')
  if (status === 403) return t('files.errForbidden')
  if (status === 404) return t('files.errNotFound')
  if (status === 413) return t('files.errTooLarge')
  if (status >= 500) return t('files.errServer', { status })

  // 5. 兜底：保留原始信息
  return msg || t('files.errServer', { status })
}
