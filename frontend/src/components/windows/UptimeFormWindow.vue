<!--
  UptimeFormWindow.vue — 站点可用性监控项添加 / 编辑表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 UptimeWindow 的「添加 / 编辑监控项」modal 弹窗独立成桌面窗口，
    避免点击灰色遮罩误关导致已填内容丢失。props.item 为编辑对象
    （null 表示新增），以此区分新增 / 编辑两条保存路径。
  后端模块：
    /api/uptime 的 createItem / updateItem（对应 api.js 的 uptimeApi）。
  关键状态：
    form      表单对象（名称 / URL / 预期状态码 / 间隔 / 超时 / 启用）
    error     后端校验错误信息（保留用户已填内容）
  打开方式：
    由 App.vue 的 openUptimeForm(payload) 打开，props 传入 { item }。
    保存成功后 bumpForm('uptime') 通知父窗口刷新列表，并 emit('close') 自关窗口。
-->
<template>
  <div class="uptime-form-window">
    <div class="ui-field">
      <span class="ui-label">名称</span>
      <input class="ui-input" v-model.trim="form.name" maxlength="64" placeholder="如：官网 / API 服务" />
    </div>
    <div class="ui-field">
      <span class="ui-label">监控地址（http/https URL）</span>
      <input class="ui-input" v-model.trim="form.url" placeholder="https://example.com" spellcheck="false" />
    </div>
    <div class="ui-field-row">
      <div class="ui-field">
        <span class="ui-label">预期状态码</span>
        <input class="ui-input" type="number" min="100" max="599" v-model.number="form.expect_status" />
      </div>
      <div class="ui-field">
        <span class="ui-label">检查间隔（秒）</span>
        <input class="ui-input" type="number" min="10" max="86400" v-model.number="form.interval_seconds" />
      </div>
    </div>
    <div class="ui-field-row">
      <div class="ui-field">
        <span class="ui-label">超时（秒）</span>
        <input class="ui-input" type="number" min="1" max="60" v-model.number="form.timeout_seconds" />
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
// 可用性监控 API：items/createItem/updateItem（保存调用）
import { uptimeApi } from '../../api'
// 表单保存信号：通知 UptimeWindow 刷新列表
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

// 表单对象：编辑时灌入原值，否则使用默认值（默认 200 期望码、60 秒间隔、开启）
const form = reactive({
  name: props.item?.name || '',
  url: props.item?.url || '',
  expect_status: props.item?.expect_status ?? 200,
  timeout_seconds: props.item?.timeout_seconds ?? 10,
  interval_seconds: props.item?.interval_seconds ?? 60,
  enabled: props.item?.enabled !== false,
})

// --- 保存：新增或编辑，成功后通知父窗口刷新并自关 ---
async function save() {
  if (saving.value) return
  error.value = ''
  if (!form.name.trim()) { error.value = '请填写名称'; return }
  if (!form.url.trim()) { error.value = '请填写监控地址'; return }
  const body = {
    name: form.name.trim(), url: form.url.trim(), expect_status: form.expect_status,
    timeout_seconds: form.timeout_seconds, interval_seconds: form.interval_seconds, enabled: form.enabled,
  }
  saving.value = true
  try {
    if (isEdit.value) await uptimeApi.updateItem(props.item.id, body)
    else await uptimeApi.createItem(body)
    bumpForm('uptime')       // 通知可用性监控窗口重新拉取列表
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
.uptime-form-window { padding: 14px; }
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