<template>
  <div class="db-window">
    <div class="toolbar">
      <button class="btn primary" @click="$emit('openConnectionForm')"><Plus :size="14" /> 添加连接</button>
      <span class="hint">MySQL: {{ status.mysql_libs ? '已安装' : '未安装' }} | Redis: {{ status.redis_libs ? '已安装' : '未安装' }} | PostgreSQL: {{ status.postgresql_libs ? '已安装' : '未安装' }} | MongoDB: {{ status.mongodb_libs ? '已安装' : '未安装' }}</span>
    </div>
    <div class="connections">
      <div v-for="c in connections" :key="c.id" class="conn-card">
        <div class="conn-head">
          <div class="conn-title">{{ c.name }} <span class="tag">{{ typeLabel(c.db_type) }}</span></div>
          <div class="conn-addr">{{ c.host }}:{{ c.port }}<template v-if="c.database"> / {{ c.database }}</template></div>
        </div>
        <div class="conn-actions">
          <button class="btn small" @click="testConn(c)">测试连接</button>
          <button class="btn small" @click="$emit('openConnectionForm', c)">编辑</button>
          <button class="btn small" @click="openManage(c)">管理</button>
          <button class="btn small danger" @click="removeConn(c)">删除</button>
        </div>
      </div>
      <div v-if="connections.length === 0" class="empty">暂无数据库连接</div>
    </div>

    <!-- Manage Modal -->
    <div v-if="showManage" class="modal-overlay" @click.self="showManage = false">
      <div class="modal wide">
        <h3>管理: {{ manageConn?.name }} <span class="tag">{{ typeLabel(manageConn?.db_type) }}</span></h3>
        <div class="tabs">
          <button class="tab" :class="{active: tab==='dbs'}" @click="tab='dbs'">数据库 / Keyspace</button>
          <button class="tab" :class="{active: tab==='query'}" @click="tab='query'">查询 / CLI</button>
        </div>

        <div v-if="tab==='dbs'" class="panel">
          <!-- MySQL / PostgreSQL：库列表 + 创建/删除 -->
          <div v-if="manageConn?.db_type==='mysql' || manageConn?.db_type==='postgresql'">
            <button class="btn small primary" @click="showCreateDB=true">创建数据库</button>
            <table class="mini-table">
              <thead><tr><th>数据库名</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="db in dbList" :key="db"><td>{{ db }}</td><td><button class="btn small danger" @click="dropDB(db)">删除</button></td></tr>
              </tbody>
            </table>
          </div>
          <!-- MongoDB：库列表 + 删除（数据库随首次写入自动创建） -->
          <div v-else-if="manageConn?.db_type==='mongodb'">
            <div class="hint" style="margin-bottom:8px">MongoDB 数据库随首次写入自动创建，无需手动新建。</div>
            <table class="mini-table">
              <thead><tr><th>数据库名</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="db in dbList" :key="db"><td>{{ db }}</td><td><button class="btn small danger" @click="dropDB(db)">删除</button></td></tr>
              </tbody>
            </table>
          </div>
          <!-- Redis：info 信息 -->
          <div v-else>
            <pre class="pre">{{ redisInfo }}</pre>
          </div>
        </div>

        <div v-if="tab==='query'" class="panel">
          <!-- MySQL / PostgreSQL：SQL 编辑器 -->
          <div v-if="manageConn?.db_type==='mysql' || manageConn?.db_type==='postgresql'">
            <textarea v-model="sqlText" rows="4" placeholder="输入 SELECT / SHOW / DESCRIBE 等 SQL..." />
            <button class="btn small primary" @click="execSql">执行</button>
            <div v-if="queryResult" class="result">
              <table v-if="queryResult.columns" class="mini-table">
                <thead><tr><th v-for="col in queryResult.columns" :key="col">{{ col }}</th></tr></thead>
                <tbody><tr v-for="(row,idx) in queryResult.rows" :key="idx"><td v-for="(cell, cidx) in row" :key="cidx">{{ cell }}</td></tr></tbody>
              </table>
              <div v-else>影响行数: {{ queryResult.affected }}</div>
            </div>
          </div>
          <!-- MongoDB：集合 + JSON 过滤条件 -->
          <div v-else-if="manageConn?.db_type==='mongodb'">
            <div class="mongo-row">
              <input v-model="mongoCollection" placeholder="集合名，如 users" />
              <input v-model.number="mongoLimit" type="number" placeholder="条数" title="返回条数" style="width:80px" />
            </div>
            <textarea v-model="mongoFilter" rows="3" placeholder='过滤条件 JSON，如 {"age":{"$gt":18}}（可留空表示全部）' />
            <button class="btn small primary" @click="execMongo">查询</button>
            <pre v-if="mongoResult !== null" class="pre">{{ mongoResult }}</pre>
          </div>
          <!-- Redis：命令输入 -->
          <div v-else>
            <input v-model="redisCmd" placeholder="如: GET mykey 或 HGETALL myhash" />
            <button class="btn small primary" @click="execRedis">执行</button>
            <pre v-if="redisResult !== null" class="pre">{{ redisResult }}</pre>
          </div>
        </div>

        <div class="actions">
          <button class="btn" @click="showManage = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- Create DB Modal -->
    <div v-if="showCreateDB" class="modal-overlay" @click.self="showCreateDB=false">
      <div class="modal">
        <h3>创建数据库</h3>
        <input v-model="newDbName" placeholder="数据库名" />
        <div class="actions">
          <button class="btn" @click="showCreateDB=false">取消</button>
          <button class="btn primary" @click="doCreateDB">创建</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { databasesApi } from '../../api'
import { dbVersion } from '../../store/databases'
import { Plus } from 'lucide-vue-next'

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

// 类型展示名映射（连接卡片标签 / 管理标题）
const TYPE_LABELS = { mysql: 'MySQL', redis: 'Redis', postgresql: 'PostgreSQL', mongodb: 'MongoDB' }
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

async function removeConn(c) {
  if (!confirm('删除此连接配置？')) return
  await databasesApi.deleteConn(c.id)
  await load()
}

async function testConn(c) {
  try {
    await databasesApi.test(c.id)
    alert('连接成功')
  } catch (e) {
    alert('连接失败: ' + (e?.response?.data?.detail || e.message))
  }
}

async function openManage(c) {
  manageConn.value = c
  tab.value = 'dbs'
  queryResult.value = null
  redisResult.value = null
  mongoResult.value = null
  showManage.value = true
  const data = await databasesApi.listDBs(c.id)
  if (c.db_type === 'mysql' || c.db_type === 'postgresql' || c.db_type === 'mongodb') {
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

async function dropDB(name) {
  if (!confirm(`删除数据库 ${name}？`)) return
  await databasesApi.deleteDB(manageConn.value.id, name)
  if (manageConn.value) openManage(manageConn.value)
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
