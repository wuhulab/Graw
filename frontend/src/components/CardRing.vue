<!--
  CardRing.vue — 环形百分比卡片
  作用：在桌面右侧展示单个系统指标的环形进度（负载 / CPU / 内存 / 存储），
        中心显示百分比，颜色随负载高低变化（绿 / 橙 / 红）。
  数据：由父组件传入的 data（0-100 数值）驱动，变化时实时更新。
  打开方式：Desktop 中 4 个 CardRing 分别绑定不同 metric。
-->
<template>
  <div class="glass-card ring-card">
    <div class="ring-title">{{ title }}</div>
    <div ref="chartRef" class="ring-chart"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'   // Vue 核心 API
import * as echarts from 'echarts'                              // ECharts 图表库

const props = defineProps({ title: String, metric: String, data: Number })  // 标题 / 指标键 / 百分比值
const chartRef = ref(null)                                       // 图表挂载容器
let chart = null                                                 // ECharts 实例

// 使用率配色：<60 绿、<85 橙、否则红
function getColor(v) {
  if (v < 60) return '#5cb85c'
  if (v < 85) return '#f0ad4e'
  return '#d9534f'
}

// --- 图表初始化 ---
function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value, null, { renderer: 'canvas' })
  chart.setOption({
    series: [{
      type: 'gauge',
      startAngle: 90,
      endAngle: -270,                 // 90°→-270° 即绘制整圈环形（非半圆）
      pointer: { show: false },       // 隐藏指针，用环形进度表示占比
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
        formatter: '{value}%',        // 中心显示百分比
        offsetCenter: [0, 0]
      }
    }]
  })
}

watch(() => props.data, (v) => {
  if (!chart) return
  chart.setOption({
    series: [{
      data: [{ value: Math.round(v || 0) }],        // 取整后更新占比
      progress: { itemStyle: { color: getColor(v || 0) } }   // 同步刷新告警配色
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
