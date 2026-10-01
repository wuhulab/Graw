<!--
  DatabaseCreateWindow.vue — 创建数据库（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 DatabaseWindow 管理弹窗内的「新建数据库」modal 弹窗独立为
    桌面窗口，避免误点灰色遮罩丢失已输入的库名。
  后端模块：
    /api/databases 的 createDB。
  关键状态：
    dbName  新建库名称
    error   后端错误信息回显
  打开方式：
    由 DatabaseManageWindow emit('openDatabaseCreate') 触发，
    App.vue 打开本窗口，props 传入 { conn }（所属连接）。
    保存成功后 emit('close')，并经 formBus 通知管理窗口刷新库列表。
-->
<template>
  <div class="create-db-window">
    <div v-if="error" class="error-box">{{ error }}</div>
    <label class="ui-field">
      <span class="ui-label">{{ $t('database.createDBTitle') }}</span>
      <input class="ui-input" v-model.trim="dbName" :placeholder="$t('database.dbNamePlaceholder')" @keyup.enter="create" />
    </label>
    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving || !dbName.trim()" @click="create">
        {{ saving ? $t('common.loading') : $t('common.create') }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与 props
import { ref } from 'vue'
// 数据库接口
import { databasesApi } from '../../api'
// 表单保存信号：通知数据库管理窗口刷新库列表
import { bumpForm } from '../../store/formBus'

// conn: 所属数据库连接
const props = defineProps({
  conn: { type: Object, required: true }
})
const emit = defineEmits(['close'])

const dbName = ref('')   // 新建库名称
const saving = ref(false)   // 创建中（禁用按钮防重复提交）
const error = ref('')       // 后端错误信息

// --- 创建数据库：成功后通知管理窗口刷新并自关 ---
async function create() {
  if (saving.value || !dbName.value.trim()) return
  saving.value = true
  error.value = ''
  try {
    await databasesApi.createDB(props.conn.id, dbName.value.trim())
    bumpForm('databases')   // 通知数据库管理窗口刷新库列表
    emit('close')
  } catch (e) {
    // 后端校验失败（如库已存在 / 非法名称）：回显并保留输入
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.create-db-window { padding: 14px; }
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