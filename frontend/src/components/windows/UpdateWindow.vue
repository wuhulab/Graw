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
        <!-- 仅当有新版本且为 docker compose 部署时才显示一键更新按钮 -->
        <button v-if="canApply" class="btn primary" :disabled="applying" @click="apply">
          <Download :size="14" :class="{ spinning: applying }" /> {{ applying ? '更新中…' : '立即更新' }}
        </button>
      </div>
    </div>

    <!-- 部署模式提示：非 compose 部署时不支持面板内一键更新 -->
    <div class="hint-bar">
      <Info :size="14" /> 部署模式：{{ deployModeText }}<span v-if="status && !canApply">，当前部署模式不支持面板内一键更新，请手动升级</span>
    </div>

    <!-- 错误信息：接口调用失败 / 应用更新失败 -->
    <div v-if="error" class="error-bar">
      <AlertTriangle :size="14" /> {{ error }}
    </div>

    <!-- 状态说明 -->
    <div v-if="!status" class="empty">
      <RefreshCw :size="40" style="color:#9ca3af;" />
      <div>点击「检查更新」查询面板最新版本</div>
    </div>
    <div v-else-if="status.check_error" class="empty">
      <AlertTriangle :size="40" style="color:#d97706;" />
      <div>版本检查失败：{{ status.check_error }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { RefreshCw, Download, Info, AlertTriangle } from 'lucide-vue-next'
import { updateApi } from '../../api'

// 版本状态数据（来自 GET /api/update/status）
const status = ref(null)
const loading = ref(false)
const applying = ref(false)
// 操作错误信息（检查/应用更新失败时展示）
const error = ref('')

// 部署模式文案映射（后端返回 docker / local）
const deployModeText = computed(() => {
  const mode = status.value?.deploy_mode
  if (mode === 'docker') return 'Docker 容器（compose）'
  if (mode === 'local') return '本机运行'
  return mode || '未知'
})

// 是否可一键更新：有新版本 且 部署模式为 docker compose（后端仅该模式支持）
const canApply = computed(() => !!(status.value?.update_available && status.value?.deploy_mode === 'docker'))

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

// 应用更新：POST /api/update/apply（仅 docker compose 模式）
async function apply() {
  if (applying.value) return
  if (!confirm(`确定要一键更新到 ${status.value?.latest_version || ''} 吗？面板容器将自动重建，期间页面短暂不可用。`)) return
  applying.value = true
  error.value = ''
  try {
    const res = await updateApi.apply()
    // 更新已后台启动，面板容器将重建，提示用户稍后刷新页面
    alert(res.message || '更新已启动，面板容器将自动重建，请稍后刷新页面。')
    console.log('[UpdateWindow] 更新已触发:', res)
  } catch (e) {
    error.value = '更新失败：' + (e.response?.data?.detail || e.message)
    console.warn('[UpdateWindow] 应用更新失败:', e)
  } finally {
    applying.value = false
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
</style>
