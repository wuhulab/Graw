<!--
  ContainerStatsWindow.vue — 容器资源监控图表窗口
  ==========================================================
  业务作用：
    以 Canvas 折线图实时展示某个容器的 CPU / 内存使用率。默认每 1 秒采样
    一次，保留最近 240 个采样点（约 4 分钟），可暂停/继续采集或清空数据。
    容器停止或引擎不可用时会自动暂停采集。
  后端模块：
    /api/docker 的 containerStats（读取容器实时资源占用）。
  关键状态：
    - running  是否正在采集
    - points   采样点数组 [{cpu, mem, t}]（最多 240 个）
    - cpuNow / memNow 当前值（数字图例）
  打开方式：
    由 Docker 管理窗口的容器「监控」按钮打开，props 传入容器 id 与名称。
-->
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
import { ref, onMounted, onUnmounted } from 'vue'   // 状态/挂载启动采集/卸载停止
import { Activity } from 'lucide-vue-next'   // 标题图标
import { dockerApi } from '../../api'   // /api/docker：容器实时资源接口

const props = defineProps({
  id: { type: String, default: '' },   // 容器 id
  name: { type: String, default: '' },   // 容器显示名称
})

const chart = ref(null)   // Canvas DOM 引用
const running = ref(false)   // 是否正在采集
const points = ref([])          // [{cpu, mem, t}] 采样点序列
const MAX_POINTS = 240          // 保留最近 240 个采样点（4 分钟，按 1s/次）
let timer = null   // 定时采样句柄
let ctx = null   // Canvas 2D 上下文（缓存复用）
const cpuNow = ref(0)   // 当前 CPU 使用率（顶部数字）
const memNow = ref(0)   // 当前内存使用率（顶部数字）

// --- 单次采样：拉取容器资源并追加采样点 ---
async function tick() {
  try {
    const s = await dockerApi.containerStats(props.id)
    const cpu = s.cpu_percent ?? 0
    const mem = s.mem_percent ?? 0
    cpuNow.value = Number(cpu).toFixed(1)
    memNow.value = Number(mem).toFixed(1)
    points.value.push({ cpu: Number(cpu) || 0, mem: Number(mem) || 0, t: Date.now() })
    if (points.value.length > MAX_POINTS) points.value.shift()   // 超出上限丢最旧采样点
    draw()
  } catch (e) {
    // 容器停止/引擎不可用 → 暂停采集并提示
    stop()
  }
}

// 开始采集：立即采一次，随后按 1 秒间隔轮询
function start() {
  if (running.value) return   // 已在运行则忽略
  running.value = true
  tick()  // 立即采一次
  timer = setInterval(tick, 1000)
}

// 停止采集并清理定时器
function stop() {
  running.value = false
  if (timer) { clearInterval(timer); timer = null }
}

// 清空已采集的数据并重绘
function clearData() {
  points.value = []
  cpuNow.value = 0
  memNow.value = 0
  draw()
}

// --- 绘制折线图：网格 + 刻度 + CPU/内存两条曲线 ---
function draw() {
  const c = chart.value
  if (!c) return
  ctx = c.getContext('2d')
  const W = c.width, H = c.height
  ctx.clearRect(0, 0, W, H)
  const PAD = 6   // 图表四周留白
  const n = points.value.length
  if (n < 2) {
    // 采样点不足 2 个时不画曲线，显示等待提示
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
  const xStep = (W - 2 * PAD) / (n - 1)   // 采样点横向间距（首尾贴边）
  const toY = v => PAD + (H - 2 * PAD) * (1 - Math.min(100, Math.max(0, v)) / 100)   // 百分比 → 画布 y（值钳制在 0~100）
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

onMounted(start)   // 打开窗口即开始采集
onUnmounted(stop)   // 关闭窗口停止采集，避免定时器泄漏
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