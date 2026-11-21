<!--
  监控中心窗口（后端 /api/uptime + /api/svcmonitor 模块）
  作用：把「站点监控」与「服务监控」两个独立应用合并为一个窗口，左侧页签切换视图。
  后端模块：/api/uptime（站点可用性）、/api/svcmonitor（服务监控）。
  关键状态：mode（当前视图：uptime 站点监控 / svcmonitor 服务监控）。
  打开方式：桌面「监控」卡片；实际内容由内嵌子组件 UptimeWindow / ServiceMonitorWindow 承担。
-->
<template>
  <WindowTabNav v-model="mode" :items="tabs">
    <div class="monitoring-window">
      <!-- 站点监控视图（合并自独立的「站点监控」应用） -->
    <div v-if="mode === 'uptime'" class="hub-body">
      <UptimeWindow @openUptimeForm="emit('openUptimeForm', $event)" />
    </div>

    <!-- 服务监控视图（合并自独立的「服务监控」应用） -->
    <div v-else class="hub-body">
      <ServiceMonitorWindow @openServiceMonitorForm="emit('openServiceMonitorForm', $event)" />
    </div>
    </div>
  </WindowTabNav>
</template>

<script setup>
// 响应式状态
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import WindowTabNav from '../WindowTabNav.vue'   // 统一左侧竖排页签导航
import { Activity, ServerCog } from 'lucide-vue-next'
// 合并进来的子窗口：站点监控（/api/uptime）与服务监控（/api/svcmonitor）的完整实现
import UptimeWindow from './UptimeWindow.vue'
import ServiceMonitorWindow from './ServiceMonitorWindow.vue'

// 冒泡到桌面（App.vue）的独立表单窗口事件：把子应用的「添加/编辑监控项」事件转发给桌面统一打开
const emit = defineEmits(['openUptimeForm', 'openServiceMonitorForm'])
const { t } = useI18n()

// 视图模式：'uptime' 站点监控 / 'svcmonitor' 服务监控
const mode = ref('uptime')

// 左侧页签：站点监控 / 服务监控
const tabs = computed(() => [
  { key: 'uptime', label: t('monitoring.modeUptime'), icon: Activity },
  { key: 'svcmonitor', label: t('monitoring.modeSvcmonitor'), icon: ServerCog },
])
</script>

<style scoped>
.monitoring-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; min-height: 0; }
.hub-body { flex: 1; min-height: 0; }
</style>