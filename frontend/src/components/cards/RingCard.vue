<!--
  RingCard.vue — 系统概览环形卡片（桌面版）
  作用：桌面卡片之一，用四个 ECharts 环形图展示负载 / CPU / 内存 / 存储使用率，
        中心显示百分比；使用率超过告警阈值（90%）时整环变红提醒。
  数据：overview prop 由父级传入（内含 load/cpu/memory/storage 各百分比，
        以及硬件温度列表 temps——无传感器主机为空数组，温度行自动隐藏）；
        主色与告警开关来自 uiState（可在「界面设置」中修改）。节点不可达时由 MetricsFallback 提示。
  打开方式：作为桌面卡片渲染。
-->
<template>
  <div class="win7-card">
    <div class="card-title">
      <span>{{ $t('cards.systemOverview') }}</span>
    </div>
    <!-- 温度行存在时压缩环形区高度，保证卡片总高不变 -->
    <div class="ring-row" :style="{ height: temps.length ? 'calc(100% - 46px)' : 'calc(100% - 24px)' }">
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
    <!-- 硬件温度（CPU 优先）：无传感器的主机（Windows / 虚拟机）自动隐藏整行；
         超过 80°C 高亮告警色；最多展示 3 条，其余折叠为 +N -->
    <div v-if="temps.length" class="temp-row">
      <span class="temp-title">{{ $t('cards.temp') }}</span>
      <span
        v-for="s in shownTemps"
        :key="s.name"
        class="temp-item"
        :class="{ hot: s.value >= TEMP_HOT }"
        :title="`${s.name} ${s.value}°C`"
      >{{ s.name }} {{ Math.round(s.value) }}°C</span>
      <span v-if="temps.length > shownTemps.length" class="temp-more">+{{ temps.length - shownTemps.length }}</span>
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
import { settings } from '../../store/settings'   // 本地偏好（系统概览数值精度：整数 / 两位小数）
import MetricsFallback from './MetricsFallback.vue'   // 监控数据降级提示

use([CanvasRenderer, PieChart, TitleComponent, TooltipComponent])   // 注册所需 ECharts 模块

const props = defineProps({
  overview: { type: Object, required: true }
})

// 告警红线：使用率 >90% 时变身色（可在「界面设置」中修改颜色/开关）
const ALARM_THRESHOLD = 90
const ALARM_RED = '#f5222d'
// 温度告警线：≥80°C 高亮红色（CPU/硬盘过热预警的通用经验值）
const TEMP_HOT = 80

// 温度传感器列表：由后端随 overview 一并下发（CPU 优先排序）；
// 无传感器主机（Windows / 虚拟机）为空数组 → 整行隐藏，不留空位
const temps = computed(() => (Array.isArray(props.overview?.temps) ? props.overview.temps : []))
// 卡片内最多展示 3 条，其余折叠为 "+N"（避免撑破卡片宽度）
const shownTemps = computed(() => temps.value.slice(0, 3))

// 计算环形图主色：优先「界面设置」中配置的统一颜色；启用告警且使用率超阈值时变红
function mainColor(percent) {
  if (uiState.ring_alarm && (percent || 0) > ALARM_THRESHOLD) return ALARM_RED
  return uiState.ring_color || '#409eff'
}

// 生成单个环形图配置：一段「已用」弧 + 一段「剩余」弧，中心显示百分比
function ringOption(percent, color) {
  const p = Math.max(0, Math.min(100, percent || 0))   // 百分比夹在 0-100，防止数据越界破坏图形
  // 显示精度：默认取整（31%）；开启「系统概览显示到后两位」后保留两位小数（31.25%）。
  // 读取的是响应式 settings，切换开关时本 option 的 computed 会自动重算并刷新图表。
  const decimals = settings.overviewDecimals ? 2 : 0
  return {
    series: [{
      type: 'pie',
      radius: ['62%', '85%'],
      avoidLabelOverlap: false,
      silent: true,
      label: {
        show: true,
        position: 'center',
        formatter: `${p.toFixed(decimals)}%`,
        // 两位小数时文案变长（最多「100.00%」7 个字符），同步收小字号避免溢出环形内圈
        fontSize: decimals ? 11 : 14,
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
/* 温度行：单行紧凑展示，超出裁剪（最多 3 条 + "+N"） */
.temp-row {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 20px;
  margin-top: 2px;
  overflow: hidden;
  font-size: 11px;
  color: #6e6e73;
  white-space: nowrap;
}
.temp-title {
  font-weight: 600;
  color: #1d1d1f;
}
.temp-item {
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 过热高亮：与环形图告警红保持一致 */
.temp-item.hot {
  color: #f5222d;
  font-weight: 600;
}
.temp-more {
  color: #9ca3af;
}
</style>
