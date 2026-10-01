<!--
  BackupWindow.vue — 备份中心窗口
  ==========================================================
  业务作用：
    管理服务器目录/文件的备份：新建/编辑/删除备份任务（支持 cron 计划与
    保留策略），查看备份记录并支持恢复/删除，管理 WebDAV 远程备份目标（可
    绑定到任务，备份完成后自动上传）。高风险操作（删除任务/远程目标/备份
    文件）需输入面板密码二次确认。
  后端模块：
    /api/backup 的 status / tasks / records / createTask / updateTask /
    deleteTask / run / restore / testRemote / createRemote / updateRemote /
    deleteRemote / deleteRecord 等。
  关键状态：
    - tab      当前标签页：tasks（备份任务）/ records（备份记录）/ remotes（远程目标）
    - tasks    备份任务列表
    - records  备份记录列表
    - remotes  WebDAV 远程目标列表
    - confirm  高风险操作二次确认（需输入面板密码）
  打开方式：
    由桌面/任务栏打开备份中心入口，无 props。
-->
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
        <button class="ui-btn" :disabled="loading" @click="loadAll">
          <RefreshCw :size="14" /> 刷新
        </button>
      </div>
    </div>

    <!-- 标签页 -->
    <div class="ui-tabs">
      <button class="ui-tab" :class="{ active: tab === 'tasks' }" @click="switchTab('tasks')">
        <ListChecks :size="14" /> 备份任务
        <span v-if="tasks.length" class="count-badge">{{ tasks.length }}</span>
      </button>
      <button class="ui-tab" :class="{ active: tab === 'records' }" @click="switchTab('records')">
        <History :size="14" /> 备份记录
        <span v-if="records.length" class="count-badge warn">{{ records.length }}</span>
      </button>
      <button class="ui-tab" :class="{ active: tab === 'remotes' }" @click="switchTab('remotes')">
        <CloudUpload :size="14" /> 远程备份
        <span v-if="remotes.length" class="count-badge">{{ remotes.length }}</span>
      </button>
    </div>

    <!-- ============ 标签页一：备份任务 ============ -->
    <div v-if="tab === 'tasks'" class="tab-body">
      <div class="table-toolbar">
        <span class="hint">手动或按计划备份目录/文件，自动按保留策略清理旧备份，可选上传到远程 WebDAV</span>
        <button class="ui-btn primary" @click="emit('openBackupTaskForm', { task: null, remotes })">
          <Plus :size="14" /> 新建备份任务
        </button>
      </div>

      <div v-if="loading" class="ui-empty">加载中…</div>
      <div v-else-if="tasks.length === 0" class="ui-empty">
        <Archive :size="40" style="color:#9ca3af;" />
        <div>还没有备份任务，点击「新建备份任务」创建第一个吧</div>
      </div>
      <div class="ui-table-wrap">
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
                <span class="ui-badge" :class="t.last_status === 'ok' ? 'ok' : (t.last_status === 'error' ? 'danger' : 'off')">
                  {{ t.last_status === 'ok' ? '成功' : (t.last_status === 'error' ? '失败' : '—') }}
                </span>
              </td>
              <td class="actions-cell">
                <button class="ui-btn mini" :disabled="busy" @click="doRun(t)">立即备份</button>
                <button class="ui-btn mini" :disabled="busy" @click="emit('openBackupTaskForm', { task: t, remotes })">编辑</button>
                <button class="ui-btn mini danger-text" :disabled="busy" @click="doDelete(t)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 标签页二：备份记录 ============ -->
    <div v-if="tab === 'records'" class="tab-body">
      <div v-if="loading" class="ui-empty">加载中…</div>
      <div v-else-if="records.length === 0" class="ui-empty">
        <Archive :size="40" style="color:#9ca3af;" />
        <div>暂无备份记录</div>
      </div>
      <div class="ui-table-wrap">
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
        <button class="ui-btn primary" @click="emit('openBackupRemoteForm', { remote: null })">
          <Plus :size="14" /> 添加远程目标
        </button>
      </div>

      <div v-if="loading" class="ui-empty">加载中…</div>
      <div v-else-if="remotes.length === 0" class="ui-empty">
        <CloudUpload :size="40" style="color:#9ca3af;" />
        <div>还没有远程备份目标</div>
      </div>
      <div class="ui-table-wrap">
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
                <button class="ui-btn mini" :disabled="busy" @click="doTestRemote(r)">测试</button>
                <button class="ui-btn mini" :disabled="busy" @click="emit('openBackupRemoteForm', { remote: r })">编辑</button>
                <button class="ui-btn mini danger-text" :disabled="busy" @click="doDeleteRemote(r)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 恢复确认弹窗（危险操作，保留内嵌确认） ============ -->
    <div v-if="restoreOpen" class="ui-modal-overlay" @click.self="restoreOpen = false">
      <div class="ui-modal">
        <h3 class="warn-title"><RotateCcw :size="16" /> 恢复备份</h3>
        <div class="danger-box">
          <p><b>{{ restoreTask ? restoreTask.name : '' }}</b></p>
          <p>备份文件：<code class="ui-mono">{{ restoreFile }}</code></p>
          <p>恢复将把备份内容解压到目标目录，若存在同名文件会被覆盖，请确认。</p>
        </div>
        <label class="ui-field" style="margin-top:12px;">
          <span class="ui-label">恢复目标目录（默认还原到任务源路径所在目录）</span>
          <input class="ui-input" v-model.trim="restoreTarget" :placeholder="defaultRestoreTarget" spellcheck="false" />
        </label>
        <div v-if="restoreError" class="error">{{ restoreError }}</div>
        <div class="ui-actions">
          <button class="ui-btn" :disabled="busy" @click="restoreOpen = false">取消</button>
          <button class="ui-btn primary" :disabled="busy" @click="doRestore">
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
import { ref, reactive, computed, onMounted, watch } from 'vue'   // 状态/表单/派生值/挂载钩子
import { DatabaseBackup, RefreshCw, Plus, ListChecks, History, Archive, RotateCcw, CloudUpload } from 'lucide-vue-next'   // 工具栏/标签页/弹窗图标
import { backupApi, formatBytes } from '../../api'   // /api/backup 接口 + 字节数格式化工具
import ConfirmDialog from '../ConfirmDialog.vue'   // 高风险操作的密码二次确认对话框
import { formBus } from '../../store/formBus'   // 表单保存信号：独立表单窗口保存成功后刷新列表

const emit = defineEmits(['openBackupTaskForm', 'openBackupRemoteForm'])   // 打开独立「任务 / 远程目标」表单窗口

const tab = ref('tasks')   // 当前标签页：tasks / records / remotes
const loading = ref(false)   // 列表加载中
const busy = ref(false)   // 单个动作进行中（禁用按钮防重复提交）
const tasks = ref([])   // 备份任务列表
const records = ref([])   // 备份记录列表
const remotes = ref([])   // WebDAV 远程目标列表
const status = reactive({ backup_dir: '', task_count: 0, file_count: 0, total_size: 0 })   // 顶部全局状态（备份目录/任务数/文件数/总大小）
// 高风险操作二次确认状态
const confirm = ref({ show: false, title: '', message: '', action: null })

// 新建/编辑任务、远程目标表单已拆分为独立窗口（BackupTaskFormWindow /
// BackupRemoteFormWindow）：保存成功后 bumpForm('backup') 触发此处重载
watch(() => formBus.backup, loadAll)

// 恢复弹窗（危险操作，保留内嵌确认）
const restoreOpen = ref(false)
const restoreTask = ref(null)
const restoreFile = ref('')
const restoreTarget = ref('')
const restoreError = ref('')

// 恢复默认目标目录：取任务源路径去掉最后一级文件名后的目录部分
const defaultRestoreTarget = computed(() => {
  if (!restoreTask.value) return ''
  const s = restoreTask.value.source || ''
  return s.split(/[\\/]/).slice(0, -1).join('/') || '/'
})

// ISO 时间 → "YYYY-MM-DD HH:mm:ss"；无效值原样返回
function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// 保留策略展示：份数 + 天数组合，两者都未设置显示「不限」
function keepText(t) {
  const parts = []
  if (t.keep_count > 0) parts.push(`${t.keep_count} 份`)
  if (t.keep_days > 0) parts.push(`${t.keep_days} 天`)
  return parts.join(' / ') || '不限'
}

// 远程目标显示名：按 id 查找，目标已被删除时兜底「（已删除）」
function remoteName(id) {
  if (!id) return ''
  const r = remotes.value.find((x) => x.id === id)
  return r ? r.name : '（已删除）'
}

// 统计绑定到指定远程目标的任务数（删除目标时的提示 + 列表展示）
function boundCount(id) {
  return tasks.value.filter((t) => t.remote_id === id).length
}

// --- 并行加载全部数据：状态 + 任务 + 记录 ---
async function loadAll() {
  loading.value = true
  try {
    const [st, t, rec] = await Promise.all([backupApi.status(), backupApi.tasks(), backupApi.records()])
    Object.assign(status, st || {})
    tasks.value = (t && t.tasks) || []
    remotes.value = (t && t.remotes) || []
    if (t && t.backup_dir && !status.backup_dir) status.backup_dir = t.backup_dir   // 兼容旧接口：status 缺失时用 tasks 返回的备份目录
    records.value = (rec && rec.records) || []
  } catch (e) {
    alert('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// --- 切换标签页并重新加载 ---
async function switchTab(k) {
  tab.value = k
  await loadAll()
}

// ---------- 任务操作 ----------
// --- 立即执行备份：同步等待完成并展示结果（含远程上传状态） ---
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

// 密码确认通过后按 action.type 分发到对应删除接口
async function doDeleteConfirmed() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return   // 无待执行动作直接返回（防御性）
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
// --- 测试远程目标连通性 ---
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
// 打开恢复确认：定位所属任务、预填备份文件名与默认目标目录
function openRestore(r) {
  restoreTask.value = tasks.value.find((t) => t.id === r.task_id) || null
  restoreFile.value = r.name
  restoreTarget.value = ''
  restoreError.value = ''
  restoreOpen.value = true
}

// --- 执行恢复：目标目录留空时回退到任务源路径所在目录 ---
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

onMounted(loadAll)   // 进入窗口即加载全部数据
</script>

<style scoped>
.backup-window { padding: 0; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; } /* 内嵌聚合窗口：外边距由父容器提供 */
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; background: #dbeafe; color: #1e40af; }
.hint { color: #6e6e73; font-size: 12px; }
.toolbar-actions { display: flex; gap: 8px; }

.count-badge { min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px; background: #2563eb; color: #fff; font-size: 10px; display: inline-flex; align-items: center; justify-content: center; }
.count-badge.warn { background: #dc2626; }

.tab-body { flex: 1; overflow: auto; }
.table-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; }
.sub { font-size: 10px; color: #888; }
.actions-cell { display: flex; gap: 4px; }
.danger-text { color: #b91c1c; }
.warn-title { color: #92400e; }
.error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
.danger-box { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 12px 14px; font-size: 13px; color: #7f1d1d; line-height: 1.7; }
.danger-box p { margin: 4px 0; }
</style>
