<!--
  PanelHome.vue — 标准面板模式「主页」

  作用：面板模式下点击侧边栏「主页」时展示的内容：四张监控/信息卡片布局——
        系统概览（环形统计）、实时监控（流量/磁盘IO）、系统信息、备忘录
        （系统信息与备忘录由桌面版 InfoNotesCard 拆分为两张独立卡片）。
  数据：与桌面卡片完全一致，全部来自共享 systemState（store/systemMetrics 单条 WS 驱动），
        不额外发起轮询；触摸降级等逻辑由各卡片组件自带。
  打开方式：侧边栏「主页」→ App.vue openWindow('panelhome') → 作为普通窗口渲染在本组件。
-->
<template>
  <div class="panel-home">
    <div class="panel-home-grid">
      <RingCard :overview="overview" />
      <MonitorCard />
      <InfoNotesCard fixed="info" />
      <InfoNotesCard fixed="notes" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'                              // 计算属性（与桌面 overview 同源）
import RingCard from './cards/RingCard.vue'                // 系统概览环形统计卡片
import MonitorCard from './cards/MonitorCard.vue'          // 实时流量 / 磁盘IO 监控卡片
import InfoNotesCard from './cards/InfoNotesCard.vue'      // 系统信息 / 备忘录卡片（fixed 拆分）
import { systemState } from '../store/systemMetrics'        // 共享系统指标状态

// 概览数据：由共享「单条 WS」指标推送驱动（与桌面 RingCard 传入的 overview 一致）
const overview = computed(() => systemState.overview)
</script>

<style scoped>
/* 主页容器：浅灰背景，内边距留白，超宽/超高可滚动 */
.panel-home {
  height: 100%;
  padding: 16px;
  background: #f5f6f8;
  box-sizing: border-box;
  overflow-y: auto;
}
/* 桌面 2x2 网格：概览 | 实时监控  /  系统信息 | 备忘录 */
.panel-home-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1.1fr;
  gap: 14px;
  min-height: 100%;
}
/* 移动端（内容区变窄）改为单列堆叠，避免卡片被压缩 */
@media (max-width: 767px) {
  .panel-home-grid {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto auto auto;
  }
}
</style>