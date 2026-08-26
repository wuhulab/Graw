<!--
  MonitorCard.vue — 实时流量 / 磁盘IO 监控卡片（桌面版）
  作用：桌面卡片之一，用 ECharts 面积图绘制最近约 30 秒的上传 / 下载（或读取 / 写入）
        速率曲线，可在「流量」与「磁盘IO」两个标签页间切换，切换时清空采样缓冲。
  数据：采样点由共享 systemState.network / systemState.diskio 每次 WS 推送追加，
        最多保留 MAX_POINTS(15) 个点（约 30s 滚动窗口）；节点不可达时由 MetricsFallback 提示。
  打开方式：作为桌面卡片渲染。
-->
<template>
  <div class="win7-card" style="display:flex; flex-direction:column;">
    <div class="card-title">
      <span>{{ $t('cards.realtimeMonitor') }}</span>
      <div class="tabs">
        <button :class="{ active: mode === 'net' }" @click="mode = 'net'">{{ $t('cards.traffic') }}</button>
        <button :class="{ active: mode === 'disk' }" @click="mode = 'disk'">{{ $t('cards.diskIO') }}</button>
      </div>
    </div>
    <div class="chart-area">
      <v-chart class="chart" :option="option" autoresize />
    </div>
    <!-- 当前管理节点不可达/数据过期时的降级提示 -->
    <MetricsFallback />
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'   // Vue 响应式 / 计算属性 / 侦听
import { use } from 'echarts/core'           // ECharts 按需注册
import { CanvasRenderer } from 'echarts/renderers'   // Canvas 渲染器
import { LineChart } from 'echarts/charts'           // 折线图
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'   // 网格 / 提示 / 图例组件
import VChart from 'vue-echarts'             // ECharts 的 Vue 封装
import { formatSpeed } from '../../api'       // 字节速率格式化（如 1.2M/s）
import { systemState } from '../../store/systemMetrics'   // 共享系统指标状态
import MetricsFallback from './MetricsFallback.vue'       // 监控数据降级提示

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])   // 注册所需 ECharts 模块

const mode = ref('net')   // 当前标签页：net=流量 / disk=磁盘IO
// 时间监控跨度：指标约每 2s 推送一个采样点，15 个点 ≈ 最近 30s
const MAX_POINTS = 15

const netSeries = ref({ up: [], down: [], times: [] })     // 流量采样缓冲（上传 / 下载 / 时间轴）
const diskSeries = ref({ read: [], write: [], times: [] }) // 磁盘IO 采样缓冲（读取 / 写入 / 时间轴）

// 由共享「单条 WS」指标推送驱动（见 store/systemMetrics.js）。
// systemState.network / systemState.diskio 每次推送即新增一个采样点，
// 取代原先各自 2s 的 HTTP 轮询。
watch(
  () => ({
    up: systemState.network.upload,
    down: systemState.network.download,
    read: systemState.diskio.read,
    write: systemState.diskio.write,
  }),
  (v) => {
    const t = new Date().toLocaleTimeString().slice(0, 8)
    netSeries.value.times.push(t)
    netSeries.value.up.push(v.up)
    netSeries.value.down.push(v.down)
    diskSeries.value.times.push(t)
    diskSeries.value.read.push(v.read)
    diskSeries.value.write.push(v.write)
    if (netSeries.value.times.length > MAX_POINTS) {
      netSeries.value.times.shift()
      netSeries.value.up.shift()
      netSeries.value.down.shift()
      diskSeries.value.times.shift()
      diskSeries.value.read.shift()
      diskSeries.value.write.shift()
    }
  }
)

watch(mode, () => {
  resetSeries()
})

// 切换网卡/磁盘标签页时清空采样缓冲，避免新旧数据混在同一张图上。
// 注：不再在页面切回前台时清空——后台期间共享指标流（store/systemMetrics.js）
// 只累积「最新一帧」，回前台后仅追加一个采样点继续渲染；若清空会令图表
// 每次从零重新开始，观感突兀。
function resetSeries() {
  netSeries.value.times.length = 0
  netSeries.value.up.length = 0
  netSeries.value.down.length = 0
  diskSeries.value.times.length = 0
  diskSeries.value.read.length = 0
  diskSeries.value.write.length = 0
}

// --- 图表配置（按当前标签页组装数据与系列名） ---
const option = computed(() => {
  const isNet = mode.value === 'net'
  const s = isNet ? netSeries.value : diskSeries.value
  const a = isNet ? s.up : s.read
  const b = isNet ? s.down : s.write
  const nameA = isNet ? '上传' : '读取'
  const nameB = isNet ? '下载' : '写入'
  return {
    grid: { left: 50, right: 12, top: 24, bottom: 22 },
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        return params.map(p => `${p.marker}${p.seriesName}: ${formatSpeed(p.value)}`).join('<br/>')
      }
    },
    legend: { top: 0, right: 8, textStyle: { fontSize: 10 }, itemHeight: 8, itemWidth: 12 },
    xAxis: {
      type: 'category',
      data: s.times,
      axisLabel: { fontSize: 9, color: '#0a3d7a' },
      axisLine: { lineStyle: { color: '#9bb5d8' } }
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        fontSize: 9,
        color: '#0a3d7a',
        formatter: v => formatSpeed(v)
      },
      splitLine: { lineStyle: { color: 'rgba(155,181,216,0.4)' } }
    },
    series: [
      {
        name: nameA, type: 'line', smooth: true, showSymbol: false,
        data: a, areaStyle: { opacity: 0.3, color: '#409eff' },
        lineStyle: { width: 1.5, color: '#409eff' }, itemStyle: { color: '#409eff' }
      },
      {
        name: nameB, type: 'line', smooth: true, showSymbol: false,
        data: b, areaStyle: { opacity: 0.3, color: '#67c23a' },
        lineStyle: { width: 1.5, color: '#67c23a' }, itemStyle: { color: '#67c23a' }
      }
    ]
  }
})
</script>

<style scoped>
.chart { width: 100%; height: 100%; min-height: 80px; }
</style>
