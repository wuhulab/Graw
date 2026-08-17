// 统一系统指标共享状态（连接池优化）
//
// 首页三张卡片（概览 / 实时监控 / 系统信息）原本各自用 HTTP 轮询
// （overview 2s、network/diskio 2s、info 5s），会同时保持多条请求。
// 此处收敛为「单条 WebSocket + 单一生产者」：所有订阅者共享同一条
// /api/system/ws 连接与同一份实时数据，互不重复请求。
//
// 用法：
//   import { systemState, startMetrics, stopMetrics } from '../store/systemMetrics'
//   模板/计算属性直接读取 reactive 的 systemState 即可获得响应式更新。
import { reactive } from 'vue'
import { auth } from './auth'

// 首页共享的实时指标状态
export const systemState = reactive({
  connected: false,
  overview: {
    cpu: 0,
    memory: { percent: 0, total: 0, used: 0, available: 0 },
    storage: { percent: 0, total: 0, used: 0, free: 0 },
    load: { percent: 0, load1: 0, load5: 0, load15: 0 },
  },
  network: { upload: 0, download: 0, total_sent: 0, total_recv: 0, timestamp: 0 },
  diskio: { read: 0, write: 0, timestamp: 0 },
  info: {
    hostname: '-', system: '-', release: '', machine: '-',
    cpu_count: 0, cpu_count_physical: 0, python_version: '-',
    boot_time: '', uptime_seconds: 0,
  },
})

let ws = null
let retryTimer = null
let retryCount = 0
let running = false

// 将后端推送的合并指标写入响应式状态
function applyMetrics(data) {
  if (!data) return
  if (data.overview) Object.assign(systemState.overview, data.overview)
  if (data.network) Object.assign(systemState.network, data.network)
  if (data.diskio) Object.assign(systemState.diskio, data.diskio)
  if (data.info) Object.assign(systemState.info, data.info)
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

// 建立单条 WebSocket 连接（连接池：全局仅此一条）
function connect() {
  if (!running || ws) return
  try {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/api/system/ws?token=${encodeURIComponent(auth.token || '')}`)
  } catch (e) {
    scheduleRetry()
    return
  }
  ws.onopen = () => {
    systemState.connected = true
    retryCount = 0
  }
  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (msg.type === 'metrics') applyMetrics(msg.data)
    } catch (e) { /* 忽略异常帧 */ }
  }
  ws.onclose = () => {
    systemState.connected = false
    ws = null
    scheduleRetry()
  }
  ws.onerror = () => {
    try { ws && ws.close() } catch (e) { /* ignore */ }
  }
}

// 开始共享指标订阅（幂等，全局只建一条连接）
export function startMetrics() {
  if (running) return
  running = true
  connect()
}

// 停止共享指标订阅（退出登录时调用）
export function stopMetrics() {
  running = false
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  if (ws) {
    try { ws.close() } catch (e) { /* ignore */ }
    ws = null
  }
  systemState.connected = false
}