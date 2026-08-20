<template>
  <div class="certcheck-window">
    <!-- 顶部：开关 + 汇总 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge" :class="status.enabled ? 'ok' : 'off'">提醒开关：{{ status.enabled ? '已开启' : '已关闭' }}</span>
        <span class="hint">共 {{ status.cert_count || 0 }} 个证书 / 临期 {{ status.warn_count || 0 }} / 过期 {{ status.expired_count || 0 }}</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn" :class="{ primary: !status.enabled }" :disabled="busy" @click="toggleEnabled">
          {{ status.enabled ? '关闭提醒' : '开启提醒' }}
        </button>
        <button class="btn" :disabled="busy" @click="doCheck">立即检查</button>
        <button class="btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
      </div>
    </div>

    <!-- 证书列表 -->
    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="certs.length === 0" class="empty">
      <Lock :size="40" style="color:#9ca3af;" />
      <div>还没有证书，先在「SSL」窗口上传或申请证书</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>证书</th>
            <th>域名</th>
            <th>到期时间</th>
            <th>剩余天数</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in certs" :key="c.id">
            <td>{{ c.name || c.id }}</td>
            <td class="mono">{{ (c.domains || []).join(', ') || '—' }}</td>
            <td class="mono">{{ c.expiry ? c.expiry.slice(0, 16).replace('T', ' ') : '—' }}</td>
            <td :class="{'warn-text': c.status !== 'ok' && c.status !== 'unknown'}">
              {{ c.days_left != null ? (c.days_left < 0 ? '已过期' : c.days_left + ' 天') : '—' }}
            </td>
            <td>
              <span v-if="c.status === 'ok'" class="badge ok">正常</span>
              <span v-else-if="c.status === 'warn'" class="badge warn">临期</span>
              <span v-else-if="c.status === 'expired'" class="badge danger">已过期</span>
              <span v-else class="badge off">无法解析</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 配置 -->
    <div class="config-row">
      <label class="field">
        <span class="label">提醒阈值（剩余天数，逗号分隔）</span>
        <input v-model.trim="remindDaysText" placeholder="30,7" spellcheck="false" />
        <span class="hint">证书剩余天数 ≤ 阈值时推送通知，每档只提醒一次</span>
      </label>
      <button class="btn primary" :disabled="busy" @click="saveConfig">保存配置</button>
      <span v-if="configMsg" :class="['msg', configMsgType]">{{ configMsg }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { Lock, RefreshCw } from 'lucide-vue-next'
import { certcheckApi } from '../../api'

const loading = ref(false)
const busy = ref(false)
const certs = ref([])
const status = reactive({ enabled: false, cert_count: 0, warn_count: 0, expired_count: 0, remind_days: [30, 7] })
const remindDaysText = ref('30,7')
const configMsg = ref('')
const configMsgType = ref('')

async function loadAll() {
  loading.value = true
  try {
    const [st, cs] = await Promise.all([certcheckApi.status(), certcheckApi.certs()])
    Object.assign(status, st || {})
    certs.value = (cs && cs.certs) || []
    remindDaysText.value = (st && st.remind_days && st.remind_days.length ? st.remind_days : [30, 7]).join(',')
  } catch (e) {
    alert('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function toggleEnabled() {
  busy.value = true
  try {
    const r = await certcheckApi.updateConfig({ enabled: !status.enabled })
    status.enabled = r.enabled
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

async function doCheck() {
  busy.value = true
  configMsg.value = ''
  try {
    const r = await certcheckApi.test()
    configMsg.value = `检查完成，触发 ${r.triggered || 0} 条提醒`
    configMsgType.value = 'ok'
    await loadAll()
  } catch (e) {
    configMsg.value = e.response?.data?.detail || e.message
    configMsgType.value = 'err'
  } finally {
    busy.value = false
  }
}

async function saveConfig() {
  busy.value = true
  configMsg.value = ''
  const days = (remindDaysText.value || '').split(/[,，\s]+/).map((s) => parseInt(s, 10)).filter((n) => !isNaN(n) && n > 0)
  if (days.length === 0) { configMsg.value = '请填写有效的提醒天数'; configMsgType.value = 'err'; busy.value = false; return }
  try {
    const r = await certcheckApi.updateConfig({ remind_days: days })
    configMsg.value = '配置已保存'
    configMsgType.value = 'ok'
  } catch (e) {
    configMsg.value = e.response?.data?.detail || e.message
    configMsgType.value = 'err'
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.certcheck-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.status-badge.ok { background: #d1fae5; color: #065f46; }
.status-badge.off { background: #fee2e2; color: #b91c1c; }
.hint { color: #6e6e73; font-size: 12px; }
.toolbar-actions { display: flex; gap: 8px; }

.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; flex: 1; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; }
.warn-text { color: #b45309; font-weight: 600; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.badge.warn { background: #fed7aa; color: #9a3412; }
.badge.danger { background: #fee2e2; color: #b91c1c; font-weight: 600; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }

.config-row { display: flex; align-items: flex-end; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.field { flex: 1; min-width: 220px; display: block; }
.field .label { display: block; font-size: 11px; color: #1d1d1f; font-weight: 600; margin-bottom: 5px; }
.field input { width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; font-family: inherit; }
.field input:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.field .hint { display: block; margin-top: 4px; }
.msg { font-size: 12px; }
.msg.ok { color: #065f46; }
.msg.err { color: #b91c1c; }
</style>
