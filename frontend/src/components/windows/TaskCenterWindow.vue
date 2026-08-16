<template>
  <div class="tasks-window">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <span class="title"><ListChecks :size="15" /> 任务中心</span>
      <span class="hint">刷新页面不会中断任务</span>
      <button class="btn" style="margin-left:auto;" :disabled="!selected" @click="removeSelected">
        <Trash2 :size="13" /> 删除
      </button>
      <button class="btn" :disabled="loading" @click="refreshAll">
        <RefreshCw :size="13" :class="{ spin: loading }" /> 刷新
      </button>
    </div>

    <div class="body">
      <!-- 左侧任务列表 -->
      <div class="task-list">
        <div v-if="tasks.length === 0" class="empty">暂无任务</div>
        <div
          v-for="t in tasks"
          :key="t.id"
          class="task-card"
          :class="{ active: selected?.id === t.id }"
          @click="select(t)"
        >
          <div class="task-title">
            <span class="t-name" :title="t.title">{{ t.title }}</span>
            <span class="badge" :class="t.status">{{ statusText(t) }}</span>
          </div>
          <div class="task-meta mono">
            <span v-if="t.app_name">{{ t.app_name }} · </span>{{ fmtTime(t.started_at) }}
            <span v-if="t.finished_at" class="fin">→ {{ fmtTime(t.finished_at) }}</span>
          </div>
          <div v-if="t.status === 'running'" class="running-bar"><span></span></div>
        </div>
      </div>

      <!-- 右侧日志面板 -->
      <div class="log-pane">
        <template v-if="selected">
          <div class="log-head">
            <span class="log-title">{{ selected.title }}</span>
            <span class="badge" :class="selected.status">{{ statusText(selected) }}</span>
            <span v-if="selected.status === 'running'" class="live">● 实时更新</span>
          </div>
          <div ref="logBox" class="log-box">
            <div v-for="(l, i) in logLines" :key="i" class="line" :class="lineClass(l)">
              <span v-if="l.type === 'status'" class="arrow">»</span>
              <span v-else-if="l.type === 'error'" class="arrow">✖</span>
              <span v-else-if="l.type === 'result'" class="arrow">{{ resultFailed(l) ? '✖' : '✔' }}</span>
              <span class="text">{{ lineText(l) }}</span>
            </div>
            <div v-if="selected.status === 'running'" class="line status"><span class="arrow">»</span><span class="text">等待输出...</span></div>
          </div>
        </template>
        <div v-else class="empty">选择左侧任务查看日志</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { tasksApi } from '../../api'
import { ListChecks, Trash2, RefreshCw } from 'lucide-vue-next'

const tasks = ref([])
const selected = ref(null)
const logLines = ref([])
const logBox = ref(null)
const loading = ref(false)

let timer = null

function statusText(t) {
  if (t.status === 'running') return '进行中'
  if (t.status === 'error') return '失败'
  return '完成'
}

function fmtTime(t) {
  if (!t) return ''
  return String(t).replace('T', ' ').replace(/\.\d+.*$/, '').slice(0, 19)
}

// 解析 result 日志行（JSON 字符串），失败时返回 null
function parseResult(l) {
  if (!l || l.type !== 'result' || typeof l.text !== 'string') return null
  const m = l.text.match(/\{.*\}/)
  if (!m) return null
  try { return JSON.parse(m[0]) } catch (e) { return null }
}

// result 行是否表示失败（ok !== true）
function resultFailed(l) {
  const d = parseResult(l)
  return d ? d.ok !== true : false
}

// 日志行显示文本：result 行解析出关键摘要，其余原样
function lineText(l) {
  const d = parseResult(l)
  if (d) {
    const parts = []
    if (d.app_name) parts.push(`应用 ${d.app_name}`)
    if (d.container_name) parts.push(`容器 ${d.container_name}`)
    if (d.version) parts.push(`版本 ${d.version}`)
    if (d.port) parts.push(`端口 ${d.port}`)
    const head = d.ok === true ? '安装成功' : '安装失败'
    return `${head}（${parts.join('，') || '见上方日志'}）`
  }
  return l.text || ''
}

// result 行若失败则归入 error 配色
function lineClass(l) {
  if (l.type === 'result' && resultFailed(l)) return 'error'
  return l.type || 'log'
}

async function refreshList() {
  try {
    const r = await tasksApi.list()
    tasks.value = r.tasks || []
    // 保持选中的任务对象同步最新状态
    if (selected.value) {
      const cur = tasks.value.find(t => t.id === selected.value.id)
      selected.value = cur || null
    }
  } catch (e) { /* 忽略轮询错误 */ }
}

async function refreshLog() {
  if (!selected.value) return
  try {
    const wasAtBottom = logBox.value &&
      (logBox.value.scrollHeight - logBox.value.scrollTop - logBox.value.clientHeight) < 40
    const r = await tasksApi.log(selected.value.id)
    logLines.value = r.lines || []
    // 任务运行中强制跟随底部，否则仅在原位于底部时跟随
    if (selected.value.status === 'running' || wasAtBottom) {
      nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })
    }
  } catch (e) { /* 忽略轮询错误 */ }
}

async function refreshAll() {
  loading.value = true
  await refreshList()
  await refreshLog()
  loading.value = false
}

function select(t) {
  selected.value = t
  logLines.value = []
  refreshLog()
}

async function removeSelected() {
  if (!selected.value) return
  if (!confirm(`确认删除任务「${selected.value.title}」？日志将一并清除。`)) return
  try {
    await tasksApi.remove(selected.value.id)
    selected.value = null
    await refreshList()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

onMounted(async () => {
  await refreshAll()
  // 自动选中最近的任务（优先进行中）
  if (!selected.value && tasks.value.length) {
    const running = tasks.value.find(t => t.status === 'running') || tasks.value[0]
    select(running)
  }
  timer = setInterval(refreshAll, 2000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.tasks-window { display: flex; flex-direction: column; height: 100%; overflow: hidden; }

.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
.title { font-weight: 700; font-size: 13.5px; display: inline-flex; align-items: center; gap: 6px; }
.hint { color: #9ca3af; font-size: 11.5px; }

.body { flex: 1; display: flex; min-height: 0; }
.task-list { width: 240px; flex-shrink: 0; border-right: 1px solid #e5e7eb; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
.log-pane { flex: 1; display: flex; flex-direction: column; min-width: 0; padding: 8px 10px; gap: 8px; }

.empty { flex: 1; display: flex; align-items: center; justify-content: center; color: #9ca3af; font-size: 12.5px; }

.task-card { border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px 10px; cursor: pointer; transition: border-color .15s, box-shadow .15s; position: relative; min-width: 0; overflow: hidden; }
.task-card:hover { border-color: #c7d2fe; }
.task-card.active { border-color: #6366f1; box-shadow: 0 0 0 1px rgba(99,102,241,.25); background: #f5f6ff; }
.task-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.t-name { font-weight: 600; font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 1; }
.task-meta { font-size: 10.5px; color: #9ca3af; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.task-meta .fin { color: #6b7280; }
.running-bar { position: absolute; left: 0; right: 0; bottom: 0; height: 2px; border-radius: 0 0 8px 8px; overflow: hidden; background: #eef2ff; }
.running-bar span { display: block; height: 100%; width: 40%; background: #6366f1; animation: slide 1.2s ease-in-out infinite; }
@keyframes slide { 0% { margin-left: -40%; } 100% { margin-left: 100%; } }

.badge { font-size: 10.5px; padding: 1px 8px; border-radius: 999px; flex-shrink: 0; }
.badge.running { background: #fffbeb; color: #b45309; }
.badge.error { background: #fef2f2; color: #b91c1c; }
.badge.completed { background: #ecfdf5; color: #047857; }

.log-head { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.log-title { font-weight: 700; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.live { font-size: 11px; color: #2563eb; margin-left: auto; animation: blink 1.2s infinite; }
@keyframes blink { 50% { opacity: .4; } }

.log-box { flex: 1; min-height: 0; overflow-y: auto; background: #0f172a; border-radius: 8px; padding: 10px; font-family: Consolas, monospace; font-size: 12px; line-height: 1.55; user-select: text; cursor: text; }
.line { display: flex; gap: 6px; white-space: pre-wrap; word-break: break-all; }
.line.log .text { color: #cbd5e1; }
.line.status .text { color: #60a5fa; font-weight: 600; }
.line.error .text { color: #f87171; font-weight: 600; }
.line.result .text { color: #4ade80; font-weight: 600; }
.arrow { flex-shrink: 0; color: #475569; }

.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
