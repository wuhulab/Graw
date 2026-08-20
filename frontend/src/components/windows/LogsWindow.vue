<template>
  <div class="logs-window">
    <div class="toolbar">
      <button class="btn primary" @click="showAdd=true">{{ $t('logs.add') }}</button>
      <button class="btn" @click="refresh">{{ $t('logs.refresh') }}</button>
    </div>
    <div class="layout">
      <div class="sidebar">
        <div v-for="log in logs" :key="log.id" class="log-item" :class="{active: currentId===log.id}" @click="select(log)">
          <div class="log-name">{{ logName(log) }}</div>
          <div class="log-path">{{ log.path }}</div>
          <span class="exist" :class="log.exists?'ok':'warn'">{{ log.exists ? $t('logs.exists') : $t('logs.missing') }}</span>
        </div>
      </div>
      <div class="viewer">
        <div class="viewer-toolbar" v-if="current">
          <span class="meta">{{ $t('logs.viewing', { path: current.path, lines: lines.length }) }}</span>
          <button class="btn small" @click="clearLog(current.path)">{{ $t('logs.clear') }}</button>
          <button class="btn small" @click="loadLog(current.path)">{{ $t('logs.refresh') }}</button>
        </div>
        <pre class="content">{{ contentText }}</pre>
      </div>
    </div>

    <div v-if="showAdd" class="modal-overlay" @click.self="showAdd=false">
      <div class="modal">
        <h3>{{ $t('logs.addTitle') }}</h3>
        <div class="form">
          <label>{{ $t('logs.nameLabel') }}</label><input v-model="addForm.name" />
          <label>{{ $t('logs.pathLabel') }}</label><input v-model="addForm.path" placeholder="/var/log/xxx.log" />
          <div class="actions">
            <button class="btn" @click="showAdd=false">{{ $t('common.cancel') }}</button>
            <button class="btn primary" @click="doAdd">{{ $t('common.save') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：清空日志需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="t('confirmDanger.clearLogsTitle')"
      :message="t('confirmDanger.clearLogsMsg')"
      :input-label="t('confirmDanger.inputPwdLabel')"
      :placeholder="t('confirmDanger.inputPwdPlaceholder')"
      :confirm-label="$t('logs.clear')"
      @confirm="doClearLog"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { logsApi } from '../../api'
import ConfirmDialog from '../ConfirmDialog.vue'

const { t } = useI18n()
const logs = ref([])
const currentId = ref(null)
const current = ref(null)
const lines = ref([])
const showAdd = ref(false)
const addForm = ref({ name: '', path: '' })
// 高风险操作二次确认状态
const confirm = ref({ show: false, path: '' })

const contentText = computed(() => lines.value.join(''))

// 内置日志的 desc（如“面板日志”）由后端返回中文名，这里按 id 走 i18n 翻译；
// 自定义日志用用户填写的名称。
function logName(log) {
  if (log && log.builtin) {
    const key = `logs.source.${log.id}`
    const translated = t(key)
    // 若该键缺失，t 会原样返回 key 路径，此时退回后端名称
    return translated === key ? (log.name || '') : translated
  }
  return log ? log.name : ''
}

async function refresh() {
  const data = await logsApi.list()
  logs.value = data.logs || []
}

function select(log) {
  currentId.value = log.id
  current.value = log
  if (log.exists) loadLog(log.path)
}

async function loadLog(path) {
  const data = await logsApi.read(path, 500)
  lines.value = data.lines || []
}

function clearLog(path) {
  // 高风险操作：清空日志需输入面板密码确认
  confirm.value = { show: true, path }
}

async function doClearLog() {
  const path = confirm.value.path
  confirm.value.show = false
  if (!path) return
  await logsApi.clear(path)
  await loadLog(path)
}

async function doAdd() {
  await logsApi.add({ name: addForm.value.name, path: addForm.value.path })
  showAdd.value = false
  await refresh()
}

onMounted(refresh)
</script>

<style scoped>
.logs-window { padding: 10px; display: flex; flex-direction: column; height: 100%; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.layout { display: flex; gap: 10px; flex: 1; min-height: 0; }
.sidebar { width: 220px; border: 1px solid #e5e7eb; border-radius: 8px; overflow: auto; background: #fff; }
.log-item { padding: 10px 12px; border-bottom: 1px solid #f3f4f6; cursor: pointer; }
.log-item:hover, .log-item.active { background: #f9fafb; }
.log-name { font-weight: 600; font-size: 13px; }
.log-path { font-size: 11px; color: #6b7280; word-break: break-all; }
.exist { font-size: 11px; padding: 1px 6px; border-radius: 999px; }
.exist.ok { background: #d1fae5; color: #065f46; }
.exist.warn { background: #fee2e2; color: #991b1b; }
.viewer { flex: 1; border: 1px solid #e5e7eb; border-radius: 8px; display: flex; flex-direction: column; background: #fff; overflow: hidden; }
.viewer-toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid #f3f4f6; }
.meta { font-size: 12px; color: #6b7280; margin-right: auto; }
.content { flex: 1; padding: 10px; overflow: auto; font-size: 12px; line-height: 1.5; background: #fff; color: #111827; margin: 0; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.small { padding: 4px 8px; font-size: 12px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 420px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
