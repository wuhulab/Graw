// 界面品牌配置共享 store：网站名 / 欢迎语 / Logo / 背景 / 环形图配色 / 动态壁纸
// 登录页与桌面共用同一份配置（后台 data/ui.json），保证一致显示。
//
// 为避免「先展示默认背景、再启用自定义背景」的闪烁：把上次拉取的配置
// 缓存到 localStorage，模块加载时同步回填，从而在刷新/二次登录时，
// 有自定义背景即可直接使用（不先闪默认背景）。随后再以服务器配置校准。
import { reactive } from 'vue'
import { uiApi } from '../api'

const STORAGE_KEY = 'graw_ui_config'

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

/** 将最新配置写入 localStorage，供下次同步回填。*/
function saveCache() {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        site_name: uiState.site_name,
        welcome: uiState.welcome,
        logo: uiState.logo,
        background: (uiState.backgrounds && uiState.backgrounds[0]) || uiState.background || '',
        backgrounds: uiState.backgrounds || [],
        wallpaper_video: uiState.wallpaper_video || '',
        background_mode: uiState.background_mode || 'image',
        background_interval: uiState.background_interval || 8,
        ring_color: uiState.ring_color,
        ring_alarm: uiState.ring_alarm,
      })
    )
  } catch (e) {
    console.warn('[ui] 写入界面配置缓存失败:', e)
  }
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