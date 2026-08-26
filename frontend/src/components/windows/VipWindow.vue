<!--
  VIP 授权窗口（VIP）

  这个窗口做什么：
    面板的 VIP 授权页。展示当前授权状态（是否已激活、套餐年 / 月、
    到期时间），未激活时提供授权码输入框用于激活 / 续费。
    授权码通过后端向固定授权服务校验，服务地址由后端决定、前端不可改。
    激活成功后卡片刷新为已激活状态；购买入口跳转爱发电商品页。

  用到的后端模块：
    /api/vip/*（端点内自行鉴权）——状态读取与激活校验均封装在
    全局 store/vip（refreshVip / activateVip）里，本窗口只消费状态。

  关键状态：
    vip          全局 VIP 状态（来自 store/vip，含 vip / vip_until / plan）
    code         授权码输入框
    activating   激活请求进行中
    msg / msgType   操作结果提示

  怎么被打开：
    「设置」窗口（SettingsWindow）的「VIP」页签内嵌。
-->
<template>
  <div class="vip">
    <div class="block">
      <div class="block-title">{{ $t('vip.title') }}</div>
      <!-- 当前 VIP 状态（授权码服务地址由后端固定，前端不可修改） -->
      <div class="status-card">
        <div class="status-row">
          <span class="status-label">{{ $t('vip.status') }}</span>
          <span v-if="vip.vip" class="badge badge-on">{{ $t('vip.active') }}</span>
          <span v-else class="badge badge-off">{{ $t('vip.inactive') }}</span>
        </div>
        <template v-if="vip.vip">
          <div class="status-row">
            <span class="status-label">{{ $t('vip.plan') }}</span>
            <span class="status-value">{{ vip.plan === 'year' ? $t('vip.year') : $t('vip.month') }}</span>
          </div>
          <div class="status-row">
            <span class="status-label">{{ $t('vip.expire') }}</span>
            <span class="status-value">{{ expireText }}</span>
          </div>
        </template>
        <div v-else class="unlock-hint">{{ $t('vip.inactiveHint') }}</div>
      </div>

      <!-- 激活/续费授权码 -->
      <div class="field">
        <span class="label">{{ $t('vip.codeLabel') }}</span>
        <input v-model="code" :placeholder="$t('vip.codePlaceholder')" spellcheck="false" @keyup.enter="activate" />
      </div>
      <button class="btn-primary" :disabled="activating" @click="activate">
        {{ activating ? $t('vip.activating') : $t('vip.activate') }}
      </button>
      <!-- 购买授权码：跳转爱发电下单页（新窗口打开） -->
      <div class="purchase-row">
        <span class="purchase-hint">{{ $t('vip.purchaseHint') }}</span>
        <a class="purchase-link" :href="PURCHASE_URL" target="_blank" rel="noopener">{{ $t('vip.purchase') }}</a>
      </div>
      <div v-if="msg" :class="msgType === 'ok' ? 'ok' : 'error'">{{ msg }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'   // 响应式状态、派生到期文案、挂载钩子
import { useI18n } from 'vue-i18n'   // 取 t()，界面文案跟随面板语言
import { vip as vipStore, refreshVip, activateVip } from '../../store/vip'   // 全局 VIP 状态与激活 / 刷新动作

const { t } = useI18n()

const code = ref('')          // 授权码输入框内容
const activating = ref(false) // 激活请求进行中（禁用按钮）
const msg = ref('')           // 操作结果提示
const msgType = ref('')       // 提示类型（ok / err），决定配色

// 授权码购买 / 下单地址（爱发电商品页）
const PURCHASE_URL = 'https://ifdian.net/item/24342f98a05111f1b9445254001e7c00'

// --- 到期时间格式化：Unix 时间戳 → 本地时间串（异常时回退原文） ---
const expireText = computed(() => {
  if (!vipStore.vip_until) return '-'
  try {
    return new Date(vipStore.vip_until).toLocaleString()
  } catch (e) {
    return vipStore.vip_until || '-'
  }
})

const vip = computed(() => vipStore)

// 激活/续费授权码
async function activate() {
  if (activating.value) return   // 请求进行中直接退出，防止重复提交
  msg.value = ''
  if (!code.value.trim()) { msg.value = t('vip.codeRequired'); msgType.value = 'err'; return }   // 空授权码直接拦截
  activating.value = true
  try {
    await activateVip(code.value.trim())   // 后端校验授权码并写入 VIP 状态
    msg.value = t('vip.activateSuccess')
    msgType.value = 'ok'
    code.value = ''   // 成功后清空输入框，避免误重复提交
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('vip.activateFailed')
    msgType.value = 'err'
  } finally {
    activating.value = false
  }
}

onMounted(() => {
  refreshVip()   // 打开即拉取最新 VIP 状态
})
</script>

<style scoped>
.vip { height: 100%; background: #f5f5f7; padding: 16px; display: flex; flex-direction: column; gap: 14px; }

.block-title { font-size: 12px; font-weight: 700; color: #1d1d1f; margin-bottom: 8px; }

.status-card {
  background: #ffffff; border: 1px solid rgba(0,0,0,0.08); border-radius: 10px;
  padding: 10px 12px; margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px;
}
.status-row { display: flex; align-items: center; justify-content: space-between; }
.status-label { font-size: 12px; color: #6e6e73; font-weight: 600; }
.status-value { font-size: 12px; color: #1d1d1f; font-weight: 600; }
.badge { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 20px; }
.badge-on { background: rgba(52,199,89,0.15); color: #1d7a3c; }
.badge-off { background: rgba(142,142,147,0.18); color: #6e6e73; }
.unlock-hint { font-size: 11px; color: #8e8e93; line-height: 1.6; }

.field { display: block; margin-bottom: 10px; }
.field .label { display: block; font-size: 11px; color: #6e6e73; font-weight: 600; margin-bottom: 4px; }
.field input {
  width: 100%; padding: 8px 10px; font-size: 13px; font-family: inherit;
  border: 1px solid rgba(0,0,0,0.1); border-radius: 8px; outline: none; background: #ffffff; color: #1d1d1f;
}
.field input:focus { border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.18); }

.btn-primary {
  width: 100%; padding: 10px 14px; font-size: 13px; font-weight: 600; color: #ffffff;
  background: #0a84ff; border: none; border-radius: 10px; cursor: pointer;
}
.btn-primary:hover:not(:disabled) { background: #006ee6; }
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }

.error { color: #c0392b; font-size: 12px; background: rgba(255,59,48,0.08); border: 1px solid rgba(255,59,48,0.2); border-radius: 8px; padding: 6px 10px; margin-top: 10px; }
.ok { color: #2d6a4f; font-size: 12px; background: rgba(103,194,58,0.12); border: 1px solid rgba(103,194,58,0.32); border-radius: 8px; padding: 6px 10px; margin-top: 10px; }
.purchase-row { margin-top: 10px; display: flex; align-items: center; gap: 6px; font-size: 12px; }
.purchase-hint { color: #8e8e93; }
.purchase-link { color: #0a84ff; font-weight: 600; text-decoration: none; cursor: pointer; }
.purchase-link:hover { text-decoration: underline; }
</style>