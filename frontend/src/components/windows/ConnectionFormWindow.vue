<template>
  <div class="conn-form">
    <h3>{{ isEdit ? '编辑数据库连接' : '添加数据库连接' }}</h3>
    <div class="form">
      <label>名称 *</label>
      <input v-model="form.name" placeholder="连接名称" />

      <label>类型</label>
      <select v-model="form.db_type">
        <option value="mysql">MySQL / MariaDB</option>
        <option value="postgresql">PostgreSQL</option>
        <option value="mongodb">MongoDB</option>
        <option value="redis">Redis</option>
      </select>

      <label>主机</label>
      <input v-model="form.host" placeholder="127.0.0.1" />

      <label>端口</label>
      <input v-model.number="form.port" type="number" />

      <template v-if="form.db_type !== 'redis'">
        <label>用户名</label>
        <input v-model="form.username" :placeholder="usernamePlaceholder" />
      </template>

      <label>密码</label>
      <input v-model="form.password" type="password" placeholder="留空表示无密码" />

      <template v-if="form.db_type !== 'redis'">
        <label>{{ dbLabel }}</label>
        <input v-model="form.database" :placeholder="dbPlaceholder" />
      </template>
      <div v-else class="hint">Redis 默认使用 0 号库，连接后在「管理 → 查询/CLI」中可切换库号。</div>

      <div v-if="errorMsg" class="error">{{ errorMsg }}</div>

      <div class="actions">
        <button class="btn" @click="$emit('close')">取消</button>
        <button class="btn primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, computed, watch, onMounted } from 'vue'
import { databasesApi } from '../../api'
import { notifyDatabasesChanged } from '../../store/databases'

const props = defineProps({
  // 传入连接对象则为编辑模式；为 null 表示新增
  conn: { type: Object, default: null }
})
const emit = defineEmits(['close', 'saved'])

const isEdit = !!props.conn

// 各类型默认端口 / 用户名 / 数据库与表单文案
const DEFAULTS = {
  mysql: { port: 3306, username: 'root', database: '' },
  postgresql: { port: 5432, username: 'postgres', database: 'postgres' },
  mongodb: { port: 27017, username: '', database: 'admin' },
  redis: { port: 6379, username: '', database: '' }
}

const DB_LABELS = {
  mysql: { label: '默认数据库（MySQL）', ph: '如：mydb（可留空）' },
  postgresql: { label: '默认数据库（PostgreSQL）', ph: '如：postgres' },
  mongodb: { label: '默认库 / 认证库（MongoDB）', ph: '如：admin' },
  redis: { label: '', ph: '' }
}

const form = reactive({ name: '', db_type: 'mysql', host: '127.0.0.1', port: 3306, username: 'root', password: '', database: '' })
const saving = ref(false)
const errorMsg = ref('')
// 初始化过程中抑制类型切换的默认值自动套用（避免覆盖编辑态已保存的端口等）
const suppressAuto = ref(false)

const usernamePlaceholder = computed(() =>
  form.db_type === 'mongodb' ? '可留空（无认证时）' : `如：${DEFAULTS[form.db_type]?.username || 'root'}`
)
const dbLabel = computed(() => DB_LABELS[form.db_type]?.label || '默认数据库')
const dbPlaceholder = computed(() => DB_LABELS[form.db_type]?.ph || '')

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

onMounted(() => {
  suppressAuto.value = true
  if (isEdit) {
    const d = DEFAULTS[props.conn.db_type] || DEFAULTS.mysql
    Object.assign(form, {
      name: props.conn.name || '',
      db_type: props.conn.db_type || 'mysql',
      host: props.conn.host || '127.0.0.1',
      port: props.conn.port || d.port,
      username: props.conn.username || '',
      password: props.conn.password || '',
      database: props.conn.database || ''
    })
  }
  suppressAuto.value = false
})

async function save() {
  if (!form.name.trim()) {
    errorMsg.value = '请填写连接名称'
    return
  }
  errorMsg.value = ''
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      db_type: form.db_type,
      host: form.host.trim() || '127.0.0.1',
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
