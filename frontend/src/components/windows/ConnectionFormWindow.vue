<!--
  ConnectionFormWindow.vue — 数据库连接的新增/编辑表单窗口
  ==========================================================
  业务作用：
    新增或编辑一个数据库连接（MySQL/PostgreSQL/MongoDB/Redis/SQLite）：
    按类型动态切换字段（SQLite 只填文件路径，Redis 无用户名/数据库概念），
    切换类型时自动套用该类型的默认端口/用户名/数据库。保存后通知数据库主
    窗口刷新连接列表。
  后端模块：
    /api/databases 的 createConn / updateConn（新增/更新连接）。
  关键状态：
    - form      连接表单（name/db_type/host/port/username/password/database）
    - isEdit    是否为编辑模式（由 props.conn 判断）
    - suppressAuto 初始化期间抑制类型切换的默认值自动套用
  打开方式：
    由 DatabaseWindow 的「新增连接」/「编辑」按钮打开，props 传入 conn
    （编辑时）；保存成功后 emit saved/close。
-->
<template>
  <div class="conn-form">
    <h3>{{ isEdit ? $t('connectionform.editTitle', { name: form.name }) : $t('connectionform.addTitle') }}</h3>
    <div class="form">
      <label>{{ $t('common.name') }} *</label>
      <input v-model="form.name" :placeholder="$t('connectionform.namePlaceholder')" />

      <label>{{ $t('connectionform.type') }}</label>
      <select v-model="form.db_type">
        <option value="mysql">{{ $t('connectionform.mysql') }}</option>
        <option value="postgresql">{{ $t('connectionform.postgresql') }}</option>
        <option value="mongodb">{{ $t('connectionform.mongodb') }}</option>
        <option value="redis">{{ $t('connectionform.redis') }}</option>
        <option value="sqlite">{{ $t('connectionform.sqlite') }}</option>
      </select>

      <template v-if="form.db_type !== 'sqlite'">
        <label>{{ $t('connectionform.host') }}</label>
        <input v-model="form.host" placeholder="127.0.0.1" />

        <label>{{ $t('connectionform.port') }}</label>
        <input v-model.number="form.port" type="number" />

        <template v-if="form.db_type !== 'redis'">
          <label>{{ $t('connectionform.username') }}</label>
          <input v-model="form.username" :placeholder="usernamePlaceholder" />
        </template>

        <label>{{ $t('connectionform.password') }}</label>
        <input v-model="form.password" type="password" :placeholder="passwordPlaceholder" />

        <template v-if="form.db_type !== 'redis'">
          <label>{{ dbLabel }}</label>
          <input v-model="form.database" :placeholder="dbPlaceholder" />
        </template>
        <div v-else class="hint">{{ $t('connectionform.redisHint') }}</div>
      </template>
      <!-- SQLite：嵌入式数据库，只需指定本地文件路径；支持相对（基于数据目录）或绝对路径 -->
      <template v-else>
        <label>{{ $t('connectionform.sqliteFile') }}</label>
        <input v-model="form.database" :placeholder="$t('connectionform.sqliteFilePlaceholder')" />
        <div class="hint">{{ $t('connectionform.sqliteHint') }}</div>
      </template>

      <div v-if="errorMsg" class="error">{{ errorMsg }}</div>

      <div class="actions">
        <button class="btn" @click="$emit('close')">{{ $t('connectionform.cancel') }}</button>
        <button class="btn primary" :disabled="saving" @click="save">{{ saving ? $t('common.saving') : $t('connectionform.save') }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, computed, watch, onMounted } from 'vue'   // 表单/状态/派生文案/类型切换监听/挂载初始化
import { useI18n } from 'vue-i18n'   // 翻译函数
import { databasesApi } from '../../api'   // /api/databases：连接增改接口
import { notifyDatabasesChanged } from '../../store/databases'   // 保存后通知数据库主窗口刷新连接列表

const { t } = useI18n()

const props = defineProps({
  // 传入连接对象则为编辑模式；为 null 表示新增
  conn: { type: Object, default: null }
})
const emit = defineEmits(['close', 'saved'])   // close 关窗；saved 通知父窗口保存成功

const isEdit = !!props.conn

// 各类型默认端口 / 用户名 / 数据库与表单文案（文案 key 走 i18n）
const DEFAULTS = {
  mysql: { port: 3306, username: 'root', database: '' },
  postgresql: { port: 5432, username: 'postgres', database: 'postgres' },
  mongodb: { port: 27017, username: '', database: 'admin' },
  redis: { port: 6379, username: '', database: '' },
  sqlite: { port: 0, username: '', database: '' }
}

// 各类型「默认数据库」标签 / 占位符对应的 i18n key
const DB_LABELS = {
  mysql: { labelKey: 'connectionform.defaultDB', phKey: 'connectionform.dbPlaceholderMysql' },
  postgresql: { labelKey: 'connectionform.defaultDBPg', phKey: 'connectionform.dbPlaceholderPg' },
  mongodb: { labelKey: 'connectionform.defaultAuthDB', phKey: 'connectionform.dbPlaceholderMongo' },
  redis: { labelKey: '', phKey: '' },
  sqlite: { labelKey: '', phKey: '' }
}

const form = reactive({ name: '', db_type: 'mysql', host: '127.0.0.1', port: 3306, username: 'root', password: '', database: '' })   // 连接表单，默认 MySQL
const saving = ref(false)   // 保存中
const errorMsg = ref('')   // 表单错误提示
// 初始化过程中抑制类型切换的默认值自动套用（避免覆盖编辑态已保存的端口等）
const suppressAuto = ref(false)

const usernamePlaceholder = computed(() =>
  form.db_type === 'mongodb'
    ? t('connectionform.usernameHint')
    : t('connectionform.usernameExampleValue', { username: DEFAULTS[form.db_type]?.username || 'root' })
)
// 编辑模式密码已脱敏（后端不回传明文）：留空提交表示保持原密码
const passwordPlaceholder = computed(() =>
  isEdit && props.conn?.has_password
    ? t('connectionform.passwordKeepPlaceholder')
    : t('connectionform.passwordPlaceholder')
)
const dbLabel = computed(() => {
  const key = DB_LABELS[form.db_type]?.labelKey
  return key ? t(key) : t('connectionform.defaultDBGeneric')
})
const dbPlaceholder = computed(() => {
  const key = DB_LABELS[form.db_type]?.phKey
  return key ? t(key) : ''
})

// 切换类型时自动套用该类型的默认端口/用户名/数据库
watch(
  () => form.db_type,
  (t) => {
    if (suppressAuto.value) return
    const d = DEFAULTS[t] || DEFAULTS.mysql
    form.port = d.port
    form.username = d.username
    form.database = d.database
  }
)

// --- 挂载初始化：编辑模式回填表单（密码不回填，留空即保持原密码） ---
onMounted(() => {
  suppressAuto.value = true   // 回填期间禁止默认值自动套用，防止覆盖编辑态已保存的端口等
  if (isEdit) {
    const d = DEFAULTS[props.conn.db_type] || DEFAULTS.mysql
    Object.assign(form, {
      name: props.conn.name || '',
      db_type: props.conn.db_type || 'mysql',
      host: props.conn.host || '127.0.0.1',
      port: props.conn.port || d.port,
      username: props.conn.username || '',
      // 密码已脱敏：编辑时留空提交即保持原密码（后端配合处理）
      password: '',
      database: props.conn.database || ''
    })
  }
  suppressAuto.value = false
})

// --- 保存连接：校验名称后提交到后端，成功后通知主窗口刷新 ---
async function save() {
  if (!form.name.trim()) {
    errorMsg.value = t('connectionform.nameRequired')
    return
  }
  errorMsg.value = ''
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      db_type: form.db_type,
      // SQLite 无主机/端口概念：host 置空，连接信息以 database(文件路径) 为准
      host: form.db_type === 'sqlite' ? '' : (form.host.trim() || '127.0.0.1'),
      port: Number(form.port) || (DEFAULTS[form.db_type]?.port || 3306),
      username: form.username,
      password: form.password,
      database: form.database
    }
    if (isEdit) {
      await databasesApi.updateConn(props.conn.id, payload)
    } else {
      await databasesApi.createConn(payload)
    }
    // 通知数据库主窗口刷新列表
    notifyDatabasesChanged()
    emit('saved')
    emit('close')
  } catch (e) {
    errorMsg.value = e?.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.conn-form { padding: 14px; }
.conn-form h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 8px; }
.form label { font-size: 12px; color: #374151; }
.form input, .form select { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; width: 100%; box-sizing: border-box; }
.hint { color: #6e6e73; font-size: 12px; }
.error { color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; padding: 6px 8px; border-radius: 6px; font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
