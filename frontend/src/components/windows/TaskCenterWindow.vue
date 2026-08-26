<!--
  任务中心窗口（Task Center）

  这个窗口做什么：
    面板「任务中心」应用。应用商店发起的安装 / 卸载任务在这里集中展示
    实时进度：左侧是任务卡片列表（进行中 / 成功 / 失败），右侧是当前选中
    任务的实时日志。日志每 2 秒轮询刷新，运行中的任务会自动滚动跟随底部。
    result 类型的日志行会被解析成「安装成功 / 失败」摘要（含应用名、
    容器名、版本、端口），其余日志原样显示。

  用到的后端模块：
    /api/tasks/*（管理员权限）——list 任务列表、{id}/log 任务日志。
    任务本身由后端异步执行，本窗口只负责查看进度与删除历史记录。

  关键状态：
    tasks      任务列表，左侧卡片数据源
    selected   当前选中的任务（右侧日志面板跟随它）
    logLines   选中任务的日志行
    timer      2 秒一次的轮询定时器（退出窗口时清理）
    confirm    删除任务的二次确认（需输入面板密码）

  怎么被打开：
    桌面「任务中心」应用，或「计划任务 / 任务中心」聚合窗口（TasksWindow）
    的「任务中心」页签内嵌。
-->
<template>
  <div class="tasks-window">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <span class="title"><ListChecks :size="15" /> {{ $t('taskcenter.title') }}</span>
      <span class="hint">{{ $t('taskcenter.noRefreshHint') }}</span>
      <button class="btn" style="margin-left:auto;" :disabled="!selected" @click="removeSelected">
        <Trash2 :size="13" /> {{ $t('taskcenter.delete') }}
      </button>
      <button class="btn" :disabled="loading" @click="refreshAll">
        <RefreshCw :size="13" :class="{ spin: loading }" /> {{ $t('taskcenter.refresh') }}
      </button>
    </div>

    <div class="body">
      <!-- 左侧任务列表 -->
      <div class="task-list">
        <div v-if="tasks.length === 0" class="empty">{{ $t('taskcenter.noTasks') }}</div>
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
            <span v-if="selected.status === 'running'" class="live">● {{ $t('taskcenter.liveUpdate') }}</span>
          </div>
          <div ref="logBox" class="log-box">
            <div v-for="(l, i) in logLines" :key="i" class="line" :class="lineClass(l)">
              <span v-if="l.type === 'status'" class="arrow">»</span>
              <span v-else-if="l.type === 'error'" class="arrow">✖</span>
              <span v-else-if="l.type === 'result'" class="arrow">{{ resultFailed(l) ? '✖' : '✔' }}</span>
              <span class="text">{{ lineText(l) }}</span>
            </div>
            <div v-if="selected.status === 'running'" class="line status"><span class="arrow">»</span><span class="text">{{ $t('taskcenter.waitingOutput') }}</span></div>
          </div>
        </template>
        <div v-else class="empty">{{ $t('taskcenter.noTasksSelected') }}</div>
      </div>
    </div>
  </div>

  <!-- 高风险操作二次确认：删除任务需输入面板密码 -->
  <ConfirmDialog
    :show="confirm.show"
    :mode="confirm.mode"
    :title="confirm.title"
    :message="confirm.message"
    :required-text="confirm.requiredText"
    :input-label="confirm.inputLabel"
    :placeholder="confirm.placeholder"
    :confirm-label="$t('common.delete')"
    @confirm="doConfirm"
    @cancel="confirm.show = false"
  />
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'   // 响应式状态、DOM 更新后回调、轮询定时器的启停钩子
import { useI18n } from 'vue-i18n'   // 取 t()，任务状态文案跟随面板语言
import { tasksApi } from '../../api'   // 任务中心后端能力：/api/tasks/* 的封装
import { ListChecks, Trash2, RefreshCw } from 'lucide-vue-next'   // 标题 / 删除 / 刷新图标
import ConfirmDialog from '../ConfirmDialog.vue'   // 高风险操作确认框（删除任务要求输入面板密码）

const { t } = useI18n()

const tasks = ref([])        // 任务列表，左侧卡片数据源
const selected = ref(null)   // 当前选中的任务对象
const logLines = ref([])     // 选中任务的日志行（右侧面板数据源）
const logBox = ref(null)     // 日志滚动容器 DOM 引用，用于自动滚到底部
const loading = ref(false)   // 手动刷新时的旋转态
// 高风险操作二次确认状态（删除任务需输入面板密码）
const confirm = ref({ show: false, mode: 'password', title: '', message: '', requiredText: '', inputLabel: '', placeholder: '', action: null })

let timer = null   // 2 秒轮询的定时器句柄，卸载窗口时清掉

// --- 任务状态 → 界面文案（进行中 / 失败 / 已完成） ---
function statusText(task) {
  if (task.status === 'running') return t('taskcenter.statusRunning')
  if (task.status === 'error') return t('taskcenter.statusFailed')
  return t('taskcenter.statusCompleted')
}

// --- 时间精简：去掉「T」与毫秒小数，只留到秒 ---
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
    if (d.app_name) parts.push(t('taskcenter.appLabel', { name: d.app_name }))
    if (d.container_name) parts.push(t('taskcenter.containerLabel', { name: d.container_name }))
    if (d.version) parts.push(t('taskcenter.versionLabel', { name: d.version }))
    if (d.port) parts.push(t('taskcenter.portLabel', { name: d.port }))
    const head = d.ok === true ? t('taskcenter.installSuccess') : t('taskcenter.installFailed')
    return `${head}（${parts.join('，') || t('taskcenter.seeLogAbove')}）`
  }
  return l.text || ''
}

// result 行若失败则归入 error 配色
function lineClass(l) {
  if (l.type === 'result' && resultFailed(l)) return 'error'
  return l.type || 'log'
}

// --- 拉取任务列表，并让选中项始终跟随列表里同名任务的最新状态 ---
async function refreshList() {
  try {
    const r = await tasksApi.list()
    tasks.value = r.tasks || []
    // 保持选中的任务对象同步最新状态
    if (selected.value) {
      const cur = tasks.value.find(t => t.id === selected.value.id)
      selected.value = cur || null    // 任务已从列表消失（被删）时清空选中
    }
  } catch (e) { /* 忽略轮询错误 */ }
}

// --- 拉取选中任务的日志：运行中强制滚到底，否则仅在原本就在底部时跟随 ---
async function refreshLog() {
  if (!selected.value) return
  try {
    const wasAtBottom = logBox.value &&
      (logBox.value.scrollHeight - logBox.value.scrollTop - logBox.value.clientHeight) < 40   // 距底部 40px 内视为已在底部
    const r = await tasksApi.log(selected.value.id)
    logLines.value = r.lines || []
    // 任务运行中强制跟随底部，否则仅在原位于底部时跟随
    if (selected.value.status === 'running' || wasAtBottom) {
      nextTick(() => { if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight })
    }
  } catch (e) { /* 忽略轮询错误 */ }
}

// --- 全量刷新：列表 + 日志一次拉齐 ---
async function refreshAll() {
  loading.value = true
  await refreshList()
  await refreshLog()
  loading.value = false
}

// --- 点击任务卡片：切换选中项并立即拉一次日志 ---
function select(t) {
  selected.value = t
  logLines.value = []    // 先清空旧日志，避免新任务短暂显示上一个任务的残留行
  refreshLog()
}

function removeSelected() {
  if (!selected.value) return   // 未选中任何任务时按钮本身已禁用，这里是兜底
  // 高风险操作：删除任务需输入面板密码确认后才真正执行（title 硬编码中文，缺失 i18n 键的回退先例）
  confirm.value = {
    show: true,
    mode: 'password',
    title: '删除任务确认',
    message: `删除任务「${selected.value.title}」后不可恢复。\n请输入面板密码以确认。`,
    requiredText: '',
    inputLabel: t('confirmDanger.inputPwdLabel'),
    placeholder: t('confirmDanger.inputPwdPlaceholder'),
    action: { type: 'remove', id: selected.value.id }
  }
}

// ConfirmDialog 密码校验通过后的回调：真正执行删除
async function doConfirm() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return   // 无待删除任务（异常触发）时直接退出
  try {
    await tasksApi.remove(a.id)
    selected.value = null
    await refreshList()
  } catch (e) {
    alert(t('taskcenter.deleteFailed', { error: e.response?.data?.detail || e.message }))
  }
}

onMounted(async () => {
  await refreshAll()
  // 自动选中最近的任务（优先进行中）
  if (!selected.value && tasks.value.length) {
    const running = tasks.value.find(t => t.status === 'running') || tasks.value[0]
    select(running)
  }
  timer = setInterval(refreshAll, 2000)   // 每 2 秒轮询一次列表与日志，保持任务进度实时
})

onUnmounted(() => {
  if (timer) clearInterval(timer)   // 窗口关闭后停止轮询，避免后台请求泄漏
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
