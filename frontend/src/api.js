import axios from 'axios'
import { auth, clearAuth } from './store/auth'

const api = axios.create({
  baseURL: '/api',
  timeout: 60000
})

const dockerHttp = axios.create({
  baseURL: '/api',
  timeout: 60000
})

// 请求拦截：自动附加 Bearer 令牌
function attachToken(config) {
  if (auth.token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
}
api.interceptors.request.use(attachToken)
dockerHttp.interceptors.request.use(attachToken)

// 响应拦截：
// - 401：仅在「曾经登录但 token 失效」时清除登录态并刷新。
//   从未登录（本地无 token）的请求拿到 401 是正常的，不触发刷新，
//   否则未登录页面会陷入无限 reload 循环。
// - 403「必须修改默认密码」：ShunX 强制改密触发 → 自动退出登录，
//   回到登录页走强制改密流程，避免业务接口持续 403 刷屏。
let _loggingOut = false
function forceLogout() {
  if (_loggingOut) return
  _loggingOut = true
  clearAuth()
  // 回到登录页：保留当前路径（已配置安全入口时即入口路径，可直接显示登录表单）
  const p = window.location.pathname
  if (p && p !== '/') {
    window.location.href = p
  } else {
    window.location.reload()
  }
}

function on401(err) {
  if (err?.response?.status === 401 && auth.token) forceLogout()
  return Promise.reject(err)
}

function onDefaultPassword403(err) {
  if (
    err?.response?.status === 403 &&
    err?.response?.data?.detail === '必须修改默认密码后才能使用面板' &&
    auth.token
  ) {
    forceLogout()
  }
  return Promise.reject(err)
}

api.interceptors.response.use(r => r, on401)
dockerHttp.interceptors.response.use(r => r, on401)
api.interceptors.response.use(r => r, onDefaultPassword403)
dockerHttp.interceptors.response.use(r => r, onDefaultPassword403)

export default api

// 面板基础信息（公开接口，无需登录）：状态 + 版本号，供「设置-关于」展示
export const panelApi = {
  health: () => api.get('/health').then(r => r.data)
}

// 面板自身更新：版本检测与一键更新（写操作需管理员）
export const updateApi = {
  status: () => api.get('/update/status').then(r => r.data),
  apply: () => api.post('/update/apply').then(r => r.data)
}

export const authApi = {
  // path 为浏览器地址栏路径，用于 ShunX 安全入口校验
  login: (username, password, path = '') => api.post('/auth/login', { username, password }, { headers: { 'X-ShunX-Entry': path } }).then(r => r.data),
  me: () => api.get('/auth/me').then(r => r.data),
  // token 可选：强制改密场景下尚未写入登录态，显式携带临时 token
  changePassword: (old_password, new_password, token) => api.post('/auth/password', { old_password, new_password }, token ? { headers: { Authorization: `Bearer ${token}` } } : undefined).then(r => r.data),
  listUsers: () => api.get('/auth/users').then(r => r.data),
  createUser: (username, password, role) => api.post('/auth/users', { username, password, role }).then(r => r.data),
  updateUser: (username, body) => api.put(`/auth/users/${username}`, body).then(r => r.data),
  deleteUser: (username) => api.delete(`/auth/users/${username}`).then(r => r.data)
}

export const systemApi = {
  overview: () => api.get('/system/overview').then(r => r.data),
  network: () => api.get('/system/network').then(r => r.data),
  diskio: () => api.get('/system/diskio').then(r => r.data),
  info: () => api.get('/system/info').then(r => r.data)
}

export const dockerApi = {
  status: () => dockerHttp.get('/docker/status').then(r => r.data),
  containers: () => dockerHttp.get('/docker/containers').then(r => r.data),
  images: () => dockerHttp.get('/docker/images').then(r => r.data),
  action: (id, action) => dockerHttp.post(`/docker/containers/${id}/action`, { action }).then(r => r.data),
  logs: (id, tail = 200) => dockerHttp.get(`/docker/containers/${id}/logs`, { params: { tail } }).then(r => r.data),
  // 标星
  toggleStar: (id) => dockerHttp.post(`/docker/containers/${id}/star`).then(r => r.data),
  // 备注笔记
  saveNotes: (id, note) => dockerHttp.post(`/docker/containers/${id}/notes`, { note }).then(r => r.data),
  // 详细信息
  inspect: (id) => dockerHttp.get(`/docker/containers/${id}/inspect`).then(r => r.data),
  // 备份
  backup: (id) => dockerHttp.post(`/docker/containers/${id}/backup`).then(r => r.data),
  // 升级
  upgrade: (id) => dockerHttp.post(`/docker/containers/${id}/upgrade`).then(r => r.data),
  // 制作镜像
  commit: (id, repo = '', tag = 'latest') => dockerHttp.post(`/docker/containers/${id}/commit`, { repo, tag }).then(r => r.data),
  // 引擎配置（镜像加速 / 私有仓库 / iptables / 配置文件）
  config: () => dockerHttp.get('/docker/config').then(r => r.data),
  saveConfig: (body) => dockerHttp.put('/docker/config', body).then(r => r.data),
  saveConfigRaw: (content) => dockerHttp.put('/docker/config/raw', { content }).then(r => r.data),
  // 编排（compose 项目）
  composeProjects: () => dockerHttp.get('/docker/compose/projects').then(r => r.data),
  composeAction: (name, action) => dockerHttp.post(`/docker/compose/${encodeURIComponent(name)}/action`, { action }).then(r => r.data),
  // 镜像删除
  removeImage: (id) => dockerHttp.post(`/docker/images/${id}/remove`).then(r => r.data),
  // 网络
  networks: () => dockerHttp.get('/docker/networks').then(r => r.data),
  removeNetwork: (name) => dockerHttp.post(`/docker/networks/${encodeURIComponent(name)}/remove`).then(r => r.data)
}

export const processApi = {
  list: (sort_by = 'cpu', limit = 200) => api.get('/process/list', { params: { sort_by, limit } }).then(r => r.data),
  kill: (pid, force = false) => api.post(`/process/${pid}/kill`, { force }).then(r => r.data)
}

export const filesApi = {
  list: (path) => api.get('/files/list', { params: path ? { path } : {} }).then(r => r.data),
  roots: () => api.get('/files/roots').then(r => r.data),
  read: (path) => api.get('/files/read', { params: { path } }).then(r => r.data),
  write: (path, content) => api.post('/files/write', { path, content }).then(r => r.data),
  remove: (path) => api.post('/files/delete', { path }).then(r => r.data),
  mkdir: (path) => api.post('/files/mkdir', { path }).then(r => r.data),
  rename: (src, dst) => api.post('/files/rename', { src, dst }).then(r => r.data),
  chmod: (path, mode) => api.post('/files/chmod', { path, mode }).then(r => r.data),
  copy: (src, dst) => api.post('/files/copy', { src, dst }).then(r => r.data),
  compress: (paths, archive, fmt) => api.post('/files/compress', { paths, archive, fmt }).then(r => r.data),
  extract: (archive, dest) => api.post('/files/extract', { archive, dest }).then(r => r.data),
  upload: (formData) => api.post('/files/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}

export const notesApi = {
  get: () => api.get('/notes/').then(r => r.data),
  save: (content) => api.post('/notes/', { content }).then(r => r.data)
}

export const sitesApi = {
  list: () => api.get('/sites/list').then(r => r.data),
  create: (body) => api.post('/sites/create', body).then(r => r.data),
  action: (id, action) => api.post(`/sites/${id}/action`, { action }).then(r => r.data),
  config: (id) => api.get(`/sites/${id}/config`).then(r => r.data),
  update: (id, body) => api.post(`/sites/${id}/update`, body).then(r => r.data),
  delete: (id) => api.post(`/sites/${id}/delete`).then(r => r.data)
}

export const databasesApi = {
  status: () => api.get('/databases/status').then(r => r.data),
  connections: () => api.get('/databases/connections').then(r => r.data),
  createConn: (body) => api.post('/databases/connections', body).then(r => r.data),
  updateConn: (id, body) => api.put(`/databases/connections/${id}`, body).then(r => r.data),
  deleteConn: (id) => api.delete(`/databases/connections/${id}`).then(r => r.data),
  test: (id) => api.post(`/databases/connections/${id}/test`).then(r => r.data),
  listDBs: (id) => api.get(`/databases/connections/${id}/databases`).then(r => r.data),
  query: (id, body) => api.post(`/databases/connections/${id}/query`, body).then(r => r.data),
  createDB: (id, name) => api.post(`/databases/connections/${id}/create-db`, { name }).then(r => r.data),
  deleteDB: (id, name) => api.post(`/databases/connections/${id}/delete-db`, { name }).then(r => r.data)
}

export const cronApi = {
  list: () => api.get('/cron/list').then(r => r.data),
  create: (body) => api.post('/cron/create', body).then(r => r.data),
  update: (id, body) => api.post(`/cron/${id}/update`, body).then(r => r.data),
  delete: (id) => api.post(`/cron/${id}/delete`).then(r => r.data),
  run: (id) => api.post(`/cron/${id}/run`).then(r => r.data)
}

export const firewallApi = {
  status: () => api.get('/firewall/status').then(r => r.data),
  rules: () => api.get('/firewall/rules').then(r => r.data),
  addPort: (body) => api.post('/firewall/port', body).then(r => r.data),
  delPort: (id) => api.delete(`/firewall/port/${id}`).then(r => r.data),
  addIp: (body) => api.post('/firewall/ip', body).then(r => r.data),
  delIp: (id) => api.delete(`/firewall/ip/${id}`).then(r => r.data),
  toggle: (enabled) => api.post('/firewall/toggle', { enabled }).then(r => r.data)
}

export const sslApi = {
  list: () => api.get('/ssl/list').then(r => r.data),
  upload: (formData) => api.post('/ssl/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data),
  letsencrypt: (body) => api.post('/ssl/letsencrypt', body).then(r => r.data),
  delete: (id) => api.post(`/ssl/${id}/delete`).then(r => r.data)
}

export const logsApi = {
  list: () => api.get('/logs/list').then(r => r.data),
  read: (path, tail = 200) => api.get('/logs/read', { params: { path, tail } }).then(r => r.data),
  add: (body) => api.post('/logs/add', body).then(r => r.data),
  clear: (path) => api.post('/logs/clear', { path }).then(r => r.data)
}

export const protectionApi = {
  status: () => api.get('/protection/status').then(r => r.data),
  scanDocker: () => api.get('/protection/docker').then(r => r.data),
  mapDocker: (name) => dockerHttp.post(`/protection/docker/${encodeURIComponent(name)}/map`, {}, { timeout: 300000 }).then(r => r.data),
  scanDbFiles: () => api.get('/protection/db-files').then(r => r.data),
  addBackup: (path, schedule) => api.post('/protection/db-files/backup', { path, schedule }).then(r => r.data),
  batchBackup: (paths) => api.post('/protection/db-files/batch-backup', { paths }).then(r => r.data),
  removeBackup: (path) => api.post('/protection/db-files/unbackup', { path }).then(r => r.data),
  ignore: (kind, key, name, permanent) => api.post('/protection/ignore', { kind, key, name, permanent }).then(r => r.data),
  unignore: (kind, key) => api.post('/protection/unignore', { kind, key }).then(r => r.data),
  listIgnored: () => api.get('/protection/ignored').then(r => r.data),
  listBackups: () => api.get('/protection/backups').then(r => r.data)
}

export const shunxApi = {
  // 公开接口：查询是否已配置安全入口、当前路径是否匹配
  status: (path) => api.get('/shunx/status', { params: { path } }).then(r => r.data),
  // 受保护接口：查询/修改配置（查询需登录，修改仅管理员）
  config: () => api.get('/shunx/config').then(r => r.data),
  update: (entry_path, enabled = true) => api.put('/shunx/config', { entry_path, enabled }).then(r => r.data)
}

export const tamperApi = {
  // ShunX 网页防篡改：全局状态 + 站点防护 + 篡改历史
  status: () => api.get('/tamper/status').then(r => r.data),
  sites: () => api.get('/tamper/sites').then(r => r.data),
  get: (siteId) => api.get(`/tamper/sites/${encodeURIComponent(siteId)}`).then(r => r.data),
  create: (body) => api.post('/tamper/sites', body).then(r => r.data),
  update: (siteId, body) => api.put(`/tamper/sites/${encodeURIComponent(siteId)}`, body).then(r => r.data),
  remove: (siteId) => api.delete(`/tamper/sites/${encodeURIComponent(siteId)}`).then(r => r.data),
  backupNow: (siteId) => api.post(`/tamper/sites/${encodeURIComponent(siteId)}/backup-now`).then(r => r.data),
  scanNow: (siteId) => api.post(`/tamper/sites/${encodeURIComponent(siteId)}/scan-now`).then(r => r.data),
  restore: (siteId, file) => api.post(`/tamper/sites/${encodeURIComponent(siteId)}/restore`, { file }).then(r => r.data),
  // 关闭 / 启用（关闭模式：temporary 临时分钟数 / manual 完全关闭需手动开启）
  disable: (minutes, mode = 'temporary') => api.post('/tamper/disable', { minutes, mode }).then(r => r.data),
  enable: () => api.post('/tamper/enable').then(r => r.data),
  history: () => api.get('/tamper/history').then(r => r.data)
}

export const tasksApi = {
  // 任务中心：长线任务（应用商店安装等）
  list: () => api.get('/tasks').then(r => r.data),
  get: (id) => api.get(`/tasks/${encodeURIComponent(id)}`).then(r => r.data),
  log: (id) => api.get(`/tasks/${encodeURIComponent(id)}/log`).then(r => r.data),
  remove: (id) => api.delete(`/tasks/${encodeURIComponent(id)}`).then(r => r.data)
}

export const runtimeApi = {
  // 运行环境：模板 / 列表 / 创建 / 删除 / 容器动作
  templates: () => api.get('/runtime/templates').then(r => r.data),
  list: () => api.get('/runtime/list').then(r => r.data),
  create: (body) => api.post('/runtime/create', body).then(r => r.data),
  delete: (id) => api.post(`/runtime/${encodeURIComponent(id)}/delete`).then(r => r.data),
  action: (id, action) => api.post(`/runtime/${encodeURIComponent(id)}/action`, { action }).then(r => r.data)
}

export const disksApi = {
  // 磁盘管理：查看块设备与分区信息
  list: () => api.get('/disks/list').then(r => r.data),
  // 挂载非系统盘分区
  mount: (device, mountpoint) => api.post('/disks/mount', null, { params: { device: device.replace('/dev/', ''), mountpoint } }).then(r => r.data)
}

export const nodesApi = {
  // 多节点（多机）管理：列表、当前主机、增删改、测试连接、切换
  list: () => api.get('/nodes').then(r => r.data),
  current: () => api.get('/nodes/current').then(r => r.data),
  setCurrent: (node_id) => api.post('/nodes/current', { node_id }).then(r => r.data),
  create: (body) => api.post('/nodes', body).then(r => r.data),
  update: (node_id, body) => api.put(`/nodes/${encodeURIComponent(node_id)}`, body).then(r => r.data),
  delete: (node_id) => api.delete(`/nodes/${encodeURIComponent(node_id)}`).then(r => r.data),
  test: (node_id) => api.post(`/nodes/${encodeURIComponent(node_id)}/test`).then(r => r.data)
}

export const uiApi = {
  // 界面设置：公开接口（登录页展示使用），无需登录
  public: () => api.get('/ui/public').then(r => r.data),
  // 界面设置：管理员读取 / 更新配置（网站名 / 欢迎语 / Logo）
  config: () => api.get('/ui/config').then(r => r.data),
  update: (body) => api.put('/ui/config', body).then(r => r.data)
}

export const frpApi = {
  // Frp（内网穿透）管理：配置 / 代理 / 进程
  status: () => api.get('/frp/status').then(r => r.data),
  config: () => api.get('/frp/config').then(r => r.data),
  preview: () => api.get('/frp/preview').then(r => r.data),
  save: (body) => api.put('/frp/config', body).then(r => r.data),
  switchMode: (mode) => api.post('/frp/mode', { mode }).then(r => r.data),
  addProxy: (body) => api.post('/frp/proxies', body).then(r => r.data),
  updateProxy: (id, body) => api.put(`/frp/proxies/${encodeURIComponent(id)}`, body).then(r => r.data),
  deleteProxy: (id) => api.delete(`/frp/proxies/${encodeURIComponent(id)}`).then(r => r.data),
  toggleProxy: (id, enabled) => api.post(`/frp/toggle-proxy/${encodeURIComponent(id)}`, { enabled }).then(r => r.data),
  start: () => api.post('/frp/start').then(r => r.data),
  stop: () => api.post('/frp/stop').then(r => r.data),
  restart: () => api.post('/frp/restart').then(r => r.data)
}

export const wafApi = {
  // WAF 应用防火墙：全局状态 / 站点策略 / 生成片段 / 拦截日志 / 拦截地图
  status: () => api.get('/waf/status').then(r => r.data),
  toggle: (enabled) => api.post('/waf/toggle', { enabled }).then(r => r.data),
  sites: () => api.get('/waf/sites').then(r => r.data),
  get: (site) => api.get(`/waf/site/${encodeURIComponent(site)}`).then(r => r.data),
  save: (site, body) => api.put(`/waf/site/${encodeURIComponent(site)}`, body).then(r => r.data),
  disable: (site) => api.post(`/waf/site/${encodeURIComponent(site)}/disable`).then(r => r.data),
  preview: (site) => api.get('/waf/preview', { params: { site_id: site } }).then(r => r.data),
  apply: () => api.post('/waf/apply').then(r => r.data),
  logs: (params) => api.get('/waf/logs', { params }).then(r => r.data),
  recordLog: (body) => api.post('/waf/logs/record', body).then(r => r.data),
  clearLogs: () => api.post('/waf/logs/clear').then(r => r.data),
  blockmap: (days = 30) => api.get('/waf/blockmap', { params: { days } }).then(r => r.data)
}

export const appStoreApi = {
  // 索引地址配置
  config: () => api.get('/appstore/config').then(r => r.data),
  saveConfig: (index_url) => api.put('/appstore/config', { index_url }).then(r => r.data),
  // 应用商店索引（refresh=1 强制重新拉取）
  index: (refresh = false) => api.get('/appstore/index', { params: { refresh } }).then(r => r.data),
  // 获取某个应用的 docker-compose.yml 原文（用于"编辑 compose"）
  compose: (app_id) => api.get(`/appstore/app/${encodeURIComponent(app_id)}/compose`).then(r => r.data),
  // 获取某个应用的 GitHub README
  readme: (app_id) => api.get(`/appstore/app/${encodeURIComponent(app_id)}/readme`).then(r => r.data),
  // 安装应用（同步，超时放宽到 30 分钟）
  install: (body) => api.post('/appstore/install', body, { timeout: 1800000 }).then(r => r.data),
  // 流式安装：SSE 逐步推送日志，onEvent 回调收到 {type:'status'|'log'|'result'|'error', ...}
  // 返回 AbortController（可中断请求）
  installStream: (body, onEvent) => {
    const controller = new AbortController()
    fetch('/api/appstore/install/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${auth.token}`
      },
      body: JSON.stringify(body),
      signal: controller.signal
    }).then(async (resp) => {
      if (!resp.ok) {
        let msg = resp.statusText
        try {
          const j = await resp.json()
          msg = j.detail || msg
        } catch (e) { /* ignore */ }
        onEvent({ type: 'error', message: msg })
        return
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      const handleChunk = (chunk) => {
        const lines = chunk.split('\n')
        for (const raw of lines) {
          const t = raw.trim()
          if (!t.startsWith('data:')) continue
          const payload = t.slice(5).trim()
          if (!payload) continue
          try { onEvent(JSON.parse(payload)) } catch (e) { /* ignore */ }
        }
      }
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          let idx
          while ((idx = buffer.indexOf('\n\n')) !== -1) {
            handleChunk(buffer.slice(0, idx))
            buffer = buffer.slice(idx + 2)
          }
        }
        if (buffer.trim()) handleChunk(buffer)
      } catch (e) {
        if (e.name !== 'AbortError') onEvent({ type: 'error', message: '连接中断: ' + e.message })
      }
    }).catch((e) => {
      if (e.name !== 'AbortError') onEvent({ type: 'error', message: '请求失败: ' + e.message })
    })
    return controller
  }
}

export const netstorageApi = {
  // 网络储存：连接管理 + 远程文件操作（FTP/FTPS/SMB/WebDAV/对象存储）
  connections: () => api.get('/netstorage/connections').then(r => r.data),
  createConn: (body) => api.post('/netstorage/connections', body).then(r => r.data),
  updateConn: (id, body) => api.put(`/netstorage/connections/${encodeURIComponent(id)}`, body).then(r => r.data),
  deleteConn: (id) => api.delete(`/netstorage/connections/${encodeURIComponent(id)}`).then(r => r.data),
  test: (id) => api.post(`/netstorage/connections/${encodeURIComponent(id)}/test`).then(r => r.data),
  // 远程文件操作（connId 为连接 id；path 为云端逻辑路径，以 / 开头）
  list: (id, path) => api.get(`/netstorage/connections/${encodeURIComponent(id)}/list`, { params: path ? { path } : {} }).then(r => r.data),
  read: (id, path) => api.get(`/netstorage/connections/${encodeURIComponent(id)}/read`, { params: { path } }).then(r => r.data),
  write: (id, path, content) => api.post(`/netstorage/connections/${encodeURIComponent(id)}/write`, { path, content }).then(r => r.data),
  mkdir: (id, path) => api.post(`/netstorage/connections/${encodeURIComponent(id)}/mkdir`, { path }).then(r => r.data),
  remove: (id, path) => api.post(`/netstorage/connections/${encodeURIComponent(id)}/delete`, { path }).then(r => r.data),
  rename: (id, src, dst) => api.post(`/netstorage/connections/${encodeURIComponent(id)}/rename`, { src, dst }).then(r => r.data),
  upload: (id, path, formData) => api.post(`/netstorage/connections/${encodeURIComponent(id)}/upload`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data)
}

export function formatBytes(bytes) {
  if (bytes == null) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v < 10 && i > 0 ? 2 : v < 100 ? 1 : 0)} ${units[i]}`
}

export function formatSpeed(bytesPerSec) {
  return formatBytes(bytesPerSec) + '/s'
}
