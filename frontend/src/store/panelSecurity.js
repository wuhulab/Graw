/* panelSecurity.js — 面板安全设置的全局响应式副本。
   后端把配置存在 data/panel_security.json；这里只缓存水印文字与标签页锁，
   供 App.vue（水印层）和 PanelLayout（标签页关闭按钮）实时读取。
   登录后调用 loadPanelSecurity()；在设置窗口保存后会派发
   graw:panel-security-updated 事件，本 store 监听并刷新。 */
import { reactive } from 'vue'
import { panelSecurityApi } from '../api'

export const panelSecurity = reactive({
  watermark: '',
  tab_lock: false,
  loaded: false,
})

export async function loadPanelSecurity() {
  try {
    const data = await panelSecurityApi.get()
    const s = data.security || {}
    panelSecurity.watermark = s.watermark || ''
    panelSecurity.tab_lock = !!s.tab_lock
    panelSecurity.loaded = true
  } catch {
    // 权限不足或接口失败时保持空配置，不影响主界面
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('graw:panel-security-updated', loadPanelSecurity)
}
