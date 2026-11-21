<!--
  DockerOverviewPanel.vue — Docker「概览」页签
  ==========================================================
  业务作用：
    展示容器/镜像/数据卷/网络/编排数量与磁盘占用，并提供一键清理入口；
    同时显示面板配置的镜像加速与私有仓库。
  后端模块：
    /api/docker/overview、/api/docker/prune（handlers_docker_extra.go）。
  打开方式：
    由 DockerWindow 的「概览」页签渲染（切到即重新加载）。
-->
<template>
  <div class="overview">
    <div v-if="loading && !data" class="empty">{{ $t('common.loading') }}</div>
    <template v-else-if="data">
      <div class="cards">
        <div class="stat-card">
          <div class="stat-label">{{ $t('docker.p1.containers') }}</div>
          <div class="stat-value">{{ counts.running }}<span class="stat-sub">/{{ counts.containers }}</span></div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ $t('docker.p1.images') }}</div>
          <div class="stat-value">{{ counts.images }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ $t('docker.p1.volumes') }}</div>
          <div class="stat-value">{{ counts.volumes }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ $t('docker.p1.networks') }}</div>
          <div class="stat-value">{{ counts.networks }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ $t('docker.p1.compose') }}</div>
          <div class="stat-value">{{ counts.compose_projects }}</div>
        </div>
      </div>

      <div v-if="data.status && !data.status.available" class="warn">{{ $t('docker.p1.unavailable', { reason: data.status.reason }) }}</div>

      <div class="section-title">{{ $t('docker.p1.diskUsage') }}</div>
      <table class="dt mini">
        <thead><tr><th>{{ $t('docker.p1.type') }}</th><th>{{ $t('docker.p1.total') }}</th><th>{{ $t('docker.p1.size') }}</th><th>{{ $t('docker.p1.reclaimable') }}</th></tr></thead>
        <tbody>
          <tr v-for="d in data.disk || []" :key="d.type">
            <td>{{ d.type }}</td><td>{{ d.total }}</td><td>{{ d.size }}</td><td>{{ d.reclaimable }}</td>
          </tr>
          <tr v-if="!(data.disk || []).length"><td colspan="4" class="muted">{{ $t('docker.p1.noDisk') }}</td></tr>
        </tbody>
      </table>

      <div class="section-title">{{ $t('docker.p1.cleanup') }}</div>
      <div class="clean-actions">
        <button v-for="t in pruneTargets" :key="t" class="btn sm" :disabled="pruning === t" @click="prune(t)">
          {{ pruning === t ? $t('common.loading') : $t('docker.p1.prune_' + t) }}
        </button>
      </div>
      <div v-if="msg" class="msg">{{ msg }}</div>

      <div class="section-title">{{ $t('docker.p1.registry') }}</div>
      <div class="kv">
        <span class="k">{{ $t('docker.p1.mirrors') }}</span>
        <span class="v mono">{{ fmtList(data.mirrors) }}</span>
      </div>
      <div class="kv">
        <span class="k">{{ $t('docker.p1.privateRegistries') }}</span>
        <span class="v mono">{{ fmtList(data.registries) }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { dockerApi } from '../../api'

const data = ref(null)
const loading = ref(false)
const pruning = ref('')
const msg = ref('')
const pruneTargets = ['containers', 'images', 'networks', 'volumes', 'builder']
const counts = computed(() => data.value?.counts || {})

function fmtList(v) {
  if (Array.isArray(v)) return v.length ? v.join(', ') : '-'
  return v || '-'
}

async function load() {
  loading.value = true
  try {
    data.value = await dockerApi.overview()
  } catch (e) {
    msg.value = e?.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function prune(target) {
  if (!window.confirm(`确定清理 ${target}？该操作不可恢复。`)) return
  pruning.value = target
  msg.value = ''
  try {
    const r = await dockerApi.prune(target)
    msg.value = r.output || '完成'
    await load()
  } catch (e) {
    msg.value = e?.response?.data?.detail || e.message
  } finally {
    pruning.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.overview { padding: 12px; height: 100%; overflow: auto; }
.empty { color: var(--g-text-muted); text-align: center; padding: 40px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; margin-bottom: 14px; }
.stat-card { border: 1px solid var(--g-border); border-radius: 10px; background: var(--g-surface); padding: 10px 12px; }
.stat-label { font-size: 11.5px; color: var(--g-text-muted); }
.stat-value { font-size: 22px; font-weight: 700; margin-top: 4px; }
.stat-sub { font-size: 12px; color: var(--g-text-muted); font-weight: 400; }
.warn { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; border-radius: 8px; padding: 7px 10px; font-size: 12px; margin-bottom: 10px; }
.section-title { font-weight: 700; font-size: 12.5px; margin: 14px 0 6px; }
.dt.mini { width: auto; min-width: 420px; }
.muted { color: var(--g-text-muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11.5px; }
.clean-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.msg { margin-top: 8px; font-size: 11.5px; color: var(--g-text-muted); white-space: pre-wrap; max-height: 120px; overflow: auto; }
.kv { display: flex; gap: 10px; font-size: 12px; margin-bottom: 4px; }
.kv .k { width: 160px; color: var(--g-text-muted); flex-shrink: 0; }
</style>
