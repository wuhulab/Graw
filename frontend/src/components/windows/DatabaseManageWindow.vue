<!--
  DatabaseManageWindow.vue — 数据库连接管理控制台（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 DatabaseWindow 的「管理连接」modal 弹窗独立为桌面窗口，
    有更大的操作空间，且误点灰色遮罩不再丢失已输入的 SQL/查询内容。
    按连接类型展示库列表（MySQL/Pg/MongoDB）、Redis info、SQLite 表，
    支持执行 SQL、MongoDB 查询、Redis 命令；支持创建/删除数据库（删除
    需输入库名二次确认）。
  后端模块：
    /api/databases 的 listDBs / query / createDB / deleteDB。
  关键状态：
    tab          dbs（库列表）/ query（查询）标签
    dbList       MySQL/Pg/Mongo 的库列表
    queryResult  SQL 查询结果
    confirm      删除数据库的二次确认（需输入库名）
  打开方式：
    由 App.vue 的 openDatabaseManage(payload) 打开，props 传入 { conn }。
    创建数据库另开 DatabaseCreateWindow（emit('openDatabaseCreate')），
    保存成功后经 formBus 通知本窗口刷新库列表。
-->
<template>
  <div class="manage-window">
    <div class="ui-tabs">
      <button class="ui-tab" :class="{ active: tab === 'dbs' }" @click="tab='dbs'">{{ $t('database.databases') }}</button>
      <button class="ui-tab" :class="{ active: tab === 'query' }" @click="tab='query'">{{ $t('database.query') }}</button>
    </div>

    <!-- 库列表标签 -->
    <div v-if="tab==='dbs'" class="panel">
      <!-- MySQL / PostgreSQL：库列表 + 创建/删除 -->
      <div v-if="conn?.db_type==='mysql' || conn?.db_type==='postgresql'">
        <button class="ui-btn primary mini" @click="emit('openDatabaseCreate', { conn })">{{ $t('database.createDB') }}</button>
        <div class="ui-table-wrap" style="margin-top:8px;">
          <table>
            <thead><tr><th>{{ $t('database.dbNamePlaceholder') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
            <tbody>
              <tr v-for="db in dbList" :key="db"><td>{{ db }}</td><td><button class="ui-btn mini danger" @click="dropDB(db)">{{ $t('database.dropDB') }}</button></td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <!-- MongoDB：库列表 + 删除（数据库随首次写入自动创建） -->
      <div v-else-if="conn?.db_type==='mongodb'">
        <div class="ui-hint" style="margin-bottom:8px">{{ $t('database.mongoHint') }}</div>
        <div class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('database.dbNamePlaceholder') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
            <tbody>
              <tr v-for="db in dbList" :key="db"><td>{{ db }}</td><td><button class="ui-btn mini danger" @click="dropDB(db)">{{ $t('database.dropDB') }}</button></td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <!-- Redis：info 信息 -->
      <div v-else-if="conn?.db_type==='redis'">
        <pre class="pre">{{ redisInfo }}</pre>
      </div>
      <!-- SQLite：单文件库，展示库内数据表列表 + 文件信息 -->
      <div v-else-if="conn?.db_type==='sqlite'">
        <div class="ui-hint" style="margin-bottom:8px">
          {{ $t('database.sqliteFileInfo', { path: conn.database }) }}<template v-if="sqliteFileSize"> · {{ $t('database.sqliteSize', { size: sqliteFileSize }) }}</template>
        </div>
        <div v-if="sqliteTables.length" class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('database.sqliteTableLabel') }}</th></tr></thead>
            <tbody><tr v-for="tb in sqliteTables" :key="tb"><td>{{ tb }}</td></tr></tbody>
          </table>
        </div>
        <div v-else class="ui-hint">{{ $t('database.sqliteNoTables') }}</div>
      </div>
    </div>

    <!-- 查询标签 -->
    <div v-if="tab==='query'" class="panel">
      <!-- MySQL / PostgreSQL / SQLite：SQL 编辑器 -->
      <div v-if="conn?.db_type==='mysql' || conn?.db_type==='postgresql' || conn?.db_type==='sqlite'">
        <textarea class="ui-textarea sql-box" v-model="sqlText" rows="5" :placeholder="$t('database.sqlPlaceholder')" />
        <button class="ui-btn primary mini" @click="execSql">{{ $t('database.execute') }}</button>
        <div v-if="queryError" class="query-error">{{ queryError }}</div>
        <div v-if="queryResult" class="result">
          <div v-if="queryResult.columns" class="ui-table-wrap">
            <table>
              <thead><tr><th v-for="col in queryResult.columns" :key="col">{{ col }}</th></tr></thead>
              <tbody><tr v-for="(row,idx) in queryResult.rows" :key="idx"><td v-for="(cell, cidx) in row" :key="cidx">{{ cell }}</td></tr></tbody>
            </table>
          </div>
          <div v-else class="ui-hint">{{ $t('database.affectedRows', { count: queryResult.affected }) }}</div>
        </div>
      </div>
      <!-- MongoDB：集合 + JSON 过滤条件 -->
      <div v-else-if="conn?.db_type==='mongodb'">
        <div class="mongo-row">
          <input class="ui-input" v-model="mongoCollection" :placeholder="$t('database.mongoCollectionPlaceholder')" />
          <input class="ui-input" v-model.number="mongoLimit" type="number" :placeholder="$t('database.mongoLimitPlaceholder')" :title="$t('database.mongoLimitTitle')" style="width:80px" />
        </div>
        <textarea class="ui-textarea" v-model="mongoFilter" rows="3" :placeholder="$t('database.mongoFilterPlaceholder')" />
        <button class="ui-btn primary mini" @click="execMongo">{{ $t('database.mongoQuery') }}</button>
        <div v-if="queryError" class="query-error">{{ queryError }}</div>
        <pre v-if="mongoResult !== null" class="pre">{{ mongoResult }}</pre>
      </div>
      <!-- Redis：命令输入 -->
      <div v-else>
        <input class="ui-input" v-model="redisCmd" :placeholder="$t('database.redisPlaceholder')" @keyup.enter="execRedis" />
        <button class="ui-btn primary mini" @click="execRedis">{{ $t('database.execute') }}</button>
        <div v-if="queryError" class="query-error">{{ queryError }}</div>
        <pre v-if="redisResult !== null" class="pre">{{ redisResult }}</pre>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除数据库需输入库名 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="text"
      :title="confirm.title"
      :message="confirm.message"
      :required-text="confirm.requiredText"
      :input-label="confirm.inputLabel"
      :placeholder="confirm.placeholder"
      :confirm-label="$t('common.delete')"
      @confirm="doDropDB"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
// 状态/共享信号监听
import { ref, onMounted, watch } from 'vue'
// 翻译函数
import { useI18n } from 'vue-i18n'
// 数据库管理接口
import { databasesApi } from '../../api'
// 打开「创建数据库」独立窗口
import ConfirmDialog from '../ConfirmDialog.vue'   // 删除库的「输入库名」二次确认
import { formBus } from '../../store/formBus'   // 创建库窗口保存成功后刷新库列表

const { t } = useI18n()

// conn: 当前管理的连接对象（由 App.vue 打开窗口时传入）
const props = defineProps({
  conn: { type: Object, required: true }
})
const emit = defineEmits(['openDatabaseCreate'])   // 打开独立「创建数据库」窗口

const tab = ref('dbs')   // 库列表 / 查询 标签
const dbList = ref([])   // MySQL/Pg/Mongo 的库列表
const redisInfo = ref('')   // Redis info 原文
const sqlText = ref('')   // SQL 编辑器内容
const redisCmd = ref('')   // Redis 命令输入
const queryResult = ref(null)   // SQL 查询结果
const queryError = ref('')      // 查询/命令执行错误回显（后端不可达、SQL 语法错等）
const redisResult = ref(null)   // Redis 命令结果
const mongoCollection = ref('')   // MongoDB 集合名
const mongoFilter = ref('')   // MongoDB 过滤 JSON
const mongoLimit = ref(100)   // MongoDB 查询条数上限（默认 100）
const mongoResult = ref(null)   // MongoDB 查询结果
// SQLite 库内数据表列表与文件大小（仅 sqlite 连接使用）
const sqliteTables = ref([])
const sqliteFileSize = ref(0)
// 删除数据库二次确认状态（需输入库名）
const confirm = ref({ show: false, title: '', message: '', requiredText: '', inputLabel: '', placeholder: '', dbName: '' })

// --- 按连接类型加载库列表 / info / SQLite 表 ---
async function loadData() {
  const c = props.conn
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

// 打开即加载库列表；创建数据库窗口保存成功后（formBus.databases）自动刷新
watch(() => formBus.databases, loadData)

// --- 执行 SQL（MySQL / PostgreSQL / SQLite）；失败在结果区回显错误 ---
async function execSql() {
  queryError.value = ''
  try {
    queryResult.value = await databasesApi.query(props.conn.id, { sql: sqlText.value })
  } catch (e) {
    queryResult.value = null
    queryError.value = e?.response?.data?.detail || e?.message || String(e)
  }
}

// --- 执行 Redis 命令 ---
async function execRedis() {
  queryError.value = ''
  try {
    const data = await databasesApi.query(props.conn.id, { command: redisCmd.value })
    redisResult.value = data.result
  } catch (e) {
    redisResult.value = null
    queryError.value = e?.response?.data?.detail || e?.message || String(e)
  }
}

// --- 执行 MongoDB 查询（集合 + 过滤条件 + 条数限制） ---
async function execMongo() {
  queryError.value = ''
  try {
    const data = await databasesApi.query(props.conn.id, {
      collection: mongoCollection.value,
      filter: mongoFilter.value,
      limit: Number(mongoLimit.value) || 100   // 非法输入回退默认 100 条
    })
    mongoResult.value = JSON.stringify(data.result, null, 2)
  } catch (e) {
    mongoResult.value = null
    queryError.value = e?.response?.data?.detail || e?.message || String(e)
  }
}

// 删除数据库第一步：只弹确认框（需输入库名），不真删
function dropDB(name) {
  // 高风险操作：删除数据库需输入数据库名称确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.deleteDbTitle'),
    message: t('confirmDanger.deleteDbMsg', { name }),
    requiredText: name,
    inputLabel: t('confirmDanger.inputNameLabel'),
    placeholder: t('confirmDanger.inputDbPlaceholder', { name }),
    dbName: name
  }
}

// 删除数据库第二步：确认框校验通过后真正下发删除并刷新
async function doDropDB() {
  const name = confirm.value.dbName
  confirm.value.show = false
  await databasesApi.deleteDB(props.conn.id, name)
  await loadData()
}

onMounted(loadData)
</script>

<style scoped>
.manage-window { padding: 14px; display: flex; flex-direction: column; gap: 10px; height: 100%; box-sizing: border-box; }
.panel { flex: 1; overflow: auto; min-height: 0; }
.sql-box { margin-bottom: 8px; }
.query-error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin: 8px 0; word-break: break-all; }
.pre { background: #f3f4f6; padding: 10px; border-radius: 6px; font-size: 12px; overflow: auto; max-height: 300px; white-space: pre-wrap; word-break: break-all; }
.result { margin-top: 8px; }
.mongo-row { display: flex; gap: 8px; margin-bottom: 8px; }
.mongo-row input:first-child { flex: 1; }
</style>