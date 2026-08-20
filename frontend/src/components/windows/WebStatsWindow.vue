<template>
  <div class="webstats-window">
    <!-- 工具栏：日志路径 / 统计天数 / 域名过滤 / 刷新 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <label class="sel-label">日志</label>
        <select v-model="logPath" class="sel">
          <option value="">自动探测</option>
          <option v-for="p in logFiles" :key="p" :value="p">{{ p }}</option>
        </select>
        <label class="sel-label">统计范围</label>
        <select v-model="days" class="sel" @change="load">
          <option :value="1">近 1 天</option>
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
          <option :value="90">近 90 天</option>
        </select>
        <input
          v-model.trim="domain"
          class="domain-input"
          placeholder="按域名过滤（可选）"
          @keyup.enter="load"
        />
        <button class="btn" :disabled="loading" @click="load">
          <RefreshCw :size="13" /> 分析
        </button>
      </div>
      <span v-if="loading" class="status">解析中…</span>
      <span v-else-if="err" class="status err">{{ err }}</span>
    </div>

    <div v-if="err && !stats" class="empty">
      <BarChart3 :size="40" style="color:#9ca3af;" />
      <div>未获取到统计数据。请确认日志路径或启动 nginx 访问日志。</div>
    </div>

    <template v-else-if="stats">
      <!-- 总览卡片 -->
      <div class="cards">
        <div class="card"><div class="num">{{ fmt(stats.pv) }}</div><div class="label">总请求 (PV)</div></div>
        <div class="card"><div class="num">{{ fmt(stats.page_pv) }}</div><div class="label">页面请求</div></div>
        <div class="card"><div class="num">{{ fmt(stats.uv) }}</div><div class="label">独立访客 (UV)</div></div>
        <div class="card"><div class="num">{{ fmt(stats.ip_count) }}</div><div class="label">独立 IP</div></div>
        <div class="card"><div class="num">{{ stats.bots }}</div><div class="label">疑似爬虫</div></div>
        <div class="card"><div class="num">{{ stats.lines }}</div><div class="label">日志行数</div></div>
      </div>
      <div v-if="stats.truncated" class="hint-warn">日志过大已截断尾部分析，结果可能不完整</div>

      <!-- 图表区 -->
      <div class="charts">
        <div class="chart-box">
          <div class="chart-title">PV / UV 走势</div>
          <div ref="trendRef" class="chart-body"></div>
        </div>
        <div class="chart-box">
          <div class="chart-title">状态码分布</div>
          <div ref="statusRef" class="chart-body"></div>
        </div>
        <div class="chart-box">
          <div class="chart-title">热门页面 Top 10</div>
          <div ref="pagesRef" class="chart-body"></div>
        </div>
        <div class="chart-box">
          <div class="chart-title">热门 IP Top 10</div>
          <div ref="ipsRef" class="chart-body"></div>
        </div>
        <div class="chart-box">
          <div class="chart-title">来源站点 Top 10</div>
          <div ref="refererRef" class="chart-body"></div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { BarChart3, RefreshCw } from 'lucide-vue-next'
import { webstatsApi } from '../../api'

const logFiles = ref([])
const logPath = ref('')
const days = ref(7)
const domain = ref('')
const loading = ref(false)
const err = ref('')
const stats = ref(null)

// 各图表容器与实例（内存释放：统一 dispose）
const trendRef = ref(null)
const statusRef = ref(null)
const pagesRef = ref(null)
const ipsRef = ref(null)
const refererRef = ref(null)
let trendChart = null
let statusChart = null
let pagesChart = null
let ipsChart = null
let refererChart = null

// 千分位格式化
function fmt(n) {
  if (n == null) return '0'
  return Number(n).toLocaleString()
}

async function loadLogs() {
  try {
    const r = await webstatsApi.logs()
    logFiles.value = (r && r.logs) || []
  } catch (e) {
    // 日志探测失败不阻断：仍可手动输入路径
    logFiles.value = []
  }
}

async function load() {
  loading.value = true
  err.value = ''
  try {
    const r = await webstatsApi.analyze({
      log_path: logPath.value,
      days: days.value,
      domain: domain.value
    })
    stats.value = r
    renderCharts()
  } catch (e) {
    err.value = e.response?.data?.detail || e.message
    stats.value = null
  } finally {
    loading.value = false
  }
}

// 渲染全部图表
function renderCharts() {
  renderTrend()
  renderStatus()
  pagesChart = renderBar(pagesRef.value, pagesChart, stats.value.top_pages.map(x => x.path), stats.value.top_pages.map(x => x.count))
  ipsChart = renderBar(ipsRef.value, ipsChart, stats.value.top_ips.map(x => x.ip), stats.value.top_ips.map(x => x.count))
  refererChart = renderBar(refererRef.value, refererChart, stats.value.referers.map(x => x.host), stats.value.referers.map(x => x.count))
}

// PV/UV 折线
function renderTrend() {
  const el = trendRef.value
  if (!el) return
  if (!trendChart) trendChart = echarts.init(el, null, { renderer: 'canvas' })
  const daily = stats.value.daily || []
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['PV', 'UV'], top: 0 },
    grid: { left: 48, right: 16, top: 28, bottom: 24 },
    xAxis: { type: 'category', data: daily.map(d => d.date), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    series: [
      { name: 'PV', type: 'line', smooth: true, data: daily.map(d => d.pv), itemStyle: { color: '#0a84ff' } },
      { name: 'UV', type: 'line', smooth: true, data: daily.map(d => d.uv), itemStyle: { color: '#34c759' } }
    ]
  }, true)
}

// 状态码饼图
function renderStatus() {
  const el = statusRef.value
  if (!el) return
  if (!statusChart) statusChart = echarts.init(el, null, { renderer: 'canvas' })
  const st = stats.value.status || {}
  const entries = Object.entries(st)
  statusChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', left: 'left', top: 'middle', textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['45%', '70%'], center: ['58%', '50%'],
      label: { show: false },
      data: entries.map(([k, v]) => ({ name: k, value: v }))
    }]
  }, true)
}

// 横向条形图（热门页面/IP/来源）；返回图表实例供外部管理生命周期
function renderBar(el, chart, labels, values) {
  if (!el) return null
  if (!chart) chart = echarts.init(el, null, { renderer: 'canvas' })
  const data = (labels || []).map((l, i) => ({ name: String(l), value: values[i] })).slice(0, 10)
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 48, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    yAxis: {
      type: 'category',
      data: data.map(d => d.name).reverse(),
      axisLabel: { fontSize: 10, width: 120, overflow: 'truncate' }
    },
    series: [{ type: 'bar', data: data.map(d => d.value).reverse(), itemStyle: { color: '#0a84ff' }, barMaxWidth: 16 }]
  }, true)
  return chart
}

function onResize() {
  ;[trendChart, statusChart, pagesChart, ipsChart, refererChart].forEach(c => c && c.resize())
}

onMounted(async () => {
  await loadLogs()
  await load()
  window.addEventListener('resize', onResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  ;[trendChart, statusChart, pagesChart, ipsChart, refererChart].forEach(c => c && c.dispose())
  trendChart = statusChart = pagesChart = ipsChart = refererChart = null
})
</script>

<style scoped>
.webstats-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; overflow: auto; }
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.toolbar-left { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.sel-label { font-size: 11px; color: #6b7280; }
.sel { padding: 4px 6px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12px; background: #fff; }
.domain-input { padding: 4px 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12px; width: 170px; }
.status { font-size: 12px; color: #888; }
.status.err { color: #b91c1c; }

.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px; margin-bottom: 12px; }
.card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 14px; background: #fff; }
.card .num { font-size: 22px; font-weight: 700; color: #111827; }
.card .label { font-size: 11px; color: #6b7280; margin-top: 2px; }
.hint-warn { font-size: 12px; color: #b45309; background: #fef3c7; border: 1px solid #fde68a; border-radius: 8px; padding: 6px 10px; margin-bottom: 10px; }

.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.chart-box { border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px; background: #fff; }
.chart-title { font-size: 12px; font-weight: 600; color: #1d1d1f; margin-bottom: 6px; }
.chart-body { height: 220px; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.btn { padding: 5px 10px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12px; display: inline-flex; align-items: center; gap: 5px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
