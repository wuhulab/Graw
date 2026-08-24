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
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { uiState } from '../../store/ui'

use([CanvasRenderer, PieChart, TitleComponent, TooltipComponent])

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

function ringOption(percent, color) {
  const p = Math.max(0, Math.min(100, percent || 0))
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

const loadPercent = () => props.overview?.load?.percent
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
