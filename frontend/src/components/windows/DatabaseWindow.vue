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
          <button class="ui-btn mini danger" @click="removeConn(c)">{{ $t('database.delete') }}</button>
        </div>
      </div>
      <div v-if="connections.length === 0" class="ui-empty">{{ $t('database.noConnections') }}</div>
    </div>

    <!-- 「管理连接」与「创建数据库」已拆分为独立窗口（DatabaseManageWindow /
         DatabaseCreateWindow），避免内嵌弹窗误触遮罩丢失查询内容 -->

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
import { ref, onMounted, watch } from 'vue'   // 状态/挂载加载/共享信号监听
import { useI18n } from 'vue-i18n'   // 翻译函数
import { databasesApi } from '../../api'   // /api/databases：数据库管理接口
import { dbVersion } from '../../store/databases'   // 共享版本信号：连接增改后触发本窗口刷新
import { Plus } from 'lucide-vue-next'   // 添加连接按钮图标
import ConfirmDialog from '../ConfirmDialog.vue'   // 高风险操作的二次确认对话框

const { t } = useI18n()

// openConnectionForm 打开新增/编辑连接表单；openDatabaseManage 打开独立的管理连接窗口
const emit = defineEmits(['openConnectionForm', 'openDatabaseManage'])

const connections = ref([])   // 已保存的连接列表
const status = ref({})   // 各类型客户端库是否可用（顶部提示）
// 高风险操作二次确认状态（删除连接需输入面板密码）
const confirm = ref({ show: false, title: '', message: '', action: null })

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
watch(dbVersion, () => load())

onMounted(load)   // 进入窗口即加载
</script>

<style scoped>
.db-window { padding: 10px; }
.connections { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.conn-card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; background: #fff; }
.conn-head { margin-bottom: 8px; }
.conn-title { font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 6px; }
.tag { background: #eef2ff; color: #3730a3; font-size: 11px; padding: 2px 6px; border-radius: 4px; }
.conn-addr { color: #6b7280; font-size: 12px; margin-top: 4px; }
.conn-actions { display: flex; gap: 6px; flex-wrap: wrap; }
</style>
