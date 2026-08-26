// 界面品牌配置共享 store：网站名 / 欢迎语 / Logo / 背景 / 环形图配色 / 动态壁纸
// 登录页与桌面共用同一份配置（后台 data/ui.json），保证一致显示。
//
// 为避免「先展示默认背景、再启用自定义背景」的闪烁：把上次拉取的配置
// 缓存到 localStorage，模块加载时同步回填，从而在刷新/二次登录时，
// 有自定义背景即可直接使用（不先闪默认背景）。随后再以服务器配置校准。
import { reactive } from 'vue'     // 品牌配置响应式：登录页与桌面读取同一份配置自动更新
import { uiApi } from '../api'     // 界面品牌配置接口（public 无需登录 / effective 账号级）

const STORAGE_KEY = 'graw_ui_config'   // 本地缓存键：存上次拉取的品牌配置，用于刷新/二次登录时防闪烁

/** 读取本地缓存配置（损坏/缺失时回退默认值）。*/
function readCache() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const v = JSON.parse(raw)
    return v && typeof v === 'object' ? v : null
  } catch {
    return null
  }
}

// 共享的界面配置状态（默认品牌值；优先用缓存中的背景，避免默认背景先闪现）
const cached = readCache()
export const uiState = reactive({
  site_name: 'Graw',
  welcome: '',
  logo: cached?.logo || '',
  background: (cached?.backgrounds && cached.backgrounds[0]) || cached?.background || '',
  backgrounds: cached?.backgrounds || (cached?.background ? [cached.background] : []),
  wallpaper_video: cached?.wallpaper_video || '',
  background_mode: cached?.background_mode || 'image',
  background_interval: cached?.background_interval || 8,
  ring_color: cached?.ring_color || '#409eff',
  ring_alarm: cached?.ring_alarm !== false, // 默认开启「超 90% 变红」
})

// localStorage 单键缓存上限（约 1.6MB，实际配额多为 5MB）：超限时逐级降级写入，
// 避免把用户上传的大体积 base64 背景图塞满配额导致 QuotaExceededError 反复刷屏。
const CACHE_MAX_BYTES = 1600 * 1024
// 本会话内缓存彻底不可用（配额不足且降级仍失败）时置为 false，跳过后续写入
let cacheWritable = true

/** 构造缓存载荷；keepBackgrounds=false 时不写背景，避免超大 base64 撑爆配额。*/
function buildPayload(keepBackgrounds) {
  const backgrounds = keepBackgrounds ? (uiState.backgrounds || []) : []
  return {
    site_name: uiState.site_name,
    welcome: uiState.welcome,
    logo: uiState.logo,
    background: backgrounds[0] || uiState.background || '',
    backgrounds,
    wallpaper_video: uiState.wallpaper_video || '',
    background_mode: uiState.background_mode || 'image',
    background_interval: uiState.background_interval || 8,
    ring_color: uiState.ring_color,
    ring_alarm: uiState.ring_alarm,
  }
}

/** 尝试将载荷序列化写入 localStorage；返回是否成功（写入失败不抛错）。*/
function writePayload(payload) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
    return true
  } catch (e) {
    // 配额超限等环境限制：交给调用方降级处理
    return false
  }
}

/** 将最新配置写入 localStorage，供下次同步回填。*/
function saveCache() {
  // 本会话已确认缓存不可用：直接跳过，避免每次加载都重试并刷控制台警告
  if (!cacheWritable) return
  // 优先完整写入；序列化长度超限或写入失败时逐级降级：
  // 第一张背景 → 去掉背景（仅保留面板设置），仍失败则放弃整键并标记本会话不可写
  const candidates = [true, false]
  for (const keepBackgrounds of candidates) {
    const payload = buildPayload(keepBackgrounds)
    if (JSON.stringify(payload).length > CACHE_MAX_BYTES) continue
    if (writePayload(payload)) return
    // 写入失败（如配额被其它数据占满）：清掉可能残留的半写数据再试下一级
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch (e) {
      // removeItem 失败（如隐私模式下存储被禁用），继续降级
    }
  }
  // 无背景精简载荷仍失败：存储能力确实不可用，标记后跳过后续写入
  cacheWritable = false
  console.warn('[ui] 界面配置缓存不可用（容量不足或被禁用），本次会话跳过缓存写入')
}

// 防并发：同一时刻只允许一个加载请求
let loading = null

/**
 * 加载界面品牌配置（公开接口，无需登录）。
 * 返回 Promise<uiState>，失败时保留现有/默认值并抛出。
 */
export function loadUi() {
  if (loading) return loading
  loading = uiApi
    .public()
    .then((res) => {
      uiState.site_name = res.site_name || 'Graw'
      uiState.welcome = res.welcome || ''
      uiState.logo = res.logo || ''
      const list = Array.isArray(res.backgrounds) && res.backgrounds.length
        ? res.backgrounds
        : (res.background ? [res.background] : [])
      uiState.backgrounds = list
      uiState.background = list[0] || ''
      uiState.wallpaper_video = res.wallpaper_video || ''
      uiState.background_mode = res.background_mode === 'video' ? 'video' : 'image'
      uiState.background_interval = res.background_interval || 8
      uiState.ring_color = res.ring_color || '#409eff'
      uiState.ring_alarm = res.ring_alarm !== false
      saveCache()
      return uiState
    })
    .catch((e) => {
      console.warn('[ui] 加载界面配置失败:', e)
      throw e
    })
    .finally(() => {
      loading = null
    })
  return loading
}

// 登录后按当前账号加载「动态壁纸 / 环形图」：
// 优先该账号的个人覆盖，其次全局（loadUi 已应用），最后默认。其它账号互不影响。
export function loadUiEffective() {
  return uiApi
    .effective()
    .then((res) => {
      if (!res) return uiState
      if (Array.isArray(res.backgrounds)) {
        uiState.backgrounds = res.backgrounds
        uiState.background = res.backgrounds[0] || ''
      }
      uiState.wallpaper_video = res.wallpaper_video || ''
      uiState.background_mode = res.background_mode === 'video' ? 'video' : 'image'
      uiState.background_interval = res.background_interval || 8
      uiState.ring_color = res.ring_color || '#409eff'
      uiState.ring_alarm = res.ring_alarm !== false
      saveCache()
      return uiState
    })
    .catch((e) => {
      console.warn('[ui] 加载账号级界面配置失败:', e)
      return uiState
    })
}