<!--
  通知中心窗口
  业务：管理告警渠道（Webhook/Telegram/钉钉/企业微信/Server酱/SMTP）、告警规则（资源阈值）与历史告警记录。
  后端模块：/api/notify
  关键状态：channels / rules / logs（三标签页数据）、cfg（全局开关与间隔）、confirm（删除/清空高危二次确认）
  表单拆分：
    新增/编辑「通知渠道」「告警规则」已拆为独立窗口 NotifyChannelFormWindow /
    NotifyRuleFormWindow：保存成功后 bumpForm('notify') 触发此处 watch 重新拉取数据。
  打开方式：ShunX 安全中心「通知」标签页聚合，或独立「通知中心」入口挂载
-->
<template>
  <div class="notify-window">
    <!-- 顶部：总开关 + 配置 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge" :class="cfg.enabled ? 'ok' : 'off'">总开关：{{ cfg.enabled ? '已开启' : '已关闭' }}</span>
        <span class="ui-hint">检查间隔 {{ cfg.interval_seconds }}s / 冷却 {{ cfg.cooldown_seconds }}s</span>
      </div>
      <div class="toolbar-actions">
        <button class="ui-btn" :class="{ primary: !cfg.enabled }" :disabled="busy" @click="toggleEnabled">
          {{ cfg.enabled ? '关闭告警' : '开启告警' }}
        </button>
        <button class="ui-btn" :disabled="busy" @click="doTestAlert">测试告警</button>
        <button class="ui-btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
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
        <span class="ui-hint">告警推送渠道：Webhook / Telegram / 钉钉 / 企业微信 / Server酱 / SMTP 邮件</span>
        <button class="ui-btn primary" @click="emit('openNotifyChannelForm', { channel: null })"><Plus :size="14" /> 添加渠道</button>
      </div>
      <div v-if="loading" class="ui-empty">加载中…</div>
      <div v-else-if="channels.length === 0" class="ui-empty">
        <BellRing :size="40" style="color:#9ca3af;" />
        <div>还没有通知渠道</div>
      </div>
      <div v-else class="ui-table-wrap">
        <table>
          <thead>
            <tr><th>名称</th><th>类型</th><th>配置摘要</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in channels" :key="c.id">
              <td>{{ c.name }}</td>
              <td>{{ typeLabel(c.type) }}</td>
              <td class="ui-mono">{{ summary(c) }}</td>
              <td>
                <span class="ui-badge" :class="c.enabled ? 'ok' : 'off'">{{ c.enabled ? '启用' : '停用' }}</span>
              </td>
              <td class="actions-cell">
                <button class="ui-btn mini" :disabled="busy" @click="toggleChannel(c)">{{ c.enabled ? '停用' : '启用' }}</button>
                <button class="ui-btn mini" :disabled="busy" @click="doTestChannel(c)">测试</button>
                <button class="ui-btn mini" :disabled="busy" @click="emit('openNotifyChannelForm', { channel: c })">编辑</button>
                <button class="ui-btn mini danger-text" :disabled="busy" @click="doDeleteChannel(c)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 规则 ============ -->
    <div v-if="tab === 'rules'" class="tab-body">
      <div class="table-toolbar">
        <span class="ui-hint">资源使用率超过阈值时触发告警并推送所有启用渠道</span>
        <button class="ui-btn primary" @click="emit('openNotifyRuleForm', { rule: null })"><Plus :size="14" /> 添加规则</button>
      </div>
      <div v-if="loading" class="ui-empty">加载中…</div>
      <div v-else-if="rules.length === 0" class="ui-empty">
        <Gauge :size="40" style="color:#9ca3af;" />
        <div>还没有告警规则</div>
      </div>
      <div v-else class="ui-table-wrap">
        <table>
          <thead>
            <tr><th>指标</th><th>阈值</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in rules" :key="r.id">
              <td>{{ metricLabel(r.metric) }}</td>
              <td>≥ {{ r.threshold }}%</td>
              <td><span class="ui-badge" :class="r.enabled ? 'ok' : 'off'">{{ r.enabled ? '启用' : '停用' }}</span></td>
              <td class="actions-cell">
                <button class="ui-btn mini" :disabled="busy" @click="toggleRule(r)">{{ r.enabled ? '停用' : '启用' }}</button>
                <button class="ui-btn mini" :disabled="busy" @click="emit('openNotifyRuleForm', { rule: r })">编辑</button>
                <button class="ui-btn mini danger-text" :disabled="busy" @click="doDeleteRule(r)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 记录 ============ -->
    <div v-if="tab === 'logs'" class="tab-body">
      <div class="table-toolbar">
        <span class="ui-hint">历史告警记录（最多保留 200 条）</span>
        <button class="ui-btn ghost danger-text" :disabled="busy" @click="doClearLogs">清空记录</button>
      </div>
      <div v-if="loading" class="ui-empty">加载中…</div>
      <div v-else-if="logs.length === 0" class="ui-empty">
        <History :size="40" style="color:#9ca3af;" />
        <div>暂无告警记录</div>
      </div>
      <div v-else class="ui-table-wrap">
        <table>
          <thead>
            <tr><th>时间</th><th>指标</th><th>数值</th><th>发送结果</th><th>消息</th></tr>
          </thead>
          <tbody>
            <tr v-for="l in logs" :key="l.id">
              <td class="ui-mono">{{ fmtTime(l.time) }}</td>
              <td>{{ metricLabel(l.metric) }}</td>
              <td>{{ l.value }}%（阈值 {{ l.threshold }}%）</td>
              <td>
                <span v-if="l.failed_channels === 0" class="ui-badge ok">全部发送</span>
                <span v-else-if="l.sent_channels === 0" class="ui-badge danger">全部失败</span>
                <span v-else class="ui-badge warn">部分失败</span>
              </td>
              <td class="ui-mono">{{ l.message }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除渠道/规则、清空记录需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="confirm.title"
      :message="confirm.message"
      :input-label="t('confirmDanger.inputPwdLabel')"
      :placeholder="t('confirmDanger.inputPwdPlaceholder')"
      :confirm-label="confirm.confirmLabel"
      @confirm="doConfirm"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'     // Composition API 响应式与生命周期钩子
import { useI18n } from 'vue-i18n'                          // 国际化：取 t() 生成动态文案
import { BellRing, Gauge, History, RefreshCw, Plus } from 'lucide-vue-next'   // 图标集合
import { notifyApi } from '../../api'                       // 通知中心后端接口封装
import ConfirmDialog from '../ConfirmDialog.vue'            // 高危操作二次确认弹窗（输入面板密码）
import { formBus } from '../../store/formBus'               // 表单保存信号：独立表单窗口保存成功后刷新列表

const { t } = useI18n()

const emit = defineEmits(['openNotifyChannelForm', 'openNotifyRuleForm'])   // 打开独立「渠道 / 规则」表单窗口

const tab = ref('channels')
const loading = ref(false)
const busy = ref(false)
const channels = ref([])
const rules = ref([])
const logs = ref([])
const cfg = reactive({ enabled: false, interval_seconds: 60, cooldown_seconds: 300 })

// 高风险操作二次确认状态（删除渠道/规则、清空记录共用；action.type 区分操作）
const confirm = ref({ show: false, title: '', message: '', confirmLabel: '', action: null })

// 新增/编辑渠道、规则表单已拆为独立窗口（NotifyChannelFormWindow /
// NotifyRuleFormWindow）：保存成功后 bumpForm('notify') 触发此处重载
watch(() => formBus.notify, loadAll)

const typeLabels = {
  webhook: '通用 Webhook', telegram: 'Telegram', dingtalk: '钉钉',
  wecom: '企业微信', serverchan: 'Server酱', smtp: 'SMTP 邮件',
}
const metricLabels = { cpu: 'CPU 使用率', mem: '内存使用率', disk: '磁盘使用率', load: '系统负载' }
const typeLabel = (tp) => typeLabels[tp] || tp
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
  if (!window.confirm('手动触发一次测试告警（当前所有启用规则将按当前指标评估）？')) return
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
  // 高风险操作：删除通知渠道需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.deleteNotifyChannelTitle'),
    message: t('confirmDanger.deleteNotifyChannelMsg', { name: c.name }),
    confirmLabel: t('common.delete'),
    action: { type: 'channel', id: c.id }
  }
}

// ---------- 规则 ----------
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
  // 高风险操作：删除告警规则需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.deleteAlertRuleTitle'),
    message: t('confirmDanger.deleteAlertRuleMsg'),
    confirmLabel: t('common.delete'),
    action: { type: 'rule', id: r.id }
  }
}

async function doClearLogs() {
  // 高风险操作：清空全部告警记录需输入面板密码确认
  confirm.value = {
    show: true,
    title: '清空告警记录确认',
    message: '清空全部告警记录？此操作不可恢复。\n请输入面板密码以确认。',
    confirmLabel: '清空',
    action: { type: 'clearLogs' }
  }
}

// ConfirmDialog 密码校验通过后按 action.type 分发执行真正的删除/清空逻辑
async function doConfirm() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return
  busy.value = true
  try {
    if (a.type === 'channel') {
      await notifyApi.deleteChannel(a.id)
      await loadAll()
    } else if (a.type === 'rule') {
      await notifyApi.deleteRule(a.id)
      await loadAll()
    } else if (a.type === 'clearLogs') {
      await notifyApi.clearLogs()
      logs.value = []
    }
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.notify-window { padding: 0; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; } /* 内嵌聚合窗口：外边距由父容器提供 */
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.status-badge.ok { background: #d1fae5; color: #065f46; }
.status-badge.off { background: #fee2e2; color: #b91c1c; }
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
.actions-cell { display: flex; gap: 4px; }
.danger-text { color: #b91c1c; }
</style>