<template>
  <div style="display:flex; flex-direction:column; height:100%;" @click="closeMenus"
    @dragover.prevent="onDragOver"
    @dragenter.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop">
    <div class="toolbar">
      <button class="btn" @click="goUp" :disabled="!parent"><ArrowUp :size="14" /> {{ $t('files.parent') }}</button>
      <button class="btn" @click="refresh">{{ $t('files.refresh') }}</button>
      <input type="text" v-model="pathInput" @keyup.enter="go" />
      <div style="position:relative;">
        <button class="btn" @click.stop="newMenuOpen = !newMenuOpen">{{ $t('files.new') }}</button>
        <div v-if="newMenuOpen" style="position:absolute; top:100%; left:0; margin-top:4px; background:#fff; border:1px solid rgba(0,0,0,0.1); border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,0.12); z-index:100; min-width:120px;">
          <div class="menu-item" @click="createFolder">{{ $t('files.newFolder') }}</div>
          <div class="menu-item" @click="createFile">{{ $t('files.newFile') }}</div>
        </div>
      </div>
      <label class="btn" style="cursor:pointer;">
        <input type="file" style="display:none;" @change="onUpload" />
        <Upload :size="14" /> {{ $t('files.upload') }}
      </label>
    </div>
    <!-- 拖拽上传提示遮罩 -->
    <div v-if="dragOver" class="drag-overlay">
      <div class="drag-inner">
        <Upload :size="48" />
        <div class="drag-text">{{ $t('files.dropUpload', { path }) }}</div>
      </div>
    </div>
    <!-- 批量上传进度条 -->
    <div v-if="uploadingFiles.length > 0" class="upload-progress">
      <span>{{ $t('files.uploading', { current: uploadIdx + 1, total: uploadingFiles.length }) }}</span>
      <div class="progress-bar"><div class="progress-fill" :style="{ width: ((uploadIdx / uploadingFiles.length) * 100) + '%' }"></div></div>
    </div>
    <div style="flex:1; overflow:auto;">
      <table class="dt">
        <thead>
          <tr>
            <th>{{ $t('files.name') }}</th>
            <th style="width:120px;">{{ $t('files.size') }}</th>
            <th style="width:160px;">{{ $t('files.modified') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in items" :key="it.path" @dblclick="openItem(it)" @contextmenu.prevent="onContextMenu($event, it)">
            <td>
               <span style="margin-right:4px;"><component :is="it.is_dir ? Folder : (isImage(it.name) ? ImageIcon : isVideo(it.name) ? Film : FileText)" :size="14" /></span>{{ it.name }}
            </td>
            <td>{{ it.is_dir ? '-' : formatBytes(it.size) }}</td>
            <td>{{ it.modified ? formatTime(it.modified) : '-' }}</td>
          </tr>
          <tr v-if="!items.length">
            <td colspan="3"><div class="empty">{{ $t('files.emptyDir') }}</div></td>
          </tr>
        </tbody>
      </table>
    </div>
    <Teleport to="body">
      <div v-if="contextMenu.show" class="context-menu" :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }" @click.stop>
        <div class="menu-item" @click="menuEdit">{{ $t('files.openEdit') }}</div>
        <div class="menu-item" @click="menuRename">{{ $t('files.rename') }}</div>
        <div class="menu-item" @click="menuDelete">{{ $t('files.delete') }}</div>
        <div class="menu-item" @click="menuCopy">{{ $t('files.copyTo') }}</div>
        <div class="menu-item" @click="menuCompress">{{ $t('files.compress') }}</div>
        <div class="menu-item" @click="menuExtract" v-if="isArchive(contextMenu.item?.name)">{{ $t('files.extract') }}</div>
        <div class="menu-item" @click="menuChmod">{{ $t('files.permissions') }}</div>
        <div class="menu-item" @click="menuDownload">{{ $t('files.download') }}</div>
        <div class="menu-item" @click="menuOpenTerminal">{{ $t('files.openTerminal') }}</div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { filesApi, formatBytes } from '../../api'
import { getApiErrorMessage } from '../../utils/apiErrors'
import { auth } from '../../store/auth'
import { ArrowUp, Folder, FileText, Image as ImageIcon, Film, Upload } from 'lucide-vue-next'

const { t } = useI18n()
const items = ref([])
const parent = ref(null)
const path = ref('')
const pathInput = ref('')
const newMenuOpen = ref(false)
const contextMenu = ref({ show: false, x: 0, y: 0, item: null })
// 拖拽上传状态
const dragOver = ref(false)
const uploadingFiles = ref([])
const uploadIdx = ref(0)

const emit = defineEmits(['openTerminal', 'openEditor', 'openMedia'])
const props = defineProps({ path: String })

async function load(p) {
  try {
    const r = await filesApi.list(p)
    items.value = r.items
    parent.value = r.parent
    path.value = r.path
    pathInput.value = r.path
  } catch (e) {
    alert(t('files.accessFailed', { error: getApiErrorMessage(e, t) }))
  }
}

function refresh() { load(path.value) }
function go() { load(pathInput.value || '') }
function goUp() { if (parent.value) load(parent.value) }

function isImage(name) {
  const ext = name.split('.').pop().toLowerCase()
  return ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'].includes(ext)
}

function isVideo(name) {
  const ext = name.split('.').pop().toLowerCase()
  return ['mp4', 'webm', 'mov', 'avi', 'mkv'].includes(ext)
}

function openItem(it) {
  if (it.is_dir) load(it.path)
  else if (isImage(it.name)) emit('openMedia', { path: it.path, name: it.name, type: 'image' })
  else if (isVideo(it.name)) emit('openMedia', { path: it.path, name: it.name, type: 'video' })
  else if (it.size < 2 * 1024 * 1024) openEditorWindow(it)
}

async function openEditorWindow(it) {
  try {
    const r = await filesApi.read(it.path)
    emit('openEditor', { path: r.path, content: r.content })
  } catch (e) {
    alert(t('files.readFailed', { error: getApiErrorMessage(e, t) }))
  }
}

async function remove(it) {
  if (!confirm(t('files.confirmDelete', { name: it.name }))) return
  try {
    await filesApi.remove(it.path)
    refresh()
  } catch (e) {
    alert(t('files.deleteFailed', { error: getApiErrorMessage(e, t) }))
  }
}

async function renameItem(it) {
  const name = prompt(t('files.renamePrompt'), it.name)
  if (!name || name === it.name) return
  const dst = it.path.replace(/[\\/][^\\/]+$/, m => m[0] + name)
  try {
    await filesApi.rename(it.path, dst)
    refresh()
  } catch (e) {
    alert(t('files.renameFailed', { error: getApiErrorMessage(e, t) }))
  }
}

async function createFolder() {
  newMenuOpen.value = false
  const name = prompt(t('files.newFolderPrompt'))
  if (!name) return
  const sep = path.value.includes('\\') ? '\\' : '/'
  const newPath = path.value.replace(/[\\/]$/, '') + sep + name
  try {
    await filesApi.mkdir(newPath)
    refresh()
  } catch (e) {
    alert(t('files.createFailed', { error: getApiErrorMessage(e, t) }))
  }
}

async function createFile() {
  newMenuOpen.value = false
  const name = prompt(t('files.newFilePrompt'))
  if (!name) return
  const sep = path.value.includes('\\') ? '\\' : '/'
  const newPath = path.value.replace(/[\\/]$/, '') + sep + name
  try {
    await filesApi.write(newPath, '')
    refresh()
  } catch (e) {
    alert(t('files.createFailed', { error: getApiErrorMessage(e, t) }))
  }
}

// 下载必须携带 Authorization 头：下载接口有登录鉴权，
// window.open 打开的新窗口不会带 Bearer 头（原实现必 401），
// 改用 fetch + blob 后由临时 <a> 触发浏览器保存
async function download(it) {
  try {
    const resp = await fetch(`/api/files/download?path=${encodeURIComponent(it.path)}`, {
      headers: { Authorization: `Bearer ${auth.token}` },
    })
    if (!resp.ok) {
      const detail = await resp.json().catch(() => '')
      throw new Error(detail?.detail || `HTTP ${resp.status}`)
    }
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = it.name
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    alert(t('files.downloadFailed', { error: getApiErrorMessage(e, t) }))
  }
}

function formatTime(t) {
  return new Date(t * 1000).toLocaleString()
}

function closeMenus() {
  newMenuOpen.value = false
  contextMenu.value.show = false
}

function onContextMenu(e, it) {
  contextMenu.value = { show: true, x: e.clientX, y: e.clientY, item: it }
}

function menuRename() {
  const it = contextMenu.value.item
  closeMenus()
  if (it) renameItem(it)
}

function menuDelete() {
  const it = contextMenu.value.item
  closeMenus()
  if (it) remove(it)
}

function menuOpenTerminal() {
  closeMenus()
  if (path.value) emit('openTerminal', path.value)
}

function menuEdit() {
  const it = contextMenu.value.item
  closeMenus()
  if (!it || it.is_dir) return
  if (isImage(it.name)) emit('openMedia', { path: it.path, name: it.name, type: 'image' })
  else if (isVideo(it.name)) emit('openMedia', { path: it.path, name: it.name, type: 'video' })
  else openEditorWindow(it)
}

function isArchive(name) {
  const ext = name.split('.').pop().toLowerCase()
  return ['zip', 'tar', 'gz', 'tgz', 'bz2', 'xz', '7z', 'rar'].includes(ext) || name.endsWith('.tar.gz')
}

function menuDownload() {
  const it = contextMenu.value.item
  closeMenus()
  if (it && !it.is_dir) download(it)
}

async function menuCopy() {
  const it = contextMenu.value.item
  closeMenus()
  if (!it) return
  const dst = prompt(t('files.copyToPrompt'), it.path + '_copy')
  if (!dst) return
  try { await filesApi.copy(it.path, dst); refresh() } catch (e) { alert(t('files.copyFailed', { error: getApiErrorMessage(e, t) })) }
}

async function menuCompress() {
  const it = contextMenu.value.item
  closeMenus()
  if (!it) return
  const fmt = prompt(t('files.compressFormatPrompt'), 'zip')
  if (!fmt) return
  const archive = prompt(t('files.compressPathPrompt'), it.path + (fmt==='zip'?'.zip':'.tar.gz'))
  if (!archive) return
  try { await filesApi.compress([it.path], archive, fmt); refresh() } catch (e) { alert(t('files.compressFailed', { error: getApiErrorMessage(e, t) })) }
}

async function menuExtract() {
  const it = contextMenu.value.item
  closeMenus()
  if (!it) return
  const dest = prompt(t('files.extractToPrompt'), path.value)
  if (!dest) return
  try { await filesApi.extract(it.path, dest); refresh() } catch (e) { alert(t('files.extractFailed', { error: getApiErrorMessage(e, t) })) }
}

async function menuChmod() {
  const it = contextMenu.value.item
  closeMenus()
  if (!it) return
  const modeStr = prompt(t('files.permissionPrompt'), '755')
  if (!modeStr) return
  const mode = parseInt(modeStr, 8)
  if (isNaN(mode)) { alert(t('files.permissionInvalid')); return }
  try { await filesApi.chmod(it.path, mode); refresh() } catch (e) { alert(t('files.permissionFailed', { error: getApiErrorMessage(e, t) })) }
}

async function onUpload(e) {
  const file = e.target.files[0]
  if (!file) return
  try {
    const fd = new FormData()
    fd.append('path', path.value)
    fd.append('file', file)
    await filesApi.upload(fd)
    refresh()
  } catch (e) {
    alert(t('files.uploadFailed', { error: getApiErrorMessage(e, t) }))
  }
}

// ---------- 拖拽上传 ----------
function onDragOver() {
  dragOver.value = true
}

function onDragLeave(e) {
  // 仅当离开整个容器时才隐藏遮罩（避免子元素切换闪烁）
  if (e.relatedTarget === null) dragOver.value = false
}

async function onDrop(e) {
  dragOver.value = false
  const files = Array.from(e.dataTransfer?.files || [])
  if (files.length === 0) return
  uploadingFiles.value = files
  uploadIdx.value = 0
  for (let i = 0; i < files.length; i++) {
    uploadIdx.value = i
    try {
      const fd = new FormData()
      fd.append('path', path.value)
      fd.append('file', files[i])
      await filesApi.upload(fd)
    } catch (err) {
      alert(t('files.uploadFileFailed', { name: files[i].name, error: err.response?.data?.detail || err.message }))
    }
  }
  uploadingFiles.value = []
  uploadIdx.value = 0
  refresh()
}

onMounted(async () => {
  await load(props.path || '')
})
</script>

<style scoped>
.menu-item { padding: 8px 12px; font-size: 12px; cursor: pointer; }
.menu-item:hover { background: #f5f5f7; }
.context-menu {
  position: fixed;
  background: #fff;
  border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
  z-index: 200;
  min-width: 140px;
  padding: 4px 0;
}
/* 拖拽上传遮罩 */
.drag-overlay {
  position: absolute; inset: 0; background: rgba(10, 132, 255, 0.12);
  border: 2px dashed #0a84ff; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  z-index: 50; pointer-events: none;
}
.drag-inner { display: flex; flex-direction: column; align-items: center; gap: 10px; color: #0a84ff; }
.drag-text { font-size: 14px; font-weight: 600; }
/* 上传进度条 */
.upload-progress {
  padding: 6px 12px; background: #f0f7ff; border: 1px solid #b3d7ff;
  border-radius: 6px; margin-bottom: 6px; font-size: 12px; color: #0a3d7a;
}
.progress-bar { height: 4px; background: #e5e7eb; border-radius: 2px; margin-top: 4px; overflow: hidden; }
.progress-fill { height: 100%; background: #0a84ff; transition: width 0.3s; }
</style>
