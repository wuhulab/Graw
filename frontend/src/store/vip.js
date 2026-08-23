import { reactive } from 'vue'
import { vipApi } from '../api'

// 付费功能（VIP/月卡/年卡）全局状态。
// 当启用「统一面板兼容」这一付费功能时，需要当前账号为生效 VIP 才能开启。
export const vip = reactive({
  loaded: false,
  vip: false,
  is_vip: false,
  plan: '',          // 'month' | 'year'
  vip_until: '',     // VIP 截止时间 ISO
  activated_at: ''
})

// 强制刷新当前用户的 VIP 状态；接口失败时静默（付费解锁仅作展示，不阻塞面板）
export async function refreshVip() {
  try {
    const r = await vipApi.status()
    vip.vip = !!r.vip
    vip.is_vip = !!r.is_vip
    vip.plan = r.plan || ''
    vip.vip_until = r.vip_until || ''
    vip.activated_at = r.activated_at || ''
    vip.loaded = true
    return r
  } catch (e) {
    vip.loaded = true
    return null
  }
}

// 用授权码激活当前账号 VIP；成功返回后端状态并刷新本地
export async function activateVip(code) {
  const r = await vipApi.activate(code)
  vip.vip = !!r.vip
  vip.is_vip = !!r.is_vip
  vip.plan = r.plan || ''
  vip.vip_until = r.vip_until || ''
  vip.activated_at = r.activated_at || ''
  return r
}

// 当前账号是否已开通生效 VIP（付费功能的开关依赖它）
export const isVip = () => !!vip.vip