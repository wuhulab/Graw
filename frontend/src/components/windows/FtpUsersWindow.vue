<!--
  FTP 虚拟用户管理窗口（后端 /api/ftpusers 模块）
  作用：管理面板内置的纯 Python 虚拟 FTP 用户（增删改查、启停用），无需在系统创建真实账号，
        用户数据持久化于后端 data/ftp_users.json，每个用户绑定一个 chroot 目录。
  后端模块：/api/ftpusers（list 列表、create 新增、update 编辑/启停用、delete 删除）。
  关键状态：items（用户列表）、confirm（删除二次确认）；添加/编辑表单已改为独立窗口
            FtpUserFormWindow（保存后经 formBus.ftpusers 刷新本列表）。
  删除用户为高风险操作，需输入面板密码（ConfirmDialog）确认。
  打开方式：桌面「FTP 用户」卡片。
-->
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
      <button class="btn primary" @click="emit('openFtpUserForm', { user: null })"><Plus :size="14" /> 添加用户</button>
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
              <button class="btn mini" :disabled="busy" @click="emit('openFtpUserForm', { user: u })">编辑</button>
              <button class="btn mini danger-text" :disabled="busy" @click="doDelete(u)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
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
// 响应式状态
import { ref, onMounted, watch } from 'vue'
// 图标（用户 / 刷新 / 添加）
import { UserCheck, RefreshCw, Plus } from 'lucide-vue-next'
// FTP 用户 API：list/create/update/delete
import { ftpusersApi } from '../../api'
// 高风险操作「输入面板密码」二次确认弹窗
import ConfirmDialog from '../ConfirmDialog.vue'
// 表单保存信号：独立「添加 / 编辑 FTP 用户」窗口保存成功后刷新本列表
import { formBus } from '../../store/formBus'

const loading = ref(false)              // 列表加载中
const busy = ref(false)                 // 行内操作（启停用/删除）进行中
const items = ref([])                   // FTP 用户列表
// 高风险操作二次确认状态（删除 FTP 用户需输入面板密码）
const confirm = ref({ show: false, target: null })

const emit = defineEmits(['openFtpUserForm'])   // 打开独立「添加/编辑 FTP 用户」表单窗口（props 传 user）

// 添加 / 编辑表单已改为独立窗口承载：保存成功后 bumpForm('ftpusers') 触发此处重载
watch(() => formBus.ftpusers, loadAll)

// 后端返回 ISO 时间串 → 本地可读格式（YYYY-MM-DD HH:mm:ss）
function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// --- 动作：拉取全部 FTP 用户列表 ---
async function loadAll() {
  loading.value = true
  try {
    const r = await ftpusersApi.list()   // 调用 /api/ftpusers/list
    items.value = (r && r.users) || []
  } catch (e) {
    alert('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// --- 动作：启用/停用用户（通过 update 接口传 enabled 反向值） ---
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
  if (!u) return   // 无待删除目标则提前返回
  busy.value = true
  try {
    await ftpusersApi.delete(u.id)   // 调用 /api/ftpusers/delete
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
</style>
