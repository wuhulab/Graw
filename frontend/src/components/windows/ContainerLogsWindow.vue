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
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { dockerApi } from '../../api'

const { t } = useI18n()

// props: id 容器 ID, name 容器名称；emit: close 关闭窗口
const props = defineProps({ id: String, name: String })
const emit = defineEmits(['close'])

const logs = ref(t('containerlogs.fetching'))
const loading = ref(false)
const tail = ref(300)
const autoRefresh = ref(true)
const logBox = ref(null)
let timer = null
let stickBottom = true  // 是否跟随滚动到底部

async function load() {
  loading.value = true
  try {
    const r = await dockerApi.logs(props.id, tail.value)
    logs.value = r.logs || t('containerlogs.empty')
  } catch (e) {
    logs.value = t('containerlogs.fetchFailed', { error: e.response?.data?.detail || e.message })
  } finally {
    loading.value = false
    if (stickBottom) scrollBottom()
  }
}

function scrollBottom() {
  const el = logBox.value
  if (el) el.scrollTop = el.scrollHeight
}

function onScroll() {
  const el = logBox.value
  if (!el) return
  // 距底部小于 30px 视为跟随底部
  stickBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30
}

function toggleAuto() {
  if (timer) { clearInterval(timer); timer = null }
  if (autoRefresh.value) {
    timer = setInterval(load, 2000)
  }
}

onMounted(async () => {
  await load()
  if (autoRefresh.value) timer = setInterval(load, 2000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
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
