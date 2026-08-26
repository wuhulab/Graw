// 统一系统指标共享状态（连接池优化）
//
// 首页三张卡片（概览 / 实时监控 / 系统信息）原本各自用 HTTP 轮询
// （overview 2s、network/diskio 2s、info 5s），会同时保持多条请求。
// 此处收敛为「单条 WebSocket + 单一生产者」：所有订阅者共享同一条
// /api/system/ws 连接与同一份实时数据，互不重复请求。
//
// 节点联动：
//   - 全局当前主机为「已配置 Agent 的 SSH 子节点」时，连接携带 ?node=<id>，
//     后端会把浏览器 WS 桥接到子节点自身的 /api/system/ws（原生 psutil 指标）。
//   - 全局主机切换后自动重连到新节点。
//
// 稳定性（本轮修复）：
//   - 收到任意服务端帧（metrics / unavailable）立即刷新 lastSeen，不再依赖
//     300ms 节流计时器——页面在后台时浏览器会节流定时器，此前会造成「假过期」。
//   - 心跳帧每 25s 发送一次，防止闲置连接被反代空读超时静默掐断。
//   - 切后台不做过期判定；回前台立即补刷最新帧，若连接疑似失效则立刻重建。
//
// 用法：
//   import { systemState, startMetrics, stopMetrics } from '../store/systemMetrics'
//   模板/计算属性直接读取 reactive 的 systemState 即可获得响应式更新。
import { reactive, watch } from 'vue'
import { auth } from './auth'
import { nodes } from './nodes'

// 首页共享的实时指标状态
export const systemState = reactive({
  connected: false,
  // 后端广播的采集不可用原因（非空即展示降级提示；收到新指标帧后自动清空）
  unavailable: '',
  // 最近一次收到服务端帧的时间（ms）；0 表示尚未收到过任何帧
  lastSeen: 0,
  // 连接正常但长时间收不到数据帧（stale 判定），由看门狗定时器维护
  stale: false,
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

// 超过该时长（ms）未收到服务端帧即判定数据已过期（约 6 个 2s 周期）
const STALE_AFTER = 12000
// 心跳间隔（ms）：低于常见反代（nginx）的默认空读超时 60s，保证连接活性
const HEARTBEAT_INTERVAL = 25000

let ws = null
let retryTimer = null
let watchdogTimer = null
let heartbeatTimer = null
let retryCount = 0
let running = false
// 手动重连时置位：该次 onclose 不再触发退避重连（立即 connect 已接管）
let suppressRetryOnce = false

// ---- 指标应用节流（合并积压，回到前台不「跳数据」） ----
// 标签页在后台时，浏览器会节流定时器并积压 WebSocket 帧；回到前台时若逐条
// 应用这些积压帧，概览/实时监控图表会在瞬间连续重绘（数据跳得很快，体验差）。
// 因此：后台期间不写系统状态，仅保存「最新一帧」；回前台立即应用一次最新值，
// 前台运行期间再以 300ms 节流合并应用，避免同一时刻多帧连续触发。
let applyTimer = null
let latestFrame = null
let docVisible = typeof document !== 'undefined' ? document.visibilityState !== 'hidden' : true

// 应用最近一帧（若存在），用于节流与回前台补刷
function flushLatestFrame() {
  if (latestFrame) {
    applyMetrics(latestFrame)
    latestFrame = null
  }
}

// 后台期间只记录最新帧、不应用；前台用 300ms 合并窗口，窗口内多帧取最后一帧
function scheduleApply(data) {
  latestFrame = data
  if (applyTimer) return
  applyTimer = setTimeout(() => {
    applyTimer = null
    flushLatestFrame()
  }, 300)
}

// 页面可见性切换：回前台立即应用最新帧；连接疑似失效则立刻重建
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    docVisible = document.visibilityState !== 'hidden'
    if (!docVisible) return
    if (applyTimer) { clearTimeout(applyTimer); applyTimer = null }
    flushLatestFrame()
    // 后台期间定时器被浏览器节流，连接也可能在休眠/切后台时被系统掐断。
    // 回前台时若 socket 不健康或长时间无帧，立即重建连接，而不是等退避计时器。
    const alive = ws && ws.readyState === 1 &&
      systemState.lastSeen !== 0 &&
      (Date.now() - systemState.lastSeen) < STALE_AFTER
    if (!alive) forceReconnect()
  })
}

// 将后端推送的合并指标写入响应式状态
function applyMetrics(data) {
  if (!data) return
  if (data.overview) Object.assign(systemState.overview, data.overview)
  if (data.network) Object.assign(systemState.network, data.network)
  if (data.diskio) Object.assign(systemState.diskio, data.diskio)
  if (data.info) Object.assign(systemState.info, data.info)
}

// 连接正常且在可见状态下周期性检查数据新鲜度
function tickWatchdog() {
  if (!docVisible) return
  systemState.stale = systemState.connected && systemState.lastSeen !== 0 &&
    (Date.now() - systemState.lastSeen) > STALE_AFTER
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

// 立即重建连接（节点切换 / 回前台发现连接失效时）：取消退避计时器并重连
function forceReconnect() {
  if (!running) return
  suppressRetryOnce = true
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  if (ws) {
    try { ws.close() } catch (e) { /* ignore */ }
  }
  ws = null
  connect()
}

// 计算当前应连接的指标节点：全局当前主机为「已配置 Agent 的 SSH 子节点」时
// 携带 node 参数（后端桥接到子节点原生 WS）；本地 / 未配置 agent 的远程节点
// 不带参数（主面板本地生产者经 SSH 脚本采集或直接 psutil）。
function targetNodeId() {
  const cur = nodes.list.find((n) => n.id === nodes.currentId)
  return cur && cur.type === 'ssh' && cur.agent_enabled ? cur.id : ''
}

// 组装 WS 地址：token 鉴权 + 子节点桥接参数
function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  let url = `${proto}://${location.host}/api/system/ws?token=${encodeURIComponent(auth.token || '')}`
  const nodeId = targetNodeId()
  if (nodeId) url += `&node=${encodeURIComponent(nodeId)}`
  return url
}

// 全局主机切换后，桌面卡片立即切到对应节点的指标流
watch(
  () => nodes.currentId,
  () => {
    if (running) forceReconnect()
  }
)

// 建立单条 WebSocket 连接（连接池：全局仅此一条）
function connect() {
  if (!running || ws) return
  // 新连接接管后，旧连接残留的 suppress 标记不再有意义（旧 onclose 会因
  // ws !== sock 提前返回，不会消费它），统一在此重置
  suppressRetryOnce = false
  const sock = new WebSocket(wsUrl())
  ws = sock
  sock.onopen = () => {
    if (ws !== sock) return // 已被新连接接管，忽略旧连接回调
    systemState.connected = true
    systemState.stale = false
    systemState.unavailable = ''
    systemState.lastSeen = Date.now()
    retryCount = 0
    // 数据新鲜度看门狗（每 4s；收到帧会同步刷新 lastSeen）
    if (!watchdogTimer) watchdogTimer = setInterval(tickWatchdog, 4000)
    // 心跳：向后端周期性发文本帧，防止反代空读超时掐断连接
    if (!heartbeatTimer) {
      heartbeatTimer = setInterval(() => {
        try {
          if (sock.readyState === 1) sock.send(JSON.stringify({ type: 'ping' }))
        } catch (e) { /* 发送失败忽略，交给 onclose 处理 */ }
      }, HEARTBEAT_INTERVAL)
    }
  }
  sock.onmessage = (ev) => {
    // 收到任意服务端帧立即刷新存活时间——不依赖节流定时器，
    // 后台页面定时器被节流时也能如实反映「连接仍在、数据仍在来」
    systemState.lastSeen = Date.now()
    try {
      const msg = JSON.parse(ev.data)
      if (!msg || typeof msg.type !== 'string') return
      if (msg.type === 'metrics') {
        // 指标帧：清除不可用标记并进入节流更新
        if (systemState.unavailable) systemState.unavailable = ''
        scheduleApply(msg.data)
      } else if (msg.type === 'unavailable') {
        // 当前管理节点采集失败（节点不可达等），展示降级提示
        systemState.unavailable = typeof msg.reason === 'string'
          ? msg.reason
          : '监控数据暂不可用'
      }
    } catch (e) { /* 忽略异常帧 */ }
  }
  sock.onclose = () => {
    if (ws !== sock) return // 已有新连接接管
    systemState.connected = false
    systemState.stale = false
    ws = null
    if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null }
    if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null }
    if (suppressRetryOnce) suppressRetryOnce = false
    else scheduleRetry()
  }
  sock.onerror = () => {
    try { sock.close() } catch (e) { /* ignore */ }
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
  suppressRetryOnce = false
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null }
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null }
  if (ws) {
    try { ws.close() } catch (e) { /* ignore */ }
    ws = null
  }
  systemState.connected = false
  systemState.stale = false
  systemState.unavailable = ''
  systemState.lastSeen = 0
}