<!--
  AppStoreInstallLogWindow.vue — 应用商店安装/升级的流式日志窗口
  ==========================================================
  业务作用：
    以流式方式展示某个应用的安装（或升级）全过程输出：阶段状态、逐步日志、
    最终结果（访问地址/容器名/项目目录）或错误信息。窗口打开即向后端发起
    流式安装请求，接收 SSE 事件驱动界面更新。
  后端模块：
    /api/appstore 的流式安装接口（appStoreApi.installStream）。
  关键状态：
    - phase    运行阶段：running（安装中）/ done（完成）/ error（失败）
    - lines    日志行数组，每行带类型（log/status/error）决定图标与颜色
    - result   安装成功后的结果信息（应用名、容器名、版本、端口、项目目录、告警）
    - errorMsg 失败原因
    - taskId   后端返回的任务 ID（可跳转任务中心查看）
  打开方式：
    由 AppStoreWindow（安装确认流程）以 props 传入 app（应用信息）与
    request（安装请求参数）打开；关闭窗口时中断流式请求。
-->
<template>
  <div class="log-window">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <span class="title"><Terminal :size="15" /> {{ $t('appinstalllog.title', { name: appDisplayName }) }}</span>
      <span class="badge" :class="phaseClass">{{ phaseText }}</span>
      <span v-if="taskId" class="task-hint" :title="$t('appinstalllog.taskMountedHint')"><ListChecks :size="12" /> {{ $t('appinstalllog.taskMounted') }}</span>
      <button class="btn" style="margin-left:auto;" @click="copyAll">{{ copied ? $t('appinstalllog.copied') : $t('appinstalllog.copy') }}</button>
      <button class="btn" @click="emit('openTaskCenter')"><ListChecks :size="13" /> {{ $t('appinstalllog.taskCenter') }}</button>
      <button class="btn" @click="emit('close')">{{ $t('appinstalllog.close') }}</button>
    </div>

    <div class="body">
      <!-- 日志区（user-select:text 允许手动选中复制） -->
      <div ref="logBox" class="log-box">
        <div v-for="(l, i) in lines" :key="i" class="line" :class="l.type">
          <span v-if="l.type === 'status'" class="arrow">»</span>
          <span v-else-if="l.type === 'error'" class="arrow">✖</span>
          <span class="text">{{ l.text }}</span>
        </div>
        <div v-if="phase === 'running'" class="line status"><span class="arrow">»</span><span class="text">{{ $t('appinstalllog.waitingOutput') }}</span></div>
      </div>

      <!-- 结果框 -->
      <div v-if="result" class="result-box">
        <div class="result-row"><span>{{ $t('appinstalllog.app') }}</span><b>{{ result.app_name }}</b></div>
        <div class="result-row"><span>{{ $t('appinstalllog.containerName') }}</span><b class="mono">{{ result.container_name }}</b></div>
        <div class="result-row"><span>{{ $t('appinstalllog.version') }}</span><b class="mono">{{ result.version }}</b></div>
        <div class="result-row" v-if="result.port"><span>{{ $t('appinstalllog.accessUrl') }}</span><b class="mono">{{ $t('appinstalllog.accessUrlHint', { port: result.port }) }}</b></div>
        <div class="result-row"><span>{{ $t('appinstalllog.projectDir') }}</span><b class="mono">{{ result.project_dir }}</b></div>
        <div v-if="result.warnings && result.warnings.length" class="warn-box">
          <div v-for="(w, i) in result.warnings" :key="i" class="warn-line"><AlertTriangle :size="13" /> {{ w }}</div>
        </div>
      </div>

      <!-- 错误框 -->
      <div v-if="errorMsg" class="error-box">
        <AlertTriangle :size="14" />
        <span>{{ errorMsg }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'   // 状态/派生值/渲染后滚动/生命周期钩子
import { useI18n } from 'vue-i18n'   // 取当前语种与翻译函数
import { appStoreApi } from '../../api'   // 应用商店 API：发起流式安装
import { localizedName } from '../../appStoreL10n'   // 应用索引内嵌翻译（多语言应用名）
import { Terminal, AlertTriangle, ListChecks } from 'lucide-vue-next'   // 工具栏/结果框图标

const { t, locale } = useI18n()

const props = defineProps({
  app: Object,   // 要安装的应用信息（名称用于标题展示）
  request: Object   // 安装请求参数（应用 ID、版本、端口等），直接透传给后端
})
const emit = defineEmits(['close', 'openTaskCenter'])   // close 关窗；openTaskCenter 跳转任务中心

// 应用显示名称：优先索引内嵌翻译（i18n.<locale>.yml），
// 其次前端语言包内 appNames 覆盖，最后回退索引默认名称
const appDisplayName = computed(() =>
  localizedName(props.app, locale.value) || t('appstore.appNames.' + props.app.id, props.app?.name || '')
)

const phase = ref('running') // running | done | error
const lines = ref([])   // 已接收的日志行（每行 {type, text}）
const result = ref(null)   // 安装成功后的结果对象
const errorMsg = ref('')   // 安装失败时的错误文本
const logBox = ref(null)   // 日志滚动容器 DOM 引用
const copied = ref(false)  // 「已复制」提示开关
const taskId = ref('')     // 后端下发的任务 ID（可跳转任务中心追踪）

let controller = null   // 可中断的流式请求句柄（关闭窗口时 abort）
let copiedTimer = null  // 「已复制」提示的自动复位定时器

// 根据运行阶段派生徽章文案与颜色（running 琥珀 / error 红 / done 绿）
const phaseText = computed(() => {
  if (phase.value === 'running') return t('appinstalllog.installing')
  if (phase.value === 'error') return t('appinstalllog.installFailed')
  return t('appinstalllog.installComplete')
})
const phaseClass = computed(() => {
  if (phase.value === 'running') return 'warn'
  if (phase.value === 'error') return 'err'
  return 'ok'
})

// --- 追加一行日志并自动滚到底部 ---
function push(type, text) {
  lines.value.push({ type, text })
  scrollBottom()
}

// 等 DOM 更新后再滚动，确保新行已渲染出来
function scrollBottom() {
  nextTick(() => {
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
  })
}

// --- 处理流式事件：按类型分发到日志/结果/错误 ---
function onEvent(evt) {
  if (evt.type === 'task_id') {
    taskId.value = evt.task_id
  } else if (evt.type === 'status') {
    push('status', evt.message)   // 阶段状态行（蓝色加粗）
  } else if (evt.type === 'log') {
    push('log', evt.line)   // 普通输出行
  } else if (evt.type === 'result') {
    result.value = evt.data   // 安装成功：记录结果信息并切换到 done
    push('status', t('appinstalllog.installSuccess'))
    phase.value = 'done'
  } else if (evt.type === 'error') {
    // 安装失败：记录错误文本并切换到 error（带未知错误兜底文案）
    errorMsg.value = evt.message || t('appinstalllog.unknownError')
    push('error', evt.message || t('appinstalllog.unknownError'))
    phase.value = 'error'
  }
}

onMounted(() => {
  push('status', t('appinstalllog.installStarted', { name: appDisplayName.value, id: props.app?.id || '' }))
  // 启动流式安装
  controller = appStoreApi.installStream(props.request, onEvent)
})

onUnmounted(() => {
  // 关闭窗口时中断流式请求（后端会停止子进程）并清理定时器
  if (controller) controller.abort()
  clearTimeout(copiedTimer)
})
</script>

<style scoped>
.log-window { display: flex; flex-direction: column; height: 100%; overflow: hidden; }

.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
.title { font-weight: 700; font-size: 13.5px; display: inline-flex; align-items: center; gap: 6px; }
.badge { font-size: 11px; padding: 1px 8px; border-radius: 999px; }
.badge.warn { background: #fffbeb; color: #b45309; }
.badge.err { background: #fef2f2; color: #b91c1c; }
.badge.ok { background: #ecfdf5; color: #047857; }
.task-hint { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: #047857; background: #ecfdf5; border: 1px solid #bbf7d0; border-radius: 999px; padding: 1px 8px; }

.body { flex: 1; display: flex; flex-direction: column; gap: 10px; padding: 10px 12px; overflow-y: auto; }

.log-box { flex: 1; min-height: 0; overflow-y: auto; background: #0f172a; border-radius: 8px; padding: 10px; font-family: Consolas, monospace; font-size: 12px; line-height: 1.55; user-select: text; cursor: text; }
.line { display: flex; gap: 6px; white-space: pre-wrap; word-break: break-all; }
.line.log .text { color: #cbd5e1; }
.line.status .text { color: #60a5fa; font-weight: 600; }
.line.error .text { color: #f87171; font-weight: 600; }
.arrow { flex-shrink: 0; color: #475569; }

.result-box { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; }
.result-row { display: flex; gap: 8px; }
.result-row span { color: #6b7280; width: 72px; flex-shrink: 0; }
.warn-box { margin-top: 4px; padding: 6px 8px; background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; }
.warn-line { display: flex; align-items: flex-start; gap: 6px; color: #92400e; font-size: 12px; line-height: 1.5; }

.error-box { display: flex; align-items: flex-start; gap: 6px; background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; border-radius: 8px; padding: 10px 12px; font-size: 12.5px; white-space: pre-wrap; word-break: break-word; }
</style>
