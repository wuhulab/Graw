<!--
  RingCard.vue — 系统概览环形卡片（桌面版）
  作用：桌面卡片之一，用四个 ECharts 环形图展示负载 / CPU / 内存 / 存储使用率，
        中心显示百分比；使用率超过告警阈值（90%）时整环变红提醒。
  数据：overview prop 由父级传入（内含 load/cpu/memory/storage 各百分比）；
        主色与告警开关来自 uiState（可在「界面设置」中修改）。节点不可达时由 MetricsFallback 提示。
  打开方式：作为桌面卡片渲染。
-->
<template>
  <div class="win7-card">
    <div class="card-title">
      <span>{{ $t('cards.systemOverview') }}</span>
    </div>
    <div class="ring-row" style="height: calc(100% - 24px)">
      <div class="ring-cell">
        <v-chart class="ring-chart" :option="loadOption" autoresize />
        <div class="ring-label">{{ $t('cards.ring.load') }}</div>
      </div>
      <div class="ring-cell">
        <v-chart class="ring-chart" :option="cpuOption" autoresize />
        <div class="ring-label">{{ $t('cards.ring.cpu') }}</div>
      </div>
      <div class="ring-cell">
        <v-chart class="ring-chart" :option="memOption" autoresize />
        <div class="ring-label">{{ $t('cards.ring.memory') }}</div>
      </div>
      <div class="ring-cell">
        <v-chart class="ring-chart" :option="storageOption" autoresize />
        <div class="ring-label">{{ $t('cards.ring.storage') }}</div>
      </div>
    </div>
    <!-- 当前管理节点不可达/数据过期时的降级提示 -->
    <MetricsFallback />
  </div>
</template>

<script setup>
import { computed } from 'vue'   // Vue 计算属性
import { use } from 'echarts/core'   // ECharts 按需注册
import { CanvasRenderer } from 'echarts/renderers'   // Canvas 渲染器
import { PieChart } from 'echarts/charts'   // 饼图（环形图由饼图去中心化实现）
import { TitleComponent, TooltipComponent } from 'echarts/components'   // 标题 / 提示组件
import VChart from 'vue-echarts'   // ECharts 的 Vue 封装
import { uiState } from '../../store/ui'   // 界面设置（环形图主色与告警开关）
import MetricsFallback from './MetricsFallback.vue'   // 监控数据降级提示

use([CanvasRenderer, PieChart, TitleComponent, TooltipComponent])   // 注册所需 ECharts 模块

const props = defineProps({
  overview: { type: Object, required: true }
})

// 告警红线：使用率 >90% 时变身色（可在「界面设置」中修改颜色/开关）
const ALARM_THRESHOLD = 90
const ALARM_RED = '#f5222d'

// 计算环形图主色：优先「界面设置」中配置的统一颜色；启用告警且使用率超阈值时变红
function mainColor(percent) {
  if (uiState.ring_alarm && (percent || 0) > ALARM_THRESHOLD) return ALARM_RED
  return uiState.ring_color || '#409eff'
}

// 生成单个环形图配置：一段「已用」弧 + 一段「剩余」弧，中心显示百分比
function ringOption(percent, color) {
  const p = Math.max(0, Math.min(100, percent || 0))   // 百分比夹在 0-100，防止数据越界破坏图形
  return {
    series: [{
      type: 'pie',
      radius: ['62%', '85%'],
      avoidLabelOverlap: false,
      silent: true,
      label: {
        show: true,
        position: 'center',
        formatter: `${p.toFixed(0)}%`,
        fontSize: 14,
        fontWeight: 'bold',
        color: '#0a3d7a'
      },
      data: [
        { value: p, itemStyle: { color } },
        { value: 100 - p, itemStyle: { color: 'rgba(180,200,220,0.35)' } }
      ],
      animationDuration: 400
    }]
  }
}

const loadPercent = () => props.overview?.load?.percent   // 负载取内层 percent 字段，其余指标直接是百分比
const loadOption = computed(() => ringOption(loadPercent(), mainColor(loadPercent())))
const cpuOption = computed(() => ringOption(props.overview?.cpu, mainColor(props.overview?.cpu)))
const memOption = computed(() => ringOption(props.overview?.memory?.percent, mainColor(props.overview?.memory?.percent)))
const storageOption = computed(() => ringOption(props.overview?.storage?.percent, mainColor(props.overview?.storage?.percent)))
</script>

<style scoped>
.ring-chart {
  width: 100%;
  height: 100%;
  min-height: 70px;
}
</style>
