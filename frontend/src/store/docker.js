// Docker 共享状态（本地缓存 + 单例轮询）
//
// 目标：
//   1. 打开 Docker 窗口时不再「转圈等很久」——先把上次成功的快照从
//      localStorage 渲染出来，随后后台刷新覆盖。
//   2. 多开多个 Docker 窗口时共用同一个后台轮询（连接池优化），
//      避免每个窗口各自 5s 拉一次容器列表。
// 数据来源为 dockerApi（已携带 Bearer 鉴权）。
import { reactive } from 'vue'
import { dockerApi } from '../api'

const STORAGE_KEY = 'graw_docker_cache'
const POLL_INTERVAL = 8000 // 共享后台轮询周期（毫秒）

function loadCache() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const c = JSON.parse(raw)
    if (!c || typeof c !== 'object') return null
    return c
  } catch (e) {
    return null
  }
}

function saveCache() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      status: state.status,
      containers: state.containers,
      lastUpdated: state.lastUpdated,
    }))
  } catch (e) {
    // 缓存写入失败不影响主流程，仅忽略
  }
}

// 共享的 Docker 状态
export const docker = reactive({
  status: null,          // { available, reason, containers, ... }
  containers: [],        // 容器列表
  loading: false,        // 是否正在拉取
  lastUpdated: 0,        // 最近一次成功时间戳（ms）
  hasCache: false,       // 是否已有本地缓存可即时渲染
})

// 模块加载时先用上次缓存回填，让首次打开即可渲染
const cached = loadCache()
if (cached) {
  docker.status = cached.status ?? null
  docker.containers = Array.isArray(cached.containers) ? cached.containers : []
  docker.lastUpdated = cached.lastUpdated || 0
  docker.hasCache = true
}

let timer = null
let refreshing = false

// 立即刷新一次（去重：并发调用只执行一次）
export async function refresh() {
  if (refreshing) return docker
  refreshing = true
  docker.loading = true
  try {
    const status = await dockerApi.status()
    docker.status = status
    if (status.available) {
      docker.containers = await dockerApi.containers()
    } else {
      docker.containers = []
    }
    docker.lastUpdated = Date.now()
    docker.hasCache = true
    saveCache()
  } catch (e) {
    // 拉取失败时保留上次缓存，仅标记不可用
    docker.status = { available: false, reason: e.message || 'Docker 不可用' }
  } finally {
    docker.loading = false
    refreshing = false
  }
  return docker
}

// 启动共享后台轮询（幂等：全局只有一个定时器）
export function startDocker() {
  if (timer) return
  timer = setInterval(() => refresh(), POLL_INTERVAL)
}

// 停止共享后台轮询
export function stopDocker() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}