<!--
  系统体检窗口（后端 /api/healthcheck 模块）
  作用：以只读方式扫描系统安全隐患（弱密码 / 异常登录 / 危险端口 / 可疑定时任务 / 面板安全配置），
        给出综合评分与逐项处理建议，不做任何修复操作。
  后端模块：/api/healthcheck（run：执行扫描并返回体检报告 report）。
  关键状态：report（体检报告：评分 + 按级别分组的检查项）、flatItems（按高→中→低排序的表格行）、
            loading（扫描进行中）。
  打开方式：桌面「系统体检」卡片，点击「开始体检」触发扫描。
-->
<template>
  <div class="healthcheck-window">
    <!-- 顶部工具条：说明 + 评分汇总 + 运行按钮（与其它应用一致） -->
    <div class="toolbar">
      <div class="toolbar-left">
        <span class="title"><ShieldCheck :size="15" /> 系统体检</span>
        <span class="hint">只读扫描：弱密码 / 异常登录 / 危险端口 / 可疑定时任务 / 面板安全配置，不做任何修复操作</span>
      </div>
      <div class="toolbar-right">
        <template v-if="report">
          <span class="score-txt">综合评分 <b :class="scoreClass">{{ report.score }}</b></span>
          <span class="badge total">{{ report.summary?.total || 0 }} 项</span>
          <span class="badge high">高危 {{ report.summary?.high || 0 }}</span>
          <span class="badge med">中危 {{ report.summary?.medium || 0 }}</span>
          <span class="badge low">低危 {{ report.summary?.low || 0 }}</span>
        </template>
        <button class="btn primary" :disabled="loading" @click="run">
          <RefreshCw :size="14" :class="{ spinning: loading }" /> {{ loading ? '体检中…' : '开始体检' }}
        </button>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-if="loading" class="empty">正在扫描，请稍候…</div>
    <div v-else-if="!report" class="empty">
      <ShieldCheck :size="40" style="color:#9ca3af;" />
      <div>点击「开始体检」扫描系统安全隐患</div>
    </div>
    <div v-else-if="report.items.length === 0" class="empty">
      <ShieldCheck :size="40" style="color:#16a34a;" />
      <div>未发现明显安全隐患，继续保持！</div>
    </div>

    <!-- 体检结果：表格（与其它应用风格统一） -->
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th style="width:70px;">级别</th>
            <th style="width:180px;">检查项</th>
            <th>说明</th>
            <th style="width:40%;">处理建议</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(it, idx) in flatItems" :key="idx">
            <td><span class="badge" :class="'lv-' + it.level">{{ levelText(it.level) }}</span></td>
            <td class="mono">{{ it.title }}</td>
            <td>{{ it.detail }}</td>
            <td><span class="advice-label">建议</span> {{ it.advice }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与计算属性
import { ref, computed } from 'vue'
// 图标（体检入口与状态）
import { RefreshCw, ShieldCheck } from 'lucide-vue-next'
// 体检 API：封装 /api/healthcheck/run
import { healthcheckApi } from '../../api'

const loading = ref(false)   // 扫描进行中（防止重复提交）
const report = ref(null)     // 体检报告（评分 + 检查项），null 表示尚未扫描

// 按 高→中→低 顺序拍平所有体检项，作为表格行
const flatItems = computed(() => {
  const items = report.value?.items || []
  const order = { high: 0, medium: 1, low: 2 }
  return [...items].sort((a, b) => (order[a.level] ?? 3) - (order[b.level] ?? 3))
})

// 评分配色与文案
const scoreClass = computed(() => {
  const s = report.value?.score ?? 100
  if (s >= 90) return 'good'
  if (s >= 70) return 'warn'
  return 'bad'
})

// 危险级别 → 中文文案
function levelText(lv) {
  return { high: '高危', medium: '中危', low: '低危' }[lv] || lv
}

// --- 动作：执行安全体检扫描 ---
async function run() {
  if (loading.value) return   // 扫描中直接返回，避免并发重复请求
  loading.value = true
  try {
    const r = await healthcheckApi.run()   // 调用 /api/healthcheck/run
    report.value = r
  } catch (e) {
    alert('体检失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.healthcheck-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: space-between; }
.toolbar-left { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.title { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 700; }
.hint { color: #6e6e73; font-size: 12px; }
.toolbar-right { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.score-txt { font-size: 12px; color: #374151; margin-right: 6px; }
.score-txt b { font-size: 16px; }
.score-txt b.good { color: #16a34a; }
.score-txt b.warn { color: #d97706; }
.score-txt b.bad { color: #dc2626; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge.total { background: #e0e7ff; color: #3730a3; }
.badge.high, .badge.lv-high { background: #fee2e2; color: #b91c1c; }
.badge.med, .badge.lv-medium { background: #ffedd5; color: #c2410c; }
.badge.low, .badge.lv-low { background: #d1fae5; color: #065f46; }
.badge.lv-high, .badge.lv-medium, .badge.lv-low { font-weight: 600; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.table-wrap { overflow: auto; flex: 1; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: top; }
th { background: #f9fafb; position: sticky; top: 0; font-weight: 600; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; font-weight: 600; }
.advice-label { color: #374151; font-weight: 600; }
</style>