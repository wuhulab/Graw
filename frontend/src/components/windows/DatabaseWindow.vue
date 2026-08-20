<template>
  <div class="db-window">
    <div class="toolbar">
      <button class="btn primary" @click="$emit('openConnectionForm')"><Plus :size="14" /> {{ $t('database.addConnection') }}</button>
      <span class="hint">
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
          <button class="btn small" @click="testConn(c)">{{ $t('database.testConnection') }}</button>
          <button class="btn small" @click="$emit('openConnectionForm', c)">{{ $t('database.edit') }}</button>
          <button class="btn small" @click="openManage(c)">{{ $t('database.manage') }}</button>
          <button class="btn small danger" @click="removeConn(c)">{{ $t('database.delete') }}</button>
        </div>
      </div>
      <div v-if="connections.length === 0" class="empty">{{ $t('database.noConnections') }}</div>
    </div>

    <!-- Manage Modal -->
    <div v-if="showManage" class="modal-overlay" @click.self="showManage = false">
      <div class="modal wide">
        <h3>{{ $t('database.manageTitle', { name: manageConn?.name }) }} <span class="tag">{{ typeLabel(manageConn?.db_type) }}</span></h3>
        <div class="tabs">
          <button class="tab" :class="{active: tab==='dbs'}" @click="tab='dbs'">{{ $t('database.databases') }}</button>
          <button class="tab" :class="{active: tab==='query'}" @click="tab='query'">{{ $t('database.query') }}</button>
        </div>

        <div v-if="tab==='dbs'" class="panel">
          <!-- MySQL / PostgreSQL：库列表 + 创建/删除 -->
          <div v-if="manageConn?.db_type==='mysql' || manageConn?.db_type==='postgresql'">
            <button class="btn small primary" @click="showCreateDB=true">{{ $t('database.createDB') }}</button>
            <table class="mini-table">
              <thead><tr><th>{{ $t('database.dbNamePlaceholder') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
              <tbody>
                <tr v-for="db in dbList" :key="db"><td>{{ db }}</td><td><button class="btn small danger" @click="dropDB(db)">{{ $t('database.dropDB') }}</button></td></tr>
              </tbody>
            </table>
          </div>
          <!-- MongoDB：库列表 + 删除（数据库随首次写入自动创建） -->
          <div v-else-if="manageConn?.db_type==='mongodb'">
            <div class="hint" style="margin-bottom:8px">{{ $t('database.mongoHint') }}</div>
            <table class="mini-table">
              <thead><tr><th>{{ $t('database.dbNamePlaceholder') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
              <tbody>
                <tr v-for="db in dbList" :key="db"><td>{{ db }}</td><td><button class="btn small danger" @click="dropDB(db)">{{ $t('database.dropDB') }}</button></td></tr>
              </tbody>
            </table>
          </div>
          <!-- Redis：info 信息 -->
          <div v-else-if="manageConn?.db_type==='redis'">
            <pre class="pre">{{ redisInfo }}</pre>
          </div>
          <!-- SQLite：单文件库，展示库内数据表列表 + 文件信息 -->
          <div v-else-if="manageConn?.db_type==='sqlite'">
            <div class="hint" style="margin-bottom:8px">
              {{ $t('database.sqliteFileInfo', { path: manageConn.database }) }}<template v-if="sqliteFileSize"> · {{ $t('database.sqliteSize', { size: sqliteFileSize }) }}</template>
            </div>
            <table v-if="sqliteTables.length" class="mini-table">
              <thead><tr><th>{{ $t('database.sqliteTableLabel') }}</th></tr></thead>
              <tbody><tr v-for="tb in sqliteTables" :key="tb"><td>{{ tb }}</td></tr></tbody>
            </table>
            <div v-else class="hint">{{ $t('database.sqliteNoTables') }}</div>
          </div>
        </div>

        <div v-if="tab==='query'" class="panel">
          <!-- MySQL / PostgreSQL / SQLite：SQL 编辑器 -->
          <div v-if="manageConn?.db_type==='mysql' || manageConn?.db_type==='postgresql' || manageConn?.db_type==='sqlite'">
            <textarea v-model="sqlText" rows="4" :placeholder="$t('database.sqlPlaceholder')" />
            <button class="btn small primary" @click="execSql">{{ $t('database.execute') }}</button>
            <div v-if="queryResult" class="result">
              <table v-if="queryResult.columns" class="mini-table">
                <thead><tr><th v-for="col in queryResult.columns" :key="col">{{ col }}</th></tr></thead>
                <tbody><tr v-for="(row,idx) in queryResult.rows" :key="idx"><td v-for="(cell, cidx) in row" :key="cidx">{{ cell }}</td></tr></tbody>
              </table>
              <div v-else>{{ $t('database.affectedRows', { count: queryResult.affected }) }}</div>
            </div>
          </div>
          <!-- MongoDB：集合 + JSON 过滤条件 -->
          <div v-else-if="manageConn?.db_type==='mongodb'">
            <div class="mongo-row">
              <input v-model="mongoCollection" :placeholder="$t('database.mongoCollectionPlaceholder')" />
              <input v-model.number="mongoLimit" type="number" :placeholder="$t('database.mongoLimitPlaceholder')" :title="$t('database.mongoLimitTitle')" style="width:80px" />
            </div>
            <textarea v-model="mongoFilter" rows="3" :placeholder="$t('database.mongoFilterPlaceholder')" />
            <button class="btn small primary" @click="execMongo">{{ $t('database.mongoQuery') }}</button>
            <pre v-if="mongoResult !== null" class="pre">{{ mongoResult }}</pre>
          </div>
          <!-- Redis：命令输入 -->
          <div v-else>
            <input v-model="redisCmd" :placeholder="$t('database.redisPlaceholder')" />
            <button class="btn small primary" @click="execRedis">{{ $t('database.execute') }}</button>
            <pre v-if="redisResult !== null" class="pre">{{ redisResult }}</pre>
          </div>
        </div>

        <div class="actions">
          <button class="btn" @click="showManage = false">{{ $t('common.close') }}</button>
        </div>
      </div>
    </div>

    <!-- Create DB Modal -->
    <div v-if="showCreateDB" class="modal-overlay" @click.self="showCreateDB=false">
      <div class="modal">
        <h3>{{ $t('database.createDBTitle') }}</h3>
        <input v-model="newDbName" :placeholder="$t('database.dbNamePlaceholder')" />
        <div class="actions">
          <button class="btn" @click="showCreateDB=false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" @click="doCreateDB">{{ $t('common.create') }}</button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除连接需密码；删除数据库需输入库名 -->
    <ConfirmDialog
      :show="confirm.show"
      :mode="confirm.mode"
      :title="confirm.title"
      :message="confirm.message"
      :required-text="confirm.requiredText"
      :input-label="confirm.inputLabel"
      :placeholder="confirm.placeholder"
      :confirm-label="$t('common.delete')"
      @confirm="doConfirm"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { databasesApi } from '../../api'
import { dbVersion } from '../../store/databases'
import { Plus } from 'lucide-vue-next'
import ConfirmDialog from '../ConfirmDialog.vue'

const { t } = useI18n()

const emit = defineEmits(['openConnectionForm'])

const connections = ref([])
const status = ref({})
const showManage = ref(false)
const showCreateDB = ref(false)
const manageConn = ref(null)
const tab = ref('dbs')
const dbList = ref([])
const redisInfo = ref('')
const sqlText = ref('')
const redisCmd = ref('')
const queryResult = ref(null)
const redisResult = ref(null)
const mongoCollection = ref('')
const mongoFilter = ref('')
const mongoLimit = ref(100)
const mongoResult = ref(null)
const newDbName = ref('')
// SQLite 库内数据表列表与文件大小（仅 sqlite 连接使用）
const sqliteTables = ref([])
const sqliteFileSize = ref(0)
// 高风险操作二次确认状态
const confirm = ref({ show: false, mode: 'password', title: '', message: '', requiredText: '', inputLabel: '', placeholder: '', action: null })

// 类型展示名映射（连接卡片标签 / 管理标题）
const TYPE_LABELS = { mysql: 'MySQL', redis: 'Redis', postgresql: 'PostgreSQL', mongodb: 'MongoDB', sqlite: 'SQLite' }
function typeLabel(t) { return TYPE_LABELS[t] || t || '' }

async function load() {
  try {
    status.value = await databasesApi.status()
    const data = await databasesApi.connections()
    connections.value = data.connections || []
  } catch (e) {
    // 接口异常时保持旧数据，避免列表闪空
  }
}

function removeConn(c) {
  // 高风险操作：删除连接需输入面板密码确认
  confirm.value = {
    show: true,
    mode: 'password',
    title: t('confirmDanger.deleteDbTitle'),
    message: t('confirmDanger.deleteDbMsg', { name: c.name }),
    requiredText: '',
    inputLabel: t('confirmDanger.inputPwdLabel'),
    placeholder: t('confirmDanger.inputPwdPlaceholder'),
    action: { type: 'conn', conn: c }
  }
}

async function doConfirm() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return
  if (a.type === 'conn') {
    await databasesApi.deleteConn(a.conn.id)
    await load()
  } else if (a.type === 'db') {
    await databasesApi.deleteDB(manageConn.value.id, a.name)
    if (manageConn.value) openManage(manageConn.value)
  }
}

async function testConn(c) {
  try {
    await databasesApi.test(c.id)
    alert(t('database.testSuccess'))
  } catch (e) {
    alert(t('database.testFailed', { error: e?.response?.data?.detail || e.message }))
  }
}

async function openManage(c) {
  manageConn.value = c
  tab.value = 'dbs'
  queryResult.value = null
  redisResult.value = null
  mongoResult.value = null
  sqliteTables.value = []
  sqliteFileSize.value = 0
  showManage.value = true
  const data = await databasesApi.listDBs(c.id)
  if (c.db_type === 'sqlite') {
    // SQLite：data.tables 为库内数据表列表，data.size 为文件字节数
    sqliteTables.value = data.tables || []
    sqliteFileSize.value = data.size || 0
  } else if (c.db_type === 'mysql' || c.db_type === 'postgresql' || c.db_type === 'mongodb') {
    dbList.value = data.databases || []
  } else {
    redisInfo.value = JSON.stringify(data.info, null, 2)
  }
}

async function execSql() {
  const data = await databasesApi.query(manageConn.value.id, { sql: sqlText.value })
  queryResult.value = data
}

async function execRedis() {
  const data = await databasesApi.query(manageConn.value.id, { command: redisCmd.value })
  redisResult.value = data.result
}

async function execMongo() {
  const data = await databasesApi.query(manageConn.value.id, {
    collection: mongoCollection.value,
    filter: mongoFilter.value,
    limit: Number(mongoLimit.value) || 100
  })
  mongoResult.value = JSON.stringify(data.result, null, 2)
}

async function doCreateDB() {
  await databasesApi.createDB(manageConn.value.id, newDbName.value)
  showCreateDB.value = false
  newDbName.value = ''
  if (manageConn.value) openManage(manageConn.value)
}

function dropDB(name) {
  // 高风险操作：删除数据库需输入数据库名称确认
  confirm.value = {
    show: true,
    mode: 'text',
    title: t('confirmDanger.deleteDbTitle'),
    message: t('confirmDanger.deleteDbMsg', { name }),
    requiredText: name,
    inputLabel: t('confirmDanger.inputNameLabel'),
    placeholder: t('confirmDanger.inputDbPlaceholder', { name }),
    action: { type: 'db', name }
  }
}

// 添加/编辑连接在独立窗口完成后通过共享信号触发刷新
watch(dbVersion, () => load())

onMounted(load)
</script>

<style scoped>
.db-window { padding: 10px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.hint { color: #6e6e73; font-size: 12px; }
.connections { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.conn-card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; background: #fff; }
.conn-head { margin-bottom: 8px; }
.conn-title { font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 6px; }
.tag { background: #eef2ff; color: #3730a3; font-size: 11px; padding: 2px 6px; border-radius: 4px; }
.conn-addr { color: #6b7280; font-size: 12px; margin-top: 4px; }
.conn-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.empty { color: #9ca3af; padding: 20px; text-align: center; grid-column: 1 / -1; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.small { padding: 4px 8px; font-size: 12px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 480px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal.wide { width: 780px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px; }
.tabs { display: flex; gap: 8px; border-bottom: 1px solid #e5e7eb; margin-bottom: 10px; }
.tab { padding: 8px 12px; background: none; border: none; cursor: pointer; font-size: 13px; color: #6b7280; }
.tab.active { color: #111827; border-bottom: 2px solid #111827; font-weight: 600; }
.panel { max-height: 360px; overflow: auto; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mini-table th, .mini-table td { padding: 6px 8px; border-bottom: 1px solid #f0f0f0; text-align: left; }
.pre { background: #f3f4f6; padding: 10px; border-radius: 6px; font-size: 12px; overflow: auto; max-height: 300px; }
.result { margin-top: 8px; }
.mongo-row { display: flex; gap: 8px; margin-bottom: 8px; }
.mongo-row input { flex: 1; padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
textarea, input[type="number"] { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
textarea { width: 100%; box-sizing: border-box; margin-bottom: 8px; }
</style>
