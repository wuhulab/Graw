<template>
  <div class="glass-card info-card">
    <div class="info-header">
      <div class="info-tabs">
        <span :class="{ active: tab === 'sys' }" @click="tab = 'sys'">系统信息</span>
        <span :class="{ active: tab === 'note' }" @click="tab = 'note'">备忘录</span>
      </div>
    </div>
    <div class="info-body">
      <template v-if="tab === 'sys'">
        <div class="sys-row"><b>主机名</b><span>{{ metrics.hostname }}</span></div>
        <div class="sys-row"><b>平台</b><span>{{ metrics.platform }}</span></div>
        <div class="sys-row"><b>运行时间</b><span>{{ fmtUptime(metrics.uptime) }}</span></div>
        <div class="sys-row"><b>内存使用</b><span>{{ fmtBytes(metrics.memoryUsed) }} / {{ fmtBytes(metrics.memoryTotal) }}</span></div>
        <div class="sys-row"><b>磁盘使用</b><span>{{ fmtBytes(metrics.diskUsed) }} / {{ fmtBytes(metrics.diskTotal) }}</span></div>
      </template>
      <template v-else>
        <div class="note-wrap">
          <div v-for="n in notes" :key="n.id" class="note-item" @click="editNote(n)">
            <div class="note-title">{{ n.title }}</div>
            <div class="note-content">{{ n.content }}</div>
          </div>
          <div v-if="notes.length === 0" class="note-empty">暂无备忘录</div>
        </div>
        <div class="note-actions">
          <button class="win7-btn2" @click="addNote">+ 新建</button>
        </div>
        <div v-if="editing" class="note-editor">
          <input v-model="editTitle" placeholder="标题" />
          <textarea v-model="editContent" rows="3" placeholder="内容"></textarea>
          <div class="editor-btns">
            <button class="win7-btn2 primary" @click="saveNote">保存</button>
            <button class="win7-btn2" @click="cancelEdit">取消</button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const props = defineProps({ metrics: Object })
const tab = ref('sys')
const notes = ref([])
const editing = ref(false)
const editId = ref(null)
const editTitle = ref('')
const editContent = ref('')

function fmtUptime(s) {
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${d}天 ${h}小时 ${m}分`
}
function fmtBytes(b) {
  if (!b) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (b >= 1024 && i < units.length - 1) { b /= 1024; i++ }
  return b.toFixed(2) + ' ' + units[i]
}

async function loadNotes() {
  try {
    const r = await fetch('/api/note/list')
    notes.value = await r.json()
  } catch (e) {}
}
function addNote() {
  editing.value = true
  editId.value = null
  editTitle.value = ''
  editContent.value = ''
}
function editNote(n) {
  editing.value = true
  editId.value = n.id
  editTitle.value = n.title
  editContent.value = n.content
}
function cancelEdit() {
  editing.value = false
}
async function saveNote() {
  try {
    if (editId.value) {
      await fetch(`/api/note/${editId.value}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: editTitle.value, content: editContent.value })
      })
    } else {
      await fetch('/api/note/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: editTitle.value, content: editContent.value })
      })
    }
    editing.value = false
    await loadNotes()
  } catch (e) {}
}

onMounted(() => {
  loadNotes()
})
</script>

<style scoped>
.info-card {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  color: #1d1d1f;
}
.info-header {
  margin-bottom: 10px;
}
.info-tabs {
  display: inline-flex;
  gap: 2px;
  background: rgba(0, 0, 0, 0.05);
  padding: 2px;
  border-radius: 8px;
}
.info-tabs span {
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 6px;
  cursor: pointer;
  color: #1d1d1f;
  font-weight: 500;
}
.info-tabs span.active {
  background: #ffffff;
  color: #1d1d1f;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
}
.info-body {
  flex: 1;
  overflow: auto;
  font-size: 12px;
  color: #1d1d1f;
}
.sys-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}
.sys-row b {
  font-weight: 500;
  color: #6e6e73;
}
.sys-row span {
  color: #1d1d1f;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 11px;
}
.note-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.note-item {
  background: rgba(0, 0, 0, 0.04);
  border-radius: 8px;
  padding: 8px 10px;
  cursor: pointer;
}
.note-item:hover { background: rgba(0, 0, 0, 0.07); }
.note-title {
  font-weight: 600;
  margin-bottom: 2px;
  font-size: 12px;
}
.note-content {
  font-size: 11px;
  color: #6e6e73;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.note-empty {
  color: #8e8e93;
  padding: 10px 0;
  text-align: center;
}
.note-actions {
  margin-top: 8px;
}
.note-editor {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.note-editor input, .note-editor textarea {
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 6px;
  padding: 6px 10px;
  background: #fff;
  color: #1d1d1f;
  font-family: inherit;
  font-size: 12px;
  outline: none;
}
.note-editor input:focus, .note-editor textarea:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10, 132, 255, 0.2);
}
.note-editor input::placeholder, .note-editor textarea::placeholder { color: #8e8e93; }
.editor-btns {
  display: flex;
  gap: 6px;
}
.win7-btn2 {
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 8px;
  background: #fff;
  color: #1d1d1f;
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}
.win7-btn2:hover { background: #f5f5f7; }
.win7-btn2:active { background: #ebebed; }
.win7-btn2.primary {
  background: #0a84ff;
  color: #fff;
  border-color: transparent;
}
.win7-btn2.primary:hover { background: #006edc; }
</style>
