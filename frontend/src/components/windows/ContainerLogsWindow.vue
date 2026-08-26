<!--
  ContainerLogsWindow.vue — 容器日志窗口
  ==========================================================
  业务作用：
    以深色终端样式查看某个 Docker 容器的日志输出。默认每 2 秒自动刷新并
    跟随滚动到底部；可暂停自动刷新、手动刷新或切换读取行数（100/300/1000/
    5000）。用户向上滚动查看历史时自动停止跟随。
  后端模块：
    /api/docker 的 logs（读取容器末尾 N 行日志）。
  关键状态：
    - logs         当前显示的日志文本
    - tail         读取行数（默认 300）
    - autoRefresh  自动刷新开关
    - stickBottom  是否跟随滚动到底部（向上滚动后自动取消）
  打开方式：
    由 Docker 管理窗口的容器「日志」按钮打开，props 传入容器 id 与名称。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#1e1e1e; color:#d4d4d4;">
    <!-- 工具栏 -->
    <div class="log-toolbar">
      <span class="log-title">{{ name }}</span>
      <span class="log-id">{{ id }}</span>
      <button class="btn" :disabled="!autoRefresh" @click="load">{{ $t('containerlogs.refresh') }}</button>
      <button class="btn" @click="autoRefresh = !autoRefresh; toggleAuto()">
        {{ autoRefresh ? $t('containerlogs.pause') : $t('containerlogs.autoRefresh') }}
      </button>
      <select class="tail-select" v-model="tail" @change="load">
        <option :value="100">{{ $t('containerlogs.recentLines', { count: 100 }) }}</option>
        <option :value="300">{{ $t('containerlogs.recentLines', { count: 300 }) }}</option>
        <option :value="1000">{{ $t('containerlogs.recentLines', { count: 1000 }) }}</option>
        <option :value="5000">{{ $t('containerlogs.recentLines', { count: 5000 }) }}</option>
      </select>
      <span v-if="loading" class="loading">{{ $t('common.loading') }}</span>
      <button class="btn close-btn" @click="$emit('close')">{{ $t('containerlogs.close') }}</button>
    </div>
    <!-- 日志内容 -->
    <pre ref="logBox" class="log-content" @scroll="onScroll">{{ logs }}</pre>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'   // 状态/挂载启动轮询/卸载清理
import { useI18n } from 'vue-i18n'   // 翻译函数
import { dockerApi } from '../../api'   // /api/docker：容器日志接口

const { t } = useI18n()

// props: id 容器 ID, name 容器名称；emit: close 关闭窗口
const props = defineProps({ id: String, name: String })
const emit = defineEmits(['close'])

const logs = ref(t('containerlogs.fetching'))   // 日志文本（初始为「正在获取」）
const loading = ref(false)   // 加载中
const tail = ref(300)   // 读取行数（默认 300）
const autoRefresh = ref(true)   // 自动刷新开关
const logBox = ref(null)   // 日志滚动容器 DOM 引用
let timer = null   // 自动刷新定时器
let stickBottom = true  // 是否跟随滚动到底部

// --- 读取容器日志（末尾 tail 行） ---
async function load() {
  loading.value = true
  try {
    const r = await dockerApi.logs(props.id, tail.value)
    logs.value = r.logs || t('containerlogs.empty')
  } catch (e) {
    logs.value = t('containerlogs.fetchFailed', { error: e.response?.data?.detail || e.message })
  } finally {
    loading.value = false
    if (stickBottom) scrollBottom()   // 跟随模式下每次刷新后滚到底
  }
}

// 滚动容器到最底部
function scrollBottom() {
  const el = logBox.value
  if (el) el.scrollTop = el.scrollHeight
}

// 用户滚动时更新跟随状态：距底部 < 30px 视为仍在底部
function onScroll() {
  const el = logBox.value
  if (!el) return
  // 距底部小于 30px 视为跟随底部
  stickBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30
}

// 切换自动刷新：停止旧定时器，开启时按 2 秒间隔重新轮询
function toggleAuto() {
  if (timer) { clearInterval(timer); timer = null }
  if (autoRefresh.value) {
    timer = setInterval(load, 2000)   // 2 秒轮询一次
  }
}

onMounted(async () => {
  await load()
  if (autoRefresh.value) timer = setInterval(load, 2000)   // 默认开启 2 秒自动刷新
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)   // 关闭窗口时停止轮询，避免泄漏
})
</script>

<style scoped>
.log-toolbar {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; background: #252526; border-bottom: 1px solid #333;
}
.log-title { font-weight: 600; font-size: 13px; color: #fff; }
.log-id { font-size: 11px; color: #888; font-family: Consolas, monospace; }
.btn {
  padding: 4px 10px; border: 1px solid #3c3c3c; background: #333;
  color: #ddd; border-radius: 6px; cursor: pointer; font-size: 12px;
}
.btn:hover:not(:disabled) { background: #444; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.close-btn { margin-left: auto; background: #5a1e1e; border-color: #7a2a2a; }
.close-btn:hover { background: #6e2424; }
.tail-select { padding: 4px 6px; background: #333; color: #ddd; border: 1px solid #3c3c3c; border-radius: 6px; font-size: 12px; }
.loading { color: #aaa; font-size: 12px; }
.log-content {
  flex: 1; overflow: auto; margin: 0; padding: 10px 14px;
  font-size: 12px; line-height: 1.5;
  font-family: Consolas, 'Courier New', monospace;
  white-space: pre-wrap; word-break: break-all;
}
</style>
