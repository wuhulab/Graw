<template>
  <div class="win7-card" style="display:flex; flex-direction:column;">
    <div class="card-title">
      <span>{{ mode === 'info' ? '系统信息' : '备忘录' }}</span>
      <div class="tabs">
        <button :class="{ active: mode === 'info' }" @click="mode = 'info'">系统信息</button>
        <button :class="{ active: mode === 'notes' }" @click="mode = 'notes'">备忘录</button>
      </div>
    </div>
    <div style="flex:1; min-height:0; overflow:auto;">
      <div v-if="mode === 'info'" class="sysinfo">
        <div class="row"><span class="k">主机名</span><span class="v">{{ info.hostname }}</span></div>
        <div class="row"><span class="k">系统</span><span class="v">{{ info.system }} {{ info.release }}</span></div>
        <div class="row"><span class="k">架构</span><span class="v">{{ info.machine }}</span></div>
        <div class="row"><span class="k">CPU 核心</span><span class="v">{{ info.cpu_count_physical }}核 / {{ info.cpu_count }}线程</span></div>
        <div class="row"><span class="k">Python</span><span class="v">{{ info.python_version }}</span></div>
        <div class="row"><span class="k">启动时间</span><span class="v">{{ formatTime(info.boot_time) }}</span></div>
        <div class="row"><span class="k">运行时长</span><span class="v">{{ uptimeStr }}</span></div>
      </div>
      <textarea
        v-else
        v-model="noteContent"
        class="notes-area"
        placeholder="在此记录备忘..."
        @blur="saveNote"
      ></textarea>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { notesApi } from '../../api'
import { systemState } from '../../store/systemMetrics'

const mode = ref('info')
// 系统信息由共享「单条 WS」指标推送驱动（见 store/systemMetrics.js），
// 无需再单独 5s 轮询 /api/system/info。
const info = computed(() => systemState.info)
const noteContent = ref('')

async function loadNote() {
  try { noteContent.value = (await notesApi.get()).content || '' } catch (e) {}
}
let saveTimeout = null
function saveNote() {
  clearTimeout(saveTimeout)
  saveTimeout = setTimeout(() => {
    notesApi.save(noteContent.value).catch(() => {})
  }, 300)
}

function formatTime(iso) {
  if (!iso) return '-'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

const uptimeStr = computed(() => {
  let s = info.value.uptime_seconds || 0
  const d = Math.floor(s / 86400); s %= 86400
  const h = Math.floor(s / 3600); s %= 3600
  const m = Math.floor(s / 60)
  return `${d}天 ${h}时 ${m}分`
})

onMounted(() => {
  loadNote()
})
</script>
