<template>
  <div class="metrics-history-window">
    <!-- 工具栏：时间范围预设 / 自动刷新 / 清空历史 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <label class="sel-label">时间范围</label>
        <select v-model="rangeKey" class="sel" @change="load">
          <option v-for="r in ranges" :key="r.key" :value="r.key">{{ r.label }}</option>
        </select>
        <button class="btn" :disabled="loading" @click="load">
          <RefreshCw :size="13" /> 刷新
        </button>
        <label class="auto">
          <input type="checkbox" v-model="autoRefresh" /> 每 30s 自动刷新
        </label>
        <button class="btn danger" :disabled="busy" @click="doClear">
          <Trash2 :size="13" /> 清空历史
        </button>
      </div>
      <span v-if="loading" class="status">查询中…</span>
      <span v-else-if="err" class="status err">{{ err }}</span>
      <span v-else-if="points" class="status">
        {{ points }} 个采样点 · 最早 {{ fmtTime(earliest) }} · 最新 {{ fmtTime(latest) }}
      </span>
    </div>

    <!-- 空状态 -->
    <div v-if="!loading && !err && !hasData" class="empty">
      <LineChart :size="40" style="color:#9ca3af;" />
      <div>该时间范围内暂无历史数据。面板正在持续采样并落盘（每 2 秒一个采样点），稍后刷新即可看到曲线。</div>
    </div>

    <!-- 图表区 -->
    <div v-else class="charts">
      <div class="chart-box">
        <div class="chart-title">CPU 使用率 / 负载</div>
        <div ref="cpuRef" class="chart-body"></div>
      </div>
      <div class="chart-box">
        <div class="chart-title">内存 / 磁盘占用率</div>
        <div ref="memRef" class="chart-body"></div>
      </div>
      <div class="chart-box">
        <div class="chart-title">网络流量（上传 / 下载）</div>
        <div ref="netRef" class="chart-body"></div>
      </div>
      <div class="chart-box">
        <div class="chart-title">磁盘 IO（读 / 写）</div>
        <div ref="ioRef" class="chart-body"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { RefreshCw, Trash2, LineChart } from 'lucide-vue-next'
import { systemApi } from '../../api'
import { formatSpeed } from '../../api'

// 时间范围预设：label / 秒数 / 聚合桶大小（保证输出约 300 点以内）
const ranges = [
  { key: '1h', label: '近 1 小时', seconds: 3600, bucket: 10 },
  { key: '6h', label: '近 6 小时', seconds: 21600, bucket: 60 },
  { key: '24h', label: '近 24 小时', seconds: 86400, bucket: 300 },
  { key: '3d', label: '近 3 天', seconds: 259200, bucket: 900 },
  { key: '7d', label: '近 7 天', seconds: 604800, bucket: 1800 }
]

const rangeKey = ref('24h')
const autoRefresh = ref(true)
const loading = ref(false)
const busy = ref(false)
const err = ref('')
const points = ref(0)
const earliest = ref(null)
const latest = ref(null)
const hasData = ref(false)

// 当前查询到的数据
let series = []

// 图表容器与实例（统一生命周期管理）
const cpuRef = ref(null)
const memRef = ref(null)
const netRef = ref(null)
const ioRef = ref(null)
let cpuChart = null
let memChart = null
let netChart = null
let ioChart = null
let timer = null

// 时间格式化（HH:mm:ss 或 MM-dd HH:mm）
function fmtTime(ts) {
  if (!ts) return '-'
  const d = new Date(ts * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  const date = `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  // 超过 24 小时范围时带上日期，否则只显示时分
  const r = ranges.find(x => x.key === rangeKey.value)
  return r && r.seconds > 86400 ? `${date} ${time}` : time
}

// 查询历史数据并渲染
async function load() {
  loading.value = true
  err.value = ''
  try {
    const r = ranges.find(x => x.key === rangeKey.value)
    const end = Date.now() / 1000
    const start = end - r.seconds
    const res = await systemApi.metricsHistory({
      start: Math.floor(start),
      end: Math.floor(end),
      bucket: r.bucket
    })
    series = (res && res.points) || []
    points.value = (res && res.raw) || 0
    const st = await systemApi.metricsStatus()
    earliest.value = st.earliest
    latest.value = st.latest
    hasData.value = series.length > 0
    renderCharts()
  } catch (e) {
    err.value = e.response?.data?.detail || e.message
    hasData.value = false
  } finally {
    loading.value = false
  }
}

// 渲染四张图表
function renderCharts() {
  const times = series.map(p => fmtTime(p.ts))
  // CPU / 负载（双 Y 轴）
  renderDual(cpuRef.value, cpuChart, (c) => { cpuChart = c }, {
    title: 'CPU / 负载',
    x: times,
    series: [
      { name: 'CPU %', type: 'line', smooth: true, showSymbol: false, yAxisIndex: 0, data: series.map(p => p.cpu), color: '#0a84ff' },
      { name: '负载 load1', type: 'line', smooth: true, showSymbol: false, yAxisIndex: 1, data: series.map(p => p.load1), color: '#ff9f0a' }
    ]
  })
  // 内存 / 磁盘占用率
  renderDual(memRef.value, memChart, (c) => { memChart = c }, {
    title: '内存 / 磁盘',
    x: times,
    series: [
      { name: '内存 %', type: 'line', smooth: true, showSymbol: false, yAxisIndex: 0, data: series.map(p => p.mem), color: '#34c759' },
      { name: '磁盘 %', type: 'line', smooth: true, showSymbol: false, yAxisIndex: 0, data: series.map(p => p.disk), color: '#ff3b30' }
    ]
  })
  // 网络流量（速度单位）
  renderSpeed(netRef.value, netChart, (c) => { netChart = c }, {
    title: '网络流量',
    x: times,
    series: [
      { name: '上传', data: series.map(p => p.net_up), color: '#0a84ff' },
      { name: '下载', data: series.map(p => p.net_down), color: '#34c759' }
    ]
  })
  // 磁盘 IO
  renderSpeed(ioRef.value, ioChart, (c) => { ioChart = c }, {
    title: '磁盘 IO',
    x: times,
    series: [
      { name: '读取', data: series.map(p => p.disk_read), color: '#0a84ff' },
      { name: '写入', data: series.map(p => p.disk_write), color: '#ff9f0a' }
    ]
  })
}

// 双指标折线图（可独立 Y 轴）
function renderDual(el, chart, setChart, { x, series }) {
  if (!el) return
  if (!chart) chart = echarts.init(el, null, { renderer: 'canvas' })
  setChart(chart)
  const uses2 = series.some(s => s.yAxisIndex === 1)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: uses2 ? 48 : 16, top: 28, bottom: 24 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 10 }, boundaryGap: false },
    yAxis: uses2
      ? [
          { type: 'value', axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(155,181,216,0.35)' } } },
          { type: 'value', axisLabel: { fontSize: 10 }, splitLine: { show: false } }
        ]
      : { type: 'value', axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(155,181,216,0.35)' } } },
    series: series.map(s => ({
      name: s.name, type: 'line', smooth: true, showSymbol: false,
      yAxisIndex: s.yAxisIndex || 0, data: s.data,
      lineStyle: { width: 1.5, color: s.color },
      itemStyle: { color: s.color },
      areaStyle: s.yAxisIndex === 1 ? undefined : { opacity: 0.15, color: s.color }
    }))
  }, true)
}

// 速率面积图（网络/磁盘 IO）
function renderSpeed(el, chart, setChart, { x, series }) {
  if (!el) return
  if (!chart) chart = echarts.init(el, null, { renderer: 'canvas' })
  setChart(chart)
  chart.setOption({
    tooltip: {
      trigger: 'axis',
      formatter: (params) => params.map(p => `${p.marker}${p.seriesName}: ${formatSpeed(p.value)}`).join('<br/>')
    },
    legend: { top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 64, right: 16, top: 28, bottom: 24 },
    xAxis: { type: 'category', data: x, axisLabel: { fontSize: 10 }, boundaryGap: false },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: v => formatSpeed(v) }, splitLine: { lineStyle: { color: 'rgba(155,181,216,0.35)' } } },
    series: series.map(s => ({
      name: s.name, type: 'line', smooth: true, showSymbol: false, data: s.data,
      lineStyle: { width: 1.5, color: s.color },
      itemStyle: { color: s.color },
      areaStyle: { opacity: 0.2, color: s.color }
    }))
  }, true)
}

// 自动刷新
function startTimer() {
  stopTimer()
  if (autoRefresh.value) timer = setInterval(load, 30000)
}
function stopTimer() {
  if (timer) { clearInterval(timer); timer = null }
}

// 清空历史（管理员操作）
async function doClear() {
  if (!window.confirm('确定清空全部历史监控数据吗？此操作不可恢复。')) return
  busy.value = true
  try {
    await systemApi.metricsClear()
    await load()
  } catch (e) {
    err.value = e.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

function onResize() {
  ;[cpuChart, memChart, netChart, ioChart].forEach(c => c && c.resize())
}

onMounted(async () => {
  await load()
  window.addEventListener('resize', onResize)
  startTimer()
})

onUnmounted(() => {
  stopTimer()
  window.removeEventListener('resize', onResize)
  ;[cpuChart, memChart, netChart, ioChart].forEach(c => c && c.dispose())
  cpuChart = memChart = netChart = ioChart = null
})
</script>

<style scoped>
.metrics-history-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; overflow: auto; }
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.toolbar-left { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.sel-label { font-size: 11px; color: #6b7280; }
.sel { padding: 4px 6px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12px; background: #fff; }
.auto { font-size: 12px; color: #4b5563; display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
.auto input { cursor: pointer; }
.status { font-size: 12px; color: #888; }
.status.err { color: #b91c1c; }

.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.chart-box { border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px; background: #fff; }
.chart-title { font-size: 12px; font-weight: 600; color: #1d1d1f; margin-bottom: 6px; }
.chart-body { height: 230px; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; font-size: 13px; line-height: 1.6; }
.btn { padding: 5px 10px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12px; display: inline-flex; align-items: center; gap: 5px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.danger { color: #b91c1c; border-color: #fca5a5; }
.btn.danger:hover:not(:disabled) { background: #fef2f2; }
</style>
