<!--
  登录日志窗口（后端 /api/loginlog 模块）
  作用：查看面板登录历史（成功/失败/异常，异常指新 IP / 新设备登录），管理员可筛选全部账号日志、
        开关「异常登录提醒」推送、测试推送并清空全部日志；非管理员仅看自己账号的历史。
  后端模块：/api/loginlog（status 统计与提醒配置、list 全部日志、mine 当前账号日志、
            update_config 更新提醒开关、test_alert 测试推送、clear 清空全部）。
  关键状态：logs（日志列表）、stats（成功/失败/异常统计）、alertEnabled（异常提醒开关）、
            filterUsername/filterStatus（管理员筛选条件）、confirm（清空二次确认）。
  打开方式：桌面「登录日志」卡片（也可能内嵌于「日志」中心窗口）。
  清空日志为高风险操作，需输入面板密码（ConfirmDialog）确认。
-->
<template>
  <div class="loginlog-window">
    <!-- 工具栏：统计 / 告警开关 / 操作 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge"><Fingerprint :size="14" /> 登录日志</span>
        <span class="stat" title="总登录次数">共 {{ stats.total }} 次</span>
        <span class="stat ok" title="成功">成功 {{ stats.success }}</span>
        <span class="stat err" title="失败">失败 {{ stats.failed }}</span>
        <span class="stat warn" title="异常登录（新IP/新设备）">异常 {{ stats.abnormal }}</span>
      </div>
      <div class="toolbar-actions">
        <label class="toggle" :title="'异常登录提醒：新 IP / 新设备登录时推送通知'">
          <input type="checkbox" v-model="alertEnabled" @change="onToggleAlert" :disabled="!isAdmin()" />
          异常登录提醒
        </label>
        <button v-if="isAdmin()" class="btn" :disabled="busy" @click="doTestAlert">
          <Send :size="14" /> 测试推送
        </button>
        <button class="btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
        <button v-if="isAdmin()" class="btn danger-text" :disabled="busy" @click="doClear">清空</button>
      </div>
    </div>

    <!-- 筛选（仅管理员可看全部日志） -->
    <div class="filters">
      <input v-if="isAdmin()" v-model="filterUsername" class="input" placeholder="按账号筛选…" @input="loadAll" />
      <select v-model="filterStatus" class="input select" @change="loadAll">
        <option value="">全部状态</option>
        <option value="success">成功</option>
        <option value="failed">失败</option>
      </select>
      <span v-if="!isAdmin()" class="mine-hint">仅展示当前账号的登录历史</span>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="logs.length === 0" class="empty">
      <Fingerprint :size="40" style="color:#9ca3af;" />
      <div>暂无登录日志</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>账号</th>
            <th>IP</th>
            <th>设备</th>
            <th>结果</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="log in logs" :key="log.id">
            <td class="mono">{{ log.time }}</td>
            <td>{{ log.username }}</td>
            <td class="mono">{{ log.ip || '—' }}</td>
            <td :title="log.ua">{{ log.device }}</td>
            <td>
              <span class="badge" :class="log.status === 'success' ? 'ok' : 'err'">{{ log.status === 'success' ? '成功' : '失败' }}</span>
              <span v-if="log.abnormal" class="badge warn" :title="log.abnormal_reason">异常</span>
            </td>
            <td class="detail">{{ log.detail || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="msg" :class="['msg', msgType]">{{ msg }}</div>

    <!-- 高风险操作二次确认：清空登录日志需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      title="清空登录日志确认"
      message="清空全部登录日志后不可恢复。\n请输入面板密码以确认。"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      confirm-label="清空"
      @confirm="doClearConfirmed"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
// 响应式状态与生命周期钩子
import { ref, onMounted } from 'vue'
// 图标（统计徽标 / 刷新 / 推送）
import { Fingerprint, RefreshCw, Send } from 'lucide-vue-next'
// 登录日志 API：status/list/mine/update_config/test_alert/clear
import { loginlogApi } from '../../api'
// 权限判断：管理员可看全部日志与清空，普通用户只看自己的
import { isAdmin } from '../../store/auth'
// 高风险操作「输入面板密码」二次确认弹窗
import ConfirmDialog from '../ConfirmDialog.vue'

const loading = ref(false)              // 列表加载中
const busy = ref(false)                 // 清空/测试推送等操作进行中（防重复点击）
const logs = ref([])                    // 登录日志列表
const stats = ref({ total: 0, success: 0, failed: 0, abnormal: 0 })   // 顶部统计（总/成功/失败/异常）
const alertEnabled = ref(true)          // 异常登录提醒开关（仅管理员可改）
const filterUsername = ref('')          // 按账号筛选（仅管理员）
const filterStatus = ref('')            // 按结果筛选（成功/失败，仅管理员）
const msg = ref('')                     // 操作结果提示
const msgType = ref('')                 // 提示类型（ok / err）
// 高风险操作二次确认状态
const confirm = ref({ show: false })

// --- 动作：拉取登录统计与异常提醒开关状态 ---
async function loadStats() {
  try {
    stats.value = await loginlogApi.status()
    // 首次拉取状态时同步开关（仅管理员能改）
    alertEnabled.value = stats.value.alert_enabled !== false
  } catch (e) {
    // 兼容旧后端：静默忽略统计
  }
}

// --- 动作：加载日志列表（管理员带筛选条件，普通用户固定查自己的） ---
async function loadAll() {
  loading.value = true
  try {
    const params = { limit: 200 }
    if (isAdmin()) {
      if (filterUsername.value) params.username = filterUsername.value
      if (filterStatus.value) params.status = filterStatus.value
      const r = await loginlogApi.list(params)
      logs.value = r.logs || []
    } else {
      const r = await loginlogApi.mine(200)   // 非管理员：后端只返回当前账号的历史
      logs.value = r.logs || []
    }
  } catch (e) {
    msg.value = '加载失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    loading.value = false
  }
}

// --- 动作：同时刷新统计与列表（挂载及清空后复用） ---
async function refresh() {
  await Promise.all([loadStats(), loadAll()])
}

// --- 动作：切换异常登录提醒开关并持久化到后端 ---
async function onToggleAlert() {
  try {
    await loginlogApi.updateConfig(alertEnabled.value)
    msg.value = '异常登录提醒已' + (alertEnabled.value ? '开启' : '关闭')
    msgType.value = 'ok'
  } catch (e) {
    alertEnabled.value = !alertEnabled.value // 失败回滚
    msg.value = '设置失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  }
}

// --- 动作：向所有启用渠道发送一条测试推送 ---
async function doTestAlert() {
  busy.value = true
  msg.value = ''
  try {
    const r = await loginlogApi.testAlert()
    msg.value = r.sent > 0 ? `测试推送成功（${r.sent} 个渠道）` : '未配置启用的通知渠道'
    msgType.value = r.sent > 0 ? 'ok' : 'err'
  } catch (e) {
    msg.value = '推送失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

function doClear() {
  // 高风险操作：清空登录日志需输入面板密码确认
  confirm.value = { show: true }
}

// --- 动作：密码校验通过后执行清空并刷新 ---
async function doClearConfirmed() {
  confirm.value.show = false
  busy.value = true
  msg.value = ''
  try {
    await loginlogApi.clear()
    msg.value = '已清空'
    msgType.value = 'ok'
    await refresh()
  } catch (e) {
    msg.value = '清空失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

onMounted(refresh)
</script>

<style scoped>
.loginlog-window { padding: 0; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; } /* 内嵌聚合窗口：外边距由父容器提供 */
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; background: #dbeafe; color: #1e40af; }
.stat { font-size: 12px; color: #4b5563; }
.stat.ok { color: #065f46; }
.stat.err { color: #b91c1c; }
.stat.warn { color: #9a3412; }
.toolbar-actions { display: flex; gap: 8px; align-items: center; }
.toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: #374151; cursor: pointer; }
.toggle input { cursor: pointer; }

.filters { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
.input { padding: 6px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12.5px; width: 180px; }
.select { width: 130px; }
.mine-hint { font-size: 12px; color: #9ca3af; }

.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; flex: 1; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; }
.detail { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #6b7280; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.danger-text { color: #b91c1c; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; margin-right: 4px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.err { background: #fee2e2; color: #b91c1c; }
.badge.warn { background: #fed7aa; color: #9a3412; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.msg { font-size: 12.5px; margin-top: 10px; }
.msg.ok { color: #065f46; }
.msg.err { color: #b91c1c; }
</style>
