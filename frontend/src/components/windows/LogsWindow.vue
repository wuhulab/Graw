<!--
  日志中心窗口（后端 /api/logs 模块）
  作用：浏览 / 清空服务器日志文件，支持自定义添加日志源（名称 + 路径），并把「登录日志」「审计日志」
        两个独立应用合并为标签页在此统一展示。
  后端模块：/api/logs（list 日志源列表、read 读取末尾内容、clear 清空、add 添加自定义日志源）。
  关键状态：mode（logs/login/audit 视图）、logs（日志源）、current/lines（当前查看的日志内容）、
            confirm（清空二次确认）。
  清空日志为高风险操作，需输入面板密码（ConfirmDialog）确认。
  打开方式：桌面「日志」卡片；登录 / 审计视图内嵌 LoginLogWindow / AuditLogWindow 子组件。
-->
<template>
  <div class="logs-window">
    <div class="toolbar">
      <!-- 视图切换：系统日志 / 登录日志 / 审计日志 -->
      <div class="mode-tabs">
        <button class="tab" :class="{ active: mode === 'logs' }" @click="switchMode('logs')">{{ $t('logs.modeSys') }}</button>
        <button class="tab" :class="{ active: mode === 'login' }" @click="switchMode('login')">{{ $t('logs.modeLogin') }}</button>
        <button class="tab" :class="{ active: mode === 'audit' }" @click="switchMode('audit')">{{ $t('logs.modeAudit') }}</button>
      </div>
      <template v-if="mode === 'logs'">
        <button class="btn primary" @click="emit('openLogCollectForm', { source: null })">{{ $t('logs.add') }}</button>
        <button class="btn" @click="refresh">{{ $t('logs.refresh') }}</button>
      </template>
    </div>

    <!-- 系统日志视图 -->
    <div v-if="mode === 'logs'" class="layout">
      <div class="sidebar">
        <div v-for="log in logs" :key="log.id" class="log-item" :class="{active: currentId===log.id}" @click="select(log)">
          <div class="log-name">{{ logName(log) }}</div>
          <div class="log-path">{{ log.path }}</div>
          <span class="exist" :class="log.exists?'ok':'warn'">{{ log.exists ? $t('logs.exists') : $t('logs.missing') }}</span>
        </div>
      </div>
      <div class="viewer">
        <div class="viewer-toolbar" v-if="current">
          <span class="meta">{{ $t('logs.viewing', { path: current.path, lines: lines.length }) }}</span>
          <button class="btn small" @click="clearLog(current.path)">{{ $t('logs.clear') }}</button>
          <button class="btn small" @click="loadLog(current.path)">{{ $t('logs.refresh') }}</button>
        </div>
        <pre class="content">{{ contentText }}</pre>
      </div>
    </div>

    <!-- 登录日志视图（合并自独立的「登录日志」应用） -->
    <div v-else-if="mode === 'login'" class="login-wrap">
      <LoginLogWindow />
    </div>

    <!-- 审计日志视图（合并自独立的「审计日志」应用） -->
    <div v-else class="login-wrap">
      <AuditLogWindow />
    </div>

    <!-- 添加日志源表单改由独立窗口承载（LogCollectFormWindow），避免点遮罩误关丢失输入 -->

    <!-- 高风险操作二次确认：清空日志需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="t('confirmDanger.clearLogsTitle')"
      :message="t('confirmDanger.clearLogsMsg')"
      :input-label="t('confirmDanger.inputPwdLabel')"
      :placeholder="t('confirmDanger.inputPwdPlaceholder')"
      :confirm-label="$t('logs.clear')"
      @confirm="doClearLog"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
// 响应式状态、计算属性与生命周期钩子
import { ref, computed, watch, onMounted } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 日志 API：list/read/clear/add
import { logsApi } from '../../api'
// 高风险操作「输入面板密码」二次确认弹窗
import ConfirmDialog from '../ConfirmDialog.vue'
// 合并进来的子窗口：登录日志（/api/loginlog）与审计日志（/api/logs audit）的完整实现
import LoginLogWindow from './LoginLogWindow.vue'
import AuditLogWindow from './AuditLogWindow.vue'
// 表单保存信号：独立「添加日志源」窗口保存成功后刷新本列表
import { formBus } from '../../store/formBus'

const { t } = useI18n()

const emit = defineEmits(['openLogCollectForm'])   // 打开独立「添加日志源」窗口（source: 编辑对象或 null）

const logs = ref([])         // 日志源列表（内置 + 自定义）
const currentId = ref(null)  // 当前选中的日志源 id
const current = ref(null)    // 当前选中的日志源对象
const lines = ref([])        // 当前日志文件读出的内容行
// 高风险操作二次确认状态
const confirm = ref({ show: false, path: '' })
// 视图模式：'logs' 系统日志 / 'login' 登录日志 / 'audit' 审计日志
const mode = ref('logs')

// 添加日志源改由独立窗口承载：保存成功后 bumpForm('logs') 触发此处重载
watch(() => formBus.logs, refresh)

// --- 动作：切换视图标签（系统/登录/审计） ---
function switchMode(m) {
  mode.value = m
}

// 内容视图：把多行数组直接拼成文本供 <pre> 显示
const contentText = computed(() => lines.value.join(''))

// 内置日志的 desc（如“面板日志”）由后端返回中文名，这里按 id 走 i18n 翻译；
// 自定义日志用用户填写的名称。
function logName(log) {
  if (log && log.builtin) {
    const key = `logs.source.${log.id}`
    const translated = t(key)
    // 若该键缺失，t 会原样返回 key 路径，此时退回后端名称
    return translated === key ? (log.name || '') : translated
  }
  return log ? log.name : ''
}

// --- 动作：拉取日志源列表 ---
async function refresh() {
  const data = await logsApi.list()   // 调用 /api/logs/list
  logs.value = data.logs || []
}

// --- 动作：选中日志源（存在则加载内容） ---
function select(log) {
  currentId.value = log.id
  current.value = log
  if (log.exists) loadLog(log.path)   // 文件存在才读取，避免对缺失文件报错
}

// --- 动作：读取日志文件末尾 500 行 ---
async function loadLog(path) {
  const data = await logsApi.read(path, 500)
  lines.value = data.lines || []
}

// 清空日志：高风险操作，先弹密码确认框
function clearLog(path) {
  // 高风险操作：清空日志需输入面板密码确认
  confirm.value = { show: true, path }
}

// --- 动作：密码校验通过后真正清空并重读 ---
async function doClearLog() {
  const path = confirm.value.path
  confirm.value.show = false
  if (!path) return   // 无目标路径则提前返回
  await logsApi.clear(path)   // 调用 /api/logs/clear
  await loadLog(path)
}

onMounted(refresh)   // 打开即加载日志源列表
</script>

<style scoped>
.logs-window { padding: 10px; display: flex; flex-direction: column; height: 100%; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
/* 视图切换标签 */
.mode-tabs { display: inline-flex; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }
.mode-tabs .tab { padding: 6px 14px; font-size: 13px; background: #fff; border: none; cursor: pointer; color: #6b7280; }
.mode-tabs .tab.active { background: #111827; color: #fff; }
/* 登录日志视图容器 */
.login-wrap { flex: 1; min-height: 0; }
.layout { display: flex; gap: 10px; flex: 1; min-height: 0; }
.sidebar { width: 220px; border: 1px solid #e5e7eb; border-radius: 8px; overflow: auto; background: #fff; }
.log-item { padding: 10px 12px; border-bottom: 1px solid #f3f4f6; cursor: pointer; }
.log-item:hover, .log-item.active { background: #f9fafb; }
.log-name { font-weight: 600; font-size: 13px; }
.log-path { font-size: 11px; color: #6b7280; word-break: break-all; }
.exist { font-size: 11px; padding: 1px 6px; border-radius: 999px; }
.exist.ok { background: #d1fae5; color: #065f46; }
.exist.warn { background: #fee2e2; color: #991b1b; }
.viewer { flex: 1; border: 1px solid #e5e7eb; border-radius: 8px; display: flex; flex-direction: column; background: #fff; overflow: hidden; }
.viewer-toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid #f3f4f6; }
.meta { font-size: 12px; color: #6b7280; margin-right: auto; }
.content { flex: 1; padding: 10px; overflow: auto; font-size: 12px; line-height: 1.5; background: #fff; color: #111827; margin: 0; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.small { padding: 4px 8px; font-size: 12px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
</style>
