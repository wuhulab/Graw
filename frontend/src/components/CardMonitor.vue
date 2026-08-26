<!--
  CardMonitor.vue — 实时监控曲线卡片
  作用：在桌面右侧展示系统实时曲线，可在「流量」与「磁盘IO」两个标签页间切换，
        分别绘制发送/接收（或读取/写入）双序列面积图。
  数据：由父组件传入的 metrics 响应式对象驱动（后端 WebSocket 推送刷新）。
  打开方式：作为 Desktop 桌面卡片之一渲染。
-->
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
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'   // Vue 核心 API
import * as echarts from 'echarts'                              // ECharts 图表库

const props = defineProps({ metrics: Object })   // 父级传入的实时指标对象
const mode = ref('net')                          // 当前标签页：net=流量 / disk=磁盘IO
const chartRef = ref(null)                       // 图表挂载容器
let chart = null                                 // ECharts 实例

const maxPoints = 60                             // 仅保留最近 60 个采样点（滚动窗口）
const data1 = []                                 // 序列一缓冲（流量=发送 / 磁盘=读取）
const data2 = []                                 // 序列二缓冲（流量=接收 / 磁盘=写入）
const times = []                                 // 时间轴缓冲

// 追加一个采样点，超出窗口则丢弃最旧点
function pushData(t, v1, v2) {
  times.push(t)
  data1.push(v1)
  data2.push(v2)
  if (times.length > maxPoints) {
    times.shift(); data1.shift(); data2.shift()
  }
}

// --- 图表初始化 ---
function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value, null, { renderer: 'canvas' })
  chart.setOption({
    grid: { left: 40, right: 20, top: 10, bottom: 20 },
    xAxis: { type: 'category', data: times, axisLine: { lineStyle: { color: '#fff' } }, axisLabel: { color: 'rgba(255,255,255,0.7)', fontSize: 10 } },
    // Y 轴按字节速率格式化（如 1.2M/s）
    yAxis: { type: 'value', splitLine: { lineStyle: { color: 'rgba(255,255,255,0.15)' } }, axisLabel: { color: 'rgba(255,255,255,0.7)', fontSize: 10, formatter: v => formatBytes(v) + '/s' } },
    tooltip: { trigger: 'axis' },
    series: [
      { name: '发送/读取', type: 'line', smooth: true, showSymbol: false, data: data1, lineStyle: { color: '#5cb85c', width: 2 }, areaStyle: { color: 'rgba(92,184,92,0.15)' } },
      { name: '接收/写入', type: 'line', smooth: true, showSymbol: false, data: data2, lineStyle: { color: '#5bc0de', width: 2 }, areaStyle: { color: 'rgba(91,192,222,0.15)' } }
    ]
  })
}

function formatBytes(b) {
  if (b > 1e9) return (b/1e9).toFixed(1) + 'G'   // 1e9 字节 ≈ 1 GB
  if (b > 1e6) return (b/1e6).toFixed(1) + 'M'   // 1e6 ≈ 1 MB
  if (b > 1e3) return (b/1e3).toFixed(1) + 'K'   // 1e3 ≈ 1 KB
  return b + 'B'
}

// 指标变化即追加采样点并重绘（net=发送/接收，disk=读取/写入）
watch(() => props.metrics, (m) => {
  if (!chart) return
  const t = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  if (mode.value === 'net') {
    pushData(t, m.netSent, m.netRecv)             // 流量：发送 / 接收
  } else {
    pushData(t, m.dioRead, m.dioWrite)            // 磁盘IO：读取 / 写入
  }
  chart.setOption({
    xAxis: { data: times },
    series: [
      { name: mode.value === 'net' ? '发送' : '读取', data: [...data1] },
      { name: mode.value === 'net' ? '接收' : '写入', data: [...data2] }
    ]
  })
}, { deep: true })

// 切换标签时清空缓冲，避免流量与磁盘IO 数据混在同一张图
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
