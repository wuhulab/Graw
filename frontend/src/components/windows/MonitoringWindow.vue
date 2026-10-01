<!--
  监控中心窗口（后端 /api/uptime + /api/svcmonitor 模块）
  作用：把「站点监控」与「服务监控」两个独立应用合并为一个窗口，顶部标签切换视图。
  后端模块：/api/uptime（站点可用性）、/api/svcmonitor（服务监控）。
  关键状态：mode（当前视图：uptime 站点监控 / svcmonitor 服务监控）。
  打开方式：桌面「监控」卡片；实际内容由内嵌子组件 UptimeWindow / ServiceMonitorWindow 承担。
-->
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
      <UptimeWindow @openUptimeForm="emit('openUptimeForm', $event)" />
    </div>

    <!-- 服务监控视图（合并自独立的「服务监控」应用） -->
    <div v-else class="hub-body">
      <ServiceMonitorWindow @openServiceMonitorForm="emit('openServiceMonitorForm', $event)" />
    </div>
  </div>
</template>

<script setup>
// 响应式状态
import { ref } from 'vue'
// 合并进来的子窗口：站点监控（/api/uptime）与服务监控（/api/svcmonitor）的完整实现
import UptimeWindow from './UptimeWindow.vue'
import ServiceMonitorWindow from './ServiceMonitorWindow.vue'

// 冒泡到桌面（App.vue）的独立表单窗口事件：把子应用的「添加/编辑监控项」事件转发给桌面统一打开
const emit = defineEmits(['openUptimeForm', 'openServiceMonitorForm'])

// 视图模式：'uptime' 站点监控 / 'svcmonitor' 服务监控
const mode = ref('uptime')

// --- 动作：切换标签，仅切换本地状态（子组件按需挂载渲染） ---
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