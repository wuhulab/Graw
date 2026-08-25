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
      <CronWindow />
    </div>

    <!-- 任务中心视图（合并自独立的「任务中心」应用） -->
    <div v-else class="hub-body">
      <TaskCenterWindow />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import CronWindow from './CronWindow.vue'
import TaskCenterWindow from './TaskCenterWindow.vue'

// 视图模式：'cron' 计划任务 / 'taskcenter' 任务中心
const mode = ref('cron')

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