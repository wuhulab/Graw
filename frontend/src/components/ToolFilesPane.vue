<!--
  ToolFilesPane.vue — 「工具」浮窗里的单个文件管理标签

  定位：不是完整版「文件管理」窗口（FilesWindow.vue）的替代品，而是浮窗里
  轻量、快捷的浏览/上传/下载面板：路径栏 + 上级/刷新/上传 + 文件列表。

  节点绑定：创建标签时选定的节点固定不变。所有请求显式带 X-Graw-Node 头，
  不跟随当前聚焦窗口的请求级节点（api.js 会优先采用显式头）；远端 Agent
  节点由后端中间件经隧道代理，裸 SSH 节点由 hostops 远程原语执行。

  数据来源：
    GET  /api/files/list?path=      目录列表（返回 path / parent / items）
    POST /api/files/upload          上传（multipart: path + file）
    GET  /api/files/download?path=  下载（需 Authorization 头，故用 fetch+blob）
-->
<template>
  <div class="tfp-root">
    <div class="tfp-bar">
      <button class="tfp-btn" :disabled="!parent" :title="$t('files.parent')" @click="goUp">
        <ArrowUp :size="14" />
      </button>
      <input v-model="pathInput" class="tfp-path" spellcheck="false"
        :placeholder="$t('toolDock.pathPlaceholder')" @keydown.enter="go" />
      <button class="tfp-btn" :title="$t('files.refresh')" :disabled="loading" @click="load(path)">
        <RefreshCw :size="14" />
      </button>
      <button class="tfp-btn" :title="$t('files.upload')" :disabled="uploading" @click="pickUpload">
        <Upload :size="14" />
      </button>
      <input ref="fileEl" type="file" multiple class="tfp-file" @change="onUpload" />
    </div>

    <!-- 错误条：显示后端原因并提供重试（不弹 alert，避免打断浮窗里的操作） -->
    <div v-if="error" class="tfp-error">
      <span class="tfp-error-text">{{ error }}</span>
      <button class="tfp-retry" @click="load(path)">{{ $t('files.refresh') }}</button>
    </div>

    <div class="tfp-list">
      <div v-if="loading && !items.length" class="tfp-placeholder">{{ $t('common.loading') }}</div>
      <div v-else-if="!items.length" class="tfp-placeholder">{{ $t('files.emptyDir') }}</div>
      <div v-for="it in items" :key="it.path" class="tfp-row" :title="it.path" @dblclick="openItem(it)">
        <Folder v-if="it.is_dir" :size="15" class="tfp-icon dir" />
        <FileText v-else :size="15" class="tfp-icon" />
        <span class="tfp-name">{{ it.name }}</span>
        <span class="tfp-size">{{ it.is_dir ? '' : formatBytes(it.size) }}</span>
        <span class="tfp-time">{{ it.modified ? formatTime(it.modified) : '' }}</span>
        <button v-if="!it.is_dir" class="tfp-dl" :title="$t('files.download')" @click.stop="download(it)">
          <Download :size="13" />
        </button>
        <span v-else></span>
      </div>
    </div>

    <div class="tfp-status">
      <span class="tfp-status-path">{{ path || '/' }}</span>
      <span v-if="uploading" class="tfp-status-upload">{{ $t('toolDock.uploading') }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowUp, RefreshCw, Upload, Download, Folder, FileText } from 'lucide-vue-next'
import api, { formatBytes } from '../api'
import { auth } from '../store/auth'
import { getApiErrorMessage } from '../utils/apiErrors'

const props = defineProps({
  nodeId: { type: String, default: '' },      // 目标节点（创建标签时选定，固定不变）
  initialPath: { type: String, default: '' }, // 上次浏览到的目录（标签恢复时用）
})
const emit = defineEmits(['path-change'])     // 目录变化回传，由 ToolDock 存进标签描述

const { t } = useI18n()

const items = ref([])
const parent = ref(null)      // 上级目录；null 表示已在根
const path = ref('')          // 后端规范化后的当前目录
const pathInput = ref('')     // 地址栏输入值
const loading = ref(false)
const uploading = ref(false)
const error = ref('')
const fileEl = ref(null)      // 隐藏的上传 input

// 显式节点头：让请求固定路由到本标签绑定的节点
function nodeHeader() {
  return props.nodeId ? { 'X-Graw-Node': props.nodeId } : {}
}

async function load(p) {
  loading.value = true
  error.value = ''
  try {
    const r = await api.get('/files/list', {
      params: p ? { path: p } : {},
      headers: nodeHeader(),
      timeout: 60000,
    })
    items.value = r.data.items || []
    parent.value = r.data.parent || null
    path.value = r.data.path || ''
    pathInput.value = path.value
    emit('path-change', path.value)
  } catch (e) {
    items.value = []
    error.value = t('files.accessFailed', { error: getApiErrorMessage(e, t) })
  } finally {
    loading.value = false
  }
}

function refresh() { load(path.value) }
function go() { load(pathInput.value.trim()) }
function goUp() { if (parent.value) load(parent.value) }

// 双击：目录进入，文件直接下载
function openItem(it) {
  if (it.is_dir) load(it.path)
  else download(it)
}

// 下载需要 Bearer 头与节点头，window.open 带不了，故 fetch 成 blob 再触发保存
async function download(it) {
  try {
    const resp = await fetch(`/api/files/download?path=${encodeURIComponent(it.path)}`, {
      headers: { Authorization: `Bearer ${auth.token}`, ...nodeHeader() },
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = it.name
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = t('files.downloadFailed', { error: e.message || String(e) })
  }
}

function pickUpload() { if (fileEl.value) fileEl.value.click() }

async function onUpload(e) {
  const files = Array.from(e.target.files || [])
  e.target.value = ''   // 清空，允许重复选择同一文件
  if (!files.length) return
  uploading.value = true
  error.value = ''
  try {
    for (const f of files) {
      const fd = new FormData()
      fd.append('path', path.value)
      fd.append('file', f)
      await api.post('/files/upload', fd, {
        headers: { 'Content-Type': 'multipart/form-data', ...nodeHeader() },
        timeout: 600000,   // 大文件上传：放宽超时（后端全局限额另算）
      })
    }
    await load(path.value)
  } catch (err) {
    error.value = t('files.uploadFailed', { error: getApiErrorMessage(err, t) })
  } finally {
    uploading.value = false
  }
}

function formatTime(ts) {
  try { return new Date(ts * 1000).toLocaleString() } catch (e) { return '' }
}

onMounted(() => load(props.initialPath || ''))
</script>

<style scoped>
.tfp-root {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--g-surface);
  color: var(--g-text);
}
/* 工具栏：上级 / 路径 / 刷新 / 上传 */
.tfp-bar {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
  padding: 6px 8px;
  border-bottom: 1px solid var(--g-border-soft);
  background: var(--g-surface-soft);
}
.tfp-btn {
  width: 26px;
  height: 26px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  background: transparent;
  color: var(--g-text);
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.tfp-btn:hover:not(:disabled) { background: var(--g-hover); }
.tfp-btn:disabled { opacity: 0.45; cursor: default; }
.tfp-path {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 8px;
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  color: var(--g-text);
  background: var(--g-surface);
  border: 1px solid var(--g-border);
  border-radius: 6px;
  outline: none;
}
.tfp-path:focus { border-color: var(--g-primary); }
.tfp-file { display: none; }

/* 错误条 */
.tfp-error {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
  padding: 6px 10px;
  font-size: 12px;
  color: var(--ui-danger-fg, #c62828);
  background: var(--ui-danger-bg, rgba(226, 50, 79, 0.12));
}
.tfp-error-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tfp-retry {
  flex: none;
  padding: 2px 8px;
  font-size: 11px;
  color: inherit;
  background: transparent;
  border: 1px solid currentColor;
  border-radius: 5px;
  cursor: pointer;
}

/* 文件列表 */
.tfp-list { flex: 1; min-height: 0; overflow: auto; }
.tfp-placeholder {
  padding: 40px 10px;
  text-align: center;
  font-size: 12px;
  color: var(--g-text-muted);
}
.tfp-row {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) 76px 132px 22px;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: default;
  user-select: none;
}
.tfp-row:hover { background: var(--g-surface-hover); }
.tfp-icon { color: var(--g-text-muted); flex: none; }
.tfp-icon.dir { color: var(--g-primary); }
.tfp-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tfp-size, .tfp-time { color: var(--g-text-muted); font-size: 11px; white-space: nowrap; }
.tfp-time { text-align: right; }
.tfp-dl {
  width: 20px;
  height: 20px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: var(--g-text-muted);
  border: none;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0;
}
.tfp-row:hover .tfp-dl { opacity: 1; }
.tfp-dl:hover { background: var(--g-hover); color: var(--g-text); }

/* 状态栏 */
.tfp-status {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
  padding: 4px 10px;
  font-size: 11px;
  color: var(--g-text-muted);
  border-top: 1px solid var(--g-border-soft);
  background: var(--g-surface-soft);
}
.tfp-status-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: Consolas, "Courier New", monospace;
}
</style>
