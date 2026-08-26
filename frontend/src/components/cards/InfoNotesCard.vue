<!--
  InfoNotesCard.vue — 系统信息 / 备忘录卡片（桌面版）
  作用：桌面卡片之一，在「系统信息」与「备忘录」两个标签页间切换。系统信息展示
        主机名 / 系统 / 架构 / CPU 核数 / Python / 启动时间 / 运行时长；备忘录为
        一块失焦自动保存的文本区。
  数据：系统信息来自共享 systemState.info（store/systemMetrics，单条 WS 驱动）；
        备忘录经 notesApi 读写。节点不可达时由 MetricsFallback 覆盖提示。
  打开方式：作为桌面卡片渲染。
-->
<template>
  <div class="win7-card" style="display:flex; flex-direction:column;">
    <div class="card-title">
      <span>{{ $t(mode === 'info' ? 'cards.systemInfo' : 'cards.notes') }}</span>
      <div class="tabs">
        <button :class="{ active: mode === 'info' }" @click="mode = 'info'">{{ $t('cards.systemInfo') }}</button>
        <button :class="{ active: mode === 'notes' }" @click="mode = 'notes'">{{ $t('cards.notes') }}</button>
      </div>
    </div>
    <div style="flex:1; min-height:0; overflow:auto;">
      <div v-if="mode === 'info'" class="sysinfo">
        <div class="row"><span class="k">{{ $t('cards.info.hostname') }}</span><span class="v">{{ info.hostname }}</span></div>
        <div class="row"><span class="k">{{ $t('cards.info.system') }}</span><span class="v">{{ info.system }} {{ info.release }}</span></div>
        <div class="row"><span class="k">{{ $t('cards.info.arch') }}</span><span class="v">{{ info.machine }}</span></div>
        <div class="row"><span class="k">{{ $t('cards.info.cpuCount') }}</span><span class="v">{{ $t('cards.info.cpuCountValue', { physical: info.cpu_count_physical, logical: info.cpu_count }) }}</span></div>
        <div class="row"><span class="k">Python</span><span class="v">{{ info.python_version }}</span></div>
        <div class="row"><span class="k">{{ $t('cards.info.bootTime') }}</span><span class="v">{{ formatTime(info.boot_time) }}</span></div>
        <div class="row"><span class="k">{{ $t('cards.info.uptime') }}</span><span class="v">{{ uptimeStr }}</span></div>
      </div>
      <textarea
        v-else
        v-model="noteContent"
        class="notes-area"
        :placeholder="$t('cards.notesPlaceholder')"
        @blur="saveNote"
      ></textarea>
    </div>
    <!-- 当前管理节点不可达/数据过期时的降级提示（仅系统信息页展示，备忘录与监控无关） -->
    <MetricsFallback v-if="mode === 'info'" />
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'   // Vue 响应式与生命周期
import { notesApi } from '../../api'             // 备忘录 API
import { systemState } from '../../store/systemMetrics'   // 共享系统指标状态（单条 WS 驱动）
import MetricsFallback from './MetricsFallback.vue'       // 监控数据降级提示组件

const mode = ref('info')   // 当前标签页：info=系统信息 / notes=备忘录
// 系统信息由共享「单条 WS」指标推送驱动（见 store/systemMetrics.js），
// 无需再单独 5s 轮询 /api/system/info。
const info = computed(() => systemState.info)
const noteContent = ref('')   // 备忘录文本（textarea 双向绑定）

// 拉取已有备忘录内容
async function loadNote() {
  try { noteContent.value = (await notesApi.get()).content || '' } catch (e) {}   // 读取失败静默，保留空白可继续输入
}
let saveTimeout = null   // 防抖句柄，避免每次失焦都重复打接口
// 失焦防抖保存：300ms 内连续保存合并为一次
function saveNote() {
  clearTimeout(saveTimeout)
  saveTimeout = setTimeout(() => {
    notesApi.save(noteContent.value).catch(() => {})   // 保存失败静默，不打断用户
  }, 300)
}

// 系统信息里 ISO 时间的展示格式化；无值显示占位符
function formatTime(iso) {
  if (!iso) return '-'
  try { return new Date(iso).toLocaleString() } catch { return iso }   // 非法时间回退原始串
}

// 运行时长：把秒数拆成「天 / 时 / 分」
const uptimeStr = computed(() => {
  let s = info.value.uptime_seconds || 0
  const d = Math.floor(s / 86400); s %= 86400   // 86400 秒 = 1 天
  const h = Math.floor(s / 3600); s %= 3600     // 3600 秒 = 1 小时
  const m = Math.floor(s / 60)                  // 剩余秒数折算为分钟
  return `${d}天 ${h}时 ${m}分`
})

onMounted(() => {
  loadNote()   // 打开卡片即载入备忘录
})
</script>
