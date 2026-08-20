<template>
  <div class="notify-window">
    <!-- 顶部：总开关 + 配置 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge" :class="cfg.enabled ? 'ok' : 'off'">总开关：{{ cfg.enabled ? '已开启' : '已关闭' }}</span>
        <span class="hint">检查间隔 {{ cfg.interval_seconds }}s / 冷却 {{ cfg.cooldown_seconds }}s</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn" :class="{ primary: !cfg.enabled }" :disabled="busy" @click="toggleEnabled">
          {{ cfg.enabled ? '关闭告警' : '开启告警' }}
        </button>
        <button class="btn" :disabled="busy" @click="doTestAlert">测试告警</button>
        <button class="btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
      </div>
    </div>

    <!-- 标签页 -->
    <div class="tabs">
      <button class="tab" :class="{ active: tab === 'channels' }" @click="switchTab('channels')">
        <BellRing :size="14" /> 通知渠道
        <span v-if="channels.length" class="count-badge">{{ channels.length }}</span>
      </button>
      <button class="tab" :class="{ active: tab === 'rules' }" @click="switchTab('rules')">
        <Gauge :size="14" /> 告警规则
        <span v-if="rules.length" class="count-badge">{{ rules.length }}</span>
      </button>
      <button class="tab" :class="{ active: tab === 'logs' }" @click="switchTab('logs')">
        <History :size="14" /> 告警记录
        <span v-if="logs.length" class="count-badge warn">{{ logs.length }}</span>
      </button>
    </div>

    <!-- ============ 渠道 ============ -->
    <div v-if="tab === 'channels'" class="tab-body">
      <div class="table-toolbar">
        <span class="hint">告警推送渠道：Webhook / Telegram / 钉钉 / 企业微信 / Server酱 / SMTP 邮件</span>
        <button class="btn primary" @click="openChannelAdd"><Plus :size="14" /> 添加渠道</button>
      </div>
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="channels.length === 0" class="empty">
        <BellRing :size="40" style="color:#9ca3af;" />
        <div>还没有通知渠道</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr><th>名称</th><th>类型</th><th>配置摘要</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in channels" :key="c.id">
              <td>{{ c.name }}</td>
              <td>{{ typeLabel(c.type) }}</td>
              <td class="mono">{{ summary(c) }}</td>
              <td>
                <span class="badge" :class="c.enabled ? 'ok' : 'off'">{{ c.enabled ? '启用' : '停用' }}</span>
              </td>
              <td class="actions-cell">
                <button class="btn mini" :disabled="busy" @click="toggleChannel(c)">{{ c.enabled ? '停用' : '启用' }}</button>
                <button class="btn mini" :disabled="busy" @click="doTestChannel(c)">测试</button>
                <button class="btn mini" :disabled="busy" @click="openChannelEdit(c)">编辑</button>
                <button class="btn mini danger-text" :disabled="busy" @click="doDeleteChannel(c)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 规则 ============ -->
    <div v-if="tab === 'rules'" class="tab-body">
      <div class="table-toolbar">
        <span class="hint">资源使用率超过阈值时触发告警并推送所有启用渠道</span>
        <button class="btn primary" @click="openRuleAdd"><Plus :size="14" /> 添加规则</button>
      </div>
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="rules.length === 0" class="empty">
        <Gauge :size="40" style="color:#9ca3af;" />
        <div>还没有告警规则</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr><th>指标</th><th>阈值</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rules" :key="r.id">
              <td>{{ metricLabel(r.metric) }}</td>
              <td>≥ {{ r.threshold }}%</td>
              <td><span class="badge" :class="r.enabled ? 'ok' : 'off'">{{ r.enabled ? '启用' : '停用' }}</span></td>
              <td class="actions-cell">
                <button class="btn mini" :disabled="busy" @click="toggleRule(r)">{{ r.enabled ? '停用' : '启用' }}</button>
                <button class="btn mini" :disabled="busy" @click="openRuleEdit(r)">编辑</button>
                <button class="btn mini danger-text" :disabled="busy" @click="doDeleteRule(r)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 记录 ============ -->
    <div v-if="tab === 'logs'" class="tab-body">
      <div class="table-toolbar">
        <span class="hint">历史告警记录（最多保留 200 条）</span>
        <button class="btn danger-text" :disabled="busy" @click="doClearLogs">清空记录</button>
      </div>
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="logs.length === 0" class="empty">
        <History :size="40" style="color:#9ca3af;" />
        <div>暂无告警记录</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr><th>时间</th><th>指标</th><th>数值</th><th>发送结果</th><th>消息</th></tr>
          </thead>
          <tbody>
            <tr v-for="l in logs" :key="l.id">
              <td class="mono">{{ fmtTime(l.time) }}</td>
              <td>{{ metricLabel(l.metric) }}</td>
              <td>{{ l.value }}%（阈值 {{ l.threshold }}%）</td>
              <td>
                <span v-if="l.failed_channels === 0" class="badge ok">全部发送</span>
                <span v-else-if="l.sent_channels === 0" class="badge danger">全部失败</span>
                <span v-else class="badge warn">部分失败</span>
              </td>
              <td class="mono">{{ l.message }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 渠道 新建/编辑 弹窗 ============ -->
    <div v-if="channelFormOpen" class="modal-overlay" @click.self="channelFormOpen = false">
      <div class="modal">
        <h3><BellRing :size="16" /> {{ channelEditing ? '编辑通知渠道' : '添加通知渠道' }}</h3>

        <label class="field">
          <span class="label">名称</span>
          <input v-model.trim="channelForm.name" maxlength="64" placeholder="如：运维群 / 告警邮箱" />
        </label>

        <label class="field">
          <span class="label">类型</span>
          <select v-model="channelForm.type" @change="onChannelTypeChange">
            <option v-for="t in CHANNEL_TYPES" :key="t" :value="t">{{ typeLabel(t) }}</option>
          </select>
        </label>

        <!-- Webhook -->
        <template v-if="channelForm.type === 'webhook'">
          <label class="field"><span class="label">Webhook URL</span><input v-model.trim="channelForm.config.url" placeholder="https://example.com/hook" spellcheck="false" /></label>
        </template>
        <!-- Telegram -->
        <template v-else-if="channelForm.type === 'telegram'">
          <label class="field"><span class="label">Bot Token{{ hasSecret('bot_token') ? '（留空保持原值）' : '' }}</span><input v-model="channelForm.config.bot_token" type="password" autocomplete="new-password" /></label>
          <label class="field"><span class="label">接收 chat_id</span><input v-model.trim="channelForm.config.chat_id" placeholder="123456789" spellcheck="false" /></label>
        </template>
        <!-- 钉钉 -->
        <template v-else-if="channelForm.type === 'dingtalk'">
          <label class="field"><span class="label">钉钉机器人 Webhook</span><input v-model.trim="channelForm.config.webhook" placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." spellcheck="false" /></label>
        </template>
        <!-- 企业微信 -->
        <template v-else-if="channelForm.type === 'wecom'">
          <label class="field"><span class="label">企业微信机器人 Webhook</span><input v-model.trim="channelForm.config.webhook" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." spellcheck="false" /></label>
        </template>
        <!-- Server酱 -->
        <template v-else-if="channelForm.type === 'serverchan'">
          <label class="field"><span class="label">SendKey{{ hasSecret('key') ? '（留空保持原值）' : '' }}</span><input v-model="channelForm.config.key" type="password" autocomplete="new-password" /></label>
        </template>
        <!-- SMTP -->
        <template v-else-if="channelForm.type === 'smtp'">
          <div class="field-row">
            <label class="field"><span class="label">SMTP 主机</span><input v-model.trim="channelForm.config.host" placeholder="smtp.example.com" spellcheck="false" /></label>
            <label class="field"><span class="label">端口</span><input type="number" v-model.number="channelForm.config.port" placeholder="465" /></label>
          </div>
          <label class="field check"><input type="checkbox" v-model="channelForm.config.ssl" /><span>SSL 加密</span></label>
          <label class="field check"><input type="checkbox" v-model="channelForm.config.starttls" /><span>STARTTLS</span></label>
          <div class="field-row">
            <label class="field"><span class="label">用户名</span><input v-model.trim="channelForm.config.username" autocomplete="off" spellcheck="false" /></label>
            <label class="field"><span class="label">密码{{ hasSecret('password') ? '（留空保持原值）' : '' }}</span><input v-model="channelForm.config.password" type="password" autocomplete="new-password" /></label>
          </div>
          <div class="field-row">
            <label class="field"><span class="label">发件人</span><input v-model.trim="channelForm.config.from" placeholder="alert@example.com" spellcheck="false" /></label>
            <label class="field"><span class="label">收件人（逗号分隔）</span><input v-model.trim="channelForm.config.to" placeholder="ops@example.com" spellcheck="false" /></label>
          </div>
          <label class="field"><span class="label">邮件主题</span><input v-model.trim="channelForm.config.subject" placeholder="Graw 资源告警" /></label>
        </template>

        <div v-if="channelFormError" class="error">{{ channelFormError }}</div>
        <div class="actions">
          <button class="btn" :disabled="channelSaving" @click="channelFormOpen = false">取消</button>
          <button class="btn primary" :disabled="channelSaving" @click="saveChannelForm">{{ channelSaving ? '保存中…' : '保存' }}</button>
        </div>
      </div>
    </div>

    <!-- ============ 规则 新建/编辑 弹窗 ============ -->
    <div v-if="ruleFormOpen" class="modal-overlay" @click.self="ruleFormOpen = false">
      <div class="modal">
        <h3><Gauge :size="16" /> {{ ruleEditing ? '编辑告警规则' : '添加告警规则' }}</h3>
        <label class="field">
          <span class="label">指标</span>
          <select v-model="ruleForm.metric">
            <option v-for="m in METRICS" :key="m" :value="m">{{ metricLabel(m) }}</option>
          </select>
        </label>
        <label class="field">
          <span class="label">阈值（%）</span>
          <input type="number" min="0" max="10000" v-model.number="ruleForm.threshold" />
        </label>
        <label class="field check"><input type="checkbox" v-model="ruleForm.enabled" /><span>启用</span></label>
        <div v-if="ruleFormError" class="error">{{ ruleFormError }}</div>
        <div class="actions">
          <button class="btn" :disabled="ruleSaving" @click="ruleFormOpen = false">取消</button>
          <button class="btn primary" :disabled="ruleSaving" @click="saveRuleForm">{{ ruleSaving ? '保存中…' : '保存' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { BellRing, Gauge, History, RefreshCw, Plus } from 'lucide-vue-next'
import { notifyApi } from '../../api'

const CHANNEL_TYPES = ['webhook', 'telegram', 'dingtalk', 'wecom', 'serverchan', 'smtp']
const METRICS = ['cpu', 'mem', 'disk', 'load']

const tab = ref('channels')
const loading = ref(false)
const busy = ref(false)
const channels = ref([])
const rules = ref([])
const logs = ref([])
const cfg = reactive({ enabled: false, interval_seconds: 60, cooldown_seconds: 300 })

// 渠道表单
const channelFormOpen = ref(false)
const channelEditing = ref(null)
const channelSaving = ref(false)
const channelFormError = ref('')
const channelForm = reactive({ name: '', type: 'webhook', config: {} })

// 规则表单
const ruleFormOpen = ref(false)
const ruleEditing = ref(null)
const ruleSaving = ref(false)
const ruleFormError = ref('')
const ruleForm = reactive({ metric: 'cpu', threshold: 90, enabled: true })

const typeLabels = {
  webhook: '通用 Webhook', telegram: 'Telegram', dingtalk: '钉钉',
  wecom: '企业微信', serverchan: 'Server酱', smtp: 'SMTP 邮件',
}
const metricLabels = { cpu: 'CPU 使用率', mem: '内存使用率', disk: '磁盘使用率', load: '系统负载' }
const typeLabel = (t) => typeLabels[t] || t
const metricLabel = (m) => metricLabels[m] || m

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function summary(c) {
  const cfg = c.config || {}
  if (c.type === 'webhook') return cfg.url || ''
  if (c.type === 'telegram') return `chat_id: ${cfg.chat_id || ''}`
  if (c.type === 'dingtalk' || c.type === 'wecom') return cfg.webhook || ''
  if (c.type === 'serverchan') return cfg.has_key ? '已配置 SendKey' : '未配置'
  if (c.type === 'smtp') return `${cfg.host || ''}:${cfg.port || ''} → ${cfg.to || ''}`
  return ''
}

function hasSecret(key) {
  if (!channelEditing.value) return false
  const c = channelEditing.value.config || {}
  return !!c[`has_${key}`]
}

async function loadAll() {
  loading.value = true
  try {
    const [st, ch, ru, lg] = await Promise.all([
      notifyApi.status(), notifyApi.channels(), notifyApi.rules(), notifyApi.logs(100),
    ])
    Object.assign(cfg, st || {})
    channels.value = (ch && ch.channels) || []
    rules.value = (ru && ru.rules) || []
    logs.value = (lg && lg.logs) || []
  } catch (e) {
    alert('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function switchTab(k) {
  tab.value = k
  await loadAll()
}

async function toggleEnabled() {
  busy.value = true
  try {
    const r = await notifyApi.updateConfig({ enabled: !cfg.enabled })
    cfg.enabled = r.enabled
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

async function doTestAlert() {
  if (!confirm('手动触发一次测试告警（当前所有启用规则将按当前指标评估）？')) return
  busy.value = true
  try {
    await notifyApi.testAlert()
    alert('已触发测试告警')
    await loadAll()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// ---------- 渠道 ----------
function emptyConfig(type) {
  const base = {}
  if (type === 'webhook') return { url: '' }
  if (type === 'telegram') return { bot_token: '', chat_id: '' }
  if (type === 'dingtalk' || type === 'wecom') return { webhook: '' }
  if (type === 'serverchan') return { key: '' }
  if (type === 'smtp') return { host: '', port: 465, ssl: true, starttls: false, username: '', password: '', from: '', to: '', subject: '' }
  return base
}

function openChannelAdd() {
  channelEditing.value = null
  channelFormError.value = ''
  Object.assign(channelForm, { name: '', type: 'webhook', config: emptyConfig('webhook') })
  channelFormOpen.value = true
}

function openChannelEdit(c) {
  channelEditing.value = c
  channelFormError.value = ''
  Object.assign(channelForm, {
    name: c.name || '',
    type: c.type || 'webhook',
    config: Object.assign(emptyConfig(c.type), c.config || {}),
  })
  channelFormOpen.value = true
}

function onChannelTypeChange() {
  channelForm.config = emptyConfig(channelForm.type)
}

async function saveChannelForm() {
  if (channelSaving.value) return
  channelFormError.value = ''
  if (!channelForm.name.trim()) { channelFormError.value = '请填写名称'; return }
  const body = { name: channelForm.name.trim(), type: channelForm.type, config: channelForm.config, enabled: true }
  channelSaving.value = true
  try {
    if (channelEditing.value) await notifyApi.updateChannel(channelEditing.value.id, body)
    else await notifyApi.createChannel(body)
    channelFormOpen.value = false
    await loadAll()
  } catch (e) {
    channelFormError.value = e.response?.data?.detail || e.message
  } finally {
    channelSaving.value = false
  }
}

async function toggleChannel(c) {
  busy.value = true
  try {
    await notifyApi.updateChannel(c.id, { name: c.name, type: c.type, config: {}, enabled: !c.enabled })
    await loadAll()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

async function doTestChannel(c) {
  busy.value = true
  try {
    await notifyApi.testChannel(c.id)
    alert(`测试发送成功：${c.name}`)
  } catch (e) {
    alert('发送失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

async function doDeleteChannel(c) {
  if (!confirm(`删除通知渠道「${c.name}」？`)) return
  busy.value = true
  try {
    await notifyApi.deleteChannel(c.id)
    await loadAll()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// ---------- 规则 ----------
function openRuleAdd() {
  ruleEditing.value = null
  ruleFormError.value = ''
  Object.assign(ruleForm, { metric: 'cpu', threshold: 90, enabled: true })
  ruleFormOpen.value = true
}

function openRuleEdit(r) {
  ruleEditing.value = r
  ruleFormError.value = ''
  Object.assign(ruleForm, { metric: r.metric, threshold: r.threshold, enabled: r.enabled !== false })
  ruleFormOpen.value = true
}

async function saveRuleForm() {
  if (ruleSaving.value) return
  ruleFormError.value = ''
  if (ruleForm.threshold == null || isNaN(ruleForm.threshold)) { ruleFormError.value = '请填写阈值'; return }
  const body = { metric: ruleForm.metric, threshold: ruleForm.threshold, enabled: ruleForm.enabled }
  ruleSaving.value = true
  try {
    if (ruleEditing.value) await notifyApi.updateRule(ruleEditing.value.id, body)
    else await notifyApi.createRule(body)
    ruleFormOpen.value = false
    await loadAll()
  } catch (e) {
    ruleFormError.value = e.response?.data?.detail || e.message
  } finally {
    ruleSaving.value = false
  }
}

async function toggleRule(r) {
  busy.value = true
  try {
    await notifyApi.updateRule(r.id, { metric: r.metric, threshold: r.threshold, enabled: !r.enabled })
    await loadAll()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

async function doDeleteRule(r) {
  if (!confirm(`删除告警规则（${metricLabel(r.metric)} ≥ ${r.threshold}%）？`)) return
  busy.value = true
  try {
    await notifyApi.deleteRule(r.id)
    await loadAll()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

async function doClearLogs() {
  if (!confirm('清空全部告警记录？')) return
  busy.value = true
  try {
    await notifyApi.clearLogs()
    logs.value = []
  } catch (e) {
    alert('清空失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.notify-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.status-badge.ok { background: #d1fae5; color: #065f46; }
.status-badge.off { background: #fee2e2; color: #b91c1c; }
.hint { color: #6e6e73; font-size: 12px; }
.toolbar-actions { display: flex; gap: 8px; }

.tabs { display: flex; gap: 4px; margin-bottom: 10px; }
.tab {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border: 1px solid #d1d5db; background: #fff;
  border-radius: 8px; cursor: pointer; font-size: 13px; color: #374151;
}
.tab:hover { background: #f9fafb; }
.tab.active { background: #111827; color: #fff; border-color: #111827; }
.count-badge { min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px; background: #2563eb; color: #fff; font-size: 10px; display: inline-flex; align-items: center; justify-content: center; }
.count-badge.warn { background: #dc2626; }

.tab-body { flex: 1; overflow: auto; }
.table-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; }
.actions-cell { display: flex; gap: 4px; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.mini { padding: 3px 8px; font-size: 11.5px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.danger-text { color: #b91c1c; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.badge.danger { background: #fee2e2; color: #b91c1c; font-weight: 600; }
.badge.warn { background: #fed7aa; color: #9a3412; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 18px; width: 580px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,0.18); max-height: 92vh; overflow: auto; }
.modal h3 { margin: 0 0 12px; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.field { display: block; margin-bottom: 10px; }
.field .label { display: block; font-size: 11px; color: #1d1d1f; font-weight: 600; margin-bottom: 5px; }
.field input, .field select { width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; font-family: inherit; }
.field input:focus, .field select:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.field.check { display: flex; align-items: center; gap: 8px; }
.field.check input { width: auto; }
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }
.error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
