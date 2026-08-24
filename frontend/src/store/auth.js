import { reactive } from 'vue'

const STORAGE_KEY = 'graw_auth'

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { token: null, user: null }
    return JSON.parse(raw)
  } catch {
    return { token: null, user: null }
  }
}

export const auth = reactive(load())

export function setAuth(token, user) {
  auth.token = token
  auth.user = user
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ token, user }))
}

export function clearAuth() {
  auth.token = null
  auth.user = null
  localStorage.removeItem(STORAGE_KEY)
}

export function isLoggedIn() {
  return !!auth.token
}

export function isAdmin() {
  return auth.user?.role === 'admin'
}
