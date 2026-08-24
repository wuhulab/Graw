// ShunX 网页防篡改 - 前端共享状态
//
// 职责：
//  1. 建立单条 /api/tamper/ws 连接（登录后启用，退出登录时关闭），
//     接收后端推送的「篡改告警」与「开关状态变化」。
//  2. 维护告警队列（tamperState.alerts）供 TamperAlert.vue 弹窗展示；
//     维护全局开关状态供管理窗口与告警按钮使用。
//  3. 提供「10 分钟内关闭防篡改」「完全关闭防篡改」「重新启用」等动作。
//
// 用法：
//   import { tamperState, startTamper, stopTamper } from '../store/tamper'
//   模板中直接读取 reactive 的 tamperState 即可获得响应式更新。
import { reactive } from 'vue'
import { auth, isAdmin } from './auth'
import { tamperApi } from '../api'

// 防篡改全局共享状态
export const tamperState = reactive({
  connected: false,
  enabled: true,            // 是否处于防护开启状态
  temporarilyDisabled: false, // 是否处于「临时关闭（到期自动恢复）」状态
  disabledUntil: null,      // 临时关闭截止时间（ISO）
  alerts: [],               // 待展示的篡改告警队列（最新在前）
  lastEvent: null,          // 最近一次篡改事件
})

let ws = null
let retryTimer = null
let retryCount = 0
let running = false

// 应用后端推送的全局状态
function applyStatus(data) {
  if (!data) return
  tamperState.enabled = !!data.enabled
  tamperState.temporarilyDisabled = !!data.temporarily_disabled
  tamperState.disabledUntil = data.disabled_until || null
  tamperState.lastEvent = data.last_event || tamperState.lastEvent
}

// 把后端推送的篡改事件加入告警队列（按 id 去重，最新在前）
function pushAlert(ev) {
  if (!ev || !ev.id) return
  tamperState.alerts = [
    ev,
    ...tamperState.alerts.filter((a) => a.id !== ev.id),
  ].slice(0, 20) // 最多保留 20 条，避免无限堆积
}

// 关闭（移除）某条告警，展示下一条
export function dismissAlert(id) {
  tamperState.alerts = tamperState.alerts.filter((a) => a.id !== id)
}

// 退避重连：2s → 3s → 4.5s … 上限 30s，避免断线时高频重连
function scheduleRetry() {
  if (!running || retryTimer) return
  retryCount += 1
  const delay = Math.min(30000, 2000 * Math.pow(1.5, Math.min(retryCount - 1, 6)))
  retryTimer = setTimeout(() => {
    retryTimer = null
    connect()
  }, delay)
}

// 建立单条告警 WebSocket（全局仅此一条）
function connect() {
  if (!running || ws) return
  try {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/api/tamper/ws?token=${encodeURIComponent(auth.token || '')}`)
  } catch (e) {
    scheduleRetry()
    return
  }
  ws.onopen = () => {
    tamperState.connected = true
    retryCount = 0
  }
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'status') applyStatus(msg.data)
      else if (msg.type === 'tamper_alert') pushAlert(msg.data)
    } catch (e) { /* 忽略异常帧 */ }
  }
  ws.onclose = () => {
    tamperState.connected = false
    ws = null
    scheduleRetry()
  }
  ws.onerror = () => {
    try { ws && ws.close() } catch (e) { /* ignore */ }
  }
}

// 开始订阅防篡改告警（幂等，登录后调用）
export function startTamper() {
  if (running) return
  running = true
  connect()
}

// 停止订阅（退出登录时调用）
export function stopTamper() {
  running = false
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  if (ws) {
    try { ws.close() } catch (e) { /* ignore */ }
    ws = null
  }
  tamperState.connected = false
}

// 刷新全局状态（管理窗口打开时同步一次，避免等待 WS 推送）
export async function refreshTamperStatus() {
  try {
    const data = await tamperApi.status()
    applyStatus(data)
    return data
  } catch (e) {
    return null
  }
}

// 「10 分钟内关闭防篡改」：临时关闭，到期自动恢复
export async function disableForMinutes(minutes = 10) {
  const data = await tamperApi.disable(minutes, 'temporary')
  applyStatus(data)
  return data
}

// 「高级：完全关闭防篡改」：需手动重新开启（前端已弹窗警告）
export async function disableManual() {
  const data = await tamperApi.disable(null, 'manual')
  applyStatus(data)
  return data
}

// 重新启用防篡改
export async function enableProtection() {
  const data = await tamperApi.enable()
  applyStatus(data)
  return data
}

// 当前登录用户是否可操作「关闭/启用」（仅管理员）
export function canOperate() {
  return isAdmin()
}
