<!--
  NotifyRuleFormWindow.vue — 告警规则 新建/编辑 表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 NotifyWindow 的「添加/编辑告警规则」modal 弹窗独立为
    桌面窗口，避免点击灰色遮罩误关丢已填内容。维护资源指标（CPU/
    内存/磁盘/负载）的使用率阈值与启用开关。
  后端模块：
    /api/notify 的 createRule / updateRule。
  关键状态：
    form   规则表单（metric / threshold / enabled）
    error  必填校验 / 后端错误信息回显
  打开方式：
    由 App.vue 的 openNotifyRuleForm(payload) 打开，props 传入
    { rule: 编辑对象或 null }。保存成功后 emit('close') 自关，
    并经 formBus 通知 NotifyWindow 刷新规则列表。
-->
<template>
  <div class="rule-form-window">
    <!-- 后端校验错误回显（红底错误框，保留用户已填内容） -->
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">指标</span>
      <select class="ui-select" v-model="form.metric">
        <option v-for="m in METRICS" :key="m" :value="m">{{ metricLabel(m) }}</option>
      </select>
    </label>

    <label class="ui-field">
      <span class="ui-label">阈值（%）</span>
      <input class="ui-input" type="number" min="0" max="10000" v-model.number="form.threshold" />
    </label>

    <label class="ui-field check">
      <input type="checkbox" v-model="form.enabled" style="width:auto;" />
      <span>启用</span>
    </label>

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
// 通知中心 API：createRule / updateRule
import { notifyApi } from '../../api'
// 表单保存信号：通知 NotifyWindow 刷新规则列表
import { bumpForm } from '../../store/formBus'

// rule: 编辑对象（null = 新建）
const props = defineProps({
  rule: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 必填校验 / 后端错误信息

// 可监控的资源指标
const METRICS = ['cpu', 'mem', 'disk', 'load']
// 指标 → 中文名（展示用）
const metricLabels = { cpu: 'CPU 使用率', mem: '内存使用率', disk: '磁盘使用率', load: '系统负载' }
const metricLabel = (m) => metricLabels[m] || m

// 表单初值：编辑时回填（enabled 用 !== false 兼容历史缺省值），新建用默认值
const form = reactive(props.rule
  ? { metric: props.rule.metric || 'cpu', threshold: props.rule.threshold ?? 90, enabled: props.rule.enabled !== false }
  : { metric: 'cpu', threshold: 90, enabled: true })

// --- 保存：新建走 createRule，编辑走 updateRule，成功后通知父窗口刷新并自关 ---
async function save() {
  if (saving.value) return   // 防重复提交
  error.value = ''
  if (form.threshold == null || isNaN(form.threshold)) { error.value = '请填写阈值'; return }
  const body = { metric: form.metric, threshold: form.threshold, enabled: form.enabled }
  saving.value = true
  try {
    if (props.rule) await notifyApi.updateRule(props.rule.id, body)
    else await notifyApi.createRule(body)
    bumpForm('notify')   // 通知通知中心窗口重新拉取规则列表
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
.rule-form-window { padding: 14px; }
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