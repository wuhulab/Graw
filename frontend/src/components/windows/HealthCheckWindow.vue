<template>
  <div class="healthcheck-window">
    <!-- 顶部：评分 + 运行按钮 -->
    <div class="toolbar">
      <div class="score-block">
        <div class="score-ring" :class="scoreClass">
          <span class="score-num">{{ report ? report.score : '—' }}</span>
        </div>
        <div class="score-info">
          <div class="score-title">系统体检评分</div>
          <div class="score-sub">{{ scoreText }}</div>
        </div>
      </div>
      <div class="toolbar-right">
        <div v-if="report" class="summary">
          <span class="badge total">{{ report.summary.total }} 项</span>
          <span class="badge high">高危 {{ report.summary.high }}</span>
          <span class="badge med">中危 {{ report.summary.medium }}</span>
          <span class="badge low">低危 {{ report.summary.low }}</span>
        </div>
        <button class="btn primary" :disabled="loading" @click="run">
          <RefreshCw :size="14" :class="{ spinning: loading }" /> {{ loading ? '体检中…' : '开始体检' }}
        </button>
      </div>
    </div>

    <!-- 体检说明 -->
    <div class="hint-bar">
      <ShieldCheck :size="14" /> 体检项均只读：弱密码、异常登录、危险开放端口、可疑定时任务、面板安全配置。不做任何修复操作。
    </div>

    <!-- 结果列表 -->
    <div v-if="loading" class="empty">正在扫描，请稍候…</div>
    <div v-else-if="!report" class="empty">
      <ShieldCheck :size="40" style="color:#9ca3af;" />
      <div>点击「开始体检」扫描系统安全隐患</div>
    </div>
    <div v-else-if="report.items.length === 0" class="empty">
      <ShieldCheck :size="40" style="color:#16a34a;" />
      <div>未发现明显安全隐患，继续保持！</div>
    </div>
    <div v-else class="items-wrap">
      <!-- 高危 -->
      <div v-if="grouped.high.length" class="group">
        <div class="group-title high"><AlertTriangle :size="15" /> 高危（{{ grouped.high.length }}）</div>
        <div v-for="(it, idx) in grouped.high" :key="'h' + idx" class="item high">
          <div class="item-title">{{ it.title }}</div>
          <div class="item-detail">{{ it.detail }}</div>
          <div class="item-advice"><span>建议：</span>{{ it.advice }}</div>
        </div>
      </div>
      <!-- 中危 -->
      <div v-if="grouped.medium.length" class="group">
        <div class="group-title medium"><AlertCircle :size="15" /> 中危（{{ grouped.medium.length }}）</div>
        <div v-for="(it, idx) in grouped.medium" :key="'m' + idx" class="item medium">
          <div class="item-title">{{ it.title }}</div>
          <div class="item-detail">{{ it.detail }}</div>
          <div class="item-advice"><span>建议：</span>{{ it.advice }}</div>
        </div>
      </div>
      <!-- 低危 -->
      <div v-if="grouped.low.length" class="group">
        <div class="group-title low"><Info :size="15" /> 低危（{{ grouped.low.length }}）</div>
        <div v-for="(it, idx) in grouped.low" :key="'l' + idx" class="item low">
          <div class="item-title">{{ it.title }}</div>
          <div class="item-detail">{{ it.detail }}</div>
          <div class="item-advice"><span>建议：</span>{{ it.advice }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { RefreshCw, ShieldCheck, AlertTriangle, AlertCircle, Info } from 'lucide-vue-next'
import { healthcheckApi } from '../../api'

const loading = ref(false)
const report = ref(null)

// 按级别分组，便于分级展示
const grouped = computed(() => {
  const items = report.value?.items || []
  return {
    high: items.filter((i) => i.level === 'high'),
    medium: items.filter((i) => i.level === 'medium'),
    low: items.filter((i) => i.level === 'low'),
  }
})

// 评分对应的语义与配色
const scoreClass = computed(() => {
  const s = report.value?.score ?? 100
  if (s >= 90) return 'good'
  if (s >= 70) return 'warn'
  return 'bad'
})
const scoreText = computed(() => {
  const s = report.value?.score ?? 100
  if (s >= 90) return '状态良好'
  if (s >= 70) return '存在隐患，建议处理'
  return '风险较高，请尽快处理'
})

async function run() {
  if (loading.value) return
  loading.value = true
  try {
    const r = await healthcheckApi.run()
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
.toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: space-between; }
.score-block { display: flex; align-items: center; gap: 12px; }
.score-ring { width: 58px; height: 58px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: #fff; font-weight: 700; font-size: 20px; }
.score-ring.good { background: #16a34a; }
.score-ring.warn { background: #d97706; }
.score-ring.bad { background: #dc2626; }
.score-info .score-title { font-size: 14px; font-weight: 700; }
.score-info .score-sub { font-size: 12px; color: #6e6e73; }
.toolbar-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.summary { display: flex; gap: 6px; flex-wrap: wrap; }
.badge { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.badge.total { background: #e0e7ff; color: #3730a3; }
.badge.high { background: #fee2e2; color: #b91c1c; }
.badge.med { background: #ffedd5; color: #c2410c; }
.badge.low { background: #d1fae5; color: #065f46; }
.hint-bar { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #6e6e73; background: #f9fafb; border: 1px solid #f0f0f0; border-radius: 8px; padding: 6px 10px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.items-wrap { overflow: auto; flex: 1; display: flex; flex-direction: column; gap: 12px; padding-right: 4px; }
.group { display: flex; flex-direction: column; gap: 6px; }
.group-title { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 700; padding: 6px 0; }
.group-title.high { color: #b91c1c; }
.group-title.medium { color: #c2410c; }
.group-title.low { color: #065f46; }
.item { border-radius: 8px; padding: 10px 12px; border-left: 3px solid; }
.item.high { background: #fef2f2; border-color: #dc2626; }
.item.medium { background: #fff7ed; border-color: #d97706; }
.item.low { background: #f0fdf4; border-color: #16a34a; }
.item-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.item-detail { font-size: 12px; color: #4b5563; line-height: 1.5; }
.item-advice { font-size: 12px; color: #6b7280; margin-top: 4px; }
.item-advice span { color: #374151; font-weight: 600; }
</style>
