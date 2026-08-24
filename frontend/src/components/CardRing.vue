<template>
  <div class="glass-card ring-card">
    <div class="ring-title">{{ title }}</div>
    <div ref="chartRef" class="ring-chart"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ title: String, metric: String, data: Number })
const chartRef = ref(null)
let chart = null

function getColor(v) {
  if (v < 60) return '#5cb85c'
  if (v < 85) return '#f0ad4e'
  return '#d9534f'
}

function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value, null, { renderer: 'canvas' })
  chart.setOption({
    series: [{
      type: 'gauge',
      startAngle: 90,
      endAngle: -270,
      pointer: { show: false },
      progress: {
        show: true,
        overlap: false,
        roundCap: true,
        clip: false,
        itemStyle: { color: getColor(props.data || 0) }
      },
      axisLine: { lineStyle: { width: 10, color: [[1, 'rgba(255,255,255,0.2)']] } },
      splitLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: false },
      data: [{ value: props.data || 0 }],
      detail: {
        width: 40,
        height: 14,
        fontSize: 16,
        color: '#fff',
        formatter: '{value}%',
        offsetCenter: [0, 0]
      }
    }]
  })
}

watch(() => props.data, (v) => {
  if (!chart) return
  chart.setOption({
    series: [{
      data: [{ value: Math.round(v || 0) }],
      progress: { itemStyle: { color: getColor(v || 0) } }
    }]
  })
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
.ring-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-width: 0;
}
.ring-title {
  font-size: 13px;
  margin-bottom: 4px;
  text-shadow: 0 1px 2px rgba(0,0,0,0.4);
}
.ring-chart {
  width: 100%;
  height: calc(100% - 20px);
}
</style>
