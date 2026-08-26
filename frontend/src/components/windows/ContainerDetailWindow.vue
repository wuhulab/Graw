<!--
  ContainerDetailWindow.vue — 容器详情窗口
  ==========================================================
  业务作用：
    展示某个 Docker 容器的运行时详情：CPU/内存/缓存用量与限制、分层与虚拟
    大小、创建时间，以及基础信息（状态/镜像/PID/重启次数/网络模式/重启策略）、
    启动命令、挂载卷与环境变量。打开即自动拉取。
  后端模块：
    /api/docker 的 inspect（docker inspect 包装）。
  关键状态：
    - info  容器 inspect 结果对象
    - error 拉取失败提示
  打开方式：
    由 Docker 管理窗口的容器「详情」按钮打开，props 传入容器 id 与名称。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <span style="color:#0a3d7a; font-weight:600;">{{ $t('containerdetail.title', { name }) }}</span>
      <span style="font-size:11px; color:#888; font-family:Consolas,monospace;">{{ id }}</span>
      <button class="btn" style="margin-left:auto;" @click="load">{{ $t('containerdetail.refresh') }}</button>
      <button class="btn" @click="$emit('close')">{{ $t('containerdetail.close') }}</button>
    </div>
    <div style="flex:1; overflow:auto; padding:12px;">
      <div v-if="loading" class="empty">{{ $t('containerdetail.loading') }}</div>
      <div v-else-if="error" class="empty" style="color:#b91c1c;">{{ error }}</div>
      <div v-else-if="info" class="detail-grid">
        <div class="detail-card">
          <div class="detail-label">{{ $t('containerdetail.createdAt') }}</div>
          <div class="detail-value">{{ formatTime(info.created) }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">{{ $t('containerdetail.cpuUsage') }}</div>
          <div class="detail-value">
            <span :style="{ color: cpuColor(info.cpu_percent) }">{{ info.cpu_percent }}%</span>
            <span class="sub" v-if="info.mem_percent">{{ $t('containerdetail.memUsage', { percent: info.mem_percent }) }}</span>
          </div>
        </div>
        <div class="detail-card">
          <div class="detail-label">{{ $t('containerdetail.cpuTime') }}</div>
          <div class="detail-value">{{ info.cpu_time || '—' }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">{{ $t('containerdetail.cpuCores') }}</div>
          <div class="detail-value">{{ info.cpu_cores ? $t('containerdetail.cores', { count: info.cpu_cores }) : $t('containerdetail.unlimited') }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">{{ $t('containerdetail.memoryUsage') }}</div>
          <div class="detail-value">{{ formatBytes(info.mem_usage) }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">{{ $t('containerdetail.cacheUsage') }}</div>
          <div class="detail-value">{{ formatBytes(info.cache_usage) }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">{{ $t('containerdetail.memoryLimit') }}</div>
          <div class="detail-value">{{ info.mem_limit ? formatBytes(info.mem_limit) : $t('containerdetail.unlimited') }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">{{ $t('containerdetail.layerSize') }}</div>
          <div class="detail-value">{{ formatBytes(info.layer_size) }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">{{ $t('containerdetail.virtualSize') }}</div>
          <div class="detail-value">{{ formatBytes(info.virtual_size) }}</div>
        </div>

        <div class="detail-card wide">
          <div class="detail-label">{{ $t('containerdetail.basicInfo') }}</div>
          <div class="detail-value kv">
            <div><span>{{ $t('containerdetail.status') }}</span><b>{{ info.state }}</b></div>
            <div><span>{{ $t('containerdetail.image') }}</span><b class="wrap">{{ info.image }}</b></div>
            <div><span>{{ $t('containerdetail.pid') }}</span><b>{{ info.pid || '—' }}</b></div>
            <div><span>{{ $t('containerdetail.restartCount') }}</span><b>{{ info.restart_count }}</b></div>
            <div><span>{{ $t('containerdetail.networkMode') }}</span><b>{{ info.network_mode || '—' }}</b></div>
            <div><span>{{ $t('containerdetail.restartPolicy') }}</span><b>{{ info.restart_policy || '—' }}</b></div>
          </div>
        </div>

        <div class="detail-card wide">
          <div class="detail-label">{{ $t('containerdetail.command') }}</div>
          <div class="detail-value mono wrap">{{ cmdText }}</div>
        </div>

        <div class="detail-card wide" v-if="info.mounts && info.mounts.length">
          <div class="detail-label">{{ $t('containerdetail.mounts', { count: info.mounts.length }) }}</div>
          <div class="detail-value mono wrap">
            <div v-for="(m, i) in info.mounts" :key="i" style="margin:2px 0;">
              <span style="color:#555;">{{ m.Type || 'bind' }}:</span> {{ m.Source || m.Name || '' }} → {{ m.Destination || '' }}
            </div>
          </div>
        </div>

        <div class="detail-card wide" v-if="info.env && info.env.length">
          <div class="detail-label">{{ $t('containerdetail.env', { count: info.env.length }) }}</div>
          <div class="detail-value mono wrap">
            <div v-for="(e, i) in info.env" :key="i" style="margin:1px 0;">{{ e }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'   // 状态/挂载加载/派生命令文本
import { useI18n } from 'vue-i18n'   // 翻译函数
import { dockerApi } from '../../api'   // /api/docker：容器 inspect 接口

const { t } = useI18n()

const props = defineProps({ id: String, name: String })   // 容器 id 与显示名称
const emit = defineEmits(['close'])   // 对外仅暴露 close

const info = ref(null)   // 容器 inspect 结果对象
const loading = ref(false)   // 加载中
const error = ref('')   // 加载失败提示

// 启动命令 = entrypoint + cmd 合并展示（两者可能是字符串或数组，统一归一化）
const cmdText = computed(() => {
  if (!info.value) return ''
  const parts = []
  if (info.value.entrypoint) parts.push(...(Array.isArray(info.value.entrypoint) ? info.value.entrypoint : [info.value.entrypoint]))
  if (info.value.cmd) parts.push(...(Array.isArray(info.value.cmd) ? info.value.cmd : [info.value.cmd]))
  return parts.join(' ') || '—'
})

// --- 拉取容器详情 ---
async function load() {
  loading.value = true
  error.value = ''
  try {
    info.value = await dockerApi.inspect(props.id)
  } catch (e) {
    error.value = t('containerdetail.loadDetailFailed', { error: e.response?.data?.detail || e.message })
  } finally {
    loading.value = false
  }
}

// ISO 时间 → "YYYY-MM-DD HH:mm:ss"；无效值原样返回
function formatTime(t) {
  if (!t) return '—'
  const d = new Date(t)
  if (isNaN(d.getTime())) return String(t)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// 字节数人性化展示；精度随数值量级递减（小数值保留小数位）
function formatBytes(bytes) {
  if (bytes == null || isNaN(bytes) || bytes === 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(v < 10 && i > 0 ? 2 : v < 100 ? 1 : 0) + ' ' + units[i]
}

// CPU 使用率配色：≥80% 红 / ≥50% 琥珀 / 其余绿
function cpuColor(pct) {
  const v = Number(pct) || 0
  if (v >= 80) return '#b91c1c'
  if (v >= 50) return '#b45309'
  return '#2a8f3c'
}

onMounted(load)   // 打开窗口即加载详情
</script>

<style scoped>
.empty { text-align: center; color: #9ca3af; padding: 40px; }
.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 10px;
}
.detail-card {
  border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px;
  background: #fafafa;
}
.detail-card.wide { grid-column: 1 / -1; }
.detail-label { font-size: 11px; color: #6b7280; margin-bottom: 4px; }
.detail-value { font-size: 13px; color: #111827; font-weight: 600; }
.detail-value.sub { font-size: 11px; font-weight: 400; color: #6b7280; margin-left: 6px; }
.detail-value.mono { font-family: Consolas, monospace; font-weight: 400; font-size: 12px; }
.detail-value.kv div { display: flex; gap: 12px; padding: 3px 0; }
.detail-value.kv span { color: #6b7280; width: 90px; flex-shrink: 0; }
.detail-value.kv b { font-weight: 600; }
.wrap { word-break: break-all; white-space: normal; }
</style>
