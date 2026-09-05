<!--
  计划任务 / 任务中心 聚合窗口（Tasks Hub）

  这个窗口做什么：
    把两个相关的定时 / 异步任务功能合并进同一个窗口，用顶部页签切换：
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
  <div class="tasks-hub">
    <!-- 视图切换：计划任务 / 任务中心 -->
    <div class="toolbar">
      <div class="mode-tabs">
        <button class="tab" :class="{ active: mode === 'cron' }" @click="switchMode('cron')">{{ $t('cron.title') }}</button>
        <button class="tab" :class="{ active: mode === 'taskcenter' }" @click="switchMode('taskcenter')">{{ $t('taskcenter.title') }}</button>
      </div>
    </div>

    <!-- 计划任务视图（合并自独立的「计划任务」应用） -->
    <div v-if="mode === 'cron'" class="hub-body">
      <CronWindow @openCronTaskForm="emit('openCronTaskForm', $event)" />
    </div>

    <!-- 任务中心视图（合并自独立的「任务中心」应用） -->
    <div v-else class="hub-body">
      <TaskCenterWindow />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'   // 页签模式状态
import CronWindow from './CronWindow.vue'   // 计划任务子窗口（crontab 定时任务管理）
import TaskCenterWindow from './TaskCenterWindow.vue'   // 任务中心子窗口（应用商店任务实时进度）

// 冒泡到桌面（App.vue）的独立表单窗口事件：把 CronWindow 弹出的「新增定时任务」事件转发给桌面统一打开
const emit = defineEmits(['openCronTaskForm'])

// 视图模式：'cron' 计划任务 / 'taskcenter' 任务中心
const mode = ref('cron')

// --- 切换顶部页签（计划任务 / 任务中心） ---
function switchMode(m) {
  mode.value = m
}
</script>

<style scoped>
.tasks-hub { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.toolbar { display: flex; align-items: center; }
.mode-tabs { display: inline-flex; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }
.mode-tabs .tab { padding: 6px 14px; font-size: 13px; background: #fff; border: none; cursor: pointer; color: #6b7280; }
.mode-tabs .tab.active { background: #111827; color: #fff; }
.hub-body { flex: 1; min-height: 0; }
</style>