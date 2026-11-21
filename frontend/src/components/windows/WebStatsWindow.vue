<!--
  网站统计窗口（Web Stats）

  这个窗口做什么：
    面板「网站统计」功能。直接解析服务器上的 nginx 访问日志，
    统计 PV / UV / 独立 IP / 疑似爬虫数，并用 ECharts 绘制：
      - PV / UV 走势折线；
      - 状态码分布饼图；
      - 热门页面 / 热门 IP / 来源站点 Top10 横向条形图。
    工具栏可选日志路径（留空自动探测）、统计天数、按域名过滤。
    日志过大时后端会截断尾部并返回 truncated 标记，界面给出提示。

  用到的后端模块：
    /api/webstats/*（管理员权限）——logs 探测可用日志路径、
    analyze 解析统计（传 log_path / days / domain）。

  关键状态：
    logFiles / logPath   可用日志路径与当前选择
    days / domain        统计天数与域名过滤
    stats                后端返回的统计结果（卡片 + 图表数据源）
    trendChart 等五个 ECharts 实例   各图表实例（卸载时统一 dispose）

  怎么被打开：
    桌面「网站统计」应用。
-->
<template>
  <div class="webstats-window">
    <!-- 工具栏：日志路径 / 统计天数 / 域名过滤 / 刷新 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <div class="mode-tabs">
          <button class="mode-tab" :class="{ active: mode === 'overview' }" @click="switchMode('overview')">概览</button>
          <button class="mode-tab" :class="{ active: mode === 'realtime' }" @click="switchMode('realtime')">实时</button>
          <button class="mode-tab" :class="{ active: mode === 'requests' }" @click="switchMode('requests')">请求明细</button>
          <button class="mode-tab" :class="{ active: mode === 'visitors' }" @click="switchMode('visitors')">访客分布</button>
        </div>
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
      <BarChart3 :size="40" style="color:var(--g-text-muted);" />
      <div>未获取到统计数据。请确认日志路径或启动 nginx 访问日志。</div>
    </div>

    <template v-else-if="mode === 'overview' && stats">
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

      <!-- 站点汇总 -->
      <div v-if="siteSummary.length" class="chart-box" style="margin-top:12px;">
        <div class="chart-title">站点汇总</div>
        <table class="mini-table">
          <thead><tr><th>站点</th><th>日志</th><th>请求</th><th>独立 IP</th><th>流量</th><th>错误率</th></tr></thead>
          <tbody>
            <tr v-for="s in siteSummary" :key="s.file">
              <td>{{ s.site }}</td><td class="mono">{{ s.file }}</td>
              <td>{{ fmt(s.pv) }}</td><td>{{ fmt(s.uv) }}</td>
              <td>{{ fmtBytes(s.bytes) }}</td><td>{{ (s.error_rate || 0).toFixed(2) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- 实时请求 -->
    <div v-else-if="mode === 'realtime'" class="panel">
      <div class="rt-stats">
        <span>{{ rtFile || '自动探测' }}</span>
        <span>请求/秒 <b>{{ realtime.rps.toFixed(2) }}</b></span>
        <span>流量/秒 <b>{{ fmtBytes(realtime.bytes_per_sec) }}/s</b></span>
        <span>窗口 {{ realtime.window_seconds }}s · 每 3 秒刷新</span>
      </div>
      <table class="mini-table">
        <thead><tr><th>时间</th><th>IP</th><th>方法</th><th>路径</th><th>状态</th><th>大小</th></tr></thead>
        <tbody>
          <tr v-for="(e, i) in realtime.entries" :key="i">
            <td class="mono">{{ e.time }}</td><td class="mono">{{ e.ip }}</td><td>{{ e.method }}</td>
            <td class="mono path-cell">{{ e.path }}</td>
            <td :class="{ bad: e.status >= 400 }">{{ e.status }}</td><td>{{ fmtBytes(e.bytes) }}</td>
          </tr>
          <tr v-if="!realtime.entries.length"><td colspan="6" class="muted">暂无请求</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 请求明细 -->
    <div v-else-if="mode === 'requests'" class="panel">
      <div class="filters">
        <input v-model.trim="reqQuery" class="sel" placeholder="关键词：IP / 路径 / UA" @keyup.enter="loadRequests" />
        <select v-model="reqMethod" class="sel">
          <option value="">全部方法</option>
          <option v-for="m in ['GET','POST','PUT','DELETE','HEAD','OPTIONS']" :key="m" :value="m">{{ m }}</option>
        </select>
        <input v-model.trim="reqStatus" class="sel" placeholder="状态码（如 404）" @keyup.enter="loadRequests" />
        <button class="btn" @click="loadRequests">查询</button>
        <span class="muted">{{ requestEntries.length }} 条</span>
      </div>
      <table class="mini-table">
        <thead><tr><th>时间</th><th>IP</th><th>方法</th><th>路径</th><th>状态</th><th>大小</th><th>UA</th></tr></thead>
        <tbody>
          <tr v-for="(e, i) in requestEntries" :key="i">
            <td class="mono">{{ e.time }}</td><td class="mono">{{ e.ip }}</td><td>{{ e.method }}</td>
            <td class="mono path-cell">{{ e.path }}</td>
            <td :class="{ bad: e.status >= 400 }">{{ e.status }}</td><td>{{ fmtBytes(e.bytes) }}</td>
            <td class="ua-cell" :title="e.ua">{{ e.ua }}</td>
          </tr>
          <tr v-if="!requestEntries.length"><td colspan="7" class="muted">没有匹配的请求</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 访客分布 -->
    <div v-else-if="mode === 'visitors'" class="panel">
      <div class="charts" style="grid-template-columns: 1fr 1fr;">
        <div class="chart-box">
          <div class="chart-title">地区分布</div>
          <div ref="visitorRef" class="chart-body"></div>
        </div>
        <div class="chart-box">
          <div class="chart-title">访客 Top IP</div>
          <table class="mini-table">
            <thead><tr><th>IP</th><th>地区</th><th>请求</th></tr></thead>
            <tbody>
              <tr v-for="v in visitorData.top_ips" :key="v.ip">
                <td class="mono">{{ v.ip }}</td><td>{{ v.region }}</td><td>{{ fmt(v.count) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="hint-warn" v-if="!visitorData.has_geoip" style="margin-top:10px;">
        未配置 GeoIP 地区映射（data/geoip_countries.json），当前只能区分内网与未知地区。
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'   // 响应式状态、图表生命周期钩子
import * as echarts from 'echarts'   // ECharts 图表库
import { BarChart3, RefreshCw } from 'lucide-vue-next'   // 空状态 / 刷新图标
import { webstatsApi } from '../../api'   // 网站统计后端能力：/api/webstats/* 的封装

const logFiles = ref([])   // 后端探测到的可用日志路径列表
const logPath = ref('')    // 当前选中的日志路径（空 = 自动探测）
const days = ref(7)        // 统计天数范围
const domain = ref('')     // 按域名过滤（空 = 不过滤）
const loading = ref(false) // 分析请求进行中
const err = ref('')        // 分析失败提示
const stats = ref(null)    // 统计结果（卡片与图表的数据源）
const mode = ref('overview')          // overview | realtime | requests | visitors
const siteSummary = ref([])           // 站点汇总
const realtime = ref({ rps: 0, bytes_per_sec: 0, window_seconds: 60, entries: [] })
const rtFile = ref('')
const requestEntries = ref([])
const reqQuery = ref('')
const reqMethod = ref('')
const reqStatus = ref('')
const visitorData = ref({ regions: [], top_ips: [], has_geoip: false })
const visitorRef = ref(null)
let visitorChart = null
let rtTimer = null

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
  return Number(n).toLocaleString()   // 大数字加千分位，如 12345 → 12,345
}

function fmtBytes(n) {
  const v = Number(n) || 0
  if (v >= 1024 * 1024 * 1024) return (v / 1024 / 1024 / 1024).toFixed(2) + ' GB'
  if (v >= 1024 * 1024) return (v / 1024 / 1024).toFixed(2) + ' MB'
  if (v >= 1024) return (v / 1024).toFixed(1) + ' KB'
  return v + ' B'
}

// 切换页签：离开实时页时停掉轮询
function switchMode(m) {
  mode.value = m
  if (rtTimer) { clearInterval(rtTimer); rtTimer = null }
  if (m === 'overview' && !stats.value) load()
  else if (m === 'overview') loadSummary()
  else if (m === 'realtime') { loadRealtime(); rtTimer = setInterval(loadRealtime, 3000) }
  else if (m === 'requests') loadRequests()
  else if (m === 'visitors') loadVisitors()
}

async function loadSummary() {
  try {
    const r = await webstatsApi.summary()
    siteSummary.value = r.items || []
  } catch { siteSummary.value = [] }
}

async function loadRealtime() {
  try {
    const r = await webstatsApi.realtime({ log_path: logPath.value, limit: 50 })
    realtime.value = { rps: r.rps || 0, bytes_per_sec: r.bytes_per_sec || 0, window_seconds: r.window_seconds || 60, entries: r.entries || [] }
    rtFile.value = r.file || ''
  } catch (e) {
    err.value = e.response?.data?.detail || e.message
  }
}

async function loadRequests() {
  try {
    const r = await webstatsApi.requests({
      log_path: logPath.value, q: reqQuery.value, method: reqMethod.value, status: reqStatus.value, limit: 300
    })
    requestEntries.value = r.entries || []
  } catch (e) {
    err.value = e.response?.data?.detail || e.message
  }
}

async function loadVisitors() {
  try {
    const r = await webstatsApi.visitors({ log_path: logPath.value, limit: 20 })
    visitorData.value = r
    await nextTick()
    renderVisitors()
  } catch (e) {
    err.value = e.response?.data?.detail || e.message
  }
}

function renderVisitors() {
  const el = visitorRef.value
  if (!el) return
  if (!visitorChart) visitorChart = echarts.init(el, null, { renderer: 'canvas' })
  const regions = visitorData.value.regions || []
  visitorChart.setOption({
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { orient: 'vertical', left: 'left', top: 'middle', textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['45%', '70%'], center: ['60%', '50%'],
      label: { show: false },
      data: regions.map(r => ({ name: r.region, value: r.count }))
    }]
  }, true)
}

// --- 探测可用的 nginx 日志路径（供下拉选择） ---
async function loadLogs() {
  try {
    const r = await webstatsApi.logs()
    logFiles.value = (r && r.logs) || []
  } catch (e) {
    // 日志探测失败不阻断：仍可手动输入路径
    logFiles.value = []
  }
}

// --- 触发分析：拿回统计结果后等 DOM 挂载图表容器再渲染 ---
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
    // 等待 DOM 渲染出图表容器（stats 所在 v-if 块）后再初始化 ECharts，
    // 否则 ref 尚未挂载、图表无从渲染
    await nextTick()
    renderCharts()
    loadSummary()
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
  const data = (labels || []).map((l, i) => ({ name: String(l), value: values[i] })).slice(0, 10)   // 只画 Top10
  chart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 48, top: 8, bottom: 8, containLabel: true },
    xAxis: { type: 'value', axisLabel: { fontSize: 10 } },
    yAxis: {
      type: 'category',
      data: data.map(d => d.name).reverse(),   // 倒序排列：ECharts 分类轴从下往上画，倒序让最高的一根落在顶部
      axisLabel: { fontSize: 10, width: 120, overflow: 'truncate' }
    },
    series: [{ type: 'bar', data: data.map(d => d.value).reverse(), itemStyle: { color: '#0a84ff' }, barMaxWidth: 16 }]
  }, true)
  return chart
}

// --- 窗口缩放时同步重绘所有图表，避免拉伸变形 ---
function onResize() {
  ;[trendChart, statusChart, pagesChart, ipsChart, refererChart, visitorChart].forEach(c => c && c.resize())
}

onMounted(async () => {
  await loadLogs()   // 先探测日志路径
  await load()       // 再做首次分析
  window.addEventListener('resize', onResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  if (rtTimer) { clearInterval(rtTimer); rtTimer = null }
  ;[trendChart, statusChart, pagesChart, ipsChart, refererChart, visitorChart].forEach(c => c && c.dispose())   // 释放 ECharts 资源
  trendChart = statusChart = pagesChart = ipsChart = refererChart = visitorChart = null
})
</script>

<style scoped>
.webstats-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; overflow: auto; }
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.toolbar-left { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.sel-label { font-size: 11px; color: var(--g-text-muted); }
.sel { padding: 4px 6px; border: 1px solid var(--g-border-strong); border-radius: 6px; font-size: 12px; background: var(--g-surface); }
.domain-input { padding: 4px 8px; border: 1px solid var(--g-border-strong); border-radius: 6px; font-size: 12px; width: 170px; }
.status { font-size: 12px; color: var(--g-text-muted); }
.status.err { color: #b91c1c; }

/* P1：页签 / 实时 / 明细 / 访客 */
.mode-tabs { display: inline-flex; border: 1px solid var(--g-border-strong); border-radius: 7px; overflow: hidden; }
.mode-tab { border: none; background: transparent; padding: 4px 9px; font-size: 12px; cursor: pointer; color: var(--g-text-muted); }
.mode-tab.active { background: var(--g-surface-muted); color: var(--g-text); font-weight: 600; }
.panel { flex: 1; min-height: 0; overflow: auto; }
.rt-stats { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px; color: var(--g-text-muted); margin-bottom: 8px; }
.rt-stats b { color: var(--g-text); }
.filters { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mini-table th, .mini-table td { border-bottom: 1px solid var(--g-border); padding: 5px 6px; text-align: left; }
.mini-table th { color: var(--g-text-muted); font-weight: 600; position: sticky; top: 0; background: var(--g-surface); }
.mini-table td.bad { color: #b91c1c; }
.path-cell { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ua-cell { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--g-text-muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.muted { color: var(--g-text-muted); }

.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px; margin-bottom: 12px; }
.card { border: 1px solid var(--g-border); border-radius: 10px; padding: 12px 14px; background: var(--g-surface); }
.card .num { font-size: 22px; font-weight: 700; color: var(--g-text); }
.card .label { font-size: 11px; color: var(--g-text-muted); margin-top: 2px; }
.hint-warn { font-size: 12px; color: #b45309; background: #fef3c7; border: 1px solid #fde68a; border-radius: 8px; padding: 6px 10px; margin-bottom: 10px; }

.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.chart-box { border: 1px solid var(--g-border); border-radius: 10px; padding: 10px; background: var(--g-surface); }
.chart-title { font-size: 12px; font-weight: 600; color: var(--g-text); margin-bottom: 6px; }
.chart-body { height: 220px; }

.empty { text-align: center; color: var(--g-text-muted); padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.btn { padding: 5px 10px; border: 1px solid var(--g-border-strong); background: var(--g-surface); border-radius: 6px; cursor: pointer; font-size: 12px; display: inline-flex; align-items: center; gap: 5px; }
.btn:hover:not(:disabled) { background: var(--g-surface-muted); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
