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
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { formatSpeed } from '../../api'
import { systemState } from '../../store/systemMetrics'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

const mode = ref('net')
// 时间监控跨度：指标约每 2s 推送一个采样点，15 个点 ≈ 最近 30s
const MAX_POINTS = 15

const netSeries = ref({ up: [], down: [], times: [] })
const diskSeries = ref({ read: [], write: [], times: [] })

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

// 返回前台时清空采样缓冲，从当前时刻重新开始累积：
// 避免后台期间遗留的旧采样点与当前数据间形成一条「断裂斜线」或快速补点。
function resetSeries() {
  netSeries.value.times.length = 0
  netSeries.value.up.length = 0
  netSeries.value.down.length = 0
  diskSeries.value.times.length = 0
  diskSeries.value.read.length = 0
  diskSeries.value.write.length = 0
}

function onVisibilityChange() {
  if (document.visibilityState !== 'hidden') resetSeries()
}

onMounted(() => document.addEventListener('visibilitychange', onVisibilityChange))
onUnmounted(() => document.removeEventListener('visibilitychange', onVisibilityChange))

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
