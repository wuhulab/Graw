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

use([CanvasRenderer, PieChart, TitleComponent, TooltipComponent])

const props = defineProps({
  overview: { type: Object, required: true }
})

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

const loadOption = computed(() => ringOption(props.overview?.load?.percent, '#e6a23c'))
const cpuOption = computed(() => ringOption(props.overview?.cpu, '#409eff'))
const memOption = computed(() => ringOption(props.overview?.memory?.percent, '#67c23a'))
const storageOption = computed(() => ringOption(props.overview?.storage?.percent, '#9b6dd6'))
</script>

<style scoped>
.ring-chart {
  width: 100%;
  height: 100%;
  min-height: 70px;
}
</style>
