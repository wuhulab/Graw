// Docker 共享状态（本地缓存 + 单例轮询）
//
// 目标：
//   1. 打开 Docker 窗口时不再「转圈等很久」——先把上次成功的快照从
//      localStorage 渲染出来，随后后台刷新覆盖。
//   2. 多开多个 Docker 窗口时共用同一个后台轮询（连接池优化），
//      避免每个窗口各自 5s 拉一次容器列表。
// 数据来源为 dockerApi（已携带 Bearer 鉴权）。
import { reactive } from 'vue'      // 让 Docker 状态变化自动驱动窗口/任务栏重渲染
import { dockerApi } from '../api'  // Docker 接口封装（自动携带 Bearer 鉴权）

const STORAGE_KEY = 'graw_docker_cache'  // 上次成功快照的本地缓存键：刷新/重开窗口时先渲染旧数据，避免白屏等待
const POLL_INTERVAL = 8000 // 共享后台轮询周期（毫秒）

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

// --- 对外暴露：全站共享的 Docker 状态（所有 Docker 窗口读同一份，避免各自拉取） ---
export const docker = reactive({
  status: null,          // { available, reason, containers, ... }——Docker 引擎可用性与整体状态
  containers: [],        // 容器列表（仅当引擎可用时才拉取填充）
  loading: false,        // 是否正在拉取
  lastUpdated: 0,        // 最近一次成功时间戳（ms）
  hasCache: false,       // 是否已有本地缓存可即时渲染
})

// 模块加载时先用上次缓存回填，让首次打开即可渲染
const cached = loadCache()
if (cached) {
  docker.status = cached.status ?? null                        // 有缓存就先把引擎状态还回去
  docker.containers = Array.isArray(cached.containers) ? cached.containers : []   // 容器列表同样回填（类型兜底）
  docker.lastUpdated = cached.lastUpdated || 0
  docker.hasCache = true                                       // 标记有缓存：窗口可先渲染旧快照
}

let timer = null        // 共享后台轮询的定时器句柄（全局仅一个）
let refreshing = false  // 拉取进行中标记，用于并发去重

// --- 动作说明：立即刷新一次 Docker 状态（并发调用只执行一次） ---
export async function refresh() {
  if (refreshing) return docker   // 正在拉取时直接复用本次结果，避免多个窗口/定时器并发请求
  refreshing = true
  docker.loading = true
  try {
    const status = await dockerApi.status()
    docker.status = status
    if (status.available) {
      docker.containers = await dockerApi.containers()   // 引擎可用才去拉容器列表
    } else {
      docker.containers = []                              // 引擎不可用（未装/没权限）时清空列表
    }
    docker.lastUpdated = Date.now()   // 记录本次成功时间，供「数据新旧」判断
    docker.hasCache = true
    saveCache()                       // 成功后落缓存，供下次打开秒渲染
  } catch (e) {
    // 拉取失败时保留上次缓存，仅标记不可用
    docker.status = { available: false, reason: e.message || 'Docker 不可用' }
  } finally {
    docker.loading = false
    refreshing = false
  }
  return docker
}

// --- 动作说明：启动共享后台轮询（幂等，全局只有一个定时器） ---
export function startDocker() {
  if (timer) return            // 已启动过就直接返回：多个 Docker 窗口同时打开也不会重复起定时器
  timer = setInterval(() => refresh(), POLL_INTERVAL)
}

// --- 动作说明：停止共享后台轮询 ---
export function stopDocker() {
  if (timer) {
    clearInterval(timer)
    timer = null               // 句柄置空，保证后续再次 start 能重新起一个干净的定时器
  }
}