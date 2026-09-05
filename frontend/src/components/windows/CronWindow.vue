<!--
  CronWindow.vue — 计划任务管理窗口
  ==========================================================
  业务作用：
    管理服务器上的计划任务（cron）。支持两种创建方式：常规记录（直接填 cron
    表达式 + 命令）和标准记录（按周期/周几/每月几日 + 时间自动生成 cron 表达式，
    提供 shell 命令、备份容器、访问 URL、清理日志、同步时间五类任务）。列表
    可执行、启停、删除任务，删除为高风险操作需面板密码二次确认。
  后端模块：
    /api/cron 的 list / create / update / run / delete。
  关键状态：
    - tasks      任务列表
    - platform   宿主平台（决定 cron 支持范围提示）
    - confirm    删除任务的密码二次确认
  打开方式：
    由桌面/任务栏（或「任务」聚合窗口）打开，无 props。
  表单拆分：
    新增任务（常规/标准）已拆为独立窗口 CronTaskFormWindow：保存成功后
    bumpForm('cron') 触发此处 watch 重新拉取列表。
-->
<template>
  <div class="cron-window">
    <div class="toolbar">
      <!-- 添加任务：点击展开「常规记录 / 标准记录」下拉 -->
      <div class="add-wrap">
        <button class="ui-btn primary" @click="toggleMenu">
          <Plus :size="14" /> {{ $t('cron.addTask') }} <ChevronDown :size="12" />
        </button>
        <div v-if="showMenu" class="dropdown" @click.self="showMenu = false">
          <div class="dropdown-item" @click="emit('openCronTaskForm', { mode: 'regular', task: null })">{{ $t('cron.regular') }}</div>
          <div class="dropdown-item" @click="emit('openCronTaskForm', { mode: 'standard', task: null })">{{ $t('cron.standard') }}</div>
        </div>
      </div>
      <span class="ui-hint">{{ $t('cron.platform', { platform }) }}</span>
    </div>

    <div class="ui-table-wrap">
      <table>
        <thead>
          <tr><th>{{ $t('cron.name') }}</th><th>{{ $t('cron.schedule') }}</th><th>{{ $t('cron.typeCommand') }}</th><th>{{ $t('common.status') }}</th><th>{{ $t('cron.action') }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id">
            <td>
              {{ t.name }}
              <span class="grp" v-if="t.group">{{ t.group }}</span>
            </td>
            <td class="ui-mono">{{ t.schedule }}</td>
            <td class="ui-mono">
              <span class="type-tag">{{ typeText(t.task_type) }}</span>
              <span class="cmd-text" :title="t.command">{{ t.command }}</span>
              <span v-if="t.alert" class="bell" :title="$t('cron.alertEnabled')">🔔</span>
            </td>
            <td><span class="ui-badge" :class="t.enabled ? 'ok' : 'off'">{{ t.enabled ? $t('cron.enabled') : $t('cron.disabled') }}</span></td>
            <td class="actions">
              <button class="iconbtn" :title="$t('cron.runNow')" @click="runNow(t)"><Play :size="14" /></button>
              <button class="iconbtn" :title="$t('cron.toggleEnable')" @click="toggleEnable(t)"><Power :size="14" /></button>
              <button class="iconbtn danger" :title="$t('common.delete')" @click="remove(t)"><Trash2 :size="14" /></button>
            </td>
          </tr>
          <tr v-if="tasks.length === 0"><td colspan="5" class="ui-empty">{{ $t('cron.noCrons') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 高风险操作二次确认：删除计划任务需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      title="删除计划任务确认"
      :message="`删除计划任务「${confirm.target?.name || ''}」后无法恢复。\n请输入面板密码以确认。`"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      confirm-label="删除"
      @confirm="doRemove"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'   // 状态/挂载加载/表单保存信号订阅
import { useI18n } from 'vue-i18n'   // 翻译函数
import { cronApi } from '../../api'   // /api/cron：计划任务接口
import { Plus, Play, Power, Trash2, ChevronDown } from 'lucide-vue-next'   // 工具栏/行操作图标
import ConfirmDialog from '../ConfirmDialog.vue'   // 删除任务的密码二次确认对话框
import { formBus } from '../../store/formBus'   // 表单保存信号：独立表单窗口保存成功后刷新列表

const { t } = useI18n()

const emit = defineEmits(['openCronTaskForm'])   // 打开独立「新增任务」窗口（mode: regular/standard）

const tasks = ref([])   // 计划任务列表
const platform = ref('')   // 宿主平台（提示 cron 支持范围）
// 高风险操作二次确认状态（删除计划任务需输入面板密码）
const confirm = ref({ show: false, target: null })
const showMenu = ref(false)   // 添加任务下拉菜单

// 新增任务改由独立窗口承载：保存成功后 bumpForm('cron') 触发此处重载
watch(() => formBus.cron, load)

// 任务类型名称：映射到 i18n key
const TYPE_KEYS = {
  shell_command: 'cron.shell',
  backup_container: 'cron.backup',
  visit_url: 'cron.visitUrl',
  clean_logs: 'cron.cleanLogs',
  sync_time: 'cron.syncTime'
}

function typeText(type) {
  const key = TYPE_KEYS[type]
  return key ? t(key) : (type || t('cron.shell'))
}

// --- 加载任务列表与平台信息 ---
async function load() {
  try {
    const data = await cronApi.list()
    tasks.value = data.tasks || []
    platform.value = data.platform || ''
  } catch (e) {
    console.error('加载计划任务失败', e)
    alert(t('cron.loadFailed', { error: e?.message || e }))
  }
}

function toggleMenu() {
  showMenu.value = !showMenu.value
}

// --- 立即执行一次任务 ---
async function runNow(task) {
  try {
    await cronApi.run(task.id)
    alert(t('cron.executed'))
  } catch (e) {
    console.error('执行任务失败', e)
    alert(t('cron.executeFailed', { error: e?.message || e }))
  }
}

// --- 启停任务 ---
async function toggleEnable(task) {
  try {
    await cronApi.update(task.id, { enabled: !task.enabled })
    await load()
  } catch (e) {
    console.error('切换任务状态失败', e)
    alert(t('cron.operationFailed', { error: e?.message || e }))
  }
}

// 删除计划任务：高风险操作，先弹出密码二次确认框
function remove(task) {
  confirm.value = { show: true, target: task }
}

// 面板密码校验通过后真正执行删除
async function doRemove() {
  const task = confirm.value.target
  confirm.value.show = false
  if (!task) return   // 无目标任务直接返回（防御性）
  try {
    await cronApi.delete(task.id)
    await load()
  } catch (e) {
    console.error('删除任务失败', e)
    alert(t('cron.deleteFailed', { error: e?.message || e }))
  }
}

onMounted(load)   // 进入窗口即加载任务列表
</script>

<style scoped>
.cron-window { padding: 0; } /* 内嵌于「任务」聚合窗口：外边距由父容器提供 */
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; position: relative; }
.add-wrap { position: relative; }
.dropdown { position: absolute; top: 100%; left: 0; margin-top: 4px; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.12); z-index: 3000; min-width: 120px; overflow: hidden; }
.dropdown-item { padding: 8px 14px; font-size: 13px; cursor: pointer; color: #111827; }
.dropdown-item:hover { background: #f3f4f6; }
.grp { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 999px; font-size: 11px; background: #eff6ff; color: #1d4ed8; }
.type-tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; background: #f3f4f6; color: #374151; margin-right: 6px; white-space: nowrap; }
.cmd-text { color: #374151; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: bottom; }
.bell { margin-left: 4px; }
.actions { display: flex; gap: 4px; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; }
.iconbtn:hover { background: #f9fafb; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
</style>