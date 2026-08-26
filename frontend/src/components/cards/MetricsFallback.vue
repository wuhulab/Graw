<!--
  MetricsFallback.vue — 监控指标降级提示
  作用：当当前管理节点不可达或指标数据过期时，在监控类卡片上叠加半透明警示层，
        明确告知监控数据缺失，避免卡片白屏让人误以为功能损坏。
  数据：完全由共享 systemState（store/systemMetrics）的 unavailable / stale 标志驱动，
        无 props、无接口调用，状态就绪前不渲染任何内容。
  打开方式：由各监控卡片（RingCard / MonitorCard / InfoNotesCard）内部按需引入渲染。
-->
<template>
  <div v-if="show" class="metrics-fallback">
    <!-- 半透明遮罩：图表保留可见，同时醒目提示监控数据缺失 -->
    <div class="metrics-fallback-backdrop"></div>
    <div class="metrics-fallback-body">
      <div class="metrics-fallback-title">{{ title }}</div>
      <div v-if="detail" class="metrics-fallback-detail">{{ detail }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'                    // Vue 计算属性
import { systemState } from '../../store/systemMetrics'   // 共享系统指标状态（含不可用/过期标志）
import { useI18n } from 'vue-i18n'                // 国际化

const { t } = useI18n()

// 降级展示条件：后端明确广播「采集不可用」，或连接正常但长时间收不到指标帧。
// 后者兜底网络/服务异常时前端自行判定过期，避免卡片永远白屏。
const show = computed(() => !!(systemState.unavailable || systemState.stale))

// 优先展示后端广播的明确原因（如「当前管理节点数据采集失败…」），
// 否则展示通用延迟提示；仅在 stale 场景补充一句排查引导。
const title = computed(() => systemState.unavailable || t('cards.metricsUnavailable'))
const detail = computed(() => (systemState.unavailable ? '' : t('cards.metricsUnavailableDetail')))
</script>

<style scoped>
.metrics-fallback {
  position: absolute;
  inset: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 16px;
  overflow: hidden;
}
.metrics-fallback-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(255, 251, 235, 0.78);
}
.metrics-fallback-body {
  position: relative;
  max-width: 84%;
  text-align: center;
  padding: 8px 14px;
}
.metrics-fallback-title {
  font-size: 13px;
  font-weight: 600;
  color: #ad6800;
  line-height: 1.5;
}
.metrics-fallback-detail {
  margin-top: 5px;
  font-size: 11px;
  color: #7a5b1e;
  line-height: 1.5;
}
</style>