/* sw.js — Graw 离线壳 Service Worker（尽力而为）

   策略：预缓存应用外壳（首页 + /assets 静态资源），fetch 命中缓存时
   stale-while-revalidate；/api/* 一律直接走网络（面板数据必须实时/鉴权，
   不做离线缓存）。仅 https 或 localhost 生效（浏览器约束），
   http + IP 访问时注册失败即优雅降级，不影响正常使用。 */
const CACHE = 'graw-shell-v1'

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(['/', '/index.html'])).catch(() => {})
  )
  self.skipWaiting()
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  )
  self.clients.claim()
})

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url)
  // API、跨源、非 GET 一律网络直连
  if (e.request.method !== 'GET') return
  if (url.pathname.startsWith('/api/')) return
  if (url.origin !== location.origin) return

  // 静态资源：stale-while-revalidate
  e.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(e.request)
      const network = fetch(e.request)
        .then((res) => {
          if (res && res.ok) cache.put(e.request, res.clone())
          return res
        })
        .catch(() => cached)
      return cached || network
    })
  )
})