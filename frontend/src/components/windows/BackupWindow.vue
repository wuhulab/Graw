<template>
  <div class="backup-window">
    <!-- 顶部：全局状态 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge">
          <DatabaseBackup :size="14" /> 备份目录：
          <span class="mono">{{ status.backup_dir || '—' }}</span>
        </span>
        <span class="hint">共 {{ status.task_count || 0 }} 个任务 / {{ status.file_count || 0 }} 个备份文件 / {{ formatBytes(status.total_size) }}</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn" :disabled="loading" @click="loadAll">
          <RefreshCw :size="14" /> 刷新
        </button>
      </div>
    </div>

    <!-- 标签页 -->
    <div class="tabs">
      <button class="tab" :class="{ active: tab === 'tasks' }" @click="switchTab('tasks')">
        <ListChecks :size="14" /> 备份任务
        <span v-if="tasks.length" class="count-badge">{{ tasks.length }}</span>
      </button>
      <button class="tab" :class="{ active: tab === 'records' }" @click="switchTab('records')">
        <History :size="14" /> 备份记录
        <span v-if="records.length" class="count-badge warn">{{ records.length }}</span>
      </button>
      <button class="tab" :class="{ active: tab === 'remotes' }" @click="switchTab('remotes')">
        <CloudUpload :size="14" /> 远程备份
        <span v-if="remotes.length" class="count-badge">{{ remotes.length }}</span>
      </button>
    </div>

    <!-- ============ 标签页一：备份任务 ============ -->
    <div v-if="tab === 'tasks'" class="tab-body">
      <div class="table-toolbar">
        <span class="hint">手动或按计划备份目录/文件，自动按保留策略清理旧备份，可选上传到远程 WebDAV</span>
        <button class="btn primary" @click="openAdd">
          <Plus :size="14" /> 新建备份任务
        </button>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="tasks.length === 0" class="empty">
        <Archive :size="40" style="color:#9ca3af;" />
        <div>还没有备份任务，点击「新建备份任务」创建第一个吧</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>任务名称</th>
              <th>源路径</th>
              <th>计划</th>
              <th>保留策略</th>
              <th>远程</th>
              <th>记录数</th>
              <th>最近备份</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in tasks" :key="t.id">
              <td>
                {{ t.name }}
                <div class="sub" :title="t.id">{{ t.type === 'file' ? '文件' : '目录' }}</div>
              </td>
              <td class="mono" :title="t.target || status.backup_dir">{{ t.source }}</td>
              <td class="mono">{{ t.schedule || '仅手动' }}</td>
              <td>{{ keepText(t) }}</td>
              <td>{{ remoteName(t.remote_id) || '—' }}</td>
              <td>{{ t.record_count || 0 }}</td>
              <td class="mono">{{ fmtTime(t.last_backup_at) }}</td>
              <td>
                <span class="badge" :class="t.last_status === 'ok' ? 'ok' : (t.last_status === 'error' ? 'danger' : 'off')">
                  {{ t.last_status === 'ok' ? '成功' : (t.last_status === 'error' ? '失败' : '—') }}
                </span>
              </td>
              <td class="actions-cell">
                <button class="btn mini" :disabled="busy" @click="doRun(t)">立即备份</button>
                <button class="btn mini" :disabled="busy" @click="openEdit(t)">编辑</button>
                <button class="btn mini danger-text" :disabled="busy" @click="doDelete(t)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 标签页二：备份记录 ============ -->
    <div v-if="tab === 'records'" class="tab-body">
      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="records.length === 0" class="empty">
        <Archive :size="40" style="color:#9ca3af;" />
        <div>暂无备份记录</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>所属任务</th>
              <th>文件名</th>
              <th>大小</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in records" :key="r.id">
              <td class="mono">{{ fmtTime(r.created_at) }}</td>
              <td>{{ r.task_name || '—' }}</td>
              <td class="mono">{{ r.name }}</td>
              <td>{{ formatBytes(r.size) }}</td>
              <td class="actions-cell">
                <button v-if="r.task_id" class="btn mini" :disabled="busy" @click="openRestore(r)">恢复</button>
                <button class="btn mini danger-text" :disabled="busy" @click="doDeleteRecord(r)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 标签页三：远程备份目标 ============ -->
    <div v-if="tab === 'remotes'" class="tab-body">
      <div class="table-toolbar">
        <span class="hint">WebDAV 远程备份目标（可对接 Nextcloud / 坚果云 / 任意 WebDAV 服务），任务可绑定上传</span>
        <button class="btn primary" @click="openRemoteAdd">
          <Plus :size="14" /> 添加远程目标
        </button>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="remotes.length === 0" class="empty">
        <CloudUpload :size="40" style="color:#9ca3af;" />
        <div>还没有远程备份目标</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>地址</th>
              <th>账号</th>
              <th>绑定任务</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in remotes" :key="r.id">
              <td>{{ r.name }}</td>
              <td class="mono">{{ r.base }}</td>
              <td>{{ r.username || '—' }}<span v-if="r.has_password" class="sub">（已设密码）</span></td>
              <td>{{ boundCount(r.id) }}</td>
              <td class="actions-cell">
                <button class="btn mini" :disabled="busy" @click="doTestRemote(r)">测试</button>
                <button class="btn mini" :disabled="busy" @click="openRemoteEdit(r)">编辑</button>
                <button class="btn mini danger-text" :disabled="busy" @click="doDeleteRemote(r)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 新建 / 编辑任务弹窗 ============ -->
    <div v-if="formOpen" class="modal-overlay" @click.self="formOpen = false">
      <div class="modal">
        <h3>
          <DatabaseBackup :size="16" />
          {{ editing ? '编辑备份任务' : '新建备份任务' }}
        </h3>

        <label class="field">
          <span class="label">任务名称</span>
          <input v-model.trim="form.name" maxlength="64" placeholder="如：网站数据备份" />
        </label>

        <label class="field">
          <span class="label">源路径（要备份的目录或文件，绝对路径）</span>
          <input v-model.trim="form.source" placeholder="/var/www/html 或 C:\site" spellcheck="false" />
        </label>

        <label class="field">
          <span class="label">备份目录（留空使用默认备份目录）</span>
          <input v-model.trim="form.target" :placeholder="status.backup_dir || '默认备份目录'" spellcheck="false" />
        </label>

        <div class="field-row">
          <label class="field">
            <span class="label">计划（cron，留空仅手动）</span>
            <input v-model.trim="form.schedule" placeholder="30 2 * * *" spellcheck="false" />
            <span class="hint">分 时 日 月 周，留空则只手动备份</span>
          </label>
        </div>

        <div class="field-row">
          <label class="field">
            <span class="label">保留份数（0=不限）</span>
            <input type="number" min="0" max="10000" v-model.number="form.keep_count" />
          </label>
          <label class="field">
            <span class="label">保留天数（0=不限）</span>
            <input type="number" min="0" max="36500" v-model.number="form.keep_days" />
          </label>
        </div>

        <label class="field">
          <span class="label">远程备份目标（可选）</span>
          <select v-model="form.remote_id">
            <option value="">不远程备份</option>
            <option v-for="r in remotes" :key="r.id" :value="r.id">{{ r.name }}（{{ r.base }}）</option>
          </select>
          <span class="hint">备份完成后自动上传到所选 WebDAV 目标</span>
        </label>

        <label class="field check">
          <input type="checkbox" v-model="form.enabled" />
          <span>启用计划备份</span>
        </label>

        <div v-if="formError" class="error">{{ formError }}</div>

        <div class="actions">
          <button class="btn" :disabled="saving" @click="formOpen = false">取消</button>
          <button class="btn primary" :disabled="saving" @click="saveForm">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 远程目标 新建/编辑 弹窗 ============ -->
    <div v-if="remoteFormOpen" class="modal-overlay" @click.self="remoteFormOpen = false">
      <div class="modal">
        <h3>
          <CloudUpload :size="16" />
          {{ remoteEditing ? '编辑远程备份目标' : '添加远程备份目标' }}
        </h3>

        <label class="field">
          <span class="label">名称</span>
          <input v-model.trim="remoteForm.name" maxlength="64" placeholder="如：坚果云 / Nextcloud" />
        </label>

        <label class="field">
          <span class="label">WebDAV 地址（http/https 根 URL）</span>
          <input v-model.trim="remoteForm.base" placeholder="https://dav.example.com/dav/" spellcheck="false" />
        </label>

        <div class="field-row">
          <label class="field">
            <span class="label">用户名</span>
            <input v-model.trim="remoteForm.username" autocomplete="off" spellcheck="false" />
          </label>
          <label class="field">
            <span class="label">密码（编辑时留空表示不修改）</span>
            <input v-model="remoteForm.password" type="password" autocomplete="new-password" />
          </label>
        </div>

        <div v-if="remoteFormError" class="error">{{ remoteFormError }}</div>

        <div class="actions">
          <button class="btn" :disabled="remoteSaving" @click="remoteFormOpen = false">取消</button>
          <button class="btn primary" :disabled="remoteSaving" @click="saveRemoteForm">
            {{ remoteSaving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 恢复确认弹窗 ============ -->
    <div v-if="restoreOpen" class="modal-overlay" @click.self="restoreOpen = false">
      <div class="modal">
        <h3 class="warn-title"><RotateCcw :size="16" /> 恢复备份</h3>
        <div class="danger-box">
          <p><b>{{ restoreTask ? restoreTask.name : '' }}</b></p>
          <p>备份文件：<code class="mono">{{ restoreFile }}</code></p>
          <p>恢复将把备份内容解压到目标目录，若存在同名文件会被覆盖，请确认。</p>
        </div>
        <label class="field" style="margin-top:12px;">
          <span class="label">恢复目标目录（默认还原到任务源路径所在目录）</span>
          <input v-model.trim="restoreTarget" :placeholder="defaultRestoreTarget" spellcheck="false" />
        </label>
        <div v-if="restoreError" class="error">{{ restoreError }}</div>
        <div class="actions">
          <button class="btn" :disabled="busy" @click="restoreOpen = false">取消</button>
          <button class="btn primary" :disabled="busy" @click="doRestore">
            {{ busy ? '恢复中…' : '开始恢复' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除备份任务/远程目标/备份文件需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="confirm.title"
      :message="confirm.message"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      confirm-label="删除"
      @confirm="doDeleteConfirmed"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { DatabaseBackup, RefreshCw, Plus, ListChecks, History, Archive, RotateCcw, CloudUpload } from 'lucide-vue-next'
import { backupApi, formatBytes } from '../../api'
import ConfirmDialog from '../ConfirmDialog.vue'

const tab = ref('tasks')
const loading = ref(false)
const busy = ref(false)
const tasks = ref([])
const records = ref([])
const remotes = ref([])
const status = reactive({ backup_dir: '', task_count: 0, file_count: 0, total_size: 0 })
// 高风险操作二次确认状态
const confirm = ref({ show: false, title: '', message: '', action: null })

// 新建/编辑任务表单
const formOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const formError = ref('')
const form = reactive({
  name: '',
  source: '',
  target: '',
  schedule: '',
  keep_count: 10,
  keep_days: 0,
  enabled: true,
  remote_id: '',
})

// 远程目标表单
const remoteFormOpen = ref(false)
const remoteEditing = ref(null)
const remoteSaving = ref(false)
const remoteFormError = ref('')
const remoteForm = reactive({ name: '', base: '', username: '', password: '' })

// 恢复弹窗
const restoreOpen = ref(false)
const restoreTask = ref(null)
const restoreFile = ref('')
const restoreTarget = ref('')
const restoreError = ref('')

const defaultRestoreTarget = computed(() => {
  if (!restoreTask.value) return ''
  const s = restoreTask.value.source || ''
  return s.split(/[\\/]/).slice(0, -1).join('/') || '/'
})

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function keepText(t) {
  const parts = []
  if (t.keep_count > 0) parts.push(`${t.keep_count} 份`)
  if (t.keep_days > 0) parts.push(`${t.keep_days} 天`)
  return parts.join(' / ') || '不限'
}

function remoteName(id) {
  if (!id) return ''
  const r = remotes.value.find((x) => x.id === id)
  return r ? r.name : '（已删除）'
}

function boundCount(id) {
  return tasks.value.filter((t) => t.remote_id === id).length
}

async function loadAll() {
  loading.value = true
  try {
    const [st, t, rec] = await Promise.all([backupApi.status(), backupApi.tasks(), backupApi.records()])
    Object.assign(status, st || {})
    tasks.value = (t && t.tasks) || []
    remotes.value = (t && t.remotes) || []
    if (t && t.backup_dir && !status.backup_dir) status.backup_dir = t.backup_dir
    records.value = (rec && rec.records) || []
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

// ---------- 任务操作 ----------
function openAdd() {
  editing.value = null
  formError.value = ''
  Object.assign(form, { name: '', source: '', target: '', schedule: '', keep_count: 10, keep_days: 0, enabled: true, remote_id: '' })
  formOpen.value = true
}

function openEdit(t) {
  editing.value = t
  formError.value = ''
  Object.assign(form, {
    name: t.name || '',
    source: t.source || '',
    target: t.target || '',
    schedule: t.schedule || '',
    keep_count: t.keep_count ?? 10,
    keep_days: t.keep_days ?? 0,
    enabled: t.enabled !== false,
    remote_id: t.remote_id || '',
  })
  formOpen.value = true
}

async function saveForm() {
  if (saving.value) return
  formError.value = ''
  if (!form.name.trim()) { formError.value = '请填写任务名称'; return }
  if (!form.source.trim()) { formError.value = '请填写源路径'; return }
  const body = {
    name: form.name.trim(),
    source: form.source.trim(),
    target: form.target.trim(),
    schedule: form.schedule.trim(),
    keep_count: form.keep_count,
    keep_days: form.keep_days,
    enabled: form.enabled,
    remote_id: form.remote_id,
  }
  saving.value = true
  try {
    if (editing.value) await backupApi.updateTask(editing.value.id, body)
    else await backupApi.createTask(body)
    formOpen.value = false
    await loadAll()
  } catch (e) {
    formError.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

async function doRun(t) {
  if (!confirm(`立即备份「${t.name}」？`)) return
  busy.value = true
  try {
    const r = await backupApi.run(t.id)
    const remoteMsg = r.remote?.uploaded ? `，远程上传成功` : (r.remote?.error ? `，远程上传失败：${r.remote.error}` : '')
    alert(`备份完成：${r.file}（${formatBytes(r.size)}，耗时 ${r.elapsed_seconds}s）${remoteMsg}`)
    await loadAll()
  } catch (e) {
    alert('备份失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

function doDelete(t) {
  // 高风险操作：删除备份任务需输入面板密码确认
  confirm.value = {
    show: true,
    title: '删除备份任务确认',
    message: `删除备份任务「${t.name}」？（已有备份文件不会被删除）\n请输入面板密码以确认。`,
    action: { type: 'task', id: t.id }
  }
}

async function doDeleteConfirmed() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return
  busy.value = true
  try {
    if (a.type === 'task') {
      await backupApi.deleteTask(a.id)
    } else if (a.type === 'remote') {
      await backupApi.deleteRemote(a.id)
    } else if (a.type === 'record') {
      await backupApi.deleteRecord(a.name)
    }
    await loadAll()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// ---------- 远程目标操作 ----------
function openRemoteAdd() {
  remoteEditing.value = null
  remoteFormError.value = ''
  Object.assign(remoteForm, { name: '', base: '', username: '', password: '' })
  remoteFormOpen.value = true
}

function openRemoteEdit(r) {
  remoteEditing.value = r
  remoteFormError.value = ''
  Object.assign(remoteForm, {
    name: r.name || '',
    base: r.base || '',
    username: r.username || '',
    password: '',
  })
  remoteFormOpen.value = true
}

async function saveRemoteForm() {
  if (remoteSaving.value) return
  remoteFormError.value = ''
  if (!remoteForm.name.trim()) { remoteFormError.value = '请填写名称'; return }
  if (!remoteForm.base.trim()) { remoteFormError.value = '请填写 WebDAV 地址'; return }
  const body = {
    name: remoteForm.name.trim(),
    base: remoteForm.base.trim(),
    username: remoteForm.username.trim(),
    password: remoteForm.password,
  }
  remoteSaving.value = true
  try {
    if (remoteEditing.value) await backupApi.updateRemote(remoteEditing.value.id, body)
    else await backupApi.createRemote(body)
    remoteFormOpen.value = false
    await loadAll()
  } catch (e) {
    remoteFormError.value = e.response?.data?.detail || e.message
  } finally {
    remoteSaving.value = false
  }
}

async function doTestRemote(r) {
  busy.value = true
  try {
    await backupApi.testRemote(r.id)
    alert(`连接成功：${r.name}`)
  } catch (e) {
    alert('连接失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

function doDeleteRemote(r) {
  const bound = boundCount(r.id)
  const extra = bound > 0 ? `（当前有 ${bound} 个任务绑定，删除后任务将改为仅本地备份）` : ''
  // 高风险操作：删除远程备份目标需输入面板密码确认
  confirm.value = {
    show: true,
    title: '删除远程备份目标确认',
    message: `删除远程备份目标「${r.name}」？${extra}\n请输入面板密码以确认。`,
    action: { type: 'remote', id: r.id }
  }
}

// ---------- 记录操作 ----------
function openRestore(r) {
  restoreTask.value = tasks.value.find((t) => t.id === r.task_id) || null
  restoreFile.value = r.name
  restoreTarget.value = ''
  restoreError.value = ''
  restoreOpen.value = true
}

async function doRestore() {
  if (busy.value) return
  if (!restoreTask.value) return
  restoreError.value = ''
  const target = restoreTarget.value.trim() || defaultRestoreTarget.value
  if (!target) { restoreError.value = '请填写恢复目标目录'; return }
  busy.value = true
  try {
    const r = await backupApi.restore(restoreTask.value.id, restoreFile.value, target)
    alert(`恢复完成：${r.file} → ${r.target}`)
    restoreOpen.value = false
  } catch (e) {
    restoreError.value = e.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

function doDeleteRecord(r) {
  // 高风险操作：删除备份文件需输入面板密码确认
  confirm.value = {
    show: true,
    title: '删除备份文件确认',
    message: `删除备份文件「${r.name}」？此操作不可恢复。\n请输入面板密码以确认。`,
    action: { type: 'record', name: r.name }
  }
}

onMounted(loadAll)
</script>

<style scoped>
.backup-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; background: #dbeafe; color: #1e40af; }
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
.sub { font-size: 10px; color: #888; }
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

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 18px; width: 560px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,0.18); max-height: 92vh; overflow: auto; }
.modal h3 { margin: 0 0 12px; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.warn-title { color: #92400e; }
.field { display: block; margin-bottom: 10px; }
.field .label { display: block; font-size: 11px; color: #1d1d1f; font-weight: 600; margin-bottom: 5px; }
.field input, .field select { width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; font-family: inherit; }
.field input:focus, .field select:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.field .hint { display: block; margin-top: 4px; }
.field.check { display: flex; align-items: center; gap: 8px; }
.field.check input { width: auto; }
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }
.error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
.danger-box { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 12px 14px; font-size: 13px; color: #7f1d1d; line-height: 1.7; }
.danger-box p { margin: 4px 0; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
