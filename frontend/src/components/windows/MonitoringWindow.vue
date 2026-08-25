<template>
  <div class="monitoring-window">
    <!-- 视图切换：站点监控 / 服务监控 -->
    <div class="toolbar">
      <div class="mode-tabs">
        <button class="tab" :class="{ active: mode === 'uptime' }" @click="switchMode('uptime')">{{ $t('monitoring.modeUptime') }}</button>
        <button class="tab" :class="{ active: mode === 'svcmonitor' }" @click="switchMode('svcmonitor')">{{ $t('monitoring.modeSvcmonitor') }}</button>
      </div>
    </div>

    <!-- 站点监控视图（合并自独立的「站点监控」应用） -->
    <div v-if="mode === 'uptime'" class="hub-body">
      <UptimeWindow />
    </div>

    <!-- 服务监控视图（合并自独立的「服务监控」应用） -->
    <div v-else class="hub-body">
      <ServiceMonitorWindow />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import UptimeWindow from './UptimeWindow.vue'
import ServiceMonitorWindow from './ServiceMonitorWindow.vue'

// 视图模式：'uptime' 站点监控 / 'svcmonitor' 服务监控
const mode = ref('uptime')

function switchMode(m) {
  mode.value = m
}
</script>

<style scoped>
.monitoring-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.toolbar { display: flex; align-items: center; }
.mode-tabs { display: inline-flex; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }
.mode-tabs .tab { padding: 6px 14px; font-size: 13px; background: #fff; border: none; cursor: pointer; color: #6b7280; }
.mode-tabs .tab.active { background: #111827; color: #fff; }
.hub-body { flex: 1; min-height: 0; }
</style>