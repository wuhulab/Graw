<!--
  网络存储连接表单窗口（新增 / 编辑）
  业务：配置到远端存储的连接（FTP / FTPS / SMB / WebDAV / S3），供面板统一挂载与备份。
  后端模块：/api/netstorage
  关键状态：form（连接字段）、isEdit（是否编辑模式）、saving / testing（保存与测试忙态）
  打开方式：由 NetStorageWindow 通过「openNetStorageForm」事件弹出；conn 为 null 表示新增，有值表示编辑
-->
<template>
  <div class="ns-form">
    <!-- 顶部工具栏：标题 + 关闭（风格同「运行环境」新建窗口） -->
    <div class="toolbar">
      <span class="title"><Server :size="15" /> {{ isEdit ? $t('netstorage.editConn') : $t('netstorage.addConn') }}</span>
      <button class="btn" style="margin-left:auto;" @click="emit('close')">{{ $t('common.close') }}</button>
    </div>

    <!-- 表单主体（可滚动） -->
    <div class="body">
      <div class="form-grid">
        <label class="field span-2">
          <span class="field-label">{{ $t('netstorage.name') }} <b class="req">*</b></span>
          <input v-model.trim="form.name" class="inp" maxlength="64" :placeholder="$t('netstorage.namePlaceholder')"
                 :class="{ err: err && !form.name }" />
        </label>

        <label class="field">
          <span class="field-label">{{ $t('netstorage.typeLabel') }} <b class="req">*</b></span>
          <select v-model="form.type" class="inp">
            <option value="ftp">{{ $t('netstorage.typeFtp') }}</option>
            <option value="ftps">{{ $t('netstorage.typeFtps') }}</option>
            <option value="smb">{{ $t('netstorage.typeSmb') }}</option>
            <option value="webdav">{{ $t('netstorage.typeWebdav') }}</option>
            <option value="s3">{{ $t('netstorage.typeS3') }}</option>
          </select>
        </label>

        <label class="field">
          <span class="field-label">{{ $t('netstorage.portLabel') }}</span>
          <input v-model.number="form.port" class="inp mono" type="number" min="1" max="65535"
                 :placeholder="'默认 ' + defaultPort" />
        </label>

        <label class="field span-2">
          <span class="field-label">{{ $t('netstorage.hostLabel') }} <b class="req">*</b></span>
          <input v-model.trim="form.host" class="inp mono" maxlength="255" :placeholder="$t('netstorage.hostPlaceholder')"
                 :class="{ err: err && !form.host }" />
        </label>

        <label class="field">
          <span class="field-label">{{ $t('netstorage.usernameLabel') }}</span>
          <input v-model.trim="form.username" class="inp mono" maxlength="128" :placeholder="$t('netstorage.usernamePlaceholder')" />
        </label>

        <label class="field">
          <span class="field-label">{{ $t('netstorage.passwordLabel') }}</span>
          <input v-model="form.password" class="inp mono" type="password" maxlength="256"
                 :placeholder="isEdit && form.has_password ? $t('netstorage.passwordKeep') : $t('netstorage.passwordPlaceholder')" />
        </label>
        <span v-if="isEdit && form.has_password" class="field-hint">{{ $t('netstorage.passwordKeepHint') }}</span>

        <label class="field span-2">
          <span class="field-label">{{ baseLabel }}</span>
          <input v-model.trim="form.base" class="inp mono" maxlength="1024" :placeholder="basePlaceholder" />
        </label>

        <!-- 对象存储扩展参数 -->
        <template v-if="form.type === 's3'">
          <label class="field">
            <span class="field-label">{{ $t('netstorage.region') }}</span>
            <input v-model.trim="paramRegion" class="inp mono" :placeholder="$t('netstorage.regionPlaceholder')" />
          </label>
          <label class="field">
            <span class="field-label">{{ $t('netstorage.secure') }}</span>
            <select v-model="paramSecure" class="inp">
              <option :value="true">{{ $t('netstorage.secureTls') }}</option>
              <option :value="false">{{ $t('netstorage.securePlain') }}</option>
            </select>
          </label>
        </template>

        <div v-if="errorMsg" class="error span-2">{{ errorMsg }}</div>
      </div>

      <!-- 底部操作 -->
      <div class="footer">
        <div class="test-wrap" v-if="isEdit">
          <button class="btn" :disabled="testing" @click="testConn">
            <span v-if="testing">{{ $t('netstorage.testing') }}</span>
            <span v-else>{{ $t('netstorage.test') }}</span>
          </button>
          <span v-if="testMsg" class="test-msg" :class="testOk ? 'ok' : 'fail'">{{ testMsg }}</span>
        </div>
        <div style="flex:1;"></div>
        <button class="btn" @click="emit('close')">{{ $t('common.cancel') }}</button>
        <button class="btn primary" :disabled="saving" @click="save">
          {{ saving ? $t('common.saving') : $t('common.save') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted } from 'vue'        // Composition API 响应式与生命周期钩子
import { useI18n } from 'vue-i18n'                              // 国际化：取 t() 生成动态文案
import { netstorageApi } from '../../api'                       // 网络存储后端接口封装
import { notifyNetStorageChanged } from '../../store/netstorage'   // 跨窗口通知：连接变更后刷新主列表
import { Server } from 'lucide-vue-next'                       // 图标：服务器

const { t } = useI18n()

const props = defineProps({
  // 传入连接对象（脱敏）则为编辑模式；null 表示新增
  conn: { type: Object, default: null }
})
const emit = defineEmits(['close', 'saved'])   // 向父窗口发出：关闭、已保存（触发主列表刷新）

const isEdit = !!props.conn

const form = reactive({
  name: '', type: 'ftp', host: '', port: null,
  username: '', password: '', base: '', params: {}, has_password: false
})
const saving = ref(false)
const testing = ref(false)
const errorMsg = ref('')
const testMsg = ref('')
const testOk = ref(false)

const defaultPort = computed(() => ({ ftp: 21, ftps: 990, smb: 445, webdav: 443, s3: 9000 })[form.type] || 0)
const baseLabel = computed(() => {
  const map = { smb: t('netstorage.shareLabel'), s3: t('netstorage.bucketLabel'), webdav: t('netstorage.webdavRootLabel') }
  return map[form.type] || t('netstorage.baseLabel')
})
const basePlaceholder = computed(() => {
  const map = { smb: t('netstorage.sharePlaceholder'), s3: t('netstorage.bucketPlaceholder'), webdav: t('netstorage.webdavRootPlaceholder') }
  return map[form.type] || ''
})
const paramRegion = computed({
  get: () => form.params?.region || '',
  set: (v) => { form.params = { ...(form.params || {}), region: v } }
})
const paramSecure = computed({
  get: () => form.params?.secure !== false,
  set: (v) => { form.params = { ...(form.params || {}), secure: !!v } }
})

onMounted(() => {
  if (!isEdit) return
  // 编辑回填；密码已脱敏（后端不回传明文），留空提交表示保持原密码
  Object.assign(form, {
    name: props.conn.name || '',
    type: props.conn.type || 'ftp',
    host: props.conn.host || '',
    port: props.conn.port || null,
    username: props.conn.username || '',
    password: '',
    base: props.conn.base || '',
    params: props.conn.params || {},
    has_password: !!props.conn.has_password
  })
})

// --- 动作：保存连接（新增或更新） ---
async function save() {
  errorMsg.value = ''
  if (!form.name.trim()) { errorMsg.value = t('netstorage.nameRequired'); return }
  if (!form.host.trim()) { errorMsg.value = t('netstorage.hostRequired'); return }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      type: form.type,
      host: form.host.trim(),
      port: form.port || null,
      username: form.username,
      password: form.password,
      base: form.base.trim() || null,
      params: form.params || {}
    }
    if (isEdit) {
      await netstorageApi.updateConn(props.conn.id, payload)
    } else {
      await netstorageApi.createConn(payload)
    }
    // 通知主窗口刷新卡片列表
    notifyNetStorageChanged()
    emit('saved')
    emit('close')
  } catch (e) {
    errorMsg.value = e?.response?.data?.detail || t('netstorage.saveFailed', { error: e.message })
  } finally {
    saving.value = false
  }
}

async function testConn() {
  testing.value = true
  testMsg.value = ''
  try {
    const r = await netstorageApi.test(props.conn.id)
    testOk.value = !!r.ok
    testMsg.value = r.ok ? t('netstorage.testOk') : (r.message || t('netstorage.testFail'))
  } catch (e) {
    testOk.value = false
    testMsg.value = e?.response?.data?.detail || e.message
  } finally {
    testing.value = false
  }
}
</script>

<style scoped>
.ns-form { display: flex; flex-direction: column; height: 100%; background: #f5f5f7; }
.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; background: #fff; }
.toolbar .title { display: flex; align-items: center; gap: 6px; font-weight: 600; color: #0a3d7a; }
.body { flex: 1; overflow: auto; padding: 12px 14px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 12px; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field.span-2 { grid-column: span 2; }
.field-label { font-size: 12px; color: #374151; }
.field-hint { font-size: 11px; color: #6e6e73; }
.req { color: #b91c1c; }
.inp { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; width: 100%; box-sizing: border-box; background: #fff; }
.inp.mono { font-family: ui-monospace, monospace; }
.inp.err { border-color: #b91c1c; }
.error { color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; padding: 6px 8px; border-radius: 6px; font-size: 12px; }
.footer { display: flex; align-items: center; gap: 8px; margin-top: 14px; }
.test-wrap { display: flex; align-items: center; gap: 8px; }
.test-msg { font-size: 12px; }
.test-msg.ok { color: #1a7f4a; }
.test-msg.fail { color: #b91c1c; }
.btn { padding: 6px 14px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
</style>