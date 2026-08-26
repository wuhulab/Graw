/* vip.js — 当前账号的付费解锁（VIP）状态。

  业务背景：面板部分功能（如「统一面板兼容」）是付费项，需账号已激活
  VIP（月卡/年卡）才能使用。这里只存「当前账号」的 VIP 状态做界面开关判断，
  真实校验仍由后端负责。

  关键状态：
    vip / is_vip —— 是否已生效（两个字段含义相同，均来自后端，历史兼容）
    plan         —— 套餐类型：'month' 月卡 | 'year' 年卡
    vip_until    —— VIP 有效期截止时间（ISO）
    activated_at —— 激活时间

  用法：App 启动后调 refreshVip() 拉状态；设置页用 activateVip(code) 激活授权码；
  组件里用 isVip() 判断付费入口是否放行。
*/
import { reactive } from 'vue'      // 让 VIP 状态变化自动驱动付费入口的显隐
import { vipApi } from '../api'     // VIP 状态查询 / 授权码激活接口

// --- 对外暴露：全站唯一的 VIP 状态单例 ---
export const vip = reactive({
  loaded: false,      // 是否已查询过后端（false 期间界面按「未开通」保守展示）
  vip: false,         // 是否已生效 VIP（与 is_vip 同义，后端双字段兼容）
  is_vip: false,
  plan: '',           // 'month' | 'year'：当前生效的套餐类型
  vip_until: '',      // VIP 截止时间 ISO
  activated_at: ''    // 最近一次激活时间
})

// --- 动作说明：强制刷新当前账号的 VIP 状态（登录后调用一次） ---
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
    // 接口失败时静默降级：付费解锁只影响界面开关，不阻塞面板正常使用
    vip.loaded = true
    return null
  }
}

// --- 动作说明：用授权码激活当前账号 VIP，成功后刷新本地状态 ---
export async function activateVip(code) {
  const r = await vipApi.activate(code)
  vip.vip = !!r.vip                // 激活返回的是后端最新状态，直接同步覆盖本地
  vip.is_vip = !!r.is_vip
  vip.plan = r.plan || ''
  vip.vip_until = r.vip_until || ''
  vip.activated_at = r.activated_at || ''
  return r
}

// --- 动作说明：判断当前账号是否已开通生效 VIP（付费功能开关依赖它） ---
export const isVip = () => !!vip.vip   // 只做布尔判断；过期与否以后端返回的 is_vip 为准