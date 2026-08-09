<template>
  <div style="display:flex; flex-direction:column; height:100%;" @click="closeMenus">
    <div class="toolbar">
      <button class="btn" @click="goUp" :disabled="!parent"><ArrowUp :size="14" /> 上级</button>
      <button class="btn" @click="refresh">刷新</button>
      <input type="text" v-model="pathInput" @keyup.enter="go" />
      <button class="btn" @click="go">转到</button>
      <div style="position:relative;">
        <button class="btn" @click.stop="newMenuOpen = !newMenuOpen">新建</button>
        <div v-if="newMenuOpen" style="position:absolute; top:100%; left:0; margin-top:4px; background:#fff; border:1px solid rgba(0,0,0,0.1); border-radius:8px; box-shadow:0 8px 24px rgba(0,0,0,0.12); z-index:100; min-width:120px;">
          <div class="menu-item" @click="createFolder">文件夹</div>
          <div class="menu-item" @click="createFile">文件</div>
        </div>
      </div>
    </div>
    <div style="flex:1; overflow:auto;">
      <table class="dt">
        <thead>
          <tr>
            <th>名称</th>
            <th style="width:120px;">大小</th>
            <th style="width:160px;">修改时间</th>
            <th style="width:200px;">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in items" :key="it.path" @dblclick="openItem(it)" @contextmenu.prevent="onContextMenu($event, it)">
            <td>
              <span style="margin-right:4px;"><component :is="it.is_dir ? Folder : FileText" :size="14" /></span>{{ it.name }}
            </td>
            <td>{{ it.is_dir ? '-' : formatBytes(it.size) }}</td>
            <td>{{ it.modified ? formatTime(it.modified) : '-' }}</td>
            <td>
              <button class="btn" v-if="!it.is_dir && it.size < 2*1024*1024" @click="editFile(it)">查看</button>
              <button class="btn" @click="download(it)" v-if="!it.is_dir">下载</button>
              <button class="btn" @click="renameItem(it)">重命名</button>
              <button class="btn danger" @click="remove(it)">删除</button>
            </td>
          </tr>
          <tr v-if="!items.length">
            <td colspan="4"><div class="empty">空目录</div></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="editing" style="position:absolute;inset:0;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:10;">
      <div style="width:80%;height:80%;background:#fff;border-radius:6px;display:flex;flex-direction:column;border:1px solid #b0c4de;">
        <div class="toolbar">
          <strong style="font-family:monospace;">{{ editing.path }}</strong>
          <button class="btn" style="margin-left:auto;" @click="saveEdit">保存</button>
          <button class="btn" @click="editing = null">关闭</button>
        </div>
        <textarea v-model="editing.content" style="flex:1;border:none;outline:none;padding:8px;font-family:Consolas,monospace;font-size:12px;"></textarea>
      </div>
    </div>
    <div v-if="contextMenu.show" class="context-menu" :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }">
      <div class="menu-item" @click="menuRename">重命名</div>
      <div class="menu-item" @click="menuDelete">删除</div>
      <div class="menu-item" @click="menuOpenTerminal">在此处打开终端</div>
      <div class="menu-item" @click="menuEdit">编辑</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { filesApi, formatBytes } from '../../api'
import { ArrowUp, Folder, FileText } from 'lucide-vue-next'

const items = ref([])
const parent = ref(null)
const path = ref('')
const pathInput = ref('')
const editing = ref(null)
const newMenuOpen = ref(false)
const contextMenu = ref({ show: false, x: 0, y: 0, item: null })

const emit = defineEmits(['openTerminal', 'openEditor'])

async function load(p) {
  try {
    const r = await filesApi.list(p)
    items.value = r.items
    parent.value = r.parent
    path.value = r.path
    pathInput.value = r.path
  } catch (e) {
    alert('无法访问：' + (e.response?.data?.detail || e.message))
  }
}

function refresh() { load(path.value) }
function go() { load(pathInput.value || '') }
function goUp() { if (parent.value) load(parent.value) }

function openItem(it) {
  if (it.is_dir) load(it.path)
  else if (it.size < 2 * 1024 * 1024) editFile(it)
}

async function editFile(it) {
  try {
    const r = await filesApi.read(it.path)
    editing.value = { path: r.path, content: r.content }
  } catch (e) {
    alert('读取失败：' + (e.response?.data?.detail || e.message))
  }
}

async function saveEdit() {
  try {
    await filesApi.write(editing.value.path, editing.value.content)
    editing.value = null
    refresh()
  } catch (e) {
    alert('保存失败：' + (e.response?.data?.detail || e.message))
  }
}

async function remove(it) {
  if (!confirm(`确认删除 ${it.name}？`)) return
  try {
    await filesApi.remove(it.path)
    refresh()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

async function renameItem(it) {
  const name = prompt('新名称', it.name)
  if (!name || name === it.name) return
  const dst = it.path.replace(/[\\/][^\\/]+$/, m => m[0] + name)
  try {
    await filesApi.rename(it.path, dst)
    refresh()
  } catch (e) {
    alert('重命名失败：' + (e.response?.data?.detail || e.message))
  }
}

async function createFolder() {
  newMenuOpen.value = false
  const name = prompt('新建文件夹名称')
  if (!name) return
  const sep = path.value.includes('\\') ? '\\' : '/'
  const newPath = path.value.replace(/[\\/]$/, '') + sep + name
  try {
    await filesApi.mkdir(newPath)
    refresh()
  } catch (e) {
    alert('创建失败：' + (e.response?.data?.detail || e.message))
  }
}

async function createFile() {
  newMenuOpen.value = false
  const name = prompt('新建文件名称')
  if (!name) return
  const sep = path.value.includes('\\') ? '\\' : '/'
  const newPath = path.value.replace(/[\\/]$/, '') + sep + name
  try {
    await filesApi.write(newPath, '')
    refresh()
  } catch (e) {
    alert('创建失败：' + (e.response?.data?.detail || e.message))
  }
}

function download(it) {
  window.open(`/api/files/download?path=${encodeURIComponent(it.path)}`, '_blank')
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
  editFile(it)
}

onMounted(async () => {
  await load('')
})
</script>

<style scoped>
.menu-item { padding: 8px 12px; font-size: 12px; cursor: pointer; }
.menu-item:hover { background: #f5f5f7; }
.context-menu {
  position: absolute;
  background: #fff;
  border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
  z-index: 200;
  min-width: 140px;
  padding: 4px 0;
}
</style>
