import axios from 'axios'
import { auth, clearAuth } from './store/auth'

const api = axios.create({
  baseURL: '/api',
  timeout: 15000
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

// 响应拦截：仅在「曾经登录但 token 失效」时清除登录态并刷新。
// 从未登录（本地无 token）的请求拿到 401 是正常的，不触发刷新，
// 否则未登录页面会陷入无限 reload 循环。
let _reloading = false
function on401(err) {
  if (err?.response?.status === 401 && auth.token && !_reloading) {
    _reloading = true
    clearAuth()
    if (location.pathname !== '/') {
      location.href = '/'
    } else {
      location.reload()
    }
  }
  return Promise.reject(err)
}
api.interceptors.response.use(r => r, on401)
dockerHttp.interceptors.response.use(r => r, on401)

export default api

export const authApi = {
  login: (username, password) => api.post('/auth/login', { username, password }).then(r => r.data),
  me: () => api.get('/auth/me').then(r => r.data),
  changePassword: (old_password, new_password) => api.post('/auth/password', { old_password, new_password }).then(r => r.data),
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
  logs: (id, tail = 200) => dockerHttp.get(`/docker/containers/${id}/logs`, { params: { tail } }).then(r => r.data)
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
  rename: (src, dst) => api.post('/files/rename', { src, dst }).then(r => r.data)
}

export const notesApi = {
  get: () => api.get('/notes/').then(r => r.data),
  save: (content) => api.post('/notes/', { content }).then(r => r.data)
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
