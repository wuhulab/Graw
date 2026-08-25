<template>
  <div class="svcmonitor-window">
    <!-- 顶部：汇总状态 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge" :class="downCount > 0 ? 'down' : 'ok'">
          <Server :size="14" /> 正常 {{ upCount }} / 异常 {{ downCount }} / 共 {{ items.length }}
        </span>
        <span class="hint">故障与恢复会自动推送到通知中心配置的渠道</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
      </div>
    </div>

    <!-- 列表 -->
    <div class="table-toolbar">
      <button class="btn primary" @click="openAdd"><Plus :size="14" /> 添加监控</button>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="items.length === 0" class="empty">
      <Server :size="40" style="color:#9ca3af;" />
      <div>还没有监控项，添加一个端口 / 进程 / systemd 服务开始检测</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>目标</th>
            <th>间隔</th>
            <th>状态</th>
            <th>详情</th>
            <th>最近检查</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="i in items" :key="i.id">
            <td>{{ i.name }}<div class="sub">{{ i.enabled ? '监控中' : '已停用' }}</div></td>
            <td>{{ kindLabel(i.kind) }}</td>
            <td class="mono" :title="i.target">{{ i.target }}</td>
            <td>{{ i.interval_seconds }}s</td>
            <td>
              <span v-if="i.last_status === 'ok'" class="badge ok">正常</span>
              <span v-else-if="i.last_status === 'down'" class="badge danger">异常</span>
              <span v-else-if="i.last_status === 'unknown'" class="badge off">不支持</span>
              <span v-else class="badge off">未检测</span>
            </td>
            <td class="mono" :title="i.last_detail">{{ i.last_detail || '—' }}</td>
            <td class="mono">{{ fmtTime(i.last_checked_at) }}</td>
            <td class="actions-cell">
              <button class="btn mini" :disabled="busy" @click="doTest(i)">测试</button>
              <button class="btn mini" :disabled="busy" @click="toggleItem(i)">{{ i.enabled ? '停用' : '启用' }}</button>
              <button class="btn mini" :disabled="busy" @click="openEdit(i)">编辑</button>
              <button class="btn mini danger-text" :disabled="busy" @click="doDelete(i)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 添加 / 编辑弹窗 -->
    <div v-if="formOpen" class="modal-overlay" @click.self="formOpen = false">
      <div class="modal">
        <h3><Server :size="16" /> {{ editing ? '编辑监控项' : '添加监控项' }}</h3>

        <label class="field">
          <span class="label">名称</span>
          <input v-model.trim="form.name" maxlength="64" placeholder="如：数据库端口 / Web 服务进程" />
        </label>
        <label class="field">
          <span class="label">监控类型</span>
          <select v-model="form.kind">
            <option value="port">TCP 端口</option>
            <option value="process">进程</option>
            <option value="service">systemd 服务（Linux）</option>
          </select>
        </label>
        <label class="field">
          <span class="label">{{ targetLabel }}</span>
          <input v-model.trim="form.target" :placeholder="targetPlaceholder" spellcheck="false" />
        </label>
        <div class="field-row">
          <label class="field">
            <span class="label">检查间隔（秒）</span>
            <input type="number" min="10" max="86400" v-model.number="form.interval_seconds" />
          </label>
          <label class="field">
            <span class="label">超时（秒）</span>
            <input type="number" min="1" max="30" v-model.number="form.timeout_seconds" />
          </label>
          <label class="field check" style="justify-content:flex-end;">
            <input type="checkbox" v-model="form.enabled" />
            <span>启用</span>
          </label>
        </div>
        <div v-if="formError" class="error">{{ formError }}</div>
        <div class="actions">
          <button class="btn" :disabled="saving" @click="formOpen = false">取消</button>
          <button class="btn primary" :disabled="saving" @click="saveForm">{{ saving ? '保存中…' : '保存' }}</button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除监控项需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      title="删除监控项确认"
      :message="`删除监控项「${confirm.target?.name}」后其历史记录也将清除。\n请输入面板密码以确认。`"
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
import { Server, RefreshCw, Plus } from 'lucide-vue-next'
import { svcmonitorApi } from '../../api'
import ConfirmDialog from '../ConfirmDialog.vue'

const loading = ref(false)
const busy = ref(false)
const items = ref([])
// 高风险操作二次确认状态（删除监控项需输入面板密码）
const confirm = ref({ show: false, target: null })

const formOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const formError = ref('')
const form = reactive({
  name: '', kind: 'port', target: '',
  timeout_seconds: 5, interval_seconds: 60, enabled: true,
})

const upCount = computed(() => items.value.filter((i) => i.last_status === 'ok').length)
const downCount = computed(() => items.value.filter((i) => i.last_status === 'down').length)

// 类型标签与目标输入提示
const kindLabel = (k) => ({ port: '端口', process: '进程', service: '服务' }[k] || k)
const targetLabel = computed(() => {
  return { port: '目标（host:port，host 可省略默认 127.0.0.1）', process: '进程名 / 命令行关键字', service: 'systemd 服务名' }[form.kind] || '目标'
})
const targetPlaceholder = computed(() => {
  return { port: '如：3306 或 127.0.0.1:3306', process: '如：nginx / mysqld', service: '如：nginx.service' }[form.kind] || ''
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
    const r = await svcmonitorApi.items()
    items.value = (r && r.items) || []
  } catch (e) {
    alert('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

function openAdd() {
  editing.value = null
  formError.value = ''
  Object.assign(form, { name: '', kind: 'port', target: '', timeout_seconds: 5, interval_seconds: 60, enabled: true })
  formOpen.value = true
}

function openEdit(i) {
  editing.value = i
  formError.value = ''
  Object.assign(form, {
    name: i.name || '', kind: i.kind || 'port', target: i.target || '',
    timeout_seconds: i.timeout_seconds ?? 5, interval_seconds: i.interval_seconds ?? 60,
    enabled: i.enabled !== false,
  })
  formOpen.value = true
}

async function saveForm() {
  if (saving.value) return
  formError.value = ''
  if (!form.name.trim()) { formError.value = '请填写名称'; return }
  if (!form.target.trim()) { formError.value = '请填写监控目标'; return }
  const body = {
    name: form.name.trim(), kind: form.kind, target: form.target.trim(),
    timeout_seconds: form.timeout_seconds, interval_seconds: form.interval_seconds, enabled: form.enabled,
  }
  saving.value = true
  try {
    if (editing.value) await svcmonitorApi.updateItem(editing.value.id, body)
    else await svcmonitorApi.createItem(body)
    formOpen.value = false
    await loadAll()
  } catch (e) {
    formError.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

async function doTest(i) {
  busy.value = true
  try {
    const r = await svcmonitorApi.test(i.id)
    const ok = r.status === 'ok'
    const label = ok ? '正常' : (r.status === 'unknown' ? '环境不支持' : '异常')
    alert(`${i.name}：${label}（${r.detail || '—'}）`)
    await loadAll()
  } catch (e) {
    alert('测试失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

async function toggleItem(i) {
  busy.value = true
  try {
    await svcmonitorApi.updateItem(i.id, { enabled: !i.enabled })
    await loadAll()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// 点击删除：弹出高风险操作二次确认（输入面板密码），不直接删除
function doDelete(i) {
  confirm.value = { show: true, target: i }
}

// 面板密码校验通过后执行真正的删除
async function doDeleteConfirmed() {
  const i = confirm.value.target
  confirm.value.show = false
  if (!i) return
  busy.value = true
  try {
    await svcmonitorApi.deleteItem(i.id)
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
.svcmonitor-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.status-badge.ok { background: #d1fae5; color: #065f46; }
.status-badge.down { background: #fee2e2; color: #b91c1c; }
.hint { color: #6e6e73; font-size: 12px; }
.toolbar-actions { display: flex; gap: 8px; }
.table-toolbar { display: flex; align-items: center; justify-content: flex-end; margin-bottom: 8px; }

.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; flex: 1; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; max-width: 220px; }
.sub { font-size: 10px; color: #888; }
.actions-cell { display: flex; align-items: center; gap: 4px; white-space: nowrap; }

.badge { padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.danger { background: #fee2e2; color: #b91c1c; }
.badge.off { background: #f3f4f6; color: #6b7280; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #0a84ff; border-color: #0a84ff; color: #fff; }
.btn.primary:hover:not(:disabled) { background: #0a6ed1; }
.btn.mini { padding: 3px 8px; font-size: 11.5px; }
.btn.danger-text { color: #b91c1c; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; font-size: 13px; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.modal { background: #fff; border-radius: 10px; padding: 20px; width: 460px; max-width: 92vw; box-shadow: 0 8px 30px rgba(0,0,0,0.18); max-height: 88vh; overflow: auto; }
.modal h3 { margin: 0 0 14px; font-size: 15px; display: flex; align-items: center; gap: 8px; }
.field { display: block; margin-bottom: 12px; }
.field .label { display: block; font-size: 12px; color: #4b5563; margin-bottom: 4px; }
.field input, .field select { width: 100%; padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; box-sizing: border-box; }
.field-row { display: flex; gap: 10px; flex-wrap: wrap; }
.field-row .field { flex: 1; min-width: 120px; }
.field.check { display: flex; align-items: center; gap: 6px; margin-top: 20px; }
.error { color: #b91c1c; font-size: 12px; margin: 6px 0; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 14px; }
</style>
