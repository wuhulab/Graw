// 界面品牌配置共享 store：网站名 / 欢迎语 / Logo / 背景
// 登录页与桌面共用同一份配置（后台 data/ui.json），保证一致显示。
import { reactive } from 'vue'
import { uiApi } from '../api'

// 共享的界面配置状态（默认品牌值）
export const uiState = reactive({
  site_name: 'Graw',
  welcome: '',
  logo: '',
  background: '',
})

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
      uiState.background = res.background || ''
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