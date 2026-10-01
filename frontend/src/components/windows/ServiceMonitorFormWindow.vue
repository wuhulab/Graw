<!--
  ServiceMonitorFormWindow.vue — 服务监控项添加 / 编辑表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 ServiceMonitorWindow 的「添加 / 编辑监控项」modal 弹窗独立成
    桌面窗口，避免点击灰色遮罩误关导致已填内容丢失。props.item 为编辑对象
    （null 表示新增），以此区分新增 / 编辑两条保存路径。
  后端模块：
    /api/svcmonitor 的 createItem / updateItem（对应 api.js 的 svcmonitorApi）。
 关键状态：
    form      表单对象（名称 / 监控类型 / 目标 / 间隔 / 超时 / 启用）
    error     后端校验错误信息（保留用户已填内容）
  打开方式：
    由 App.vue 的 openServiceMonitorForm(payload) 打开，props 传入 { item }。
    保存成功后 bumpForm('svcmonitor') 通知父窗口刷新列表，并 emit('close') 自关窗口。
-->
<template>
  <div class="svcmonitor-form-window">
    <div class="ui-field">
      <span class="ui-label">名称</span>
      <input class="ui-input" v-model.trim="form.name" maxlength="64" placeholder="如：数据库端口 / Web 服务进程" />
    </div>
    <div class="ui-field">
      <span class="ui-label">监控类型</span>
      <select class="ui-select" v-model="form.kind">
        <option value="port">TCP 端口</option>
        <option value="process">进程</option>
        <option value="service">systemd 服务（Linux）</option>
      </select>
    </div>
    <div class="ui-field">
      <span class="ui-label">{{ targetLabel }}</span>
      <input class="ui-input" v-model.trim="form.target" :placeholder="targetPlaceholder" spellcheck="false" />
    </div>
    <div class="ui-field-row">
      <div class="ui-field">
        <span class="ui-label">检查间隔（秒）</span>
        <input class="ui-input" type="number" min="10" max="86400" v-model.number="form.interval_seconds" />
      </div>
      <div class="ui-field">
        <span class="ui-label">超时（秒）</span>
        <input class="ui-input" type="number" min="1" max="30" v-model.number="form.timeout_seconds" />
      </div>
      <div class="ui-field check-field">
        <label class="check-row">
          <input type="checkbox" v-model="form.enabled" />
          <span>启用</span>
        </label>
      </div>
    </div>

    <!-- 后端校验错误回显 -->
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">取消</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态 / 计算属性与 props
import { ref, reactive, computed } from 'vue'
// 服务监控 API：items/createItem/updateItem（保存调用）
import { svcmonitorApi } from '../../api'
// 表单保存信号：通知 ServiceMonitorWindow 刷新列表
import { bumpForm } from '../../store/formBus'

// item: 编辑对象或 null（null 表示新增）
const props = defineProps({
  item: { type: [Object, null], default: null }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 后端校验错误信息

// 编辑模式：props.item 非空即为编辑已有监控项
const isEdit = computed(() => !!props.item)

// 表单对象：编辑时灌入原值，否则使用默认值
const form = reactive({
  name: props.item?.name || '',
  kind: props.item?.kind || 'port',
  target: props.item?.target || '',
  timeout_seconds: props.item?.timeout_seconds ?? 5,
  interval_seconds: props.item?.interval_seconds ?? 60,
  enabled: props.item?.enabled !== false,
})

// 按监控类型给出「目标」输入框的标签与占位提示
const targetLabel = computed(() => {
  return { port: '目标（host:port，host 可省略默认 127.0.0.1）', process: '进程名 / 命令行关键字', service: 'systemd 服务名' }[form.kind] || '目标'
})
const targetPlaceholder = computed(() => {
  return { port: '如：3306 或 127.0.0.1:3306', process: '如：nginx / mysqld', service: '如：nginx.service' }[form.kind] || ''
})

// --- 保存：新增或编辑，成功后通知父窗口刷新并自关 ---
async function save() {
  if (saving.value) return
  error.value = ''
  if (!form.name.trim()) { error.value = '请填写名称'; return }
  if (!form.target.trim()) { error.value = '请填写监控目标'; return }
  const body = {
    name: form.name.trim(), kind: form.kind, target: form.target.trim(),
    timeout_seconds: form.timeout_seconds, interval_seconds: form.interval_seconds, enabled: form.enabled,
  }
  saving.value = true
  try {
    if (isEdit.value) await svcmonitorApi.updateItem(props.item.id, body)
    else await svcmonitorApi.createItem(body)
    bumpForm('svcmonitor')   // 通知服务监控窗口重新拉取列表
    emit('close')            // 成功后关闭本窗口
  } catch (e) {
    // 后端校验失败：把 detail 回显在表单里，保留用户已填内容
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.svcmonitor-form-window { padding: 14px; }
/* 勾选框行：与 ui-field-row 其他字段垂直对齐 */
.check-field { display: flex; align-items: flex-end; }
.check-row { display: flex; align-items: center; gap: 6px; padding-bottom: 8px; font-size: 13px; }
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
</style>