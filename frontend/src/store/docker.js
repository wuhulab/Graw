// Docker 共享状态（后端实时推送 + 本地快照缓存）
//
// 设计变更（本轮修复）：
//   此前这里用 setInterval 每 8s 调一次 /api/docker/status + /api/docker/containers
//   （App.vue 登录即 startDocker + 立即 refresh），既在面板启动后持续产生无谓请求，
//   又让「打开 Docker 窗口」必须等一次 HTTP 往返才有数据。
//   现改为：后端单生产者协程按周期采集并通过 /api/docker/ws 推送，前端只做订阅：
//     1. 打开/登录时建立「单条 WebSocket」（多窗口共享，不再各自请求）；
//     2. 后端连上即回放最近一次快照 → 窗口打开即刻渲染，无需等待；
//     3. 容器操作后调用 refresh() 只发一个刷新帧，由后端立即重采并推送（不新增 HTTP 轮询）；
//     4. 本地 localStorage 快照仅用于「首帧前先渲染旧数据」，避免白屏。
//
// 数据来源：WebSocket /api/docker/ws（?token= 鉴权，与 dockerApi 的 Bearer 等价）。
import { reactive, watch } from 'vue'   // 响应式 Docker 状态 + 监听当前主机切换以重连
import { auth } from './auth'           // 取 token 拼进 WS 地址（WebSocket 无法带请求头）
import { nodes } from './nodes'         // 当前管理主机（多节点：订阅对应节点的 Docker 数据）

const STORAGE_KEY = 'graw_docker_cache'  // 上次成功快照的本地缓存键：刷新页面时先渲染旧数据，避免白屏等待
const REFRESH_TIMEOUT = 8000             // refresh() 的兜底超时（毫秒）：后端异常时也不能让调用方永久悬挂
const HEARTBEAT_INTERVAL = 25000         // 心跳间隔（毫秒）：低于常见反代默认空读超时 60s，保持连接活性
const STALE_AFTER = 20000                // 超过该时长（毫秒）未收到推送即判定数据过期（约 3 个推送周期）

// 读取上次缓存快照；读不到 / 格式损坏时返回 null，由调用方走「首次加载」逻辑
function loadCache() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null              // 从未缓存过：直接视为无缓存
    const c = JSON.parse(raw)
    if (!c || typeof c !== 'object') return null   // 缓存内容异常：宁可不用，也不能把脏数据灌进状态
    return c
  } catch (e) {
    return null                         // 解析异常（存储被改坏等）：按无缓存处理
  }
}

// 把当前 Docker 快照写入本地缓存（供下次启动秒开）
// 修复：此前这里误写成未定义的 state.*（变量早已重命名为 docker），异常被 catch 静默吞掉，
// 导致「首帧优先渲染旧快照」的本地缓存实际上从未写入。
function saveCache() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      status: docker.status,
      containers: docker.containers,
      lastUpdated: docker.lastUpdated,
    }))
  } catch (e) {
    // 缓存写入失败（隐私模式 / 配额超限）不影响主流程，仅忽略
  }
}

// --- 对外暴露：全站共享的 Docker 状态（所有 Docker 窗口读同一份，避免各自订阅） ---
export const docker = reactive({
  status: null,          // { available, reason, containers, ... }——Docker 引擎可用性与整体状态
  containers: [],        // 容器列表（仅当引擎可用时才由后端填充）
  loading: false,        // 是否正在等待新数据（打开窗口 / 主动刷新期间为 true）
  lastUpdated: 0,        // 最近一次成功推送的时间戳（ms）
  hasCache: false,       // 是否已有本地缓存可即时渲染
  connected: false,      // WebSocket 是否已连上后端
  stale: false,          // 已连接但长时间收不到推送（后端采集异常 / 链路问题）
})

// 模块加载时先用上次缓存回填，让首次打开即可渲染
const cached = loadCache()
if (cached) {
  docker.status = cached.status ?? null                        // 有缓存就先把引擎状态还回去
  docker.containers = Array.isArray(cached.containers) ? cached.containers : []   // 容器列表同样回填（类型兜底）
  docker.lastUpdated = cached.lastUpdated || 0
  docker.hasCache = true                                       // 标记有缓存：窗口可先渲染旧快照
}

let ws = null                  // 共享 WebSocket 连接（全局仅一条）
let running = false            // 是否处于「已订阅」状态（登录后为 true，退出登录置 false）
let retryTimer = null          // 断线重连计时器
let heartbeatTimer = null      // 心跳计时器
let watchdogTimer = null       // 数据新鲜度看门狗
let retryCount = 0             // 连续重连次数（用于退避）
let suppressRetryOnce = false  // 手动重连时置位：该次 onclose 不再触发退避重连
let refreshWaiters = []        // refresh() 产生的等待者：收到下一帧推送时统一唤醒
let staleTimer = null          // 打开窗口后若迟迟没有首帧，用兜底计时器解除 loading 假死

// 当前应订阅的节点：全局当前管理主机（空则由后端回落到当前节点）。
// Docker 是「跟随主机」的功能：主机切换后需重连到新节点的数据源。
function targetNodeId() {
  return nodes.currentId || ''
}

// 组装 WS 地址：token 鉴权 + 目标节点
function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  let url = `${proto}://${location.host}/api/docker/ws?token=${encodeURIComponent(auth.token || '')}`
  const nodeId = targetNodeId()
  if (nodeId) url += `&node=${encodeURIComponent(nodeId)}`
  return url
}

// 统一唤醒 refresh() 的等待者（数据到达 / 断线 / 超时都会触发）
function settleRefreshWaiters() {
  const list = refreshWaiters
  refreshWaiters = []
  for (const done of list) {
    try { done() } catch (e) { /* 单个等待者异常不影响其它调用方 */ }
  }
}

// 把后端推送的快照写入响应式状态并落缓存
function applySnapshot(data) {
  if (!data || typeof data !== 'object') return
  if (data.status) docker.status = data.status
  docker.containers = Array.isArray(data.containers) ? data.containers : []
  docker.lastUpdated = data.ts || Date.now()
  docker.hasCache = true
  docker.loading = false
  docker.stale = false
  saveCache()
  settleRefreshWaiters()   // 数据已到：唤醒所有 await refresh() 的调用方
}

// 断线清理：停掉与连接相关的计时器
function clearConnTimers() {
  if (heartbeatTimer) { clearInterval(heartbeatTimer); heartbeatTimer = null }
  if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null }
  if (staleTimer) { clearTimeout(staleTimer); staleTimer = null }
}

// 退避重连：2s → 3s → 4.5s … 上限 30s，避免后端宕机时高频重连
function scheduleRetry() {
  if (!running || retryTimer) return
  retryCount += 1
  const delay = Math.min(30000, 2000 * Math.pow(1.5, Math.min(retryCount - 1, 6)))
  retryTimer = setTimeout(() => {
    retryTimer = null
    connect()
  }, delay)
}

// 立即重建连接（主机切换 / 回前台发现连接失效时）：取消退避计时器并重连
function forceReconnect() {
  if (!running) return
  suppressRetryOnce = true
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  if (ws) {
    try { ws.close() } catch (e) { /* 关闭失败忽略：下方新建连接会接管 */ }
  }
  ws = null
  connect()
}

// 全局主机切换后，Docker 数据源随之切换（不同主机的容器列表互不相同）
watch(
  () => nodes.currentId,
  () => {
    if (running) forceReconnect()
  }
)

// 建立单条 WebSocket 连接（连接池：全局仅此一条，多窗口共享）
function connect() {
  if (!running || ws) return
  // 新连接接管后，旧连接残留的 suppress 标记不再有意义，统一在此重置
  suppressRetryOnce = false
  const sock = new WebSocket(wsUrl())
  ws = sock

  sock.onopen = () => {
    if (ws !== sock) return   // 已被新连接接管，忽略旧连接回调
    docker.connected = true
    docker.stale = false
    retryCount = 0
    // 数据新鲜度看门狗：连接正常但长时间无推送 → 标记过期并立即重连
    if (!watchdogTimer) {
      watchdogTimer = setInterval(() => {
        const stale = docker.connected && docker.lastUpdated !== 0 &&
          (Date.now() - docker.lastUpdated) > STALE_AFTER
        docker.stale = stale
      }, 5000)
    }
    // 心跳：防止反代空读超时静默掐断连接
    if (!heartbeatTimer) {
      heartbeatTimer = setInterval(() => {
        try {
          if (sock.readyState === 1) sock.send(JSON.stringify({ type: 'ping' }))
        } catch (e) { /* 发送失败交给 onclose 处理 */ }
      }, HEARTBEAT_INTERVAL)
    }
  }

  sock.onmessage = (ev) => {
    if (ws !== sock) return
    let msg   // 由下方 JSON.parse 赋值；非法帧已 return，故无需初始值
    try {
      msg = JSON.parse(ev.data)
    } catch (e) {
      return   // 非法帧（非 JSON）：忽略，不影响连接
    }
    if (!msg || typeof msg.type !== 'string') return
    if (msg.type === 'docker') applySnapshot(msg.data)
  }

  sock.onclose = () => {
    if (ws !== sock) return   // 已有新连接接管
    docker.connected = false
    docker.stale = false
    docker.loading = false
    ws = null
    clearConnTimers()
    settleRefreshWaiters()    // 连接断开：不能让 await refresh() 的调用方一直悬挂
    if (suppressRetryOnce) suppressRetryOnce = false
    else scheduleRetry()
  }

  sock.onerror = () => {
    try { sock.close() } catch (e) { /* 忽略：onclose 会走重连流程 */ }
  }
}

// --- 动作说明：开始 Docker 实时订阅（幂等，全局只建一条连接） ---
export function startDocker() {
  if (running) return   // 已订阅：多个 Docker 窗口同时打开也不会重复连接
  running = true
  if (docker.hasCache) docker.loading = false   // 有旧快照可先渲染，不必显示加载态
  connect()
  // 兜底：连接异常时 loading 不能一直亮着（数据到达/断线都会解除，这里防极端情况）
  if (staleTimer) clearTimeout(staleTimer)
  staleTimer = setTimeout(() => { docker.loading = false }, REFRESH_TIMEOUT)
}

// --- 动作说明：停止 Docker 实时订阅（退出登录时调用） ---
export function stopDocker() {
  running = false
  suppressRetryOnce = false
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  clearConnTimers()
  if (ws) {
    try { ws.close() } catch (e) { /* 忽略：连接可能已断开 */ }
    ws = null
  }
  docker.connected = false
  docker.stale = false
  docker.loading = false
  settleRefreshWaiters()
}

// --- 动作说明：请求立即刷新（容器启停/删除等操作后调用） ---
// 语义上等价于旧版「拉一次最新数据」：只向后端发一个刷新帧，由后端立即重采并推送，
// 不会新增任何 HTTP 轮询。返回的 Promise 在「下一帧推送到达 / 断线 / 超时」时兑现，
// 便于调用方 await 后再读取最新状态。
export function refresh() {
  if (!running) return Promise.resolve(docker)   // 未订阅（非管理员/已登出）：直接返回现有状态
  docker.loading = true
  if (!ws || ws.readyState !== 1) {
    // 连接尚未就绪（刚登录/刚重连）：数据稍后会由首帧推送补上。
    // 有本地快照时立即解除加载态（先渲染旧数据），无快照则保留加载态，
    // 避免窗口出现「既无数据也无提示」的空白；startDocker 的兜底计时器
    // 与断线回调都会最终解除它，不会一直亮着。
    if (docker.hasCache) docker.loading = false
    return Promise.resolve(docker)
  }
  return new Promise((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      resolve(docker)
    }
    refreshWaiters.push(finish)
    try {
      ws.send(JSON.stringify({ type: 'refresh' }))
    } catch (e) {
      // 发送失败（连接刚断开）：立即兑现，由重连后的推送补上最新数据
      finish()
      return
    }
    setTimeout(finish, REFRESH_TIMEOUT)   // 兜底超时：后端无响应也不能让调用方永久悬挂
  })
}
