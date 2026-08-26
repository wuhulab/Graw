<!--
  网络存储管理主窗口
  业务：列出已配置的网络存储连接，支持新增、编辑、测试连通性、删除（高危，需面板密码二次确认），并打开文件浏览。
  后端模块：/api/netstorage
  关键状态：conns（连接列表）、confirm（删除二次确认）、testResult（测试结果）
  打开方式：桌面/任务栏「网络存储」入口挂载；新增/编辑弹出独立 NetStorageFormWindow
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 工具栏：新增打开独立表单窗口 -->
    <div class="toolbar">
      <span style="color:#0a3d7a; font-weight:600;">{{ $t('netstorage.title') }}</span>
      <span style="color:#6e6e73; font-size:11px;">{{ $t('netstorage.count', { count: conns.length }) }}</span>
      <button class="btn" style="margin-left:auto;" @click="openAdd">+ {{ $t('netstorage.addConn') }}</button>
      <button class="btn" @click="load">{{ $t('netstorage.refresh') }}</button>
    </div>

    <!-- 卡片列表 -->
    <div style="flex:1; overflow:auto; padding:12px;">
      <div v-if="loading" class="empty">{{ $t('netstorage.loading') }}</div>
      <div v-else-if="!conns.length" class="empty">{{ $t('netstorage.noConnections') }}</div>
      <div v-else class="grid">
        <div v-for="c in conns" :key="c.id" class="card">
          <div class="card-top" :class="'proto-' + c.type">
            <component :is="typeIcon(c.type)" :size="26" />
            <div class="card-name" :title="c.name">{{ c.name }}</div>
            <span class="pill" :class="'proto-' + c.type">{{ protoLabel(c.type) }}</span>
          </div>
          <div class="card-meta">
            <div class="meta-line" :title="c.host">{{ $t('netstorage.host') }}: {{ c.host }}{{ c.port ? ':' + c.port : '' }}</div>
            <div v-if="c.base" class="meta-line" :title="c.base">{{ $t('netstorage.base') }}: {{ c.base }}</div>
            <div v-if="c.username" class="meta-line">{{ $t('netstorage.username') }}: {{ c.username }}</div>
          </div>
          <div class="card-actions">
            <button class="btn primary btn-sm" @click="openConn(c)">{{ $t('netstorage.open') }}</button>
            <button class="btn btn-sm" :disabled="testingId === c.id" @click="testConn(c)">
              {{ testingId === c.id ? $t('netstorage.testing') : $t('netstorage.test') }}
            </button>
            <button class="btn btn-sm" @click="openEdit(c)">{{ $t('netstorage.edit') }}</button>
            <button class="btn btn-sm danger" @click="removeConn(c)">{{ $t('netstorage.delete') }}</button>
          </div>
          <div v-if="testResult && testResult.id === c.id" class="test-result" :class="testResult.ok ? 'ok' : 'fail'">
            {{ testResult.message }}
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 高风险操作二次确认：删除网络存储需输入面板密码 -->
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
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'                  // Composition API：响应式、监听、挂载
import { useI18n } from 'vue-i18n'                            // 国际化：取 t() 生成动态文案
import { netstorageApi } from '../../api'                     // 网络存储后端接口封装
import { nsVersion } from '../../store/netstorage'            // 跨窗口版本信号：新增/编辑/删除后触发刷新
import { Server, Database, Folder, Cloud } from 'lucide-vue-next'   // 图标：按协议类型展示
import ConfirmDialog from '../ConfirmDialog.vue'              // 高危操作二次确认弹窗（输入面板密码）

const { t } = useI18n()

const conns = ref([])
const loading = ref(false)
const testResult = ref(null) // { id, ok, message }
const testingId = ref(null)
// 高风险操作二次确认状态（删除网络存储需输入面板密码）
const confirm = ref({ show: false, mode: 'password', title: '', message: '', requiredText: '', inputLabel: '', placeholder: '', action: null })

// 发射给 App.vue：打开「添加/编辑储存」独立窗口（conn 为空则新增）
const emit = defineEmits(['openNetStorageForm', 'openNetStorageBrowse'])   // 向桌面发出：打开表单窗口、打开文件浏览

async function load() {
  loading.value = true
  try {
    const r = await netstorageApi.connections()
    conns.value = r.connections || []
  } catch (e) {
    alert(t('netstorage.loadFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    loading.value = false
  }
}

// 跨窗口数据变化（新增/编辑/删除在独立窗口完成）→ 自动刷新列表
watch(nsVersion, () => load())

function typeIcon(type) {
  return ({ ftp: Server, ftps: Server, smb: Folder, webdav: Cloud, s3: Database })[type] || Cloud
}
function protoLabel(type) {
  return ({ ftp: 'FTP', ftps: 'FTPS', smb: 'SMB', webdav: 'WebDAV', s3: 'S3' })[type] || type
}

function openAdd() { emit('openNetStorageForm') }
function openEdit(c) { emit('openNetStorageForm', c) }

function openConn(c) {
  // 点击卡片 → 启动「文件管理」，标题为 文件管理：<名称>
  emit('openNetStorageBrowse', { id: c.id, name: c.name, type: c.type })
}

async function testConn(c) {
  testingId.value = c.id
  try {
    const r = await netstorageApi.test(c.id)
    testResult.value = { id: c.id, ok: r.ok, message: r.message }
  } catch (e) {
    testResult.value = { id: c.id, ok: false, message: e.response?.data?.detail || e.message }
  } finally {
    testingId.value = null
  }
}

function removeConn(c) {
  // 高风险操作：删除网络存储需输入面板密码确认后才真正执行
  confirm.value = {
    show: true,
    mode: 'password',
    title: t('confirmDanger.deleteNetStorageTitle'),
    message: t('confirmDanger.deleteNetStorageMsg', { name: c.name }),
    requiredText: '',
    inputLabel: t('confirmDanger.inputPwdLabel'),
    placeholder: t('confirmDanger.inputPwdPlaceholder'),
    action: { type: 'conn', conn: c }
  }
}

// ConfirmDialog 密码校验通过后的回调：真正执行删除
async function doConfirm() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return
  try {
    await netstorageApi.deleteConn(a.conn.id)
    await load()
  } catch (e) {
    alert(t('netstorage.deleteFailed', { error: e.response?.data?.detail || e.message }))
  }
}

onMounted(load)
</script>

<style scoped>
/* 与其它应用一致的主按钮配色（中性深色，替代旧的 btn-primary 蓝） */
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.card { background: #fff; border: 1px solid rgba(0,0,0,0.08); border-radius: 12px; padding: 14px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); transition: box-shadow .2s; }
.card:hover { box-shadow: 0 6px 18px rgba(0,0,0,0.1); }
.card-top { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; color: #0a84ff; }
.card-name { flex: 1; font-weight: 600; color: #1d1d1f; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pill { background: #eef5ff; color: #0a3d7a; border-radius: 10px; font-size: 11px; padding: 2px 8px; }
.pill.proto-smb { background: #fff4e6; color: #b4640a; }
.pill.proto-webdav { background: #eafaf0; color: #1a7f4a; }
.pill.proto-s3 { background: #fdf1f6; color: #b8336e; }
.card-meta { font-size: 11px; color: #6e6e73; margin-bottom: 12px; display: flex; flex-direction: column; gap: 3px; }
.meta-line { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-actions { display: flex; gap: 4px; flex-wrap: wrap; }
.btn-sm { font-size: 11px; padding: 4px 8px; }
.test-result { margin-top: 8px; font-size: 11px; border-radius: 6px; padding: 5px 8px; }
.test-result.ok { background: #eafaf0; color: #1a7f4a; }
.test-result.fail { background: #fdf0f0; color: #c0392b; }
.empty { color: #6e6e73; text-align: center; padding: 40px 0; font-size: 13px; }
</style>