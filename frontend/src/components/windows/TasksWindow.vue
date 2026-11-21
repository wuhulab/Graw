<!--
  计划任务 / 任务中心 聚合窗口（Tasks Hub）

  这个窗口做什么：
    把两个相关的定时 / 异步任务功能合并进同一个窗口，用左侧页签切换：
      - 「计划任务」页签：内嵌 CronWindow（crontab 定时任务管理）；
      - 「任务中心」页签：内嵌 TaskCenterWindow（应用商店安装任务实时进度）。
    这样管理员不用在桌面开两个应用就能看全所有「任务」。

  用到的后端模块：
    本身不直接调接口，接口调用发生在子组件内：
      CronWindow → /api/cron/*，TaskCenterWindow → /api/tasks/*（均管理员权限）。

  关键状态：
    mode  当前页签：'cron' 计划任务 / 'taskcenter' 任务中心

  怎么被打开：
    桌面「计划任务」应用（聚合入口）。
-->
<template>
  <WindowTabNav v-model="mode" :items="tabs">
    <div class="tasks-hub">
      <!-- 计划任务视图（合并自独立的「计划任务」应用） -->
    <div v-if="mode === 'cron'" class="hub-body">
      <CronWindow @openCronTaskForm="emit('openCronTaskForm', $event)" />
    </div>

    <!-- 任务中心视图（合并自独立的「任务中心」应用） -->
    <div v-else class="hub-body">
      <TaskCenterWindow />
    </div>
    </div>
  </WindowTabNav>
</template>

<script setup>
import { ref, computed } from 'vue'   // 页签模式状态
import { useI18n } from 'vue-i18n'
import WindowTabNav from '../WindowTabNav.vue'   // 统一左侧竖排页签导航
import { Clock, ListChecks } from 'lucide-vue-next'
import CronWindow from './CronWindow.vue'   // 计划任务子窗口（crontab 定时任务管理）
import TaskCenterWindow from './TaskCenterWindow.vue'   // 任务中心子窗口（应用商店任务实时进度）

// 冒泡到桌面（App.vue）的独立表单窗口事件：把 CronWindow 弹出的「新增定时任务」事件转发给桌面统一打开
const emit = defineEmits(['openCronTaskForm'])
const { t } = useI18n()

// 视图模式：'cron' 计划任务 / 'taskcenter' 任务中心
const mode = ref('cron')

// 左侧页签：计划任务 / 任务中心
const tabs = computed(() => [
  { key: 'cron', label: t('cron.title'), icon: Clock },
  { key: 'taskcenter', label: t('taskcenter.title'), icon: ListChecks },
])
</script>

<style scoped>
.tasks-hub { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; min-height: 0; }
.hub-body { flex: 1; min-height: 0; }
</style>