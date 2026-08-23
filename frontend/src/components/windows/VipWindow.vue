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
      <div v-if="msg" :class="msgType === 'ok' ? 'ok' : 'error'">{{ msg }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { vip as vipStore, refreshVip, activateVip } from '../../store/vip'

const { t } = useI18n()

const code = ref('')
const activating = ref(false)
const msg = ref('')
const msgType = ref('')

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
  if (activating.value) return
  msg.value = ''
  if (!code.value.trim()) { msg.value = t('vip.codeRequired'); msgType.value = 'err'; return }
  activating.value = true
  try {
    await activateVip(code.value.trim())
    msg.value = t('vip.activateSuccess')
    msgType.value = 'ok'
    code.value = ''
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('vip.activateFailed')
    msgType.value = 'err'
  } finally {
    activating.value = false
  }
}

onMounted(() => {
  refreshVip()
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
</style>