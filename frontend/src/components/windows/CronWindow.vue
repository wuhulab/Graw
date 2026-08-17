<template>
  <div class="cron-window">
    <div class="toolbar">
      <!-- 添加任务：点击展开「常规记录 / 标准记录」下拉 -->
      <div class="add-wrap">
        <button class="btn primary" @click="toggleMenu">
          <Plus :size="14" /> 添加任务 <ChevronDown :size="12" />
        </button>
        <div v-if="showMenu" class="dropdown" @click.self="showMenu = false">
          <div class="dropdown-item" @click="openRegular">常规记录</div>
          <div class="dropdown-item" @click="openStandard">标准记录</div>
        </div>
      </div>
      <span class="hint">平台: {{ platform }}</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>名称</th><th>周期</th><th>类型 / 命令</th><th>状态</th><th>操作</th></tr>
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
              <span v-if="t.alert" class="bell" title="已开启告警通知">🔔</span>
            </td>
            <td><span class="badge" :class="t.enabled ? 'ok' : 'off'">{{ t.enabled ? '启用' : '停用' }}</span></td>
            <td class="actions">
              <button class="iconbtn" title="立即执行" @click="runNow(t)"><Play :size="14" /></button>
              <button class="iconbtn" title="停用/启用" @click="toggleEnable(t)"><Power :size="14" /></button>
              <button class="iconbtn danger" title="删除" @click="remove(t)"><Trash2 :size="14" /></button>
            </td>
          </tr>
          <tr v-if="tasks.length === 0"><td colspan="5" class="empty">暂无计划任务</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 常规记录弹窗 -->
    <div v-if="showRegular" class="modal-overlay" @click.self="showRegular = false">
      <div class="modal">
        <h3>常规记录</h3>
        <div class="form">
          <label>任务名称</label>
          <input v-model="form.name" placeholder="备份数据库" />
          <label>Cron 表达式（分 时 日 月 周）</label>
          <input v-model="form.schedule" placeholder="0 3 * * *" />
          <label>执行命令</label>
          <textarea v-model="form.command" rows="3" placeholder="bash 命令或脚本路径" />
          <div class="actions">
            <button class="btn" @click="showRegular = false">取消</button>
            <button class="btn primary" @click="saveRegular">保存</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 标准记录弹窗 -->
    <div v-if="showStandard" class="modal-overlay" @click.self="showStandard = false">
      <div class="modal std-modal">
        <h3>标准记录</h3>
        <div class="form">
          <label>任务名称</label>
          <input v-model="std.name" placeholder="如：备份数据库" />
          <label>分组</label>
          <input v-model="std.group" placeholder="默认" />
          <label>任务类型</label>
          <select v-model="std.taskType">
            <option value="shell_command">shell命令</option>
            <option value="backup_container">备份容器</option>
            <option value="visit_url">访问url</option>
            <option value="clean_logs">清理日志</option>
            <option value="sync_time">同步服务器时间</option>
          </select>
          <label>执行周期</label>
          <div class="period">
            <select v-model="std.freq">
              <option value="daily">每天</option>
              <option value="weekly">每周</option>
              <option value="monthly">每月</option>
            </select>
            <select v-if="std.freq === 'weekly'" v-model="std.weekday">
              <option v-for="(d, i) in weekdays" :key="i" :value="i">{{ d }}</option>
            </select>
            <select v-if="std.freq === 'monthly'" v-model="std.dayOfMonth">
              <option v-for="d in 31" :key="d" :value="d">{{ d }} 日</option>
            </select>
            <input type="time" v-model="std.time" />
          </div>
          <!-- 按任务类型动态展示内容输入 -->
          <template v-if="std.taskType === 'shell_command'">
            <label>脚本内容</label>
            <textarea v-model="std.content" rows="4" placeholder="输入要执行的 shell 脚本" />
          </template>
          <template v-else-if="std.taskType === 'backup_container'">
            <label>容器名称</label>
            <input v-model="std.content" placeholder="如：mysql" />
          </template>
          <template v-else-if="std.taskType === 'visit_url'">
            <label>访问地址</label>
            <input v-model="std.content" placeholder="https://example.com/health" />
          </template>
          <template v-else-if="std.taskType === 'clean_logs'">
            <label>日志目录</label>
            <input v-model="std.content" placeholder="如：/var/log" />
          </template>
          <template v-else>
            <p class="note">同步服务器时间无需额外内容，将自动执行系统时间同步。</p>
          </template>
          <label class="check">
            <input type="checkbox" v-model="std.alert" /> 触发告警通知
          </label>
          <div class="actions">
            <button class="btn" @click="showStandard = false">取消</button>
            <button class="btn primary" @click="saveStandard">保存</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { cronApi } from '../../api'
import { Plus, Play, Power, Trash2, ChevronDown } from 'lucide-vue-next'

const tasks = ref([])
const platform = ref('')
const showMenu = ref(false)
const showRegular = ref(false)
const showStandard = ref(false)

// 常规记录表单
const form = ref({ name: '', schedule: '0 3 * * *', command: '' })
// 标准记录表单
const std = ref(defaultStd())
const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

// 任务类型中文名映射
const TYPE_TEXT = {
  shell_command: 'shell命令',
  backup_container: '备份容器',
  visit_url: '访问url',
  clean_logs: '清理日志',
  sync_time: '同步时间'
}

function typeText(type) {
  return TYPE_TEXT[type] || type || 'shell命令'
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
    alert('加载计划任务失败：' + (e?.message || e))
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
    alert('请填写任务名称和执行命令')
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
    alert('保存失败：' + (e?.message || e))
  }
}

async function saveStandard() {
  if (!std.value.name.trim()) {
    alert('请填写任务名称')
    return
  }
  if (std.value.taskType !== 'sync_time' && !std.value.content.trim()) {
    alert('请填写任务内容')
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
    alert('保存失败：' + (e?.message || e))
  }
}

async function runNow(t) {
  try {
    await cronApi.run(t.id)
    alert('任务已触发执行')
  } catch (e) {
    console.error('执行任务失败', e)
    alert('执行失败：' + (e?.message || e))
  }
}

async function toggleEnable(t) {
  try {
    await cronApi.update(t.id, { enabled: !t.enabled })
    await load()
  } catch (e) {
    console.error('切换任务状态失败', e)
    alert('操作失败：' + (e?.message || e))
  }
}

async function remove(t) {
  if (!confirm('确定删除此任务？')) return
  try {
    await cronApi.delete(t.id)
    await load()
  } catch (e) {
    console.error('删除任务失败', e)
    alert('删除失败：' + (e?.message || e))
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
