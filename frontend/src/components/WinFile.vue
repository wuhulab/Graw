<!--
  WinFile.vue — 文件管理器窗口
  作用：浏览/操作服务器文件系统。支持上级目录跳转、新建文件夹、上传/下载、
        重命名、删除。所有操作经 /api/file/* 落到后端（宿主机经 hostfs 适配层读写）。
  数据：当前目录列表由 /api/file/list 返回（items / current 路径），根目录仅记一次。
  打开方式：桌面快捷方式或开始菜单的「文件管理」。
-->
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
import { ref, onMounted } from 'vue'                                     // Vue 响应式与生命周期
import { ArrowUp, Folder, FileText } from 'lucide-vue-next'               // 文件管理图标

const items = ref([])             // 当前目录下的文件/文件夹列表
const current = ref('')           // 当前所在路径（界面展示）
const msg = ref('')               // 错误/状态提示
const uploadInput = ref(null)     // 隐藏的文件选择 <input> 引用
let root = ''                     // 首次进入记录的根目录，作回退基准

// 列出某路径下的内容
async function load(path) {
  msg.value = ''                       // 每次加载先清空旧提示
  try {
    const r = await fetch('/api/file/list?path=' + encodeURIComponent(path || ''))
    const data = await r.json()
    items.value = data.items
    current.value = data.current
    if (!root) root = data.current     // 首次加载时记下根目录
  } catch (e) { msg.value = '加载失败' }
}
// 返回上级目录
function goUp() {
  // 同时按 Windows(\) 与 Unix(/) 分隔符拆分，兼容两种路径
  const parts = current.value.replace(/\\$/, '').split(/[\\/]/)
  parts.pop()                          // 去掉最后一段即回到上级
  const up = parts.join('/') || ''
  load(up)
}
function enter(f) {
  if (f.is_dir) load(f.path)          // 仅文件夹可进入
}
function triggerUpload() {
  uploadInput.value.click()           // 触发隐藏 input 打开系统选择框
}
async function onUpload(e) {
  const file = e.target.files[0]
  if (!file) return
  const fd = new FormData()
  fd.append('file', file)
  await fetch('/api/file/upload?path=' + encodeURIComponent(current.value), { method: 'POST', body: fd })
  uploadInput.value.value = ''        // 清空选择，便于重复上传同名文件
  load(current.value)
}
async function deleteFile(path) {
  if (!confirm('确认删除?')) return    // 删除前二次确认
  await fetch('/api/file/delete?path=' + encodeURIComponent(path), { method: 'DELETE' })
  load(current.value)
}
async function renameFile(f) {
  const name = prompt('新名称:', f.name)
  if (!name || name === f.name) return
  // 拼接新路径，兼容当前路径已带或不带末尾分隔符
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
  a.click()                           // 借锚点触发浏览器下载
}
function fmtSize(b) {
  if (b > 1e9) return (b/1e9).toFixed(2) + ' GB'   // 1e9 字节 ≈ 1 GB
  if (b > 1e6) return (b/1e6).toFixed(2) + ' MB'   // 1e6 ≈ 1 MB
  if (b > 1e3) return (b/1e3).toFixed(1) + ' KB'   // 1e3 ≈ 1 KB
  return b + ' B'
}
function fmtTime(t) {
  return new Date(t * 1000).toLocaleString('zh-CN')  // 后端给的是秒级时间戳，×1000 转毫秒
}

onMounted(() => load(''))           // 打开窗口即载入根目录
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
