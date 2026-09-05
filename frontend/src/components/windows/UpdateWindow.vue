<!--
  版本更新窗口（Update）

  这个窗口做什么：
    面板「更新」应用。展示当前版本与最新版本，检查有无可用更新，
    并对 Docker 容器部署的面板执行一键更新：
      - 检查更新（GET /api/update/status）：返回当前 / 最新版本、部署模式与部署细分；
      - 一键更新（POST /api/update/apply）：支持 compose（docker compose up -d）
        与 docker run 单容器（README 安装方式）两种部署形态，触发后后端
        拉取新镜像并重建面板容器，页面会短暂不可用；
      - 查看更新日志（GET /api/update/log）：展示最近一次更新执行详情。
    本机运行（非容器）只提示手动升级，不提供一键按钮。

  用到的后端模块：
    /api/update/*（管理员权限）——status 检查版本、apply 触发更新、log 查看日志。

  关键状态：
    status      版本状态（当前 / 最新版本、deploy_mode、deploy_detail、check_error）
    loading     检查更新进行中
    applying    应用更新进行中
    error       操作失败提示
    showLog     更新日志面板展开状态

  怎么被打开：
    「设置」窗口（SettingsWindow）的「更新」页签内嵌。
-->
<template>
  <div class="update-window">
    <!-- 顶部工具栏：版本信息 + 操作按钮 -->
    <div class="toolbar">
      <div class="version-block">
        <div class="version-row">
          <span class="label">当前版本</span>
          <span class="value">{{ status?.current_version || '—' }}</span>
        </div>
        <div class="version-row">
          <span class="label">最新版本</span>
          <span class="value">{{ status?.latest_version || '—' }}</span>
          <!-- 更新徽标：有新版本显示可更新；无错误且无新版本时显示已是最新 -->
          <span v-if="status?.update_available" class="badge available">可更新</span>
          <span v-else-if="status && !status.check_error" class="badge latest">已是最新</span>
        </div>
      </div>
      <div class="toolbar-right">
        <button class="btn" :disabled="loading" @click="check">
          <RefreshCw :size="14" :class="{ spinning: loading }" /> {{ loading ? '检查中…' : '检查更新' }}
        </button>
        <!-- 仅当有新版本且后端支持一键更新（compose / docker run）时才显示 -->
        <button v-if="canApply" class="btn primary" :disabled="applying" @click="apply">
          <Download :size="14" :class="{ spinning: applying }" /> {{ applying ? '更新中…' : '立即更新' }}
        </button>
        <button class="btn" :disabled="!status" @click="loadLog">
          <FileText :size="14" /> {{ showLog ? '收起日志' : '查看日志' }}
        </button>
      </div>
    </div>

    <!-- 部署模式提示：本机 / 自定义镜像不支持面板内一键更新，需手动升级 -->
    <div class="hint-bar">
      <Info :size="14" /> 部署模式：{{ deployModeText }}<span v-if="status && !canApply">，当前部署模式不支持面板内一键更新，请手动升级</span>
    </div>

    <!-- 错误信息：接口调用失败 / 应用更新失败 -->
    <div v-if="error" class="error-bar">
      <AlertTriangle :size="14" /> {{ error }}
    </div>

    <!-- 版本状态主体 -->
    <div v-if="!status" class="empty">
      <RefreshCw :size="40" style="color:#9ca3af;" />
      <div>点击「检查更新」查询面板最新版本</div>
    </div>
    <div v-else-if="status.check_error" class="empty">
      <AlertTriangle :size="40" style="color:#d97706;" />
      <div>版本检查失败：{{ status.check_error }}</div>
    </div>

    <!-- 更新日志（最近一次一键更新执行详情，展开查看） -->
    <div v-if="showLog" class="log-box">
      <div class="log-title">最近更新日志</div>
      <pre>{{ logContent }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'   // 响应式状态、派生文案与「可更新」判断
import { RefreshCw, Download, Info, AlertTriangle, FileText } from 'lucide-vue-next'   // 检查 / 更新 / 提示 / 日志图标
import { updateApi } from '../../api'   // 更新后端能力：/api/update/* 的封装

// 版本状态数据（来自 GET /api/update/status）
const status = ref(null)
const loading = ref(false)
const applying = ref(false)
// 操作错误信息（检查/应用更新失败时展示）
const error = ref('')
// 更新日志展示
const showLog = ref(false)
const logContent = ref('')

// 部署模式文案映射（后端返回 deploy_mode: docker/local + deploy_detail: compose/docker-run/unsupported）
const deployModeText = computed(() => {
  const detail = status.value?.deploy_detail
  const detailMap = {
    compose: 'Docker 容器（compose）',
    'docker-run': 'Docker 容器（docker run）',
    unsupported: 'Docker 容器（自定义镜像）',
    local: '本机运行'
  }
  return detailMap[detail] || status.value?.deploy_mode || '未知'
})

// 是否可一键更新：有新版本 且 部署为官方镜像的 Docker 容器（compose / docker run 均可）
const canApply = computed(() => {
  const s = status.value
  if (!s?.update_available || s.deploy_mode !== 'docker') return false
  return s.deploy_detail === 'compose' || s.deploy_detail === 'docker-run'
})

// 检查更新：GET /api/update/status
async function check() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    status.value = await updateApi.status()
    console.log('[UpdateWindow] 版本状态:', status.value)
  } catch (e) {
    error.value = '检查更新失败：' + (e.response?.data?.detail || e.message)
    console.warn('[UpdateWindow] 检查更新失败:', e)
  } finally {
    loading.value = false
  }
}

// 应用更新：POST /api/update/apply（compose / docker run 部署）
async function apply() {
  if (applying.value) return
  if (!confirm(`确定要一键更新到 ${status.value?.latest_version || ''} 吗？面板容器将按原配置自动重建（挂载、端口、环境变量保持不变），期间页面短暂不可用。`)) return
  applying.value = true
  error.value = ''
  try {
    const res = await updateApi.apply()
    // 更新已后台启动，面板容器将重建，提示用户稍后刷新页面（更新详情可查看「更新日志」）
    alert(res.message || '更新已启动，面板容器将自动重建，请稍后刷新页面。')
    console.log('[UpdateWindow] 更新已触发:', res)
  } catch (e) {
    error.value = '更新失败：' + (e.response?.data?.detail || e.message)
    console.warn('[UpdateWindow] 应用更新失败:', e)
  } finally {
    applying.value = false
  }
}

// 展开 / 收起更新日志：GET /api/update/log（首次展开时拉取）
async function loadLog() {
  showLog.value = !showLog.value
  if (!showLog.value) return
  if (!logContent.value) {
    try {
      logContent.value = (await updateApi.log()).log || '(暂无更新记录)'
    } catch (e) {
      logContent.value = '读取日志失败：' + (e.response?.data?.detail || e.message)
    }
  }
}
</script>

<style scoped>
.update-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: space-between; }
.version-block { display: flex; flex-direction: column; gap: 6px; }
.version-row { display: flex; align-items: center; gap: 8px; }
.version-row .label { font-size: 12px; color: #6e6e73; min-width: 52px; }
.version-row .value { font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.badge.available { background: #fee2e2; color: #b91c1c; }
.badge.latest { background: #d1fae5; color: #065f46; }
.toolbar-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.hint-bar { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #6e6e73; background: #f9fafb; border: 1px solid #f0f0f0; border-radius: 8px; padding: 6px 10px; }
.error-bar { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 6px 10px; }
.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.log-box { flex: 1; min-height: 0; display: flex; flex-direction: column; border: 1px solid #e5e7eb; border-radius: 8px; background: #0f172a; overflow: hidden; }
.log-title { padding: 6px 10px; font-size: 12px; color: #94a3b8; background: #1e293b; border-bottom: 1px solid #334155; }
.log-box pre { flex: 1; margin: 0; padding: 10px; overflow: auto; font-size: 12px; line-height: 1.5; color: #e2e8f0; font-family: 'Cascadia Code', Consolas, 'Courier New', monospace; white-space: pre-wrap; word-break: break-all; }
</style>
