<!--
  文件管理器窗口（后端 /api/files 模块）
  作用：浏览/上传/下载/编辑/压缩/解压/改权限/复制/重命名/删除服务器文件，支持拖拽上传与右键菜单。
  后端模块：/api/files（list/read/write/delete/mkdir/rename/chmod/copy/compress/extract/upload/download）。
  关键状态：items（当前目录条目）、parent（上级目录）、path（当前路径）、uploadingFiles（批量上传进度）、
            contextMenu（右键菜单）、selected（多选选中集）、共享剪贴板（store/fileClipboard，跨窗口复制/粘贴）。
  打开方式：桌面「文件」卡片；双击图片/视频走 MediaWindow，文本文件走 EditorWindow，右键可开终端。
  快捷键：Ctrl+C 复制选中 / Ctrl+V 粘贴到当前目录 / Delete 删除选中（类 Windows）。
  下载必须带 Bearer 头（window.open 无法携带），故用 fetch+blob 触发下载（见 download）。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;" @click="onRootClick"
    @dragover.prevent="onDragOver"
    @dragenter.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
    @contextmenu.prevent="onBlankContextMenu">
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
      <!-- 剪贴板状态提示：已复制内容后可在任意文件管理窗口「粘贴」（跨窗口共享） -->
      <span v-if="fileClipboard.items.length" class="clip-chip">
        <Clipboard :size="13" /> {{ $t('files.clipboardHas', { n: fileClipboard.items.length }) }}
        <span class="clip-clear" @click="clearClipboard">&times;</span>
      </span>
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
          <tr v-for="(it, idx) in items" :key="it.path"
            :class="{ selected: isSelected(it.path) }"
            @click.stop="selectItem(it, idx, $event)"
            @dblclick="openItem(it)"
            @contextmenu.prevent.stop="onContextMenu($event, it)">
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
    <!-- 右键菜单：行条目时展示条目操作；空白处展示目录操作 -->
    <Teleport to="body">
      <div v-if="contextMenu.show" class="context-menu" :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }" @click.stop>
        <template v-if="contextMenu.item">
          <div class="menu-item" @click="menuEdit">{{ $t('files.openEdit') }}</div>
          <div class="menu-item" @click="menuRename">{{ $t('files.rename') }}</div>
          <div class="menu-item" @click="menuDelete">{{ $t('files.delete') }}</div>
          <div class="menu-item" @click="menuCopy">{{ $t('files.copy') }}</div>
          <!-- 右键目录条目时提供「粘贴到此处」（Windows 行为） -->
          <div class="menu-item" v-if="fileClipboard.items.length && contextMenu.item.is_dir" @click="menuPasteIntoFolder">{{ $t('files.pasteInto') }}</div>
          <div class="menu-item" @click="menuCompress">{{ $t('files.compress') }}</div>
          <div class="menu-item" @click="menuExtract" v-if="isArchive(contextMenu.item?.name)">{{ $t('files.extract') }}</div>
          <div class="menu-item" @click="menuChmod">{{ $t('files.permissions') }}</div>
          <div class="menu-item" @click="menuDownload">{{ $t('files.download') }}</div>
          <div class="menu-item" @click="menuOpenTerminal">{{ $t('files.openTerminal') }}</div>
        </template>
        <template v-else>
          <div class="menu-item" @click="menuPaste" :class="{ disabled: !fileClipboard.items.length }">{{ $t('files.paste') }}</div>
          <div class="menu-item" @click="createFolder">{{ $t('files.newFolder') }}</div>
          <div class="menu-item" @click="createFile">{{ $t('files.newFile') }}</div>
          <div class="menu-item" @click="menuSelectAll">{{ $t('files.selectAll') }}</div>
          <div class="menu-item" @click="refresh">{{ $t('files.refresh') }}</div>
          <div class="menu-item" @click="menuOpenTerminal">{{ $t('files.openTerminal') }}</div>
        </template>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
// 响应式状态与生命周期钩子
import { ref, onMounted, onUnmounted } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 文件管理 API 与字节格式化工具
import { filesApi, formatBytes } from '../../api'
// 统一的后端错误消息提取（读取 detail 或 i18n 兜底）
import { getApiErrorMessage } from '../../utils/apiErrors'
// 登录态 store：下载接口需带 Bearer token
import { auth } from '../../store/auth'
// 共享剪贴板 store：复制/粘贴跨文件管理窗口生效（类 Windows）
import { fileClipboard, setClipboard, clearClipboard } from '../../store/fileClipboard'
// 图标（返回上级 / 目录 / 文本 / 图片 / 视频 / 上传 / 剪贴板）
import { ArrowUp, Folder, FileText, Image as ImageIcon, Film, Upload, Clipboard } from 'lucide-vue-next'

const { t } = useI18n()
const items = ref([])            // 当前目录条目列表
const parent = ref(null)         // 上级目录路径（null 表示已到根）
const path = ref('')             // 当前路径
const pathInput = ref('')        // 地址栏输入值
const newMenuOpen = ref(false)   // 「新建」下拉菜单显隐
const contextMenu = ref({ show: false, x: 0, y: 0, item: null })   // 右键菜单
// 选中态与剪贴板（复刻 Windows：单击选中、Ctrl/Shift 多选、Ctrl+C 复制、Ctrl+V 粘贴、Delete 删除）
const selected = ref([])         // 当前选中条目路径数组（支持多选）
const anchorIdx = ref(-1)        // Shift 范围选择锚点（条目在 items 中的下标）
// 拖拽上传状态
const dragOver = ref(false)      // 是否显示拖拽上传遮罩
const uploadingFiles = ref([])   // 待批量上传的文件列表（驱动进度条）
const uploadIdx = ref(0)         // 当前正在上传的文件序号（进度条用）

// 子窗口事件：打开终端 / 编辑器 / 媒体预览（由父窗口接收创建）
const emit = defineEmits(['openTerminal', 'openEditor', 'openMedia'])
// 父窗口传入：初始目录路径
const props = defineProps({ path: String })

// --- 动作：加载指定目录的文件列表 ---
async function load(p) {
  try {
    const r = await filesApi.list(p)   // 调用 /api/files/list
    items.value = r.items
    parent.value = r.parent
    path.value = r.path
    pathInput.value = r.path
    // 目录切换后旧选中项已不在列表中，清空选中态与范围锚点
    selected.value = []
    anchorIdx.value = -1
  } catch (e) {
    alert(t('files.accessFailed', { error: getApiErrorMessage(e, t) }))
  }
}

// --- 动作：刷新 / 跳转 / 返回上级（三个导航入口） ---
function refresh() { load(path.value) }
function go() { load(pathInput.value || '') }
function goUp() { if (parent.value) load(parent.value) }

// --- 动作：单击选中条目 —— 支持 Windows 式多选 ---
// - 普通点击：单选当前项并更新 Shift 范围锚点
// - Ctrl/Meta+点击：切换该项的选中状态（多选累计）
// - Shift+点击：从锚点到当前项之间的连续范围全部选中
function selectItem(it, idx, e) {
  if (e && (e.ctrlKey || e.metaKey)) {
    toggleSelected(it.path)
    return
  }
  if (e && e.shiftKey && anchorIdx.value >= 0) {
    const from = Math.min(anchorIdx.value, idx)
    const to = Math.max(anchorIdx.value, idx)
    selected.value = items.value.slice(from, to + 1).map(x => x.path)
    return
  }
  selected.value = [it.path]
  anchorIdx.value = idx
}

// 切换单个条目是否选中（Ctrl 多选用）
function toggleSelected(p) {
  const i = selected.value.indexOf(p)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(p)
}

// 判断条目是否在选中集内（行高亮用）
function isSelected(p) {
  return selected.value.includes(p)
}

// --- 动作：Ctrl+A 全选当前目录 ---
function selectAll() {
  selected.value = items.value.map(x => x.path)
  anchorIdx.value = selected.value.length ? 0 : -1
}

// 按扩展名判断是否图片（用于选择图标与媒体预览）
function isImage(name) {
  const ext = name.split('.').pop().toLowerCase()
  return ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg'].includes(ext)
}

// 按扩展名判断是否视频
function isVideo(name) {
  const ext = name.split('.').pop().toLowerCase()
  return ['mp4', 'webm', 'mov', 'avi', 'mkv'].includes(ext)
}

// --- 动作：双击条目按类型打开（目录进入、图片/视频媒体预览、小文本文件走编辑器） ---
function openItem(it) {
  if (it.is_dir) load(it.path)
  else if (isImage(it.name)) emit('openMedia', { path: it.path, name: it.name, type: 'image' })
  else if (isVideo(it.name)) emit('openMedia', { path: it.path, name: it.name, type: 'video' })
  else if (it.size < 2 * 1024 * 1024) openEditorWindow(it)   // 超过 2MB 不自动编辑，避免一次拉取过大内容
}

// --- 动作：读取文本内容并打开编辑窗口 ---
async function openEditorWindow(it) {
  try {
    const r = await filesApi.read(it.path)   // 调用 /api/files/read
    emit('openEditor', { path: r.path, content: r.content })
  } catch (e) {
    alert(t('files.readFailed', { error: getApiErrorMessage(e, t) }))
  }
}

// --- 动作：删除文件/文件夹（先确认；回收站启用时移入回收站） ---
// 支持批量：传入的路径数组会逐个删除，单独/批量共用同一流程
async function deletePaths(paths) {
  if (!paths || !paths.length) return
  const msg = paths.length === 1
    ? t('files.confirmDelete', { name: baseName(paths[0]) })
    : t('files.confirmDeleteMulti', { n: paths.length })
  if (!confirm(msg)) return
  const errors = []
  for (const p of paths) {
    try {
      await filesApi.remove(p)   // 调用 /api/files/delete（回收站启用时移入回收站）
    } catch (e) {
      errors.push(`${baseName(p)}: ${getApiErrorMessage(e, t)}`)
    }
  }
  // 清理已删除的选中项，保持选中集只是「仍然存在的条目」
  selected.value = selected.value.filter(p => !paths.includes(p))
  refresh()
  if (errors.length) alert(t('files.deleteFailed', { error: errors.join('\n') }))
}

// --- 动作：重命名文件（替换路径最后一段，保留原分隔符） ---
async function renameItem(it) {
  const name = prompt(t('files.renamePrompt'), it.name)
  if (!name || name === it.name) return
  const dst = it.path.replace(/[\\/][^\\/]+$/, m => m[0] + name)
  try {
    await filesApi.rename(it.path, dst)   // 调用 /api/files/rename
    refresh()
  } catch (e) {
    alert(t('files.renameFailed', { error: getApiErrorMessage(e, t) }))
  }
}

// --- 动作：新建文件夹（沿用当前路径的分隔符风格拼出新路径） ---
async function createFolder() {
  newMenuOpen.value = false
  closeMenus()
  const name = prompt(t('files.newFolderPrompt'))
  if (!name) return
  const sep = path.value.includes('\\') ? '\\' : '/'
  const newPath = path.value.replace(/[\\/]$/, '') + sep + name
  try {
    await filesApi.mkdir(newPath)   // 调用 /api/files/mkdir
    refresh()
  } catch (e) {
    alert(t('files.createFailed', { error: getApiErrorMessage(e, t) }))
  }
}

// --- 动作：新建空文件 ---
async function createFile() {
  newMenuOpen.value = false
  closeMenus()
  const name = prompt(t('files.newFilePrompt'))
  if (!name) return
  const sep = path.value.includes('\\') ? '\\' : '/'
  const newPath = path.value.replace(/[\\/]$/, '') + sep + name
  try {
    await filesApi.write(newPath, '')   // 调用 /api/files/write 写空内容创建
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

// --- 动作：关闭所有弹出菜单（点击窗口空白处/选择菜单项后调用） ---
function closeMenus() {
  newMenuOpen.value = false
  contextMenu.value.show = false
}

// 记录右键位置与目标条目，弹出上下文菜单（行条目）。
// Windows 行为对齐：右键一个「未选中」的文件会先只选中它；右键已选中的文件则保持多选。
function onContextMenu(e, it) {
  if (!selected.value.includes(it.path)) {
    selected.value = [it.path]
    anchorIdx.value = items.value.findIndex(x => x.path === it.path)
  }
  contextMenu.value = { show: true, x: e.clientX, y: e.clientY, item: it }
}

// 右键/键盘删除等批量操作的对象：右键条目在选中集内则作用于整个选中集，否则仅该条目
function contextSelection() {
  const it = contextMenu.value.item
  if (it && selected.value.includes(it.path)) return selected.value.slice()
  return it ? [it.path] : []
}

// 空白处右键：item 置 null 以渲染目录级菜单（粘贴 / 新建 / 刷新 / 终端）
function onBlankContextMenu(e) {
  // 仅当点击发生在表格区域外的空白处才弹目录级菜单；防与行菜单冲突
  if (e.target.closest('tr')) return
  contextMenu.value = { show: true, x: e.clientX, y: e.clientY, item: null }
}

// ---------- 剪贴板（复制 / 粘贴，复刻 Windows，跨文件管理窗口共享） ----------
// 把当前选中集写入共享剪贴板（当前仅实现「复制」语义）。
// 复制内容存于全局 store，切换到其它文件管理窗口后粘贴同样生效。
function menuCopy() {
  const paths = contextSelection()
  closeMenus()
  if (!paths.length) return
  setClipboard(paths, 'copy')
}

// Ctrl+C：复制当前选中集（支持多选一次性复制）
function onCopySelected() {
  if (selected.value.length) setClipboard(selected.value.slice(), 'copy')
}

// 计算粘贴目标路径：拼接当前目录与源文件名（沿用当前目录分隔符风格）
function joinDir(dir, name) {
  const sep = String(dir || '').includes('\\') ? '\\' : '/'
  return String(dir || '').replace(/[\\/]$/, '') + sep + encodeName(name)
}
function encodeName(name) {
  return String(name || '').replace(/[\\/]/g, '')   // 文件名内不允许出现路径分隔符
}

// 取出路径最后一段作为文件名（兼容 / 与 \）
function baseName(p) {
  const parts = String(p || '').replace(/\\/g, '/').split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : String(p || '')
}

// --- 动作：粘贴剪贴板内容到当前目录（后端自动处理同名冲突，支持多文件） ---
async function pasteTo(dirPath) {
  const srcs = fileClipboard.items || []
  if (!srcs.length) return
  const errors = []
  for (const src of srcs) {
    try {
      await filesApi.copy(src, joinDir(dirPath, baseName(src)))
    } catch (e) {
      errors.push(`${baseName(src)}: ${getApiErrorMessage(e, t)}`)
    }
  }
  refresh()
  if (errors.length) alert(t('files.pasteFailed', { error: errors.join('\n') }))
}

function menuPaste() {
  closeMenus()
  if (fileClipboard.items.length) pasteTo(path.value)
}

// 右键目录条目时：粘贴到该目录内（Windows 行为）
function menuPasteIntoFolder() {
  const it = contextMenu.value.item
  closeMenus()
  if (it && it.is_dir) pasteTo(it.path)
}

// 空白区右键菜单「全选」
function menuSelectAll() {
  closeMenus()
  selectAll()
}

// ---------- 右键菜单动作：取出目标条目后分发到对应操作 ----------
function menuRename() {
  const it = contextMenu.value.item
  closeMenus()
  if (it) renameItem(it)
}

function menuDelete() {
  const paths = contextSelection()
  closeMenus()
  if (paths.length) deletePaths(paths)
}

// 在当前目录打开终端
function menuOpenTerminal() {
  closeMenus()
  if (path.value) emit('openTerminal', path.value)
}

// 右键「打开编辑」：目录不可编辑，图片/视频走媒体预览，其余走文本编辑器
function menuEdit() {
  const it = contextMenu.value.item
  closeMenus()
  if (!it || it.is_dir) return
  if (isImage(it.name)) emit('openMedia', { path: it.path, name: it.name, type: 'image' })
  else if (isVideo(it.name)) emit('openMedia', { path: it.path, name: it.name, type: 'video' })
  else openEditorWindow(it)
}

// 按扩展名判断是否压缩包（决定右键菜单是否显示「解压」）
function isArchive(name) {
  const ext = name.split('.').pop().toLowerCase()
  return ['zip', 'tar', 'gz', 'tgz', 'bz2', 'xz', '7z', 'rar'].includes(ext) || name.endsWith('.tar.gz')
}

function menuDownload() {
  const it = contextMenu.value.item
  closeMenus()
  if (it && !it.is_dir) download(it)
}

// --- 动作：压缩为 zip / tar.gz（zip 走 .zip，其余格式统一 .tar.gz） ---
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

// --- 动作：解压到指定目录（默认当前目录） ---
async function menuExtract() {
  const it = contextMenu.value.item
  closeMenus()
  if (!it) return
  const dest = prompt(t('files.extractToPrompt'), path.value)
  if (!dest) return
  try { await filesApi.extract(it.path, dest); refresh() } catch (e) { alert(t('files.extractFailed', { error: getApiErrorMessage(e, t) })) }
}

// --- 动作：修改权限（八进制 mode，如 755） ---
async function menuChmod() {
  const it = contextMenu.value.item
  closeMenus()
  if (!it) return
  const modeStr = prompt(t('files.permissionPrompt'), '755')
  if (!modeStr) return
  const mode = parseInt(modeStr, 8)   // 权限值按八进制解析（0755 语义）
  if (isNaN(mode)) { alert(t('files.permissionInvalid')); return }
  try { await filesApi.chmod(it.path, mode); refresh() } catch (e) { alert(t('files.permissionFailed', { error: getApiErrorMessage(e, t) })) }
}

// --- 动作：工具栏选择文件上传到当前目录 ---
async function onUpload(e) {
  const file = e.target.files[0]
  if (!file) return
  try {
    const fd = new FormData()
    fd.append('path', path.value)
    fd.append('file', file)
    await filesApi.upload(fd)   // 调用 /api/files/upload
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

// 递归遍历拖入内容：支持拖拽「文件夹」时按其目录结构上传
// - 优先 DataTransferItem.webkitGetAsEntry（Chrome/Edge/Firefox）可还原相对路径；
// - 不支持时退回 dataTransfer.files（仅能拿到文件列表，文件夹会被浏览器跳过）。
function getEntries(dataTransfer) {
  return new Promise((resolve) => {
    const items = dataTransfer && dataTransfer.items ? Array.from(dataTransfer.items) : []
    if (items.length && items[0].webkitGetAsEntry) {
      const results = []
      let pending = 0
      let done = false
      const finish = () => {
        if (done) return
        done = true
        resolve(results)
      }
      const walk = (entry, relDir) => {
        pending++
        if (entry.isFile) {
          entry.file((file) => {
            results.push({ file, relPath: relDir ? `${relDir}/${file.name}` : file.name })
            pending--
            if (!pending) finish()
          }, () => { pending--; if (!pending) finish() })
        } else if (entry.isDirectory) {
          const reader = entry.createReader()
          // 循环读取目录内全部条目（readEntries 分批返回）
          const readBatch = () => {
            reader.readEntries((batch) => {
              if (!batch.length) {
                pending--
                if (!pending) finish()
                return
              }
              batch.forEach((e) => walk(e, relDir ? `${relDir}/${entry.name}` : entry.name))
              readBatch()
            }, () => { pending--; if (!pending) finish() })
          }
          readBatch()
        } else {
          pending--
          if (!pending) finish()
        }
      }
      items.forEach((it) => {
        const entry = it.webkitGetAsEntry()
        if (entry) walk(entry, '')
      })
      // items 中没有条目时（极端情况）直接结束
      if (!items.some((it) => it.webkitGetAsEntry())) setTimeout(finish, 0)
    } else {
      // 降级：仅普通文件列表
      const files = Array.from(dataTransfer ? dataTransfer.files : [])
      resolve(files.map((f) => ({ file: f, relPath: f.name || '' })))
    }
  })
}

// --- 动作：把拖入的文件/文件夹逐个上传到当前目录并更新进度条 ---
async function onDrop(e) {
  dragOver.value = false
  const uploads = await getEntries(e.dataTransfer)
  if (uploads.length === 0) return
  uploadingFiles.value = uploads
  uploadIdx.value = 0
  for (let i = 0; i < uploads.length; i++) {
    uploadIdx.value = i
    try {
      const fd = new FormData()
      fd.append('path', path.value)
      fd.append('file', uploads[i].file)
      fd.append('relpath', uploads[i].relPath || '')
      await filesApi.upload(fd)
    } catch (err) {
      alert(t('files.uploadFileFailed', { name: uploads[i].file.name, error: err.response?.data?.detail || err.message }))
    }
  }
  uploadingFiles.value = []
  uploadIdx.value = 0
  refresh()
}

// ---------- 键盘快捷键（复刻 Windows 常用操作） ----------
function onKeyDown(e) {
  const tag = (e.target && e.target.tagName) || ''
  // 输入框中不拦截快捷键（用户可能正在编辑路径/文件名）
  if (['INPUT', 'TEXTAREA'].includes(tag)) return
  const mod = e.ctrlKey || e.metaKey
  const key = String(e.key).toLowerCase()
  if (mod && key === 'c') {
    e.preventDefault()
    onCopySelected()
  } else if (mod && key === 'v') {
    e.preventDefault()
    if (fileClipboard.items.length) pasteTo(path.value)
  } else if (mod && key === 'a') {
    // Ctrl+A 全选当前目录
    e.preventDefault()
    selectAll()
  } else if (e.key === 'Delete' || e.key === 'Backspace') {
    if (selected.value.length) {
      e.preventDefault()
      deletePaths(selected.value.slice())
    }
  }
}

// 窗口内任意点击：关闭菜单；点击非表格空白/非工具栏区域时清空选中（Windows 习惯）
function onRootClick(e) {
  closeMenus()
  if (e.target.closest('tr') || e.target.closest('.toolbar')) return
  selected.value = []
  anchorIdx.value = -1
}

onMounted(async () => {
  await load(props.path || '')   // 挂载后进入父窗口指定的初始目录
  document.addEventListener('keydown', onKeyDown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeyDown)
})
</script>

<style scoped>
.menu-item { padding: 8px 12px; font-size: 12px; cursor: pointer; }
.menu-item:hover { background: #f5f5f7; }
.menu-item.disabled { opacity: 0.45; cursor: not-allowed; }
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
/* 选中行高亮 */
tr.selected td { background: rgba(10, 132, 255, 0.10); }
/* 剪贴板状态提示 */
.clip-chip {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; color: #0a84ff; background: rgba(10,132,255,0.08);
  border: 1px solid rgba(10,132,255,0.22); border-radius: 999px;
  padding: 2px 8px;
}
.clip-clear { cursor: pointer; font-weight: 700; padding: 0 2px; }
.clip-clear:hover { color: #c0392b; }
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