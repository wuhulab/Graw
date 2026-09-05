<!--
  LogCollectFormWindow.vue — 添加自定义日志源表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 LogsWindow 的「添加日志源」modal 弹窗独立成桌面窗口，
    避免点击灰色遮罩误关导致已填内容丢失。表单负责「名称 + 路径」。
  后端模块：
    /api/logs 的 add（添加自定义日志源）。
  关键状态：
    form   新日志源表单对象（名称 + 路径）
    error  后端校验错误信息（保存失败回显，保留用户已填内容）
    saving 保存中（禁用按钮防重复提交）
  打开方式：
    由 App.vue 的 openLogCollectForm(payload) 打开，props 传入 { source }（当前暂仅新增）。
    保存成功后 emit('close') 自关窗口，并经 formBus 通知 LogsWindow 刷新日志源列表。
-->
<template>
  <div class="logcollect-form-window">
    <!-- 后端校验错误回显（顶部留出错误框，不清空用户输入） -->
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="ui-field">
      <span class="ui-label">{{ $t('logs.nameLabel') }}</span>
      <input class="ui-input" v-model.trim="form.name" />
    </div>

    <div class="ui-field">
      <span class="ui-label">{{ $t('logs.pathLabel') }}</span>
      <input class="ui-input" v-model.trim="form.path" placeholder="/var/log/xxx.log" />
    </div>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? $t('common.loading') : $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态
import { ref, reactive } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 日志 API：add（添加自定义日志源）
import { logsApi } from '../../api'
// 表单保存信号：通知 LogsWindow 刷新日志源列表
import { bumpForm } from '../../store/formBus'

const { t } = useI18n()

// source: 编辑对象或 null（由 App.vue 打开窗口时传入；当前后端仅支持新增）
const props = defineProps({
  source: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 后端校验错误信息

// 表单对象：名称 + 路径（编辑时从 props.source 回填，缺省为空模板）
const form = reactive(props.source
  ? { name: props.source.name || '', path: props.source.path || '' }
  : { name: '', path: '' })

// --- 保存：调用添加接口，成功后通知父窗口刷新并自关 ---
async function save() {
  error.value = ''
  if (!form.name.trim()) { error.value = t('logs.nameLabel'); return }
  if (!form.path.trim()) { error.value = t('logs.pathLabel'); return }
  saving.value = true
  try {
    await logsApi.add({ name: form.name.trim(), path: form.path.trim() })   // 调用 /api/logs/add
    bumpForm('logs')    // 通知 LogsWindow 重新拉取日志源列表
    emit('close')       // 成功后关闭本窗口
  } catch (e) {
    // 后端校验失败：把 detail 回显在表单顶部，保留用户已填内容
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.logcollect-form-window { padding: 14px; display: flex; flex-direction: column; gap: 2px; }
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