<template>
  <div class="file-wrap">
    <div class="file-toolbar">
      <button class="win7-btn2" @click="goUp"><ArrowUp :size="14" /> 上级</button>
      <button class="win7-btn2" @click="mkdir">新建文件夹</button>
      <button class="win7-btn2" @click="triggerUpload">上传</button>
      <span class="file-path">{{ current }}</span>
    </div>
    <div class="file-table-wrap">
      <table class="file-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>大小</th>
            <th>修改时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in items" :key="f.path" @dblclick="enter(f)">
            <td><component :is="f.is_dir ? Folder : FileText" :size="14" /> {{ f.name }}</td>
            <td>{{ f.is_dir ? '-' : fmtSize(f.size) }}</td>
            <td>{{ fmtTime(f.modified) }}</td>
            <td>
              <button v-if="!f.is_dir" class="win7-btn2" @click.stop="downloadFile(f.path)">下载</button>
              <button class="win7-btn2" @click.stop="renameFile(f)">重命名</button>
              <button class="win7-btn2 danger" @click.stop="deleteFile(f.path)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="msg" class="file-msg">{{ msg }}</div>
    <input ref="uploadInput" type="file" style="display:none" @change="onUpload" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ArrowUp, Folder, FileText } from 'lucide-vue-next'

const items = ref([])
const current = ref('')
const msg = ref('')
const uploadInput = ref(null)
let root = ''

async function load(path) {
  msg.value = ''
  try {
    const r = await fetch('/api/file/list?path=' + encodeURIComponent(path || ''))
    const data = await r.json()
    items.value = data.items
    current.value = data.current
    if (!root) root = data.current
  } catch (e) { msg.value = '加载失败' }
}
function goUp() {
  const parts = current.value.replace(/\\$/, '').split(/[\\/]/)
  parts.pop()
  const up = parts.join('/') || ''
  load(up)
}
function enter(f) {
  if (f.is_dir) load(f.path)
}
function triggerUpload() {
  uploadInput.value.click()
}
async function onUpload(e) {
  const file = e.target.files[0]
  if (!file) return
  const fd = new FormData()
  fd.append('file', file)
  await fetch('/api/file/upload?path=' + encodeURIComponent(current.value), { method: 'POST', body: fd })
  uploadInput.value.value = ''
  load(current.value)
}
async function deleteFile(path) {
  if (!confirm('确认删除?')) return
  await fetch('/api/file/delete?path=' + encodeURIComponent(path), { method: 'DELETE' })
  load(current.value)
}
async function renameFile(f) {
  const name = prompt('新名称:', f.name)
  if (!name || name === f.name) return
  const newPath = current.value + (current.value.endsWith('/') || current.value.endsWith('\\') ? '' : '/') + name
  await fetch('/api/file/rename?old=' + encodeURIComponent(f.path) + '&new=' + encodeURIComponent(newPath), { method: 'PUT' })
  load(current.value)
}
async function mkdir() {
  const name = prompt('文件夹名称:')
  if (!name) return
  const path = current.value + (current.value.endsWith('/') || current.value.endsWith('\\') ? '' : '/') + name
  await fetch('/api/file/mkdir?path=' + encodeURIComponent(path), { method: 'POST' })
  load(current.value)
}
function downloadFile(path) {
  const a = document.createElement('a')
  a.href = '/api/file/download?path=' + encodeURIComponent(path)
  a.download = ''
  a.click()
}
function fmtSize(b) {
  if (b > 1e9) return (b/1e9).toFixed(2) + ' GB'
  if (b > 1e6) return (b/1e6).toFixed(2) + ' MB'
  if (b > 1e3) return (b/1e3).toFixed(1) + ' KB'
  return b + ' B'
}
function fmtTime(t) {
  return new Date(t * 1000).toLocaleString('zh-CN')
}

onMounted(() => load(''))
</script>

<style scoped>
.file-wrap { height: 100%; display: flex; flex-direction: column; background: #fff; }
.file-toolbar { display: flex; align-items: center; gap: 8px; padding: 10px 12px; background: #f5f5f7; border-bottom: 1px solid rgba(0,0,0,0.06); }
.file-path { font-size: 12px; color: #6e6e73; margin-left: auto; font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
.file-table-wrap { flex: 1; overflow: auto; }
.file-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.file-table th, .file-table td { text-align: left; padding: 8px 10px; border-bottom: 1px solid rgba(0,0,0,0.06); }
.file-table th { background: #f5f5f7; position: sticky; top: 0; z-index: 1; font-weight: 600; color: #1d1d1f; }
.file-table tr:hover { background: #f5f5f7; }
.file-msg { padding: 10px; color: #ff3b30; font-size: 12px; }
.win7-btn2 { border: 1px solid rgba(0,0,0,0.1); border-radius: 8px; background: #fff; color: #1d1d1f; padding: 4px 12px; font-size: 12px; cursor: pointer; margin-right: 4px; font-weight: 500; display: inline-flex; align-items: center; gap: 4px; }
.win7-btn2:hover { background: #f5f5f7; }
.win7-btn2:active { background: #ebebed; }
.win7-btn2.danger { background: #ff3b30; color: #fff; border-color: transparent; }
.win7-btn2.danger:hover { background: #e0342a; }
</style>
