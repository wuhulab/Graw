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
import { computed } from 'vue'
import { systemState } from '../../store/systemMetrics'
import { useI18n } from 'vue-i18n'

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