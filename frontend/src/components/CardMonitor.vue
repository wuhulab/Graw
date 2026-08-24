<template>
  <div class="glass-card monitor-card">
    <div class="monitor-header">
      <span class="monitor-title">监控</span>
      <div class="monitor-tabs">
        <span :class="{ active: mode === 'net' }" @click="mode = 'net'">流量</span>
        <span :class="{ active: mode === 'disk' }" @click="mode = 'disk'">磁盘IO</span>
      </div>
    </div>
    <div ref="chartRef" class="monitor-chart"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ metrics: Object })
const mode = ref('net')
const chartRef = ref(null)
let chart = null

const maxPoints = 60
const data1 = []
const data2 = []
const times = []

function pushData(t, v1, v2) {
  times.push(t)
  data1.push(v1)
  data2.push(v2)
  if (times.length > maxPoints) {
    times.shift(); data1.shift(); data2.shift()
  }
}

function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value, null, { renderer: 'canvas' })
  chart.setOption({
    grid: { left: 40, right: 20, top: 10, bottom: 20 },
    xAxis: { type: 'category', data: times, axisLine: { lineStyle: { color: '#fff' } }, axisLabel: { color: 'rgba(255,255,255,0.7)', fontSize: 10 } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(255,255,255,0.15)' } }, axisLabel: { color: 'rgba(255,255,255,0.7)', fontSize: 10, formatter: v => formatBytes(v) + '/s' } },
    tooltip: { trigger: 'axis' },
    series: [
      { name: '发送/读取', type: 'line', smooth: true, showSymbol: false, data: data1, lineStyle: { color: '#5cb85c', width: 2 }, areaStyle: { color: 'rgba(92,184,92,0.15)' } },
      { name: '接收/写入', type: 'line', smooth: true, showSymbol: false, data: data2, lineStyle: { color: '#5bc0de', width: 2 }, areaStyle: { color: 'rgba(91,192,222,0.15)' } }
    ]
  })
}

function formatBytes(b) {
  if (b > 1e9) return (b/1e9).toFixed(1) + 'G'
  if (b > 1e6) return (b/1e6).toFixed(1) + 'M'
  if (b > 1e3) return (b/1e3).toFixed(1) + 'K'
  return b + 'B'
}

watch(() => props.metrics, (m) => {
  if (!chart) return
  const t = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  if (mode.value === 'net') {
    pushData(t, m.netSent, m.netRecv)
  } else {
    pushData(t, m.dioRead, m.dioWrite)
  }
  chart.setOption({
    xAxis: { data: times },
    series: [
      { name: mode.value === 'net' ? '发送' : '读取', data: [...data1] },
      { name: mode.value === 'net' ? '接收' : '写入', data: [...data2] }
    ]
  })
}, { deep: true })

watch(mode, () => {
  data1.length = 0; data2.length = 0; times.length = 0
  if (chart) chart.setOption({ xAxis: { data: [] }, series: [{ data: [] }, { data: [] }] })
})

onMounted(() => {
  initChart()
  window.addEventListener('resize', () => chart && chart.resize())
})
onBeforeUnmount(() => {
  if (chart) chart.dispose()
})
</script>

<style scoped>
.monitor-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.monitor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.monitor-title {
  font-size: 14px;
  text-shadow: 0 1px 2px rgba(0,0,0,0.4);
}
.monitor-tabs {
  display: flex;
  gap: 6px;
  background: rgba(0,0,0,0.15);
  border-radius: 4px;
  padding: 2px;
}
.monitor-tabs span {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 3px;
  cursor: pointer;
  color: rgba(255,255,255,0.8);
}
.monitor-tabs span.active {
  background: rgba(255,255,255,0.25);
  color: #fff;
}
.monitor-chart {
  flex: 1;
  min-height: 0;
}
</style>
