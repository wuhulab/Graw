<template>
  <div class="ftpusers-window">
    <!-- 顶部：说明 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge"><UserCheck :size="14" /> 虚拟 FTP 用户 · 共 {{ items.length }} 个</span>
        <span class="hint">纯 Python 虚拟用户管理，无需在系统创建真实用户（存储于 data/ftp_users.json）</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
      </div>
    </div>

    <!-- 列表 -->
    <div class="table-toolbar">
      <button class="btn primary" @click="openAdd"><Plus :size="14" /> 添加用户</button>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="items.length === 0" class="empty">
      <UserCheck :size="40" style="color:#9ca3af;" />
      <div>还没有 FTP 用户，点击「添加用户」创建一个虚拟 FTP 账号</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>用户名</th>
            <th>目录（chroot）</th>
            <th>状态</th>
            <th>描述</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in items" :key="u.id">
            <td>{{ u.username }}</td>
            <td class="mono" :title="u.directory">{{ u.directory }}</td>
            <td>
              <span v-if="u.enabled" class="badge ok">已启用</span>
              <span v-else class="badge off">已停用</span>
            </td>
            <td class="desc">{{ u.description || '—' }}</td>
            <td class="mono">{{ fmtTime(u.created_at) }}</td>
            <td class="actions-cell">
              <button class="btn mini" :disabled="busy" @click="toggleItem(u)">{{ u.enabled ? '停用' : '启用' }}</button>
              <button class="btn mini" :disabled="busy" @click="openEdit(u)">编辑</button>
              <button class="btn mini danger-text" :disabled="busy" @click="doDelete(u)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 添加 / 编辑弹窗 -->
    <div v-if="formOpen" class="modal-overlay" @click.self="formOpen = false">
      <div class="modal">
        <h3><UserCheck :size="16" /> {{ editing ? '编辑 FTP 用户' : '添加 FTP 用户' }}</h3>

        <label class="field">
          <span class="label">用户名</span>
          <input v-model.trim="form.username" maxlength="64" placeholder="仅字母/数字/._-，如 webuser" spellcheck="false" />
        </label>
        <label class="field">
          <span class="label">{{ editing ? '密码（留空保持原密码）' : '密码（至少 6 位）' }}</span>
          <input v-model="form.password" type="password" maxlength="128" placeholder="FTP 登录密码" autocomplete="new-password" />
        </label>
        <label class="field">
          <span class="label">目录（chroot 路径）</span>
          <input v-model.trim="form.directory" maxlength="1024" placeholder="如 /srv/ftp/webuser 或 C:\ftp\webuser" spellcheck="false" />
        </label>
        <div class="field-row">
          <label class="field check">
            <input type="checkbox" v-model="form.enabled" />
            <span>启用</span>
          </label>
        </div>
        <label class="field">
          <span class="label">描述</span>
          <input v-model.trim="form.description" maxlength="255" placeholder="可选，记录用途（如：官网文件上传账号）" />
        </label>
        <div v-if="formError" class="error">{{ formError }}</div>
        <div class="actions">
          <button class="btn" :disabled="saving" @click="formOpen = false">取消</button>
          <button class="btn primary" :disabled="saving" @click="saveForm">{{ saving ? '保存中…' : '保存' }}</button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除 FTP 用户需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      title="删除 FTP 用户确认"
      :message="`删除 FTP 用户「${confirm.target?.username}」后该账号将无法登录。\n请输入面板密码以确认。`"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      confirm-label="删除"
      @confirm="doDeleteConfirmed"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { UserCheck, RefreshCw, Plus } from 'lucide-vue-next'
import { ftpusersApi } from '../../api'
import ConfirmDialog from '../ConfirmDialog.vue'

const loading = ref(false)
const busy = ref(false)
const items = ref([])
// 高风险操作二次确认状态（删除 FTP 用户需输入面板密码）
const confirm = ref({ show: false, target: null })

const formOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const formError = ref('')
const form = reactive({
  username: '', password: '', directory: '', enabled: true, description: '',
})

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function loadAll() {
  loading.value = true
  try {
    const r = await ftpusersApi.list()
    items.value = (r && r.users) || []
  } catch (e) {
    alert('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function openAdd() {
  editing.value = null
  formError.value = ''
  Object.assign(form, { username: '', password: '', directory: '', enabled: true, description: '' })
  formOpen.value = true
}

function openEdit(u) {
  editing.value = u
  formError.value = ''
  // 编辑时密码留空表示保持原密码
  Object.assign(form, {
    username: u.username || '', password: '', directory: u.directory || '',
    enabled: u.enabled !== false, description: u.description || '',
  })
  formOpen.value = true
}

async function saveForm() {
  if (saving.value) return
  formError.value = ''
  if (!form.username.trim()) { formError.value = '请填写用户名'; return }
  if (!form.directory.trim()) { formError.value = '请填写目录'; return }
  const body = {
    username: form.username.trim(),
    directory: form.directory.trim(),
    enabled: form.enabled,
    description: form.description.trim(),
  }
  if (editing.value) {
    // 编辑时密码非空才更新密码
    if (form.password) body.password = form.password
  } else {
    if (!form.password) { formError.value = '请填写密码'; return }
    body.password = form.password
  }
  saving.value = true
  try {
    if (editing.value) await ftpusersApi.update(editing.value.id, body)
    else await ftpusersApi.create(body)
    formOpen.value = false
    await loadAll()
  } catch (e) {
    formError.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

async function toggleItem(u) {
  busy.value = true
  try {
    await ftpusersApi.update(u.id, { enabled: !u.enabled })
    await loadAll()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// 点击删除：弹出高风险操作二次确认（输入面板密码），不直接删除
function doDelete(u) {
  confirm.value = { show: true, target: u }
}

// 面板密码校验通过后执行真正的删除
async function doDeleteConfirmed() {
  const u = confirm.value.target
  confirm.value.show = false
  if (!u) return
  busy.value = true
  try {
    await ftpusersApi.delete(u.id)
    await loadAll()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.ftpusers-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; background: #dbeafe; color: #1e40af; }
.hint { color: #6e6e73; font-size: 12px; }
.toolbar-actions { display: flex; gap: 8px; }
.table-toolbar { display: flex; align-items: center; justify-content: flex-end; margin-bottom: 8px; }

.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; flex: 1; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; }
.desc { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
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

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 18px; width: 560px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,0.18); max-height: 92vh; overflow: auto; }
.modal h3 { margin: 0 0 12px; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.field { display: block; margin-bottom: 10px; }
.field .label { display: block; font-size: 11px; color: #1d1d1f; font-weight: 600; margin-bottom: 5px; }
.field input { width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; font-family: inherit; }
.field input:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.field.check { display: flex; align-items: center; gap: 8px; }
.field.check input { width: auto; }
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }
.error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
