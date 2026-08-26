/* Graw 前端统一 API 层：基于 Axios 封装全部 /api/* 后端调用。
   关键职责：
   - 创建默认实例 api 与 dockerHttp（Docker 大文件流 / 长超时专用）；
   - 请求拦截自动注入 Bearer 令牌与 X-Graw-Node（统一面板兼容的多节点目标）；
   - 响应拦截处理 401（token 失效登出）与 403（强制改密登出）；
   - 按业务模块导出 authApi / systemApi / dockerApi …… 等大对象，供组件 / store 调用，
     组件不直接写 axios。后端路由前缀与鉴权见 AGENTS.md 第 3、5.1 节。 */

import axios from 'axios'                               // HTTP 客户端：封装请求 / 响应拦截器
import { auth, clearAuth } from './store/auth'          // 登录态单例：读取 token、清除登录
import { getRequestNode } from './store/requestNode'    // 当前请求级目标节点（统一面板兼容）

const api = axios.create({
  baseURL: '/api',
  timeout: 60000
})

const dockerHttp = axios.create({
  baseURL: '/api',
  timeout: 60000
})

// 请求拦截：自动附加 Bearer 令牌 + 统一面板兼容的请求级目标节点
function attachToken(config) {
  if (auth.token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  // 统一面板兼容：聚焦窗口绑定的节点经 X-Graw-Node 下发给后端；为空则走全局当前节点
  const node = getRequestNode()
  if (node) {
    config.headers = config.headers || {}
    config.headers['X-Graw-Node'] = node
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

export default api                                     // 默认导出通用实例（含 token / 节点拦截），供各模块复用

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
  // path 为浏览器地址栏路径，用于 ShunX 安全入口校验；otpCode 为两步验证码（可选）
  login: (username, password, path = '', otpCode) => api.post('/auth/login', { username, password, otp_code: otpCode }, { headers: { 'X-ShunX-Entry': path } }).then(r => r.data),
  me: () => api.get('/auth/me').then(r => r.data),
  // token 可选：强制改密场景下尚未写入登录态，显式携带临时 token
  changePassword: (old_password, new_password, token) => api.post('/auth/password', { old_password, new_password }, token ? { headers: { Authorization: `Bearer ${token}` } } : undefined).then(r => r.data),
  listUsers: () => api.get('/auth/users').then(r => r.data),
  createUser: (username, password, role) => api.post('/auth/users', { username, password, role }).then(r => r.data),
  updateUser: (username, body) => api.put(`/auth/users/${username}`, body).then(r => r.data),
  deleteUser: (username) => api.delete(`/auth/users/${username}`).then(r => r.data),
  // 两步验证（2FA / TOTP）
  me2faStatus: () => api.get('/auth/2fa/status').then(r => r.data),
  twoFaSetup: () => api.post('/auth/2fa/setup').then(r => r.data),
  twoFaEnable: (code) => api.post('/auth/2fa/enable', { code }).then(r => r.data),
  twoFaDisable: (code) => api.post('/auth/2fa/disable', { code }).then(r => r.data),
  // 高风险操作二次确认：校验当前登录用户的面板密码
  verifyPassword: (password) => api.post('/auth/verify-password', { password }).then(r => r.data),
  // 会话管理：在线会话列表 / 踢出单设备 / 强制全部下线
  sessions: () => api.get('/auth/sessions').then(r => r.data),
  kickSession: (sid) => api.post(`/auth/sessions/${sid}/kick`, {}).then(r => r.data),
  kickAllSessions: (username) => api.post('/auth/sessions/kick-all', { username }).then(r => r.data)
}

export const systemApi = {
  overview: () => api.get('/system/overview').then(r => r.data),
  network: () => api.get('/system/network').then(r => r.data),
  diskio: () => api.get('/system/diskio').then(r => r.data),
  info: () => api.get('/system/info').then(r => r.data),
  // 安装完整性检测：确认是否按 README 的完整宿主机模式安装
  installCheck: () => api.get('/system/install-check').then(r => r.data),
  // 历史监控回放：状态查询 / 区间查询 / 清空
  metricsStatus: () => api.get('/system/metrics/status').then(r => r.data),
  metricsHistory: (params) => api.get('/system/metrics/history', { params }).then(r => r.data),
  metricsClear: () => api.delete('/system/metrics/clear').then(r => r.data)
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
  // 单容器实时资源（CPU/内存快照，供曲线绘制）
  containerStats: (id) => dockerHttp.get(`/docker/containers/${id}/stats`).then(r => r.data),
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
  // 镜像管理：拉取 / 打标签 / 构建
  pullImage: (name) => dockerHttp.post('/docker/images/pull', { name }).then(r => r.data),
  tagImage: (id, repo, tag = 'latest') => dockerHttp.post(`/docker/images/${id}/tag`, { repo, tag }).then(r => r.data),
  buildImage: (body) => dockerHttp.post('/docker/images/build', body).then(r => r.data),
  // 网络
  networks: () => dockerHttp.get('/docker/networks').then(r => r.data),
  removeNetwork: (name) => dockerHttp.post(`/docker/networks/${encodeURIComponent(name)}/remove`).then(r => r.data),
  // 数据卷（dockervolumes 路由）
  volumes: () => dockerHttp.get('/dockervolumes').then(r => r.data),
  removeVolume: (name) => dockerHttp.post(`/dockervolumes/${encodeURIComponent(name)}/remove`).then(r => r.data),
  volumeInspect: (name) => dockerHttp.get(`/dockervolumes/${encodeURIComponent(name)}/inspect`).then(r => r.data)
}

// 容器资源与端口编辑（containeredit 路由，仅管理员）
export const containereditApi = {
  // 读取容器可编辑配置（CPU/内存/环境变量/端口/重启策略）
  info: (id) => dockerHttp.get(`/containeredit/${encodeURIComponent(id)}/info`).then(r => r.data),
  // 更新 CPU / 内存限制
  updateLimits: (id, body) => dockerHttp.post(`/containeredit/${encodeURIComponent(id)}/update-limits`, body).then(r => r.data),
  // 重建容器（应用环境变量与端口映射修改，高风险）
  rebuild: (id, body) => dockerHttp.post(`/containeredit/${encodeURIComponent(id)}/rebuild`, body).then(r => r.data)
}

export const processApi = {
  list: (sort_by = 'cpu', limit = 200) => api.get('/process/list', { params: { sort_by, limit } }).then(r => r.data),
  kill: (pid, force = false) => api.post(`/process/${pid}/kill`, { force }).then(r => r.data)
}

// 文件管理默认 60s 超时对大目录 / 远程 SSH 慢节点偏紧（服务器侧 listdir_detail
// 单次 find/scandir 最长约 25s，读/写最长约 40s），统一放宽到 120s，
// 避免后端仍在处理时前端先抛 "timeout of 60000ms exceeded"。
const FILES_TIMEOUT = 120000

export const filesApi = {
  list: (path) => api.get('/files/list', { params: path ? { path } : {}, timeout: FILES_TIMEOUT }).then(r => r.data),
  roots: () => api.get('/files/roots', { timeout: FILES_TIMEOUT }).then(r => r.data),
  read: (path) => api.get('/files/read', { params: { path }, timeout: FILES_TIMEOUT }).then(r => r.data),
  write: (path, content) => api.post('/files/write', { path, content }, { timeout: FILES_TIMEOUT }).then(r => r.data),
  remove: (path) => api.post('/files/delete', { path }, { timeout: FILES_TIMEOUT }).then(r => r.data),
  mkdir: (path) => api.post('/files/mkdir', { path }, { timeout: FILES_TIMEOUT }).then(r => r.data),
  rename: (src, dst) => api.post('/files/rename', { src, dst }, { timeout: FILES_TIMEOUT }).then(r => r.data),
  chmod: (path, mode) => api.post('/files/chmod', { path, mode }, { timeout: FILES_TIMEOUT }).then(r => r.data),
  copy: (src, dst) => api.post('/files/copy', { src, dst }, { timeout: FILES_TIMEOUT }).then(r => r.data),
  compress: (paths, archive, fmt) => api.post('/files/compress', { paths, archive, fmt }, { timeout: FILES_TIMEOUT }).then(r => r.data),
  extract: (archive, dest) => api.post('/files/extract', { archive, dest }, { timeout: FILES_TIMEOUT }).then(r => r.data),
  upload: (formData) => api.post('/files/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: FILES_TIMEOUT }).then(r => r.data)
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
  toggle: (enabled) => api.post('/firewall/toggle', { enabled }).then(r => r.data),
  clear: () => api.post('/firewall/clear').then(r => r.data),
  listening: () => api.get('/firewall/listening').then(r => r.data),
  blockUnopened: () => api.post('/firewall/block-unopened').then(r => r.data)
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

export const agentApi = {
  // 「作为子节点」Agent 收取模式配置（仅管理员；secret 不回传明文）
  status: () => api.get('/agent/cfg').then(r => r.data),
  save: (body) => api.put('/agent/cfg', body).then(r => r.data),
  // 一次性展示子节点校验 secret（仅初次/重置后可用，返回即作废）
  revealSecret: () => api.post('/agent/reveal-secret').then(r => r.data)
}

export const uiApi = {
  // 界面设置：公开接口（登录页展示使用），无需登录
  public: () => api.get('/ui/public').then(r => r.data),
  // 界面设置：管理员读取 / 更新配置（网站名 / 欢迎语 / Logo）
  config: () => api.get('/ui/config').then(r => r.data),
  update: (body) => api.put('/ui/config', body).then(r => r.data),
  // 当前账号生效的动态壁纸与环形图（「仅用于这个账号」优先，其次全局，最后默认）
  effective: () => api.get('/ui/effective').then(r => r.data)
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

// Web 服务器引擎模式（NGINX / OpenResty）：查询与切换（仅管理员）
export const webmodeApi = {
  status: () => api.get('/webmode/status').then(r => r.data),
  setMode: (mode) => api.post('/webmode/mode', { mode }).then(r => r.data)
}

// 备份中心：目录/文件通用备份（手动 + cron 计划）、轮转、一键恢复、远程备份（管理员）
export const backupApi = {
  status: () => api.get('/backup/status').then(r => r.data),
  tasks: () => api.get('/backup/tasks').then(r => r.data),
  createTask: (body) => api.post('/backup/tasks', body).then(r => r.data),
  updateTask: (id, body) => api.put(`/backup/tasks/${encodeURIComponent(id)}`, body).then(r => r.data),
  deleteTask: (id) => api.delete(`/backup/tasks/${encodeURIComponent(id)}`).then(r => r.data),
  run: (id) => api.post(`/backup/tasks/${encodeURIComponent(id)}/run`, {}, { timeout: 1800000 }).then(r => r.data),
  restore: (id, file, target) => api.post(`/backup/tasks/${encodeURIComponent(id)}/restore`, { file, target }, { timeout: 1800000 }).then(r => r.data),
  records: () => api.get('/backup/records').then(r => r.data),
  deleteRecord: (file) => api.delete('/backup/records', { params: { file } }).then(r => r.data),
  // 远程备份目标（WebDAV）
  remotes: () => api.get('/backup/remotes').then(r => r.data),
  createRemote: (body) => api.post('/backup/remotes', body).then(r => r.data),
  updateRemote: (id, body) => api.put(`/backup/remotes/${encodeURIComponent(id)}`, body).then(r => r.data),
  deleteRemote: (id) => api.delete(`/backup/remotes/${encodeURIComponent(id)}`).then(r => r.data),
  testRemote: (id) => api.post(`/backup/remotes/${encodeURIComponent(id)}/test`, {}, { timeout: 60000 }).then(r => r.data)
}

// 通知中心：通知渠道（Webhook/Telegram/钉钉/企微/Server酱/邮件）+ 资源阈值告警（管理员）
export const notifyApi = {
  status: () => api.get('/notify/status').then(r => r.data),
  channels: () => api.get('/notify/channels').then(r => r.data),
  createChannel: (body) => api.post('/notify/channels', body).then(r => r.data),
  updateChannel: (id, body) => api.put(`/notify/channels/${encodeURIComponent(id)}`, body).then(r => r.data),
  deleteChannel: (id) => api.delete(`/notify/channels/${encodeURIComponent(id)}`).then(r => r.data),
  testChannel: (id) => api.post(`/notify/channels/${encodeURIComponent(id)}/test`, {}, { timeout: 60000 }).then(r => r.data),
  rules: () => api.get('/notify/rules').then(r => r.data),
  createRule: (body) => api.post('/notify/rules', body).then(r => r.data),
  updateRule: (id, body) => api.put(`/notify/rules/${encodeURIComponent(id)}`, body).then(r => r.data),
  deleteRule: (id) => api.delete(`/notify/rules/${encodeURIComponent(id)}`).then(r => r.data),
  updateConfig: (body) => api.put('/notify/config', body).then(r => r.data),
  testAlert: () => api.post('/notify/test-alert').then(r => r.data),
  logs: (limit = 100) => api.get('/notify/logs', { params: { limit } }).then(r => r.data),
  clearLogs: () => api.post('/notify/logs/clear').then(r => r.data)
}

// 站点可用性检测：监控网站/服务 HTTP 可用性，宕机/恢复推送通知（管理员）
export const uptimeApi = {
  status: () => api.get('/uptime/status').then(r => r.data),
  items: () => api.get('/uptime/items').then(r => r.data),
  createItem: (body) => api.post('/uptime/items', body).then(r => r.data),
  updateItem: (id, body) => api.put(`/uptime/items/${encodeURIComponent(id)}`, body).then(r => r.data),
  deleteItem: (id) => api.delete(`/uptime/items/${encodeURIComponent(id)}`).then(r => r.data),
  test: (id) => api.post(`/uptime/items/${encodeURIComponent(id)}/test`, {}, { timeout: 60000 }).then(r => r.data)
}

// 证书到期提醒：检查面板 SSL 证书剩余天数，临期/过期推送通知（管理员）
export const certcheckApi = {
  status: () => api.get('/certcheck/status').then(r => r.data),
  certs: () => api.get('/certcheck/certs').then(r => r.data),
  test: () => api.post('/certcheck/test').then(r => r.data),
  updateConfig: (body) => api.put('/certcheck/config', body).then(r => r.data)
}

// 面板自身备份：导出/导入 data/ 全部配置归档（迁移与容灾，管理员）
export const panelbackupApi = {
  list: () => api.get('/panelbackup/list').then(r => r.data),
  export: () => api.post('/panelbackup/export', {}, { timeout: 120000 }).then(r => r.data),
  download: (name) => api.get(`/panelbackup/download/${encodeURIComponent(name)}`, { responseType: 'blob' }),
  delete: (name) => api.delete(`/panelbackup/${encodeURIComponent(name)}`).then(r => r.data),
  import: (formData) => api.post('/panelbackup/import', formData, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 300000 }).then(r => r.data)
}

export const loginlogApi = {
  status: () => api.get('/loginlog/status').then(r => r.data),
  list: (params) => api.get('/loginlog/list', { params }).then(r => r.data),
  mine: (limit = 100) => api.get('/loginlog/mine', { params: { limit } }).then(r => r.data),
  clear: () => api.post('/loginlog/clear').then(r => r.data),
  updateConfig: (alert_enabled) => api.put('/loginlog/config', { alert_enabled }).then(r => r.data),
  testAlert: () => api.post('/loginlog/test-alert').then(r => r.data)
}

// 网站访问统计：解析 nginx 访问日志，输出 PV/UV/IP/来源/热门页面
export const webstatsApi = {
  // 可用访问日志路径
  logs: () => api.get('/webstats/logs').then(r => r.data),
  // 分析日志（log_path 为空则自动探测；days 统计天数；domain 按域名过滤）
  analyze: (params) => api.get('/webstats/analyze', { params }).then(r => r.data)
}

// 伪静态规则库：常用框架一键伪静态
export const rewriteApi = {
  templates: () => api.get('/rewrite/templates').then(r => r.data),
  sites: () => api.get('/rewrite/sites').then(r => r.data),
  apply: (site_id, template_id) => api.post('/rewrite/apply', { site_id, template_id }).then(r => r.data),
  clear: (site_id) => api.post('/rewrite/clear', { site_id }).then(r => r.data)
}

// 站点增强配置：防盗链 / gzip / 静态资源缓存
export const sitesoptsApi = {
  sites: () => api.get('/sitesopts/sites').then(r => r.data),
  apply: (body) => api.post('/sitesopts/apply', body).then(r => r.data),
  clear: (site_id) => api.post('/sitesopts/clear', { site_id }).then(r => r.data)
}

// 服务/端口监控：自定义监控项（端口/进程/systemd 服务）状态看板
export const svcmonitorApi = {
  items: () => api.get('/svcmonitor/items').then(r => r.data),
  createItem: (body) => api.post('/svcmonitor/items', body).then(r => r.data),
  updateItem: (id, body) => api.put(`/svcmonitor/items/${id}`, body).then(r => r.data),
  deleteItem: (id) => api.delete(`/svcmonitor/items/${id}`).then(r => r.data),
  test: (id) => api.post(`/svcmonitor/items/${id}/test`).then(r => r.data)
}

// SSH 密钥管理：生成/导入密钥并一键部署到节点
export const sshkeysApi = {
  list: () => api.get('/sshkeys').then(r => r.data),
  nodes: () => api.get('/sshkeys/nodes').then(r => r.data),
  create: (body) => api.post('/sshkeys', body).then(r => r.data),
  importKey: (body) => api.post('/sshkeys/import', body).then(r => r.data),
  publicKey: (id) => api.get(`/sshkeys/${id}/public`).then(r => r.data),
  deploy: (id, nodeId) => api.post(`/sshkeys/${id}/deploy`, { node_id: nodeId }).then(r => r.data),
  delete: (id) => api.delete(`/sshkeys/${id}`).then(r => r.data)
}

// 一键系统体检：弱密码/异常登录/危险端口/可疑任务/安全配置分级报告
export const healthcheckApi = {
  run: () => api.get('/healthcheck/run').then(r => r.data)
}

// 虚拟 FTP 用户管理：纯 Python 维护 data/ftp_users.json，无需系统用户（管理员）
export const ftpusersApi = {
  list: () => api.get('/ftpusers').then(r => r.data),
  create: (body) => api.post('/ftpusers', body).then(r => r.data),
  update: (id, body) => api.put(`/ftpusers/${encodeURIComponent(id)}`, body).then(r => r.data),
  delete: (id) => api.delete(`/ftpusers/${encodeURIComponent(id)}`).then(r => r.data)
}

// 工具箱：Base64 / 哈希 / 时间戳 / 端口扫描 / Whois（仅管理员）
export const toolboxApi = {
  exec: (body) => api.post('/toolbox/exec', body).then(r => r.data)
}

// PHP 多版本管理：探测系统 PHP/FPM 版本 + 站点 PHP 版本关联（仅管理员）
export const phpversionsApi = {
  // 探测已安装的系统 PHP 版本
  list: () => api.get('/phpversions/list').then(r => r.data),
  // 功能状态摘要（available + 版本列表 + reason）
  status: () => api.get('/phpversions/status').then(r => r.data),
  // 列出站点及其当前 PHP 版本
  sites: () => api.get('/phpversions/sites').then(r => r.data),
  // 为站点（static/subsite）绑定 PHP 版本
  setPhp: (site_id, version) => api.post(`/phpversions/site/${site_id}/set-php`, { version }).then(r => r.data)
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

// 付费功能（VIP/月卡/年卡）：查询状态 + 用授权码激活。
// 授权码服务地址固定在后端常量，前端不可修改，故无 config 接口。
export const vipApi = {
  status: () => api.get('/vip/status').then(r => r.data),
  activate: (code) => api.post('/vip/activate', { code }).then(r => r.data)
}
