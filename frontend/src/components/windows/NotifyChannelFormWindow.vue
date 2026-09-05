<!--
  NotifyChannelFormWindow.vue — 通知渠道 新建/编辑 表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 NotifyWindow 的「添加/编辑通知渠道」modal 弹窗独立为
    桌面窗口，避免点击灰色遮罩误关丢已填内容。支持 Webhook /
    Telegram / 钉钉 / 企业微信 / Server酱 / SMTP 六类渠道，
    按类型动态展示对应配置字段（密码类字段编辑时留空表示不修改）。
  后端模块：
    /api/notify 的 createChannel / updateChannel。
  关键状态：
    form       渠道表单（name / type / config）
    error      必填校验 / 后端错误信息回显
  打开方式：
    由 App.vue 的 openNotifyChannelForm(payload) 打开，props 传入
    { channel: 编辑对象或 null }。保存成功后 emit('close') 自关，
    并经 formBus 通知 NotifyWindow 刷新渠道列表。
-->
<template>
  <div class="channel-form-window">
    <!-- 后端校验错误回显（红底错误框，保留用户已填内容） -->
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">名称</span>
      <input class="ui-input" v-model.trim="form.name" maxlength="64" placeholder="如：运维群 / 告警邮箱" />
    </label>

    <label class="ui-field">
      <span class="ui-label">类型</span>
      <select class="ui-select" v-model="form.type" @change="onTypeChange">
        <option v-for="tp in CHANNEL_TYPES" :key="tp" :value="tp">{{ typeLabel(tp) }}</option>
      </select>
    </label>

    <!-- Webhook -->
    <template v-if="form.type === 'webhook'">
      <label class="ui-field"><span class="ui-label">Webhook URL</span><input class="ui-input" v-model.trim="form.config.url" placeholder="https://example.com/hook" spellcheck="false" /></label>
    </template>
    <!-- Telegram -->
    <template v-else-if="form.type === 'telegram'">
      <label class="ui-field"><span class="ui-label">Bot Token{{ hasSecret('bot_token') ? '（留空保持原值）' : '' }}</span><input class="ui-input" v-model="form.config.bot_token" type="password" autocomplete="new-password" /></label>
      <label class="ui-field"><span class="ui-label">接收 chat_id</span><input class="ui-input" v-model.trim="form.config.chat_id" placeholder="123456789" spellcheck="false" /></label>
    </template>
    <!-- 钉钉 -->
    <template v-else-if="form.type === 'dingtalk'">
      <label class="ui-field"><span class="ui-label">钉钉机器人 Webhook</span><input class="ui-input" v-model.trim="form.config.webhook" placeholder="https://oapi.dingtalk.com/robot/send?access_token=..." spellcheck="false" /></label>
    </template>
    <!-- 企业微信 -->
    <template v-else-if="form.type === 'wecom'">
      <label class="ui-field"><span class="ui-label">企业微信机器人 Webhook</span><input class="ui-input" v-model.trim="form.config.webhook" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." spellcheck="false" /></label>
    </template>
    <!-- Server酱 -->
    <template v-else-if="form.type === 'serverchan'">
      <label class="ui-field"><span class="ui-label">SendKey{{ hasSecret('key') ? '（留空保持原值）' : '' }}</span><input class="ui-input" v-model="form.config.key" type="password" autocomplete="new-password" /></label>
    </template>
    <!-- SMTP -->
    <template v-else-if="form.type === 'smtp'">
      <div class="ui-field-row">
        <label class="ui-field"><span class="ui-label">SMTP 主机</span><input class="ui-input" v-model.trim="form.config.host" placeholder="smtp.example.com" spellcheck="false" /></label>
        <label class="ui-field"><span class="ui-label">端口</span><input class="ui-input" type="number" v-model.number="form.config.port" placeholder="465" /></label>
      </div>
      <label class="ui-field check"><input type="checkbox" v-model="form.config.ssl" style="width:auto;" /><span>SSL 加密</span></label>
      <label class="ui-field check"><input type="checkbox" v-model="form.config.starttls" style="width:auto;" /><span>STARTTLS</span></label>
      <div class="ui-field-row">
        <label class="ui-field"><span class="ui-label">用户名</span><input class="ui-input" v-model.trim="form.config.username" autocomplete="off" spellcheck="false" /></label>
        <label class="ui-field"><span class="ui-label">密码{{ hasSecret('password') ? '（留空保持原值）' : '' }}</span><input class="ui-input" v-model="form.config.password" type="password" autocomplete="new-password" /></label>
      </div>
      <div class="ui-field-row">
        <label class="ui-field"><span class="ui-label">发件人</span><input class="ui-input" v-model.trim="form.config.from" placeholder="alert@example.com" spellcheck="false" /></label>
        <label class="ui-field"><span class="ui-label">收件人（逗号分隔）</span><input class="ui-input" v-model.trim="form.config.to" placeholder="ops@example.com" spellcheck="false" /></label>
      </div>
      <label class="ui-field"><span class="ui-label">邮件主题</span><input class="ui-input" v-model.trim="form.config.subject" placeholder="Graw 资源告警" /></label>
    </template>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">取消</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与 props
import { ref, reactive } from 'vue'
// 通知中心 API：createChannel / updateChannel
import { notifyApi } from '../../api'
// 表单保存信号：通知 NotifyWindow 刷新渠道列表
import { bumpForm } from '../../store/formBus'

// channel: 编辑对象（null = 新建）
const props = defineProps({
  channel: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 必填校验 / 后端错误信息

// 支持的通知渠道类型
const CHANNEL_TYPES = ['webhook', 'telegram', 'dingtalk', 'wecom', 'serverchan', 'smtp']
// 类型 → 中文名（展示用）
const typeLabels = {
  webhook: '通用 Webhook', telegram: 'Telegram', dingtalk: '钉钉',
  wecom: '企业微信', serverchan: 'Server酱', smtp: 'SMTP 邮件'
}
const typeLabel = (tp) => typeLabels[tp] || tp

// 每种渠道的空白配置模板（切换类型时据此重建 config）
function emptyConfig(type) {
  if (type === 'webhook') return { url: '' }
  if (type === 'telegram') return { bot_token: '', chat_id: '' }
  if (type === 'dingtalk' || type === 'wecom') return { webhook: '' }
  if (type === 'serverchan') return { key: '' }
  if (type === 'smtp') return { host: '', port: 465, ssl: true, starttls: false, username: '', password: '', from: '', to: '', subject: '' }
  return {}
}

// 表单初值：编辑时回填（config 以空白模板为准并覆盖已存字段），新建用空白模板
const form = reactive(props.channel
  ? {
      name: props.channel.name || '',
      type: props.channel.type || 'webhook',
      config: Object.assign(emptyConfig(props.channel.type || 'webhook'), props.channel.config || {})
    }
  : { name: '', type: 'webhook', config: emptyConfig('webhook') })

// 类型切换时重建对应配置模板（避免残留上次配置的敏感信息）
function onTypeChange() {
  form.config = emptyConfig(form.type)
}

// 编辑渠道是否已配置某敏感项（用于「留空保持原值」提示）
function hasSecret(key) {
  if (!props.channel) return false
  const c = props.channel.config || {}
  return !!c[`has_${key}`]
}

// --- 保存：新建走 createChannel，编辑走 updateChannel，成功后通知父窗口刷新并自关 ---
async function save() {
  if (saving.value) return   // 防重复提交
  error.value = ''
  if (!form.name.trim()) { error.value = '请填写名称'; return }
  const body = { name: form.name.trim(), type: form.type, config: form.config, enabled: true }
  saving.value = true
  try {
    if (props.channel) await notifyApi.updateChannel(props.channel.id, body)
    else await notifyApi.createChannel(body)
    bumpForm('notify')   // 通知通知中心窗口重新拉取渠道列表
    emit('close')        // 成功后关闭本窗口
  } catch (e) {
    // 后端校验失败：回显错误并保留用户已填内容
    error.value = e.response?.data?.detail || e.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.channel-form-window { padding: 14px; }
.error-box {
  color: #b91c1c;
  font-size: 12.5px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
  word-break: break-all;
}
.check { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.check input { width: auto; }
</style>