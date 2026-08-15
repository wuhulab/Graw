<template>
  <div class="log-window">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <span class="title"><Terminal :size="15" /> 安装日志：{{ app?.name || '' }}</span>
      <span class="badge" :class="phaseClass">{{ phaseText }}</span>
      <button class="btn" style="margin-left:auto;" @click="emit('close')">关闭</button>
    </div>

    <div class="body">
      <!-- 日志区 -->
      <div ref="logBox" class="log-box">
        <div v-for="(l, i) in lines" :key="i" class="line" :class="l.type">
          <span v-if="l.type === 'status'" class="arrow">»</span>
          <span v-else-if="l.type === 'error'" class="arrow">✖</span>
          <span class="text">{{ l.text }}</span>
        </div>
        <div v-if="phase === 'running'" class="line status"><span class="arrow">»</span><span class="text">等待输出...</span></div>
      </div>

      <!-- 结果框 -->
      <div v-if="result" class="result-box">
        <div class="result-row"><span>应用</span><b>{{ result.app_name }}</b></div>
        <div class="result-row"><span>容器名称</span><b class="mono">{{ result.container_name }}</b></div>
        <div class="result-row"><span>版本</span><b class="mono">{{ result.version }}</b></div>
        <div class="result-row" v-if="result.port"><span>访问地址</span><b class="mono">http://&lt;服务器IP&gt;:{{ result.port }}</b></div>
        <div class="result-row"><span>项目目录</span><b class="mono">{{ result.project_dir }}</b></div>
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
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { appStoreApi } from '../../api'
import { Terminal, AlertTriangle } from 'lucide-vue-next'

const props = defineProps({
  app: Object,
  request: Object
})
const emit = defineEmits(['close'])

const phase = ref('running') // running | done | error
const lines = ref([])
const result = ref(null)
const errorMsg = ref('')
const logBox = ref(null)

let controller = null

const phaseText = computed(() => {
  if (phase.value === 'running') return '正在安装...'
  if (phase.value === 'error') return '安装失败'
  return '安装完成'
})
const phaseClass = computed(() => {
  if (phase.value === 'running') return 'warn'
  if (phase.value === 'error') return 'err'
  return 'ok'
})

function push(type, text) {
  lines.value.push({ type, text })
  scrollBottom()
}

function scrollBottom() {
  nextTick(() => {
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
  })
}

function onEvent(evt) {
  if (evt.type === 'status') {
    push('status', evt.message)
  } else if (evt.type === 'log') {
    push('log', evt.line)
  } else if (evt.type === 'result') {
    result.value = evt.data
    push('status', '安装成功，容器已启动。')
    phase.value = 'done'
  } else if (evt.type === 'error') {
    errorMsg.value = evt.message || '未知错误'
    push('error', evt.message || '未知错误')
    phase.value = 'error'
  }
}

onMounted(() => {
  push('status', `开始安装 ${props.app?.name || ''}（${props.app?.id || ''}）...`)
  // 启动流式安装
  controller = appStoreApi.installStream(props.request, onEvent)
})

onUnmounted(() => {
  // 关闭窗口时中断流式请求（后端会停止子进程）
  if (controller) controller.abort()
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

.body { flex: 1; display: flex; flex-direction: column; gap: 10px; padding: 10px 12px; overflow-y: auto; }

.log-box { flex: 1; min-height: 0; overflow-y: auto; background: #0f172a; border-radius: 8px; padding: 10px; font-family: Consolas, monospace; font-size: 12px; line-height: 1.55; }
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
