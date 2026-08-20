<template>
  <div class="sessions-window">
    <div class="toolbar">
      <span class="hint">
        <MonitorSmartphone :size="14" /> 在线会话共 {{ sessions.length }} 个
        <span v-if="!isAdmin()" class="sub">（普通用户仅显示自己的会话）</span>
      </span>
      <button class="btn" :disabled="loading" @click="load"><RefreshCw :size="14" /> 刷新</button>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="sessions.length === 0" class="empty">
      <MonitorSmartphone :size="40" style="color:#9ca3af;" />
      <div>暂无在线会话</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>账号</th>
            <th>登录 IP</th>
            <th>设备</th>
            <th>登录时间</th>
            <th>状态</th>
            <th style="width:150px;">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sessions" :key="s.sid">
            <td>{{ s.username }}</td>
            <td class="mono">{{ s.ip || '—' }}</td>
            <td>{{ s.device || '—' }}</td>
            <td class="mono">{{ fmtTime(s.created_at) }}</td>
            <td>
              <span class="badge ok">在线</span>
            </td>
            <td>
              <button class="btn mini" :disabled="busy" @click="kickOne(s)">踢出设备</button>
              <button v-if="isAdmin()" class="btn mini danger-text" :disabled="busy" @click="kickAll(s)">全部下线</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { RefreshCw, MonitorSmartphone } from 'lucide-vue-next'
import { authApi } from '../../api'
import { auth, isAdmin } from '../../store/auth'

const loading = ref(false)
const busy = ref(false)
const sessions = ref([])

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  if (isNaN(d.getTime())) return '—'
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function load() {
  loading.value = true
  try {
    const r = await authApi.sessions()
    sessions.value = (r && r.sessions) || []
  } catch (e) {
    alert('加载会话失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// 踢出单个设备（强制下线）
async function kickOne(s) {
  const target = s.username === auth.user?.username ? '当前设备' : `设备（${s.device || s.ip || s.sid}）`
  if (!confirm(`确认将该设备强制下线？\n账号：${s.username}\n设备：${s.device || s.ip || '未知'}\n被踢出的设备将立即退出登录。`)) return
  busy.value = true
  try {
    await authApi.kickSession(s.sid)
    alert('已强制下线该设备')
    await load()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// 强制下线该用户全部设备（管理员）
async function kickAll(s) {
  if (!confirm(`确认将账号「${s.username}」的全部设备强制下线？\n该账号所有登录将立即失效，需重新登录。`)) return
  busy.value = true
  try {
    await authApi.kickAllSessions(s.username)
    alert(`账号「${s.username}」已全部下线`)
    await load()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.sessions-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.hint { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: #1d1d1f; }
.hint .sub { color: #6e6e73; font-size: 11px; }
.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; flex: 1; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.mini { padding: 3px 8px; font-size: 11.5px; }
.danger-text { color: #b91c1c; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
</style>
