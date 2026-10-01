<!--
  BackupTaskFormWindow.vue — 备份任务 新建/编辑 表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 BackupWindow 的「新建备份任务 / 编辑备份任务」modal 弹窗
    独立为桌面窗口，避免点击灰色遮罩误关丢已填内容。支持计划（cron）、
    保留策略（份数/天数）、启用开关与可选的远程 WebDAV 上传目标。
  后端模块：
    /api/backup 的 createTask / updateTask。
  关键状态：
    form       任务表单对象（字段与后端 tasks 保持一致）
    error      必填校验 / 后端错误信息回显
  打开方式：
    由 App.vue 的 openBackupTaskForm(payload) 打开，props 传入
    { task: 编辑对象或 null, remotes: 远程目标列表（下拉用） }。
    保存成功后 emit('close') 自关，并经 formBus 通知 BackupWindow 刷新。
-->
<template>
  <div class="task-form-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">任务名称</span>
      <input class="ui-input" v-model.trim="form.name" maxlength="64" placeholder="如：网站数据备份" />
    </label>

    <label class="ui-field">
      <span class="ui-label">源路径（要备份的目录或文件，绝对路径）</span>
      <input class="ui-input" v-model.trim="form.source" placeholder="/var/www/html 或 C:\site" spellcheck="false" />
    </label>

    <label class="ui-field">
      <span class="ui-label">备份目录（留空使用默认备份目录）</span>
      <input class="ui-input" v-model.trim="form.target" :placeholder="defaultTarget || '默认备份目录'" spellcheck="false" />
    </label>

    <label class="ui-field">
      <span class="ui-label">计划（cron，留空仅手动）</span>
      <input class="ui-input" v-model.trim="form.schedule" placeholder="30 2 * * *" spellcheck="false" />
      <span class="ui-hint">分 时 日 月 周，留空则只手动备份</span>
    </label>

    <div class="ui-field-row">
      <label class="ui-field">
        <span class="ui-label">保留份数（0=不限）</span>
        <input class="ui-input" type="number" min="0" max="10000" v-model.number="form.keep_count" />
      </label>
      <label class="ui-field">
        <span class="ui-label">保留天数（0=不限）</span>
        <input class="ui-input" type="number" min="0" max="36500" v-model.number="form.keep_days" />
      </label>
    </div>

    <label class="ui-field">
      <span class="ui-label">远程备份目标（可选）</span>
      <select class="ui-select" v-model="form.remote_id">
        <option value="">不远程备份</option>
        <option v-for="r in (remotes || [])" :key="r.id" :value="r.id">{{ r.name }}（{{ r.base }}）</option>
      </select>
      <span class="ui-hint">备份完成后自动上传到所选 WebDAV 目标</span>
    </label>

    <label class="ui-field">
      <span class="ui-label"></span>
      <input type="checkbox" v-model="form.enabled" style="width:auto;" />
      <span style="font-size:13px;">启用计划备份</span>
    </label>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? $t('common.loading') : $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与 props
import { ref, reactive } from 'vue'
// 备份 API：createTask / updateTask
import { backupApi } from '../../api'
// 表单保存信号：通知 BackupWindow 刷新任务列表
import { bumpForm } from '../../store/formBus'

// task: 编辑对象（null = 新建）；remotes: 远程目标列表（父窗口传入，供下拉选择）
const props = defineProps({
  task: { type: Object, default: null },
  remotes: { type: Array, default: () => [] }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 必填校验 / 后端错误信息

// 表单初值：编辑时回填任务字段（enabled 用 !== false 兼容历史缺省值），新建用默认值
const form = reactive(props.task
  ? {
      name: props.task.name || '',
      source: props.task.source || '',
      target: props.task.target || '',
      schedule: props.task.schedule || '',
      keep_count: props.task.keep_count ?? 10,
      keep_days: props.task.keep_days ?? 0,
      enabled: props.task.enabled !== false,
      remote_id: props.task.remote_id || ''
    }
  : { name: '', source: '', target: '', schedule: '', keep_count: 10, keep_days: 0, enabled: true, remote_id: '' })

// 默认备份目录提示：新建时显示服务端默认目录（父窗口未传则不显示）
const defaultTarget = props.remotes && props.remotes._default_dir ? props.remotes._default_dir : ''

// --- 保存：新建走 createTask，编辑走 updateTask，成功后通知父窗口刷新并自关 ---
async function save() {
  if (saving.value) return   // 防重复提交
  error.value = ''
  // 前端必填校验：任务名称与源路径缺一不可
  if (!form.name.trim()) { error.value = '请填写任务名称'; return }
  if (!form.source.trim()) { error.value = '请填写源路径'; return }
  const body = {
    name: form.name.trim(),
    source: form.source.trim(),
    target: form.target.trim(),
    schedule: form.schedule.trim(),
    keep_count: form.keep_count,
    keep_days: form.keep_days,
    enabled: form.enabled,
    remote_id: form.remote_id
  }
  saving.value = true
  try {
    if (props.task) await backupApi.updateTask(props.task.id, body)
    else await backupApi.createTask(body)
    bumpForm('backup')   // 通知备份中心窗口重新拉取列表
    emit('close')        // 成功后关闭本窗口
  } catch (e) {
    // 后端校验失败：回显错误并保留用户已填内容
    error.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.task-form-window { padding: 14px; overflow-y: auto; }
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