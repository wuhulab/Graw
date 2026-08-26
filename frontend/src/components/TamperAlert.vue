<!--
  TamperAlert.vue — 网页防篡改告警弹窗
  作用：ShunX 防篡改监控发现站点文件被改动时，对在线面板用户全局弹窗告警，
        展示被篡改的站点 / 文件 / 根目录 / 时间与后端自动恢复结果；管理员可
        「临时关闭 10 分钟」（到期自动恢复）或经二次确认后「完全关闭防篡改」。
  数据：告警队列来自 store/tamper（tamperState.alerts），弹窗始终展示队列最新一条，
        其余条数在底部提示；操作经 disableForMinutes / disableManual 下发后端。
  打开方式：由 App.vue 在收到防篡改 WS 告警时渲染，全局遮罩置顶。
-->
<template>
  <!-- ShunX 网页防篡改告警弹窗：篡改发生时对在线面板用户弹窗提示 -->
  <div class="tamper-alert-overlay">
    <div class="tamper-alert-card">
      <div class="alert-head">
        <span class="alert-logo">ShunX</span>
        <span class="alert-sub">{{ $t('tamper.alertTitle') }}</span>
      </div>

      <p class="alert-desc danger">
        <ShieldAlert :size="20" />
        <span>{{ $t('tamper.alertDesc') }}</span>
      </p>

      <!-- 当前告警详情 -->
      <div class="alert-detail">
        <div class="detail-row"><span class="k">{{ $t('tamper.alertSite') }}</span><span class="v">{{ alert.site_name || alert.site_id }}</span></div>
        <div class="detail-row"><span class="k">{{ $t('tamper.alertFile') }}</span><span class="v mono">{{ alert.file }}</span></div>
        <div class="detail-row"><span class="k">{{ $t('tamper.alertRoot') }}</span><span class="v mono">{{ alert.root }}</span></div>
        <div class="detail-row"><span class="k">{{ $t('tamper.alertTime') }}</span><span class="v mono">{{ fmtTime(alert.time) }}</span></div>
        <div class="detail-row">
          <span class="k">{{ $t('tamper.alertAction') }}</span>
          <span class="v">
            <span class="badge" :class="alert.restored ? 'ok' : 'danger'">
              {{ alert.restored ? $t('tamper.restored') : $t('tamper.restoreFailed') }}
            </span>
            <span class="reason">（{{ alert.reason === 'missing' ? $t('tamper.reasonMissing') : $t('tamper.reasonHashMismatch') }}）</span>
          </span>
        </div>
      </div>

      <!-- 操作按钮（仅管理员可操作关闭/启用） -->
      <template v-if="canOperate()">
        <!-- 快捷关闭：10 分钟内关闭 -->
        <button class="btn-10m" :disabled="busy" @click="onDisable10m">
          <Clock :size="16" /> {{ $t('tamper.alertAction10m') }}
        </button>

        <!-- 高级：展开后提供「完全关闭防篡改」 -->
        <div class="advanced">
          <button class="btn-advanced" :disabled="busy" @click="showAdvanced = !showAdvanced">
            {{ $t('tamper.alertActionAdvanced') }} <span class="caret" :class="{ open: showAdvanced }">▾</span>
          </button>
          <div v-if="showAdvanced" class="advanced-panel danger">
            <p class="warn-text">{{ $t('tamper.alertDisableWarning') }}</p>
            <button class="btn-full-disable" :disabled="busy" @click="showManualConfirm = true">
              <Power :size="14" /> {{ $t('tamper.alertActionDisable') }}
            </button>
          </div>
        </div>
      </template>

      <button class="btn-dismiss" :disabled="busy" @click="onDismiss">{{ $t('tamper.alertDismiss') }}</button>

      <!-- 仍有更多待处理告警时提示 -->
      <div v-if="remaining > 0" class="more">{{ $t('tamper.alertMore', { count: remaining }) }}</div>

      <!-- 操作结果提示 -->
      <div v-if="feedback" class="feedback" :class="feedback.kind">{{ feedback.text }}</div>
    </div>

    <!-- 完全关闭二次确认（警告：请务必记得手动打开） -->
    <div v-if="showManualConfirm" class="confirm-overlay" @click.self="showManualConfirm = false">
      <div class="confirm-card">
        <h3 class="danger-title"><OctagonAlert :size="18" /> {{ $t('tamper.alertConfirmDisableTitle') }}</h3>
        <p class="danger-box">{{ $t('tamper.alertConfirmDisable') }}</p>
        <div class="actions">
          <button class="btn" :disabled="busy" @click="showManualConfirm = false">{{ $t('common.cancel') }}</button>
          <button class="btn danger-btn" :disabled="busy" @click="onDisableManual">{{ $t('tamper.alertConfirmDisableBtn') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'   // Vue 响应式与计算属性
import { ShieldAlert, Clock, Power, OctagonAlert } from 'lucide-vue-next'   // 告警 / 操作按钮图标
import { tamperState, dismissAlert, disableForMinutes, disableManual, canOperate } from '../store/tamper'   // 防篡改告警状态与各项操作

const props = defineProps({})

// 当前展示的告警 = 队列最新一条
const alert = computed(() => tamperState.alerts[0] || {})
const remaining = computed(() => Math.max(0, tamperState.alerts.length - 1))   // 其余待处理告警条数

const showAdvanced = ref(false)      // 高级操作面板是否展开
const showManualConfirm = ref(false) // 「完全关闭」二次确认弹窗是否显示
const busy = ref(false)              // 任一操作请求进行中，期间禁用所有按钮
const feedback = ref(null)           // 操作结果提示（success / warn / error）

// 把 ISO 时间格式化为本地日期时间文本；无值或非法时间直接原样返回
function fmtTime(iso) {
  if (!iso) return '—'               // 无时间戳时用占位符
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso // 后端数据异常时保留原始串，避免显示 Invalid Date
  const pad = (n) => String(n).padStart(2, '0')   // 补零到两位
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// 显示操作结果提示，4 秒后自动消失
function showFeedback(kind, text) {
  feedback.value = { kind, text }
  setTimeout(() => { feedback.value = null }, 4000)
}

// 10 分钟内关闭防篡改：临时关闭，到期自动恢复
async function onDisable10m() {
  if (busy.value) return   // 已有操作在跑则忽略，防重复点击
  busy.value = true
  try {
    await disableForMinutes(10)
    dismissAlert(alert.value.id)
    showAdvanced.value = false
    showFeedback('success', t('tamper.alertDisabled10m'))
  } catch (e) {
    showFeedback('error', e?.response?.data?.detail || t('tamper.alertOpFailed'))
  } finally {
    busy.value = false
  }
}

// 高级：完全关闭防篡改（已确认警告）
async function onDisableManual() {
  if (busy.value) return   // 已有操作在跑则忽略，防重复点击
  busy.value = true
  try {
    await disableManual()
    dismissAlert(alert.value.id)
    showManualConfirm.value = false
    showAdvanced.value = false
    showFeedback('warn', 'tamperAlertDisabledManual')
  } catch (e) {
    showFeedback('error', e?.response?.data?.detail || '')
  } finally {
    busy.value = false
  }
}

// 关闭当前告警弹窗（不改变防护状态）
function onDismiss() {
  dismissAlert(alert.value.id)
  showAdvanced.value = false
  showManualConfirm.value = false
}
</script>

<style scoped>
.tamper-alert-overlay {
  position: fixed;
  inset: 0;
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(120, 10, 10, 0.35);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}

.tamper-alert-card {
  width: 460px;
  max-width: 94vw;
  padding: 24px 26px 20px;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
  border-top: 4px solid #dc2626;
}

.alert-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.alert-logo { font-size: 24px; font-weight: 800; color: #0a3d7a; }
.alert-sub { font-size: 13px; font-weight: 600; color: #b91c1c; }

.alert-desc {
  display: flex; align-items: center; gap: 8px;
  font-size: 14px; font-weight: 600; color: #b91c1c;
  background: #fef2f2; border: 1px solid #fecaca;
  border-radius: 8px; padding: 9px 12px; margin: 0 0 12px;
}

.alert-detail {
  background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px;
  padding: 10px 12px; margin-bottom: 14px;
}
.detail-row { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.7; }
.detail-row .k { color: #6e6e73; width: 58px; flex-shrink: 0; }
.detail-row .v { color: #1d1d1f; word-break: break-all; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; }

.badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.danger { background: #fee2e2; color: #b91c1c; }
.reason { color: #6e6e73; }

.btn-10m {
  width: 100%; display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 11px 14px; font-size: 13px; font-weight: 600;
  color: #fff; background: #0a84ff; border: none; border-radius: 10px;
  cursor: pointer; transition: background 0.15s;
}
.btn-10m:hover:not(:disabled) { background: #006ee6; }
.btn-10m:disabled { opacity: 0.6; cursor: not-allowed; }

.advanced { margin-top: 8px; }
.btn-advanced {
  width: 100%; padding: 8px 14px; font-size: 12.5px; color: #374151;
  background: #f9fafb; border: 1px solid #d1d5db; border-radius: 8px;
  cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px;
}
.btn-advanced:hover:not(:disabled) { background: #f3f4f6; }
.caret { transition: transform 0.15s; }
.caret.open { transform: rotate(180deg); }

.advanced-panel {
  margin-top: 8px; padding: 12px; border-radius: 10px;
  background: #fef2f2; border: 1px solid #fecaca;
}
.warn-text { margin: 0 0 10px; font-size: 12.5px; color: #b91c1c; font-weight: 600; }
.btn-full-disable {
  width: 100%; display: flex; align-items: center; justify-content: center; gap: 8px;
  padding: 10px 14px; font-size: 13px; font-weight: 700;
  color: #fff; background: #dc2626; border: none; border-radius: 8px; cursor: pointer;
}
.btn-full-disable:hover:not(:disabled) { background: #b91c1c; }
.btn-full-disable:disabled { opacity: 0.6; cursor: not-allowed; }

.btn-dismiss {
  width: 100%; margin-top: 8px; padding: 9px 14px; font-size: 12.5px; color: #6e6e73;
  background: transparent; border: 1px solid #d1d5db; border-radius: 8px; cursor: pointer;
}
.btn-dismiss:hover:not(:disabled) { background: #f9fafb; }

.more { margin-top: 10px; font-size: 11.5px; color: #b91c1c; text-align: center; }

.feedback { margin-top: 10px; padding: 8px 10px; border-radius: 8px; font-size: 12.5px; text-align: center; }
.feedback.success { background: #d1fae5; color: #065f46; border: 1px solid #a7f3d0; }
.feedback.warn { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
.feedback.error { background: #fee2e2; color: #b91c1c; border: 1px solid #fecaca; }

.confirm-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 9999;
}
.confirm-card { background: #fff; border-radius: 12px; padding: 20px; width: 440px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,0.2); }
.confirm-card h3 { margin: 0 0 12px; font-size: 16px; display: flex; align-items: center; gap: 8px; color: #b91c1c; }
.danger-box {
  background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px;
  padding: 12px 14px; font-size: 13px; color: #7f1d1d; line-height: 1.7; margin: 0 0 14px;
}
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.btn { padding: 8px 14px; border: 1px solid #d1d5db; background: #fff; border-radius: 8px; cursor: pointer; font-size: 12.5px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.btn.danger-btn { background: #dc2626; color: #fff; border-color: #dc2626; }
.btn.danger-btn:hover:not(:disabled) { background: #b91c1c; }
</style>
