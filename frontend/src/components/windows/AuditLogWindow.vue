<!--
  AuditLogWindow.vue — 审计日志查看窗口
  ==========================================================
  业务作用：
    查看服务器上的日志文件（默认选中面板审计日志）。可在预置/自定义日志源之间
    切换，选择读取的末尾行数（100/200/500/1000），以等宽字体展示日志内容。
  后端模块：
    /api/logs 的 list（日志源列表）与 read（读取末尾 N 行）。
  关键状态：
    - sources    日志源列表（内置 + 自定义源）
    - currentId  当前选中日志源 id（默认 'panel' 即面板审计日志）
    - tail       读取行数（默认 200，与后端 read 默认值一致）
    - lines      已读取的日志行
  打开方式：
    由桌面/任务栏（或安全中心聚合窗口）打开，无 props。
-->
<template>
  <div class="auditlog-window">
    <!-- 工具栏：日志源选择 / 行数选择 / 刷新 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge"><ScrollText :size="14" /> 审计日志</span>
        <label class="field">
          <span class="label">日志源</span>
          <select v-model="currentId" class="input select" :disabled="loadingSources" @change="onSourceChange">
            <option v-for="s in sources" :key="s.id" :value="s.id">{{ sourceName(s) }}</option>
          </select>
        </label>
        <label class="field">
          <span class="label">行数</span>
          <select v-model="tail" class="input select" :disabled="loading" @change="load">
            <option :value="100">最近 100 行</option>
            <option :value="200">最近 200 行</option>
            <option :value="500">最近 500 行</option>
            <option :value="1000">最近 1000 行</option>
          </select>
        </label>
        <span v-if="current" class="meta" :title="current.path">{{ current.name }} · {{ current.path }}</span>
      </div>
      <div class="toolbar-actions">
        <span v-if="loading" class="loading">加载中…</span>
        <button class="btn" :disabled="loading || !current" @click="load">
          <RefreshCw :size="14" :class="{ spinning: loading }" /> 刷新
        </button>
      </div>
    </div>

    <!-- 日志内容：可滚动、等宽字体、自动换行 -->
    <pre v-if="contentText" ref="logBox" class="content">{{ contentText }}</pre>
    <div v-else-if="loading" class="empty">加载中…</div>
    <div v-else class="empty">
      <ScrollText :size="40" style="color:#9ca3af;" />
      <div>{{ emptyText }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'   // 状态/派生值/挂载钩子
import { useI18n } from 'vue-i18n'   // 翻译函数
import { RefreshCw, ScrollText } from 'lucide-vue-next'   // 刷新按钮/空状态图标
import { logsApi } from '../../api'   // /api/logs：日志源列表与日志读取

const { t } = useI18n()

// 日志源列表（/logs/list 返回预置 + 自定义源）
const sources = ref([])
// 当前选中的日志源 id，默认 'panel'（审计日志）
const currentId = ref('panel')
// 读取行数，默认 200（与 logsApi.read 默认值一致）
const tail = ref(200)
const lines = ref([])   // 已读取的日志行
const loadingSources = ref(false)   // 日志源列表加载中
const loading = ref(false)   // 日志内容加载中

const current = computed(() => sources.value.find((s) => s.id === currentId.value) || null)
const contentText = computed(() => lines.value.join(''))
const emptyText = computed(() => (current && !current.exists) ? '该日志源文件不存在' : '暂无日志内容')

// 内置日志源优先用本地翻译（缺键时回退后端 desc，逻辑同 LogsWindow）
function sourceName(s) {
  if (s && s.builtin) {
    const key = `logs.source.${s.id}`
    const translated = t(key)
    // t 对缺失键原样返回 key 路径，此时退回后端名称
    return translated === key ? (s.name || '') : translated
  }
  return s ? s.name : ''
}

// 加载日志源列表，并尝试自动恢复/默认选中 'panel'
async function loadSources() {
  loadingSources.value = true
  try {
    const r = await logsApi.list()
    sources.value = (r && r.logs) || []
    // 默认选中审计日志源（panel）；若不存在则回退第一个可用源
    if (!sources.value.some((s) => s.id === currentId.value)) {
      currentId.value = sources.value.length ? sources.value[0].id : ''
    }
  } catch (e) {
    // 拉取失败：保留空列表，刷新时重试
    console.error('审计日志：加载日志源失败', e)
  } finally {
    loadingSources.value = false
  }
}

// 切换日志源后立即读取
function onSourceChange() {
  load()
}

// 读取当前选中日志源末尾 N 行
async function load() {
  if (!current.value) return
  loading.value = true
  try {
    const r = await logsApi.read(current.value.path, tail.value)
    lines.value = (r && r.lines) || []
  } catch (e) {
    lines.value = []
    console.error('审计日志：读取日志失败', e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadSources()
  await load()
})
</script>


<style scoped>
.auditlog-window { padding: 0; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; } /* 内嵌聚合窗口：外边距由父容器提供 */
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; background: #dbeafe; color: #1e40af; }
.field { display: flex; align-items: center; gap: 6px; }
.field .label { font-size: 12px; color: #4b5563; }
.input { padding: 5px 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12.5px; background: #fff; }
.input:disabled { opacity: 0.6; cursor: not-allowed; }
.select { width: auto; }
.meta { font-size: 11.5px; color: #6b7280; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.toolbar-actions { display: flex; gap: 8px; align-items: center; }
.loading { font-size: 12px; color: #6b7280; }

/* 日志展示区：等宽字体、自动换行、可滚动 */
.content {
  flex: 1; overflow: auto; margin: 0; padding: 12px 14px;
  border: 1px solid #e5e7eb; border-radius: 8px; background: #fff;
  font-family: ui-monospace, Menlo, Consolas, 'Courier New', monospace;
  font-size: 12px; line-height: 1.6; color: #111827;
  white-space: pre-wrap; word-break: break-all;
}
.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.spinning { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
</style>
