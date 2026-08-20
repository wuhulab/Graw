<template>
  <div class="panelbackup-window">
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge"><Archive :size="14" /> 面板配置备份</span>
        <span class="hint">导出/导入 data/ 下全部配置（users.json / secret.key / 各模块配置），用于迁移与容灾</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn primary" :disabled="busy" @click="doExport">
          <Download :size="14" /> 导出配置
        </button>
        <label class="btn upload-btn" :class="{ disabled: busy }">
          <Upload :size="14" /> 导入配置
          <input type="file" accept=".tar.gz" hidden @change="onImport" :disabled="busy" />
        </label>
        <button class="btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
      </div>
    </div>

    <!-- 警告 -->
    <div class="warn-box">
      <OctagonAlert :size="14" />
      导入配置会覆盖当前面板全部数据（包括 JWT 签名密钥、用户密码等敏感信息），
      导入前会自动备份当前配置到 panelbackups/ 以便回滚，导入后建议重启后端。
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="archives.length === 0" class="empty">
      <Archive :size="40" style="color:#9ca3af;" />
      <div>还没有导出过配置，点击「导出配置」备份面板数据</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>文件名</th>
            <th>大小</th>
            <th>创建时间</th>
            <th>类型</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in archives" :key="a.name">
            <td class="mono">{{ a.name }}</td>
            <td>{{ formatBytes(a.size) }}</td>
            <td class="mono">{{ fmtTime(a.created_at) }}</td>
            <td><span class="badge" :class="a.is_pre_import ? 'warn' : 'ok'">{{ a.is_pre_import ? '导入前备份' : '导出归档' }}</span></td>
            <td class="actions-cell">
              <button class="btn mini" :disabled="busy" @click="doDownload(a.name)">下载</button>
              <button class="btn mini danger-text" :disabled="busy" @click="doDelete(a.name)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="msg" :class="['msg', msgType]">{{ msg }}</div>

    <!-- 高风险操作二次确认：删除归档 / 导入覆盖配置需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="confirm.title"
      :message="confirm.message"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      :confirm-label="confirm.confirmLabel || '确认'"
      @confirm="doConfirmDanger"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Archive, Download, Upload, RefreshCw, OctagonAlert } from 'lucide-vue-next'
import { panelbackupApi, formatBytes } from '../../api'
import ConfirmDialog from '../ConfirmDialog.vue'

const loading = ref(false)
const busy = ref(false)
const archives = ref([])
const msg = ref('')
const msgType = ref('')
// 高风险操作二次确认状态
const confirm = ref({ show: false, title: '', message: '', confirmLabel: '', action: null })

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function loadAll() {
  loading.value = true
  try {
    const r = await panelbackupApi.list()
    archives.value = (r && r.archives) || []
  } catch (e) {
    msg.value = '加载失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    loading.value = false
  }
}

async function doExport() {
  busy.value = true
  msg.value = ''
  try {
    const r = await panelbackupApi.export()
    msg.value = `导出完成：${r.name}（${formatBytes(r.size)}，${r.file_count} 个文件）`
    msgType.value = 'ok'
    await loadAll()
  } catch (e) {
    msg.value = '导出失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

async function doDownload(name) {
  busy.value = true
  try {
    const r = await panelbackupApi.download(name)
    const blob = new Blob([r.data], { type: 'application/gzip' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    msg.value = '下载失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

function doDelete(name) {
  // 高风险操作：删除归档需输入面板密码确认
  confirm.value = {
    show: true,
    title: '删除面板备份确认',
    message: `删除归档「${name}」？此操作不可恢复。\n请输入面板密码以确认。`,
    confirmLabel: '删除',
    action: { type: 'delete', name }
  }
}

async function onImport(e) {
  const file = e.target?.files?.[0]
  if (!file) return
  if (!file.name.endsWith('.tar.gz')) {
    msg.value = '仅支持 .tar.gz 归档'
    msgType.value = 'err'
    return
  }
  // 高风险操作：导入覆盖配置需输入面板密码确认（先暂存文件，确认后上传）
  confirm.value = {
    show: true,
    title: '导入面板配置确认',
    message: `导入配置「${file.name}」？这将覆盖当前面板全部配置，导入前会自动备份当前配置。\n请输入面板密码以确认。`,
    confirmLabel: '导入',
    action: { type: 'import', file }
  }
  e.target.value = ''
}

async function doConfirmDanger() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return
  if (a.type === 'delete') {
    await doDeleteConfirmed(a.name)
  } else if (a.type === 'import') {
    await doImportConfirmed(a.file)
  }
}

async function doDeleteConfirmed(name) {
  busy.value = true
  msg.value = ''
  try {
    await panelbackupApi.delete(name)
    msg.value = '已删除'
    msgType.value = 'ok'
    await loadAll()
  } catch (e) {
    msg.value = '删除失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

async function doImportConfirmed(file) {
  busy.value = true
  msg.value = ''
  try {
    const fd = new FormData()
    fd.append('file', file)
    const r = await panelbackupApi.import(fd)
    msg.value = `导入完成：恢复 ${r.restored_files} 个文件（原配置已备份为 ${r.pre_backup}）`
    msgType.value = 'ok'
    await loadAll()
  } catch (e) {
    msg.value = '导入失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.panelbackup-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; background: #dbeafe; color: #1e40af; }
.hint { color: #6e6e73; font-size: 12px; }
.toolbar-actions { display: flex; gap: 8px; }
.warn-box { display: flex; align-items: flex-start; gap: 8px; padding: 8px 10px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; margin-bottom: 10px; font-size: 12px; color: #7f1d1d; line-height: 1.6; }
.upload-btn { cursor: pointer; position: relative; }
.upload-btn.disabled { opacity: 0.5; cursor: not-allowed; }

.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; flex: 1; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; }
.actions-cell { display: flex; gap: 4px; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.mini { padding: 3px 8px; font-size: 11.5px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.danger-text { color: #b91c1c; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.warn { background: #fed7aa; color: #9a3412; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }

.msg { font-size: 12.5px; margin-top: 10px; }
.msg.ok { color: #065f46; }
.msg.err { color: #b91c1c; }
</style>