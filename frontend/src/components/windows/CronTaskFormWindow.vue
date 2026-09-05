<!--
  CronTaskFormWindow.vue — 计划任务 新建 表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 CronWindow 的「常规记录 / 标准记录」两个 modal 弹窗
    独立为桌面窗口，避免点击灰色遮罩误关丢已填内容。
    通过 props.mode 区分两种创建方式：
      regular   —— 直接填 cron 表达式 + 命令
      standard  —— 按周期/周几/每月几日 + 时间自动生成 cron 表达式，
                   并提供 shell 命令、备份容器、访问 URL、清理日志、同步时间五类任务
  后端模块：
    /api/cron 的 create / update（两种模式共用同一个创建接口）。
  关键状态：
    form   常规记录表单（name / schedule / command）
    std    标准记录表单（name / group / taskType / freq / weekday / dayOfMonth / time / content / alert）
    error  前端必填校验 / 后端错误信息回显
  打开方式：
    由 App.vue 的 openCronTaskForm(payload) 打开，props 传入
    { mode: 'regular'|'standard', task: 编辑对象或 null }。
    保存成功后 emit('close') 自关，并经 formBus 通知 CronWindow 刷新列表。
-->
<template>
  <div class="cron-form-window">
    <!-- 后端校验错误回显（红底错误框，保留用户已填内容） -->
    <div v-if="error" class="error-box">{{ error }}</div>

    <!-- ===== 常规记录表单：名称 / cron 表达式 / 命令 ===== -->
    <template v-if="mode === 'regular'">
      <label class="ui-field">
        <span class="ui-label">{{ $t('cron.name') }}</span>
        <input class="ui-input" v-model.trim="form.name" :placeholder="$t('cron.name')" maxlength="64" />
      </label>
      <label class="ui-field">
        <span class="ui-label">Cron 表达式</span>
        <input class="ui-input" v-model.trim="form.schedule" placeholder="0 3 * * *" spellcheck="false" />
        <span class="ui-hint">格式：分 时 日 月 周</span>
      </label>
      <label class="ui-field">
        <span class="ui-label">{{ $t('cron.command') }}</span>
        <textarea class="ui-textarea" v-model.trim="form.command" rows="3" spellcheck="false"></textarea>
      </label>
    </template>

    <!-- ===== 标准记录表单：按周期自动生成 cron 表达式 ===== -->
    <template v-else>
      <label class="ui-field">
        <span class="ui-label">{{ $t('cron.name') }}</span>
        <input class="ui-input" v-model.trim="std.name" :placeholder="$t('cron.namePlaceholderStd')" maxlength="64" />
      </label>
      <label class="ui-field">
        <span class="ui-label">分组</span>
        <input class="ui-input" v-model.trim="std.group" :placeholder="$t('cron.groupPlaceholder')" maxlength="32" />
      </label>
      <label class="ui-field">
        <span class="ui-label">{{ $t('cron.typeLabel') }}</span>
        <select class="ui-select" v-model="std.taskType">
          <option value="shell_command">{{ $t('cron.shell') }}</option>
          <option value="backup_container">{{ $t('cron.backup') }}</option>
          <option value="visit_url">{{ $t('cron.visitUrl') }}</option>
          <option value="clean_logs">{{ $t('cron.cleanLogs') }}</option>
          <option value="sync_time">{{ $t('cron.syncTime') }}</option>
        </select>
      </label>
      <label class="ui-field">
        <span class="ui-label">{{ $t('cron.freqLabel') }}</span>
        <div class="period">
          <select class="ui-select" v-model="std.freq">
            <option value="daily">{{ $t('cron.daily') }}</option>
            <option value="weekly">{{ $t('cron.weekly') }}</option>
            <option value="monthly">{{ $t('cron.monthly') }}</option>
          </select>
          <select v-if="std.freq === 'weekly'" class="ui-select" v-model="std.weekday" :title="$t('cron.weekday')">
            <option v-for="(d, i) in weekdays" :key="i" :value="i">{{ d }}</option>
          </select>
          <select v-if="std.freq === 'monthly'" class="ui-select" v-model="std.dayOfMonth" :title="$t('cron.dayOfMonth')">
            <option v-for="d in 31" :key="d" :value="d">{{ $t('cron.dayOfMonth', { d }) }}</option>
          </select>
          <input class="ui-input time" type="time" v-model="std.time" />
        </div>
      </label>
      <!-- 按任务类型动态展示内容输入 -->
      <template v-if="std.taskType === 'shell_command'">
        <label class="ui-field">
          <span class="ui-label">{{ $t('cron.scriptContent') }}</span>
          <textarea class="ui-textarea" v-model.trim="std.content" rows="4" :placeholder="$t('cron.scriptPlaceholder')" spellcheck="false"></textarea>
        </label>
      </template>
      <template v-else-if="std.taskType === 'backup_container'">
        <label class="ui-field">
          <span class="ui-label">{{ $t('cron.containerName') }}</span>
          <input class="ui-input" v-model.trim="std.content" :placeholder="$t('cron.containerNamePlaceholder')" spellcheck="false" />
        </label>
      </template>
      <template v-else-if="std.taskType === 'visit_url'">
        <label class="ui-field">
          <span class="ui-label">{{ $t('cron.visitUrlLabel') }}</span>
          <input class="ui-input" v-model.trim="std.content" placeholder="https://example.com/health" spellcheck="false" />
        </label>
      </template>
      <template v-else-if="std.taskType === 'clean_logs'">
        <label class="ui-field">
          <span class="ui-label">{{ $t('cron.logDir') }}</span>
          <input class="ui-input" v-model.trim="std.content" placeholder="如：/var/log" spellcheck="false" />
        </label>
      </template>
      <template v-else>
        <p class="note">{{ $t('cron.syncTimeNote') }}</p>
      </template>
      <label class="ui-field check">
        <input type="checkbox" v-model="std.alert" style="width:auto;" />
        <span>{{ $t('cron.alertLabel') }}</span>
      </label>
    </template>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? $t('common.loading') : $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态、props 与派生值
import { ref, reactive, computed } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 计划任务 API：create / update
import { cronApi } from '../../api'
// 表单保存信号：通知 CronWindow 刷新任务列表
import { bumpForm } from '../../store/formBus'

const { t } = useI18n()

// mode: 'regular' 常规 / 'standard' 标准（由 App.vue 打开窗口时传入）；
// task: 编辑对象（null = 新建，当前无编辑入口，保留以兼容后续扩展）
const props = defineProps({
  mode: { type: String, default: 'regular' },
  task: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 前端必填校验 / 后端错误信息

// 常规记录表单（编辑时回填任务字段，否则用默认表达式）
const form = reactive(props.task && props.mode === 'regular'
  ? { name: props.task.name || '', schedule: props.task.schedule || '0 3 * * *', command: props.task.command || '' }
  : { name: '', schedule: '0 3 * * *', command: '' })

// 标准记录表单默认值：默认分组「默认」、每天 02:30 执行（新建与编辑回填共用）
const std = reactive(props.mode === 'standard' && props.task
  ? {
      name: props.task.name || '',
      group: props.task.group || '默认',
      taskType: props.task.task_type || 'shell_command',
      freq: 'daily',
      weekday: 1,
      dayOfMonth: 1,
      time: '02:30',
      content: props.task.content || '',
      alert: !!props.task.alert
    }
  : defaultStd())

// 星期下拉文案（顺序与 Date.getDay() 一致，0=周日）
const weekdays = computed(() => [
  t('cron.weekdays.sun'), t('cron.weekdays.mon'), t('cron.weekdays.tue'),
  t('cron.weekdays.wed'), t('cron.weekdays.thu'), t('cron.weekdays.fri'), t('cron.weekdays.sat')
])

// 将周期 + 时间转换为 cron 表达式（周几/每月几日分别落到对应字段）
function stdSchedule() {
  const [h, m] = std.time ? std.time.split(':') : ['0', '0']
  const hh = h || '0'
  const mm = m || '0'
  if (std.freq === 'weekly') return `${mm} ${hh} * * ${std.weekday}`
  if (std.freq === 'monthly') return `${mm} ${hh} ${std.dayOfMonth} * *`
  return `${mm} ${hh} * * *`
}

// --- 保存：按模式校验并调用创建/更新接口，成功后通知父窗口刷新并自关 ---
async function save() {
  if (saving.value) return   // 防重复提交
  error.value = ''
  try {
    if (props.mode === 'regular') {
      // 常规记录必填校验：名称 + 命令缺一不可
      if (!form.name.trim() || !form.command.trim()) { error.value = t('cron.nameContentRequired'); return }
      await props.task
        ? cronApi.update(props.task.id, {
            name: form.name.trim(),
            schedule: form.schedule.trim() || '0 3 * * *',
            command: form.command
          })
        : cronApi.create({
            name: form.name.trim(),
            schedule: form.schedule.trim() || '0 3 * * *',
            command: form.command
          })
    } else {
      // 标准记录必填校验：名称必填；除「同步时间」外内容必填
      if (!std.name.trim()) { error.value = t('cron.nameRequired'); return }
      if (std.taskType !== 'sync_time' && !std.content.trim()) { error.value = t('cron.contentRequired'); return }
      const body = {
        name: std.name.trim(),
        schedule: stdSchedule(),
        task_type: std.taskType,
        content: std.content.trim(),
        group: std.group.trim() || '默认',
        alert: std.alert
      }
      await props.task ? cronApi.update(props.task.id, body) : cronApi.create(body)
    }
    bumpForm('cron')   // 通知 CronWindow 重新拉取任务列表
    emit('close')      // 成功后关闭本窗口
  } catch (e) {
    // 后端校验失败：把 detail 回显在表单里，保留用户已填内容
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.cron-form-window { padding: 14px; }
.error-box {
  color: #b91c1c;
  font-size: 12.5px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
  word-break: break-all;
}
/* 频率行：周期 / 周几 / 每月几日 / 时间 横向排列 */
.period { display: flex; gap: 8px; }
.period select, .period input.time { flex: 1; }
/* 同步时间类任务的静态说明框 */
.note { font-size: 12px; color: #6b7280; background: #f9fafb; border: 1px dashed #e5e7eb; border-radius: 6px; padding: 8px 10px; }
/* 勾选项（提醒）横向排布 */
.check { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.check input { width: auto; }
</style>