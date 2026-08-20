<template>
  <div class="cron-window">
    <div class="toolbar">
      <!-- 添加任务：点击展开「常规记录 / 标准记录」下拉 -->
      <div class="add-wrap">
        <button class="btn primary" @click="toggleMenu">
          <Plus :size="14" /> {{ $t('cron.addTask') }} <ChevronDown :size="12" />
        </button>
        <div v-if="showMenu" class="dropdown" @click.self="showMenu = false">
          <div class="dropdown-item" @click="openRegular">{{ $t('cron.regular') }}</div>
          <div class="dropdown-item" @click="openStandard">{{ $t('cron.standard') }}</div>
        </div>
      </div>
      <span class="hint">{{ $t('cron.platform', { platform }) }}</span>
    </div>
    <div class="table-wrap">
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
            <td class="mono">{{ t.schedule }}</td>
            <td class="mono">
              <span class="type-tag">{{ typeText(t.task_type) }}</span>
              <span class="cmd-text" :title="t.command">{{ t.command }}</span>
              <span v-if="t.alert" class="bell" :title="$t('cron.alertEnabled')">🔔</span>
            </td>
            <td><span class="badge" :class="t.enabled ? 'ok' : 'off'">{{ t.enabled ? $t('cron.enabled') : $t('cron.disabled') }}</span></td>
            <td class="actions">
              <button class="iconbtn" :title="$t('cron.runNow')" @click="runNow(t)"><Play :size="14" /></button>
              <button class="iconbtn" :title="$t('cron.toggleEnable')" @click="toggleEnable(t)"><Power :size="14" /></button>
              <button class="iconbtn danger" :title="$t('common.delete')" @click="remove(t)"><Trash2 :size="14" /></button>
            </td>
          </tr>
          <tr v-if="tasks.length === 0"><td colspan="5" class="empty">{{ $t('cron.noCrons') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 常规记录弹窗 -->
    <div v-if="showRegular" class="modal-overlay" @click.self="showRegular = false">
      <div class="modal">
        <h3>{{ $t('cron.regular') }}</h3>
        <div class="form">
          <label>{{ $t('cron.name') }}</label>
          <input v-model="form.name" :placeholder="$t('cron.namePlaceholder')" />
          <label>{{ $t('cron.scheduleLabel') }}</label>
          <input v-model="form.schedule" placeholder="0 3 * * *" />
          <label>{{ $t('cron.commandLabel') }}</label>
          <textarea v-model="form.command" rows="3" :placeholder="$t('cron.commandPlaceholder')" />
          <div class="actions">
            <button class="btn" @click="showRegular = false">{{ $t('common.cancel') }}</button>
            <button class="btn primary" @click="saveRegular">{{ $t('common.save') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 标准记录弹窗 -->
    <div v-if="showStandard" class="modal-overlay" @click.self="showStandard = false">
      <div class="modal std-modal">
        <h3>{{ $t('cron.standard') }}</h3>
        <div class="form">
          <label>{{ $t('cron.name') }}</label>
          <input v-model="std.name" :placeholder="$t('cron.namePlaceholderStd')" />
          <label>{{ $t('cron.group') }}</label>
          <input v-model="std.group" :placeholder="$t('cron.groupPlaceholder')" />
          <label>{{ $t('cron.typeLabel') }}</label>
          <select v-model="std.taskType">
            <option value="shell_command">{{ $t('cron.shell') }}</option>
            <option value="backup_container">{{ $t('cron.backup') }}</option>
            <option value="visit_url">{{ $t('cron.visitUrl') }}</option>
            <option value="clean_logs">{{ $t('cron.cleanLogs') }}</option>
            <option value="sync_time">{{ $t('cron.syncTime') }}</option>
          </select>
          <label>{{ $t('cron.freqLabel') }}</label>
          <div class="period">
            <select v-model="std.freq">
              <option value="daily">{{ $t('cron.daily') }}</option>
              <option value="weekly">{{ $t('cron.weekly') }}</option>
              <option value="monthly">{{ $t('cron.monthly') }}</option>
            </select>
            <select v-if="std.freq === 'weekly'" v-model="std.weekday">
              <option v-for="(d, i) in weekdays" :key="i" :value="i">{{ d }}</option>
            </select>
            <select v-if="std.freq === 'monthly'" v-model="std.dayOfMonth">
              <option v-for="d in 31" :key="d" :value="d">{{ $t('cron.dayOfMonth', { d }) }}</option>
            </select>
            <input type="time" v-model="std.time" />
          </div>
          <!-- 按任务类型动态展示内容输入 -->
          <template v-if="std.taskType === 'shell_command'">
            <label>{{ $t('cron.scriptContent') }}</label>
            <textarea v-model="std.content" rows="4" :placeholder="$t('cron.scriptPlaceholder')" />
          </template>
          <template v-else-if="std.taskType === 'backup_container'">
            <label>{{ $t('cron.containerName') }}</label>
            <input v-model="std.content" :placeholder="$t('cron.containerNamePlaceholder')" />
          </template>
          <template v-else-if="std.taskType === 'visit_url'">
            <label>{{ $t('cron.visitUrlLabel') }}</label>
            <input v-model="std.content" placeholder="https://example.com/health" />
          </template>
          <template v-else-if="std.taskType === 'clean_logs'">
            <label>{{ $t('cron.logDir') }}</label>
            <input v-model="std.content" placeholder="如：/var/log" />
          </template>
          <template v-else>
            <p class="note">{{ $t('cron.syncTimeNote') }}</p>
          </template>
          <label class="check">
            <input type="checkbox" v-model="std.alert" /> {{ $t('cron.alertLabel') }}
          </label>
          <div class="actions">
            <button class="btn" @click="showStandard = false">{{ $t('common.cancel') }}</button>
            <button class="btn primary" @click="saveStandard">{{ $t('common.save') }}</button>
          </div>
        </div>
      </div>
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
import { ref, onMounted, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { cronApi } from '../../api'
import { Plus, Play, Power, Trash2, ChevronDown } from 'lucide-vue-next'
import ConfirmDialog from '../ConfirmDialog.vue'

const { t } = useI18n()

const tasks = ref([])
const platform = ref('')
// 高风险操作二次确认状态（删除计划任务需输入面板密码）
const confirm = ref({ show: false, target: null })
const showMenu = ref(false)
const showRegular = ref(false)
const showStandard = ref(false)

// 常规记录表单
const form = ref({ name: '', schedule: '0 3 * * *', command: '' })
// 标准记录表单
const std = ref(defaultStd())
const weekdays = computed(() => [
  t('cron.weekdays.sun'), t('cron.weekdays.mon'), t('cron.weekdays.tue'),
  t('cron.weekdays.wed'), t('cron.weekdays.thu'), t('cron.weekdays.fri'), t('cron.weekdays.sat')
])

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

// 标准记录表单默认值
function defaultStd() {
  return {
    name: '',
    group: '默认',
    taskType: 'shell_command',
    freq: 'daily',
    weekday: 1,
    dayOfMonth: 1,
    time: '02:30',
    content: '',
    alert: false
  }
}

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

function openRegular() {
  showMenu.value = false
  form.value = { name: '', schedule: '0 3 * * *', command: '' }
  showRegular.value = true
}

function openStandard() {
  showMenu.value = false
  std.value = defaultStd()
  showStandard.value = true
}

// 将周期 + 时间转换为 cron 表达式
function stdSchedule() {
  const [h, m] = std.value.time ? std.value.time.split(':') : ['0', '0']
  const hh = h || '0'
  const mm = m || '0'
  if (std.value.freq === 'weekly') return `${mm} ${hh} * * ${std.value.weekday}`
  if (std.value.freq === 'monthly') return `${mm} ${hh} ${std.value.dayOfMonth} * *`
  return `${mm} ${hh} * * *`
}

async function saveRegular() {
  if (!form.value.name.trim() || !form.value.command.trim()) {
    alert(t('cron.nameContentRequired'))
    return
  }
  try {
    await cronApi.create({
      name: form.value.name.trim(),
      schedule: form.value.schedule.trim() || '0 3 * * *',
      command: form.value.command
    })
    showRegular.value = false
    await load()
  } catch (e) {
    console.error('保存常规记录失败', e)
    alert(t('cron.saveFailed', { error: e?.message || e }))
  }
}

async function saveStandard() {
  if (!std.value.name.trim()) {
    alert(t('cron.nameRequired'))
    return
  }
  if (std.value.taskType !== 'sync_time' && !std.value.content.trim()) {
    alert(t('cron.contentRequired'))
    return
  }
  try {
    await cronApi.create({
      name: std.value.name.trim(),
      schedule: stdSchedule(),
      task_type: std.value.taskType,
      content: std.value.content.trim(),
      group: std.value.group.trim() || '默认',
      alert: std.value.alert
    })
    showStandard.value = false
    await load()
  } catch (e) {
    console.error('保存标准记录失败', e)
    alert(t('cron.saveFailed', { error: e?.message || e }))
  }
}

async function runNow(task) {
  try {
    await cronApi.run(task.id)
    alert(t('cron.executed'))
  } catch (e) {
    console.error('执行任务失败', e)
    alert(t('cron.executeFailed', { error: e?.message || e }))
  }
}

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
  if (!task) return
  try {
    await cronApi.delete(task.id)
    await load()
  } catch (e) {
    console.error('删除任务失败', e)
    alert(t('cron.deleteFailed', { error: e?.message || e }))
  }
}

onMounted(load)
</script>

<style scoped>
.cron-window { padding: 10px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; position: relative; }
.hint { color: #6e6e73; font-size: 12px; }
.add-wrap { position: relative; }
.dropdown { position: absolute; top: 100%; left: 0; margin-top: 4px; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.12); z-index: 3000; min-width: 120px; overflow: hidden; }
.dropdown-item { padding: 8px 14px; font-size: 13px; cursor: pointer; color: #111827; }
.dropdown-item:hover { background: #f3f4f6; }
.table-wrap { overflow: auto; max-height: 420px; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
th { background: #f9fafb; position: sticky; top: 0; }
.mono { font-family: ui-monospace, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
.grp { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 999px; font-size: 11px; background: #eff6ff; color: #1d4ed8; }
.type-tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; background: #f3f4f6; color: #374151; margin-right: 6px; white-space: nowrap; }
.cmd-text { color: #374151; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: bottom; }
.bell { margin-left: 4px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.actions { display: flex; gap: 4px; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; }
.iconbtn:hover { background: #f9fafb; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
.empty { text-align: center; color: #9ca3af; padding: 24px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 4px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 480px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.std-modal { width: 520px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input, .form textarea, .form select { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; background: #fff; }
.form select { cursor: pointer; }
.period { display: flex; gap: 8px; }
.period select, .period input { flex: 1; }
.note { font-size: 12px; color: #6b7280; background: #f9fafb; border: 1px dashed #e5e7eb; border-radius: 6px; padding: 8px 10px; }
.check { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.check input { width: auto; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
