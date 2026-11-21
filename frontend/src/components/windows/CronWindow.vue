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
      <select v-model="groupFilter" class="ui-select" style="width:130px">
        <option value="">{{ $t('cron.allGroups') }}</option>
        <option v-for="g in groups" :key="g" :value="g">{{ g }}</option>
      </select>
      <button class="ui-btn mini" @click="exportTasks"><Download :size="13" /> {{ $t('cron.export') }}</button>
      <button class="ui-btn mini" @click="triggerImport"><Upload :size="13" /> {{ $t('cron.import') }}</button>
      <button class="ui-btn mini" @click="openScripts"><FileCode2 :size="13" /> {{ $t('cron.scriptLibrary') }}</button>
      <input ref="importInput" type="file" accept="application/json" style="display:none" @change="importTasks" />
    </div>

    <div class="ui-table-wrap">
      <table>
        <thead>
          <tr><th>{{ $t('cron.name') }}</th><th>{{ $t('cron.schedule') }}</th><th>{{ $t('cron.typeCommand') }}</th><th>{{ $t('common.status') }}</th><th>{{ $t('cron.action') }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in filteredTasks" :key="t.id">
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
              <button class="iconbtn" :title="$t('cron.viewReport')" @click="viewReport(t)"><FileText :size="14" /></button>
              <button class="iconbtn" :title="$t('cron.toggleEnable')" @click="toggleEnable(t)"><Power :size="14" /></button>
              <button class="iconbtn danger" :title="$t('common.delete')" @click="remove(t)"><Trash2 :size="14" /></button>
            </td>
          </tr>
          <tr v-if="filteredTasks.length === 0"><td colspan="5" class="ui-empty">{{ $t('cron.noCrons') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 执行报告：最近一次手动执行输出 -->
    <div v-if="report.show" class="ui-modal-overlay" @click.self="report.show = false">
      <div class="ui-modal wide">
        <h3>{{ $t('cron.reportTitle', { name: report.task?.name || '' }) }}</h3>
        <pre class="report-code">{{ report.content || $t('cron.noReport') }}</pre>
        <div class="ui-actions">
          <button class="ui-btn" @click="report.show = false">{{ $t('common.close') }}</button>
        </div>
      </div>
    </div>

    <!-- 脚本库：可复用 Shell 片段，新建任务时可直接选择 -->
    <div v-if="scriptLib.show" class="ui-modal-overlay" @click.self="scriptLib.show = false">
      <div class="ui-modal wide">
        <h3>{{ $t('cron.scriptLibrary') }}</h3>
        <div class="script-form">
          <input v-model.trim="scriptLib.name" class="ui-input" :placeholder="$t('cron.scriptName')" maxlength="64" />
          <input v-model.trim="scriptLib.group" class="ui-input" :placeholder="$t('cron.groupPlaceholder')" maxlength="32" />
          <textarea v-model="scriptLib.content" class="ui-textarea" rows="3" :placeholder="$t('cron.scriptPlaceholder')" spellcheck="false"></textarea>
          <div class="ui-actions">
            <button class="ui-btn" @click="resetScriptForm">{{ $t('cron.newScript') }}</button>
            <button class="ui-btn primary" @click="saveScript">{{ scriptLib.id ? $t('common.save') : $t('common.create') }}</button>
          </div>
        </div>
        <div class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('common.name') }}</th><th>{{ $t('cron.group') }}</th><th>{{ $t('cron.action') }}</th></tr></thead>
            <tbody>
              <tr v-for="s in scripts" :key="s.id">
                <td>{{ s.name }}</td>
                <td>{{ s.group }}</td>
                <td class="actions">
                  <button class="iconbtn" :title="$t('common.edit')" @click="editScript(s)"><Pencil :size="13" /></button>
                  <button class="iconbtn danger" :title="$t('common.delete')" @click="removeScript(s)"><Trash2 :size="13" /></button>
                </td>
              </tr>
              <tr v-if="!scripts.length"><td colspan="3" class="ui-empty">{{ $t('cron.noScripts') }}</td></tr>
            </tbody>
          </table>
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
import { ref, computed, onMounted, watch } from 'vue'   // 状态/派生列表/挂载加载/表单保存信号订阅
import { useI18n } from 'vue-i18n'   // 翻译函数
import { cronApi } from '../../api'   // /api/cron：计划任务接口
import { Plus, Play, Power, Trash2, ChevronDown, FileText, Download, Upload, FileCode2, Pencil } from 'lucide-vue-next'   // 工具栏/行操作图标
import ConfirmDialog from '../ConfirmDialog.vue'   // 删除任务的密码二次确认对话框
import { formBus } from '../../store/formBus'   // 表单保存信号：独立表单窗口保存成功后刷新列表

const { t } = useI18n()

const emit = defineEmits(['openCronTaskForm'])   // 打开独立「新增任务」窗口（mode: regular/standard）

const tasks = ref([])   // 计划任务列表
const platform = ref('')   // 宿主平台（提示 cron 支持范围）
const groups = ref([])     // 后端汇总的任务分组
const groupFilter = ref('')// 当前分组筛选（空 = 全部）
// 高风险操作二次确认状态（删除计划任务需输入面板密码）
const confirm = ref({ show: false, target: null })
const showMenu = ref(false)   // 添加任务下拉菜单
const importInput = ref(null) // 隐藏的导入文件选择框
const report = ref({ show: false, task: null, content: '' })   // 执行报告弹窗
const scripts = ref([])       // 脚本库
const scriptLib = ref({ show: false, id: '', name: '', group: '默认', content: '' })

const filteredTasks = computed(() => groupFilter.value
  ? tasks.value.filter(t => (t.group || '默认') === groupFilter.value)
  : tasks.value)

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
    groups.value = data.groups || []
    platform.value = data.platform || ''
  } catch (e) {
    console.error('加载计划任务失败', e)
    alert(t('cron.loadFailed', { error: e?.message || e }))
  }
}

function toggleMenu() {
  showMenu.value = !showMenu.value
}

// --- 立即执行一次任务（后端支持超时/重试/报告） ---
async function runNow(task) {
  try {
    const res = await cronApi.run(task.id)
    alert(res.ok === false
      ? t('cron.executeFailed', { error: (res.output || '').slice(0, 2000) })
      : t('cron.executed'))
    if (res.report) {
      report.value = { show: true, task, content: res.output || '' }
    }
  } catch (e) {
    console.error('执行任务失败', e)
    alert(t('cron.executeFailed', { error: e?.message || e }))
  }
}

async function viewReport(task) {
  try {
    const data = await cronApi.report(task.id)
    report.value = { show: true, task, content: data.content || '' }
  } catch (e) {
    alert(t('cron.executeFailed', { error: e?.message || e }))
  }
}

// --- 导出 / 导入（JSON，含任务与脚本库） ---
async function exportTasks() {
  const data = await cronApi.exportAll()
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `graw-cron-${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(a.href)
}

function triggerImport() {
  importInput.value?.click()
}

async function importTasks(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  try {
    const data = JSON.parse(await file.text())
    const res = await cronApi.importAll({ tasks: data.tasks || [], scripts: data.scripts || [] })
    alert(t('cron.importDone', { tasks: res.tasks, scripts: res.scripts }))
    await load()
  } catch (err) {
    alert(t('cron.importFailed', { error: err?.message || err }))
  }
}

// --- 脚本库 ---
async function openScripts() {
  scriptLib.value = { show: true, id: '', name: '', group: '默认', content: '' }
  await loadScripts()
}

async function loadScripts() {
  try {
    const data = await cronApi.scripts()
    scripts.value = data.scripts || []
  } catch (e) {
    alert(e?.message || e)
  }
}

function editScript(s) {
  scriptLib.value = { show: true, id: s.id, name: s.name, group: s.group || '默认', content: s.content || '' }
}

function resetScriptForm() {
  scriptLib.value = { show: true, id: '', name: '', group: '默认', content: '' }
}

async function saveScript() {
  if (!scriptLib.value.name.trim() || !scriptLib.value.content.trim()) {
    alert(t('cron.nameContentRequired'))
    return
  }
  try {
    await cronApi.saveScript({
      id: scriptLib.value.id, name: scriptLib.value.name.trim(),
      group: scriptLib.value.group.trim() || '默认', content: scriptLib.value.content,
    })
    resetScriptForm()
    await loadScripts()
  } catch (e) {
    alert(e?.response?.data?.detail || e?.message || e)
  }
}

async function removeScript(s) {
  if (!window.confirm(t('cron.deleteScriptConfirm', { name: s.name }))) return
  try {
    await cronApi.deleteScript(s.id)
    await loadScripts()
  } catch (e) {
    alert(e?.response?.data?.detail || e?.message || e)
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
.dropdown { position: absolute; top: 100%; left: 0; margin-top: 4px; background: var(--g-surface); border: 1px solid var(--g-border); border-radius: 8px; box-shadow: 0 6px 20px rgba(0,0,0,0.12); z-index: 3000; min-width: 120px; overflow: hidden; }
.dropdown-item { padding: 8px 14px; font-size: 13px; cursor: pointer; color: var(--g-text); }
.dropdown-item:hover { background: var(--g-surface-soft); }
.grp { display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 999px; font-size: 11px; background: var(--g-primary-soft); color: var(--g-primary); }
.type-tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; background: var(--g-surface-soft); color: var(--g-text); margin-right: 6px; white-space: nowrap; }
.cmd-text { color: var(--g-text); max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: bottom; }
.bell { margin-left: 4px; }
.actions { display: flex; gap: 4px; }
.iconbtn { padding: 4px; border: 1px solid var(--g-border); background: var(--g-surface); border-radius: 6px; cursor: pointer; }
.iconbtn:hover { background: var(--g-surface-muted); }
.iconbtn.danger:hover { background: rgba(226, 50, 79, 0.16); border-color: var(--ui-danger-fg, #fca5a5); }
.report-code { margin: 0; padding: 10px; max-height: 52vh; overflow: auto; background: var(--g-code-bg, #1f2329); color: var(--g-text); border: 1px solid var(--g-border); border-radius: 8px; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.script-form { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--g-border-soft); }
</style>