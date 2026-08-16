<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <span style="color:#0a3d7a; font-weight:600;">容器详情 · {{ name }}</span>
      <span style="font-size:11px; color:#888; font-family:Consolas,monospace;">{{ id }}</span>
      <button class="btn" style="margin-left:auto;" @click="load">刷新</button>
      <button class="btn" @click="$emit('close')">关闭</button>
    </div>
    <div style="flex:1; overflow:auto; padding:12px;">
      <div v-if="loading" class="empty">加载中...</div>
      <div v-else-if="error" class="empty" style="color:#b91c1c;">{{ error }}</div>
      <div v-else-if="info" class="detail-grid">
        <div class="detail-card">
          <div class="detail-label">创建时间</div>
          <div class="detail-value">{{ formatTime(info.created) }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">CPU 使用</div>
          <div class="detail-value">
            <span :style="{ color: cpuColor(info.cpu_percent) }">{{ info.cpu_percent }}%</span>
            <span class="sub" v-if="info.mem_percent"> · 内存 {{ info.mem_percent }}%</span>
          </div>
        </div>
        <div class="detail-card">
          <div class="detail-label">CPU 总计时长</div>
          <div class="detail-value">{{ info.cpu_time || '—' }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">核心数（容器占用）</div>
          <div class="detail-value">{{ info.cpu_cores ? info.cpu_cores + ' 核' : '不限制' }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">内存使用</div>
          <div class="detail-value">{{ formatBytes(info.mem_usage) }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">缓存使用</div>
          <div class="detail-value">{{ formatBytes(info.cache_usage) }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">内存限额</div>
          <div class="detail-value">{{ info.mem_limit ? formatBytes(info.mem_limit) : '不限制' }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">容器层大小</div>
          <div class="detail-value">{{ formatBytes(info.layer_size) }}</div>
        </div>
        <div class="detail-card">
          <div class="detail-label">虚拟大小</div>
          <div class="detail-value">{{ formatBytes(info.virtual_size) }}</div>
        </div>

        <div class="detail-card wide">
          <div class="detail-label">基本属性</div>
          <div class="detail-value kv">
            <div><span>状态</span><b>{{ info.state }}</b></div>
            <div><span>镜像</span><b class="wrap">{{ info.image }}</b></div>
            <div><span>PID</span><b>{{ info.pid || '—' }}</b></div>
            <div><span>重启次数</span><b>{{ info.restart_count }}</b></div>
            <div><span>网络模式</span><b>{{ info.network_mode || '—' }}</b></div>
            <div><span>重启策略</span><b>{{ info.restart_policy || '—' }}</b></div>
          </div>
        </div>

        <div class="detail-card wide">
          <div class="detail-label">启动命令</div>
          <div class="detail-value mono wrap">{{ cmdText }}</div>
        </div>

        <div class="detail-card wide" v-if="info.mounts && info.mounts.length">
          <div class="detail-label">挂载点（{{ info.mounts.length }}）</div>
          <div class="detail-value mono wrap">
            <div v-for="(m, i) in info.mounts" :key="i" style="margin:2px 0;">
              <span style="color:#555;">{{ m.Type || 'bind' }}:</span> {{ m.Source || m.Name || '' }} → {{ m.Destination || '' }}
            </div>
          </div>
        </div>

        <div class="detail-card wide" v-if="info.env && info.env.length">
          <div class="detail-label">环境变量（{{ info.env.length }}）</div>
          <div class="detail-value mono wrap">
            <div v-for="(e, i) in info.env" :key="i" style="margin:1px 0;">{{ e }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { dockerApi } from '../../api'

const props = defineProps({ id: String, name: String })
const emit = defineEmits(['close'])

const info = ref(null)
const loading = ref(false)
const error = ref('')

const cmdText = computed(() => {
  if (!info.value) return ''
  const parts = []
  if (info.value.entrypoint) parts.push(...(Array.isArray(info.value.entrypoint) ? info.value.entrypoint : [info.value.entrypoint]))
  if (info.value.cmd) parts.push(...(Array.isArray(info.value.cmd) ? info.value.cmd : [info.value.cmd]))
  return parts.join(' ') || '—'
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    info.value = await dockerApi.inspect(props.id)
  } catch (e) {
    error.value = '获取详情失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

function formatTime(t) {
  if (!t) return '—'
  const d = new Date(t)
  if (isNaN(d.getTime())) return String(t)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function formatBytes(bytes) {
  if (bytes == null || isNaN(bytes) || bytes === 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(v < 10 && i > 0 ? 2 : v < 100 ? 1 : 0) + ' ' + units[i]
}

function cpuColor(pct) {
  const v = Number(pct) || 0
  if (v >= 80) return '#b91c1c'
  if (v >= 50) return '#b45309'
  return '#2a8f3c'
}

onMounted(load)
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
