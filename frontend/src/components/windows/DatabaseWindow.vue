<!--
  DatabaseWindow.vue — 数据库管理主窗口
  ==========================================================
  业务作用：
    管理已保存的数据库连接（MySQL/PostgreSQL/MongoDB/Redis/SQLite）：
    测试连接、编辑、删除（需面板密码二次确认），以及进入连接详情管理弹窗
    （按类型展示库列表/Redis info/SQLite 表，支持执行 SQL、MongoDB 查询、
    Redis 命令）。新增/编辑连接通过 ConnectionFormWindow 完成。
  后端模块：
    /api/databases 的 status / connections / listDBs / query / createDB /
    deleteDB / deleteConn / test 等。
  关键状态：
    - connections 已保存的连接列表
    - manageConn  当前打开管理弹窗的连接
    - tab         管理弹窗内标签：dbs（库）/ query（查询）
    - confirm     高风险操作二次确认（删连接需密码，删库需输入库名）
  打开方式：
    由桌面/任务栏打开数据库入口，无 props；由共享信号 dbVersion 触发刷新。
-->
<template>
  <div class="db-window">
    <div class="ui-toolbar">
      <button class="ui-btn primary" @click="$emit('openConnectionForm')"><Plus :size="14" /> {{ $t('database.addConnection') }}</button>
      <button class="ui-btn" @click="openEngines"><ServerCog :size="14" /> {{ $t('database.engines') }}</button>
      <span class="ui-hint">
        {{ $t('database.mysqlInstalled', { installed: $t(status.mysql_libs ? 'database.installed' : 'database.notInstalled') }) }}
        | {{ $t('database.redisInstalled', { installed: $t(status.redis_libs ? 'database.installed' : 'database.notInstalled') }) }}
        | {{ $t('database.postgresqlInstalled', { installed: $t(status.postgresql_libs ? 'database.installed' : 'database.notInstalled') }) }}
        | {{ $t('database.mongodbInstalled', { installed: $t(status.mongodb_libs ? 'database.installed' : 'database.notInstalled') }) }}
        | {{ $t('database.sqliteAvailable', { installed: $t('database.installed') }) }}
      </span>
    </div>
    <div class="connections">
      <div v-for="c in connections" :key="c.id" class="conn-card">
        <div class="conn-head">
          <div class="conn-title">{{ c.name }} <span class="tag">{{ typeLabel(c.db_type) }}</span></div>
          <div class="conn-addr">
            <!-- SQLite 无主机/端口，地址展示本地文件路径 -->
            <template v-if="c.db_type === 'sqlite'">{{ c.database }}</template>
            <template v-else>{{ c.host }}:{{ c.port }}<template v-if="c.database"> / {{ c.database }}</template></template>
          </div>
        </div>
        <div class="conn-actions">
          <button class="ui-btn mini" @click="testConn(c)">{{ $t('database.testConnection') }}</button>
          <button class="ui-btn mini" @click="$emit('openConnectionForm', c)">{{ $t('database.edit') }}</button>
          <button class="ui-btn mini" @click="emit('openDatabaseManage', { conn: c })">{{ $t('database.manage') }}</button>
          <button v-if="c.db_type === 'mysql' || c.db_type === 'postgresql'" class="ui-btn mini" @click="openUsers(c)">{{ $t('database.users') }}</button>
          <button v-if="c.db_type === 'mysql' || c.db_type === 'postgresql'" class="ui-btn mini" @click="openBackups(c)">{{ $t('database.backups') }}</button>
          <button v-if="c.db_type === 'mysql' || c.db_type === 'postgresql'" class="ui-btn mini" @click="openCluster(c)">{{ $t('database.cluster') }}</button>
          <button class="ui-btn mini danger" @click="removeConn(c)">{{ $t('database.delete') }}</button>
        </div>
      </div>
    </div>
    <!-- 空状态独立于卡片网格：垂直水平居中于应用内容区中央 -->
    <div v-if="connections.length === 0" class="conn-empty">
      <Database :size="36" style="color:var(--g-text-muted);" />
      <div>{{ $t('database.noConnections') }}</div>
    </div>

    <!-- 「管理连接」与「创建数据库」已拆分为独立窗口（DatabaseManageWindow /
         DatabaseCreateWindow），避免内嵌弹窗误触遮罩丢失查询内容 -->

    <!-- 引擎状态：MySQL/PostgreSQL/Redis/MongoDB 的进程与服务 -->
    <div v-if="engines.show" class="ui-modal-overlay" @click.self="engines.show = false">
      <div class="ui-modal">
        <h3>{{ $t('database.engines') }}</h3>
        <div class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('database.engine') }}</th><th>{{ $t('common.status') }}</th><th>Unit</th><th></th></tr></thead>
            <tbody>
              <tr v-for="e in engines.list" :key="e.engine">
                <td>{{ e.engine }}</td>
                <td><span class="ui-badge" :class="e.running ? 'ok' : 'off'">{{ e.running ? $t('database.running') : $t('database.stopped') }}</span></td>
                <td class="mono">{{ e.unit || '-' }}</td>
                <td class="actions">
                  <button class="ui-btn mini" :disabled="!e.unit" @click="engineAction(e, 'start')">{{ $t('common.start') }}</button>
                  <button class="ui-btn mini" :disabled="!e.unit" @click="engineAction(e, 'restart')">{{ $t('common.restart') }}</button>
                  <button class="ui-btn mini danger" :disabled="!e.unit" @click="engineAction(e, 'stop')">{{ $t('common.stop') }}</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="ui-actions"><button class="ui-btn" @click="engines.show = false">{{ $t('common.close') }}</button></div>
      </div>
    </div>

    <!-- 用户与授权 -->
    <div v-if="users.show" class="ui-modal-overlay" @click.self="users.show = false">
      <div class="ui-modal wide">
        <h3>{{ $t('database.usersTitle', { name: users.conn?.name || '' }) }}</h3>
        <div class="acct-form">
          <input v-model.trim="users.username" class="ui-input" :placeholder="$t('database.username')" />
          <input v-model="users.password" type="password" class="ui-input" :placeholder="$t('database.password')" />
          <input v-model.trim="users.host" class="ui-input" :placeholder="$t('database.userHost')" />
          <input v-model.trim="users.database" class="ui-input" :placeholder="$t('database.databaseName')" />
          <input v-model.trim="users.privileges" class="ui-input" placeholder="SELECT, INSERT, UPDATE" />
          <button class="ui-btn primary" @click="createUser">{{ $t('common.create') }}</button>
        </div>
        <div class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('database.username') }}</th><th>Host</th><th></th></tr></thead>
            <tbody>
              <tr v-for="u in users.list" :key="u.username + u.host">
                <td>{{ u.username }}</td><td class="mono">{{ u.host || '-' }}</td>
                <td><button class="iconbtn danger" @click="dropUser(u)"><Trash2 :size="13" /></button></td>
              </tr>
              <tr v-if="!users.list.length"><td colspan="3" class="ui-empty">{{ $t('database.noUsers') }}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="ui-actions"><button class="ui-btn" @click="users.show = false">{{ $t('common.close') }}</button></div>
      </div>
    </div>

    <!-- 备份与恢复 -->
    <div v-if="backups.show" class="ui-modal-overlay" @click.self="backups.show = false">
      <div class="ui-modal wide">
        <h3>{{ $t('database.backupsTitle', { name: backups.conn?.name || '' }) }}</h3>
        <div class="acct-form">
          <input v-model.trim="backups.database" class="ui-input" :placeholder="$t('database.databaseOptional')" />
          <button class="ui-btn primary" :disabled="backups.busy" @click="createBackup">{{ $t('database.backupNow') }}</button>
        </div>
        <div class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('common.name') }}</th><th>{{ $t('database.size') }}</th><th>{{ $t('database.time') }}</th><th></th></tr></thead>
            <tbody>
              <tr v-for="b in backups.list" :key="b.name">
                <td class="mono">{{ b.name }}</td><td>{{ fmtSize(b.size) }}</td><td>{{ b.time }}</td>
                <td class="actions">
                  <button class="ui-btn mini" @click="restoreBackup(b)">{{ $t('database.restore') }}</button>
                  <button class="iconbtn danger" @click="removeBackup(b)"><Trash2 :size="13" /></button>
                </td>
              </tr>
              <tr v-if="!backups.list.length"><td colspan="4" class="ui-empty">{{ $t('database.noBackups') }}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="ui-actions"><button class="ui-btn" @click="backups.show = false">{{ $t('common.close') }}</button></div>
      </div>
    </div>

    <!-- 集群 / 复制状态（只读） -->
    <div v-if="cluster.show" class="ui-modal-overlay" @click.self="cluster.show = false">
      <div class="ui-modal wide">
        <h3>{{ $t('database.clusterTitle', { name: cluster.conn?.name || '' }) }}</h3>
        <div v-if="cluster.loading" class="ui-empty">{{ $t('common.loading') }}</div>
        <pre v-else class="cluster-json">{{ cluster.text }}</pre>
        <div class="ui-actions"><button class="ui-btn" @click="cluster.show = false">{{ $t('common.close') }}</button></div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除连接需面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="confirm.title"
      :message="confirm.message"
      :input-label="t('confirmDanger.inputPwdLabel')"
      :placeholder="t('confirmDanger.inputPwdPlaceholder')"
      :confirm-label="$t('common.delete')"
      @confirm="doConfirm"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'   // 状态/挂载加载/共享信号监听
import { useI18n } from 'vue-i18n'   // 翻译函数
import { databasesApi, formatBytes } from '../../api'   // /api/databases：数据库管理接口
import { dbVersion } from '../../store/databases'   // 共享版本信号：连接增改后触发本窗口刷新
import { Plus, Database, ServerCog, Trash2 } from 'lucide-vue-next'   // 工具栏/空状态/运维操作图标
import ConfirmDialog from '../ConfirmDialog.vue'   // 高风险操作的二次确认对话框

const { t } = useI18n()

// openConnectionForm 打开新增/编辑连接表单；openDatabaseManage 打开独立的管理连接窗口
const emit = defineEmits(['openConnectionForm', 'openDatabaseManage'])

const connections = ref([])   // 已保存的连接列表
const status = ref({})   // 各类型客户端库是否可用（顶部提示）
// 高风险操作二次确认状态（删除连接需输入面板密码）
const confirm = ref({ show: false, title: '', message: '', action: null })

// 引擎状态 / 用户管理 / 备份管理
const engines = reactive({ show: false, list: [] })
const users = reactive({ show: false, conn: null, list: [], username: '', password: '', host: '%', database: '', privileges: 'SELECT' })
const backups = reactive({ show: false, conn: null, list: [], database: '', busy: false })
const cluster = reactive({ show: false, conn: null, loading: false, text: '' })

function fmtSize(n) { return formatBytes ? formatBytes(Number(n) || 0) : `${n} B` }

// 类型展示名映射（连接卡片标签）
const TYPE_LABELS = { mysql: 'MySQL', redis: 'Redis', postgresql: 'PostgreSQL', mongodb: 'MongoDB', sqlite: 'SQLite' }
function typeLabel(t) { return TYPE_LABELS[t] || t || '' }

// --- 加载库状态与连接列表（相互独立：单个接口失败不阻塞另一项） ---
async function load() {
  // status 与 connections 彼此无关，分开捕获——避免 status 偶发失败时连接列表被清空
  try {
    status.value = await databasesApi.status()
  } catch (e) {
    // 状态接口失败：保持旧值，不阻塞连接列表加载
  }
  try {
    const data = await databasesApi.connections()
    connections.value = data.connections || []
  } catch (e) {
    // 连接列表失败：保持旧数据，避免闪空
  }
}

function removeConn(c) {
  // 高风险操作：删除连接需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.deleteDbTitle'),
    message: t('confirmDanger.deleteDbMsg', { name: c.name }),
    action: { type: 'conn', conn: c }
  }
}

// 二次确认通过后删除连接
async function doConfirm() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return   // 无待执行动作直接返回（防御性）
  if (a.type === 'conn') {
    await databasesApi.deleteConn(a.conn.id)
    await load()
  }
}

// --- 测试连接连通性 ---
async function testConn(c) {
  try {
    await databasesApi.test(c.id)
    alert(t('database.testSuccess'))
  } catch (e) {
    alert(t('database.testFailed', { error: e?.response?.data?.detail || e.message }))
  }
}

// 添加/编辑连接在独立窗口完成后通过共享信号触发刷新
// --- 引擎状态与启停 ---
async function openEngines() {
  engines.show = true
  try {
    const data = await databasesApi.engines()
    engines.list = data.engines || []
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function engineAction(e, action) {
  if (action === 'stop' && !window.confirm(t('database.engineStopConfirm', { engine: e.engine }))) return
  try {
    await databasesApi.engineAction(e.engine, action)
    await openEngines()
  } catch (err) {
    alert(err?.response?.data?.detail || err.message)
  }
}

// --- 用户与授权 ---
async function openUsers(c) {
  users.conn = c
  users.show = true
  users.username = ''
  users.password = ''
  users.host = '%'
  users.database = c.database || ''
  users.privileges = 'SELECT'
  await loadUsers()
}

async function loadUsers() {
  try {
    const data = await databasesApi.users(users.conn.id)
    users.list = data.users || []
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function createUser() {
  if (!users.username.trim() || users.password.length < 6) {
    alert(t('database.userRequired'))
    return
  }
  try {
    await databasesApi.createUser(users.conn.id, {
      username: users.username.trim(), password: users.password, host: users.host.trim() || '%',
      database: users.database.trim(), privileges: users.privileges.split(',').map(s => s.trim()).filter(Boolean),
    })
    users.password = ''
    await loadUsers()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function dropUser(u) {
  if (!window.confirm(t('database.dropUserConfirm', { name: u.username }))) return
  try {
    await databasesApi.deleteUser(users.conn.id, u.username, u.host || '')
    await loadUsers()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

// --- 集群 / 复制状态（只读） ---
async function openCluster(c) {
  cluster.conn = c
  cluster.show = true
  cluster.loading = true
  cluster.text = ''
  try {
    const data = await databasesApi.cluster(c.id)
    cluster.text = JSON.stringify(data, null, 2)
  } catch (e) {
    cluster.text = e?.response?.data?.detail || e.message
  } finally {
    cluster.loading = false
  }
}

// --- 备份与恢复 ---
async function openBackups(c) {
  backups.conn = c
  backups.show = true
  backups.database = c.database || ''
  await loadBackups()
}

async function loadBackups() {
  try {
    const data = await databasesApi.backups(backups.conn.id)
    backups.list = data.backups || []
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function createBackup() {
  backups.busy = true
  try {
    await databasesApi.backup(backups.conn.id, backups.database.trim())
    await loadBackups()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  } finally {
    backups.busy = false
  }
}

async function restoreBackup(b) {
  if (!window.confirm(t('database.restoreConfirm', { name: b.name }))) return
  try {
    await databasesApi.restoreBackup(backups.conn.id, b.name, backups.database.trim())
    alert(t('database.restoreDone'))
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function removeBackup(b) {
  if (!window.confirm(t('database.deleteBackupConfirm', { name: b.name }))) return
  try {
    await databasesApi.deleteBackup(backups.conn.id, b.name)
    await loadBackups()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

watch(dbVersion, () => load())

onMounted(load)   // 进入窗口即加载
</script>

<style scoped>
.db-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; position: relative; }
/* 「添加连接」工具栏下方的分隔线 */
.db-window :deep(.ui-toolbar) { border-bottom: 1px solid var(--g-border); padding-bottom: 10px; margin-bottom: 4px; }
.connections { flex: 1; min-height: 0; overflow: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); align-content: start; gap: 10px; padding: 4px; }
/* 空状态：绝对定位覆盖内容区，垂直水平居中于应用中央（不拦截工具栏点击） */
.conn-empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--g-text-muted); font-size: 13px; pointer-events: none; }
.conn-card { border: 1px solid var(--g-border); border-radius: 10px; padding: 12px; background: var(--g-surface); }
.conn-head { margin-bottom: 8px; }
.conn-title { font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 6px; }
.tag { background: #eef2ff; color: #3730a3; font-size: 11px; padding: 2px 6px; border-radius: 4px; }
.acct-form { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--g-border-soft); }
.acct-form .ui-input { flex: 1 1 160px; }
.actions { display: flex; gap: 4px; align-items: center; }
.iconbtn { padding: 4px; border: 1px solid var(--g-border); background: var(--g-surface); border-radius: 6px; cursor: pointer; }
.iconbtn.danger:hover { background: rgba(226, 50, 79, 0.16); border-color: var(--ui-danger-fg, #fca5a5); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.conn-addr { color: var(--g-text-muted); font-size: 12px; margin-top: 4px; }
.conn-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.cluster-json { background: #0f1722; color: #d7e3f4; border-radius: 8px; padding: 10px; font-size: 11.5px; max-height: 420px; overflow: auto; white-space: pre-wrap; margin: 8px 0; }
</style>
