<template>
  <div class="cstats-window">
    <div class="toolbar">
      <span class="title"><Activity :size="15" /> 资源图表：{{ name }}</span>
      <div class="legend">
        <span class="lg cpu"><i></i>CPU</span>
        <span class="lg mem"><i></i>内存</span>
      </div>
      <div class="toolbar-right">
        <span class="now">
          CPU <b>{{ cpuNow }}%</b> · 内存 <b>{{ memNow }}%</b>
        </span>
        <button class="btn" :disabled="!running" @click="stop">{{ running ? '暂停' : '继续' }}</button>
        <button class="btn" @click="clearData">清空</button>
      </div>
    </div>

    <div v-if="!running && points.length === 0" class="empty">容器未运行或无监控数据</div>

    <canvas ref="chart" class="chart" width="900" height="260"></canvas>

    <div class="foot">
      <span v-if="running" class="live"><span class="dot"></span> 每 1s 采集一次，共 {{ points.length }} 个采样点</span>
      <span v-else class="paused">已暂停采集</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { Activity } from 'lucide-vue-next'
import { dockerApi } from '../../api'

const props = defineProps({
  id: { type: String, default: '' },
  name: { type: String, default: '' },
})

const chart = ref(null)
const running = ref(false)
const points = ref([])          // [{cpu, mem, t}]
const MAX_POINTS = 240          // 保留最近 240 个采样点（4 分钟）
let timer = null
let ctx = null
const cpuNow = ref(0)
const memNow = ref(0)

async function tick() {
  try {
    const s = await dockerApi.containerStats(props.id)
    const cpu = s.cpu_percent ?? 0
    const mem = s.mem_percent ?? 0
    cpuNow.value = Number(cpu).toFixed(1)
    memNow.value = Number(mem).toFixed(1)
    points.value.push({ cpu: Number(cpu) || 0, mem: Number(mem) || 0, t: Date.now() })
    if (points.value.length > MAX_POINTS) points.value.shift()
    draw()
  } catch (e) {
    // 容器停止/引擎不可用 → 暂停采集并提示
    stop()
  }
}

function start() {
  if (running.value) return
  running.value = true
  tick()  // 立即采一次
  timer = setInterval(tick, 1000)
}

function stop() {
  running.value = false
  if (timer) { clearInterval(timer); timer = null }
}

function clearData() {
  points.value = []
  cpuNow.value = 0
  memNow.value = 0
  draw()
}

function draw() {
  const c = chart.value
  if (!c) return
  ctx = c.getContext('2d')
  const W = c.width, H = c.height
  ctx.clearRect(0, 0, W, H)
  const PAD = 6
  const n = points.value.length
  if (n < 2) {
    ctx.fillStyle = '#9ca3af'
    ctx.font = '12px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('等待数据…', W / 2, H / 2)
    return
  }
  // 网格线
  ctx.strokeStyle = '#f0f0f0'
  ctx.lineWidth = 1
  for (let i = 0; i <= 4; i++) {
    const y = PAD + (H - 2 * PAD) * (i / 4)
    ctx.beginPath(); ctx.moveTo(PAD, y); ctx.lineTo(W - PAD, y); ctx.stroke()
  }
  // 刻度文字（0/25/50/75/100%）
  ctx.fillStyle = '#9ca3af'
  ctx.font = '10px sans-serif'
  ctx.textAlign = 'left'
  for (let i = 0; i <= 4; i++) {
    const y = PAD + (H - 2 * PAD) * (i / 4)
    ctx.fillText(`${100 - i * 25}%`, 4, y - 3)
  }
  const xStep = (W - 2 * PAD) / (n - 1)
  const toY = v => PAD + (H - 2 * PAD) * (1 - Math.min(100, Math.max(0, v)) / 100)
  // CPU 曲线（蓝）
  ctx.strokeStyle = '#0a84ff'
  ctx.lineWidth = 2
  ctx.beginPath()
  points.value.forEach((p, i) => {
    const x = PAD + i * xStep
    const y = toY(p.cpu)
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
  })
  ctx.stroke()
  // 内存曲线（绿）
  ctx.strokeStyle = '#16a34a'
  ctx.beginPath()
  points.value.forEach((p, i) => {
    const x = PAD + i * xStep
    const y = toY(p.mem)
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
  })
  ctx.stroke()
}

onMounted(start)
onUnmounted(stop)
</script>

<style scoped>
.cstats-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.toolbar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.title { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; }
.legend { display: flex; gap: 12px; font-size: 12px; }
.legend i { display: inline-block; width: 12px; height: 4px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }
.legend .cpu i { background: #0a84ff; }
.legend .mem i { background: #16a34a; }
.toolbar-right { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.now { font-size: 12px; color: #374151; }
.now b { color: #111827; }
.chart { width: 100%; height: 260px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; flex: 1; min-height: 200px; }
.empty { text-align: center; color: #9ca3af; padding: 40px; }
.foot { font-size: 11.5px; color: #6e6e73; display: flex; align-items: center; gap: 6px; }
.live .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #16a34a; margin-right: 4px; }
.paused { color: #b45309; }
.btn { padding: 4px 10px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>