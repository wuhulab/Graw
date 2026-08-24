<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#fff;" @click="closeMenus">
    <!-- 工具栏：协议徽标 + 当前连接名 + 路径导航 -->
    <div class="toolbar">
      <span class="proto-tag" :class="'tag-' + (conn?.type || '')">{{ protoLabel(conn?.type) }}</span>
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

    <!-- 表格 -->
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

    <!-- 右键菜单 -->
    <Teleport to="body">
      <div v-if="contextMenu.show" class="context-menu" :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }" @click.stop>
        <div class="menu-item" @click="menuEdit">{{ $t('files.openEdit') }}</div>
        <div class="menu-item" @click="menuRename">{{ $t('files.rename') }}</div>
        <div class="menu-item" @click="menuDelete">{{ $t('files.delete') }}</div>
        <div class="menu-item" @click="menuDownload">{{ $t('files.download') }}</div>
      </div>
    </Teleport>

    <!-- 文本编辑对话框 -->
    <div v-if="editor.show" class="modal-mask" @click.self="closeEditor">
      <div class="modal edit-modal">
        <div class="modal-title">{{ $t('netstorage.editTitle', { name: editor.name }) }}</div>
        <textarea v-model="editor.content" class="editor-area" spellcheck="false"></textarea>
        <div class="modal-actions">
          <button class="btn" @click="closeEditor">{{ $t('common.cancel') }}</button>
          <button class="btn-primary" :disabled="editor.saving" @click="saveEditor">
            {{ editor.saving ? $t('common.saving') : $t('common.save') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { netstorageApi, formatBytes } from '../../api'
import { getApiErrorMessage } from '../../utils/apiErrors'
import { auth } from '../../store/auth'
import { ArrowUp, Folder, FileText, Image as ImageIcon, Film, Upload } from 'lucide-vue-next'

const { t } = useI18n()

// props：通过桌面点「网络储存」卡片进入，conn 为 { id, name, type }
const props = defineProps({ conn: { type: Object, default: null } })

const items = ref([])
const parent = ref(null)
const path = ref('/')
const pathInput = ref('/')
const newMenuOpen = ref(false)
const contextMenu = ref({ show: false, x: 0, y: 0, item: null })
const editor = ref({ show: false, name: '', path: '', content: '', saving: false })

function protoLabel(type) {
  return ({ ftp: 'FTP', ftps: 'FTPS', smb: 'SMB', webdav: 'WebDAV', s3: 'S3' })[type] || type || ''
}

async function load(p) {
  if (!props.conn?.id) return
  try {
    const r = await netstorageApi.list(props.conn.id, p)
    items.value = r.items || []
    parent.value = r.parent || null
    path.value = r.path || '/'
    pathInput.value = path.value
  } catch (e) {
    alert(t('files.accessFailed', { error: getApiErrorMessage(e, t) }))
  }
}

function refresh() { load(path.value) }
function go() { load(pathInput.value || '/') }
function goUp() { if (parent.value) load(parent.value) }

function isImage(name) {
  return ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'].includes((name.split('.').pop() || '').toLowerCase())
}
function isVideo(name) {
  return ['mp4', 'webm', 'mov', 'avi', 'mkv'].includes((name.split('.').pop() || '').toLowerCase())
}

function openItem(it) {
  if (it.is_dir) load(it.path)
  else editText(it)
}

// 下载：fetch + blob（带 Authorization 头），由临时 <a> 触发浏览器保存
async function download(it) {
  try {
    const resp = await fetch(`/api/netstorage/connections/${props.conn.id}/download?path=${encodeURIComponent(it.path)}`, {
      headers: { Authorization: `Bearer ${auth.token}` },
    })
    if (!resp.ok) {
      const d = await resp.json().catch(() => '')
      throw new Error(d?.detail || `HTTP ${resp.status}`)
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

async function editText(it) {
  editor.value = { show: true, name: it.name, path: it.path, content: '', saving: false }
  try {
    const r = await netstorageApi.read(props.conn.id, it.path)
    editor.value.content = r.content
  } catch (e) {
    alert(t('files.readFailed', { error: getApiErrorMessage(e, t) }))
    editor.value.show = false
  }
}

async function saveEditor() {
  editor.value.saving = true
  try {
    await netstorageApi.write(props.conn.id, editor.value.path, editor.value.content)
    editor.value.show = false
  } catch (e) {
    alert(t('files.createFailed', { error: getApiErrorMessage(e, t) }))
  } finally {
    editor.value.saving = false
  }
}
function closeEditor() { editor.value.show = false }

async function remove(it) {
  if (!confirm(t('files.confirmDelete', { name: it.name }))) return
  try {
    await netstorageApi.remove(props.conn.id, it.path)
    refresh()
  } catch (e) {
    alert(t('files.deleteFailed', { error: getApiErrorMessage(e, t) }))
  }
}

async function renameItem(it) {
  const name = prompt(t('files.renamePrompt'), it.name)
  if (!name || name === it.name) return
  const dst = it.path.replace(/\/[^\/]+$/, m => '/' + name)
  try {
    await netstorageApi.rename(props.conn.id, it.path, dst)
    refresh()
  } catch (e) {
    alert(t('files.renameFailed', { error: getApiErrorMessage(e, t) }))
  }
}

async function createFolder() {
  newMenuOpen.value = false
  const name = prompt(t('files.newFolderPrompt'))
  if (!name) return
  const newPath = (path.value.endsWith('/') ? path.value : path.value + '/') + name
  try {
    await netstorageApi.mkdir(props.conn.id, newPath)
    refresh()
  } catch (e) {
    alert(t('files.createFailed', { error: getApiErrorMessage(e, t) }))
  }
}

async function createFile() {
  newMenuOpen.value = false
  const name = prompt(t('files.newFilePrompt'))
  if (!name) return
  const newPath = (path.value.endsWith('/') ? path.value : path.value + '/') + name
  try {
    await netstorageApi.write(props.conn.id, newPath, '')
    refresh()
  } catch (e) {
    alert(t('files.createFailed', { error: getApiErrorMessage(e, t) }))
  }
}

async function onUpload(e) {
  const file = e.target.files[0]
  if (!file) return
  try {
    const fd = new FormData()
    fd.append('path', path.value)
    fd.append('file', file)
    await netstorageApi.upload(props.conn.id, path.value, fd)
    refresh()
  } catch (e) {
    alert(t('files.uploadFailed', { error: getApiErrorMessage(e, t) }))
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
function menuEdit() {
  const it = contextMenu.value.item
  closeMenus()
  if (it && !it.is_dir) editText(it)
}
function menuDelete() { const it = contextMenu.value.item; closeMenus(); if (it) remove(it) }
function menuRename() { const it = contextMenu.value.item; closeMenus(); if (it) renameItem(it) }
function menuDownload() { const it = contextMenu.value.item; closeMenus(); if (it && !it.is_dir) download(it) }

onMounted(() => load('/'))
</script>

<style scoped>
.menu-item { padding: 8px 12px; font-size: 12px; cursor: pointer; }
.menu-item:hover { background: #f5f5f7; }
.context-menu {
  position: fixed; background: #fff; border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.12); z-index: 200;
  min-width: 140px; padding: 4px 0;
}
.proto-tag { background: #eef5ff; color: #0a3d7a; border-radius: 10px; font-size: 11px; padding: 3px 9px; font-weight: 600; }
.proto-tag.tag-smb { background: #fff4e6; color: #b4640a; }
.proto-tag.tag-webdav { background: #eafaf0; color: #1a7f4a; }
.proto-tag.tag-s3 { background: #fdf1f6; color: #b8336e; }
.empty { color: #6e6e73; text-align: center; padding: 40px 0; font-size: 13px; }
.editor-area { width: 100%; height: 300px; font-family: ui-monospace, monospace; font-size: 12px; border: 1px solid rgba(0,0,0,0.12); border-radius: 6px; padding: 8px; resize: vertical; }
</style>