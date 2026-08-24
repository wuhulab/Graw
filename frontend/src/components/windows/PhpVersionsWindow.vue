<template>
  <div class="phpversions-window">
    <!-- 顶部：功能状态 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge" :class="available ? 'ok' : 'off'">
          <ServerCog :size="14" /> {{ available ? $t('phpversions.available') : $t('phpversions.unavailable') }}
        </span>
        <span class="hint">{{ reasonText }}</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> {{ $t('phpversions.refresh') }}</button>
      </div>
    </div>

    <!-- 已检测的 PHP 版本表 -->
    <div class="section-title">{{ $t('phpversions.detectedTitle') }}</div>
    <div v-if="loading" class="empty">{{ $t('phpversions.loading') }}</div>
    <div v-else-if="phpVersions.length === 0" class="empty">
      <ServerCog :size="40" style="color:#9ca3af;" />
      <div>{{ $t('phpversions.noPhp') }}</div>
      <div class="hint">{{ $t('phpversions.noPhpHint') }}</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ $t('phpversions.version') }}</th>
            <th>{{ $t('phpversions.sapi') }}</th>
            <th>{{ $t('phpversions.path') }}</th>
            <th>{{ $t('phpversions.fpmSock') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in phpVersions" :key="p.version">
            <td><span class="badge ok">PHP {{ p.version }}</span></td>
            <td>{{ p.sapi }}</td>
            <td class="mono">{{ p.path }}</td>
            <td class="mono">{{ p.fpm_sock || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 站点 PHP 版本关联 -->
    <div class="section-title">{{ $t('phpversions.sitesTitle') }}</div>
    <div v-if="siteLoading" class="empty">{{ $t('phpversions.loading') }}</div>
    <div v-else-if="sites.length === 0" class="empty">
      {{ $t('phpversions.noSites') }}
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ $t('phpversions.siteName') }}</th>
            <th>{{ $t('phpversions.type') }}</th>
            <th>{{ $t('phpversions.phpVersion') }}</th>
            <th>{{ $t('phpversions.action') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sites" :key="s.id">
            <td>{{ s.name }}<div class="sub">{{ s.id }}</div></td>
            <td>{{ typeLabel(s.type) }}</td>
            <td>
              <select
                class="php-select"
                :value="s.php_version || ''"
                :disabled="busy"
                @change="(e) => onSelectSitePhp(s, e.target.value)"
              >
                <option value="">{{ $t('phpversions.unset') }}</option>
                <option v-for="p in phpVersions" :key="p.version" :value="p.version">{{ p.version }}</option>
              </select>
            </td>
            <td class="actions-cell">
              <button v-if="s.php_version" class="btn mini" :disabled="busy" @click="onSelectSitePhp(s, '')">{{ $t('phpversions.clear') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="hint table-hint">{{ $t('phpversions.sitesHint') }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ServerCog, RefreshCw } from 'lucide-vue-next'
import { phpversionsApi } from '../../api'

const loading = ref(false)
const siteLoading = ref(false)
const busy = ref(false)
const status = ref({ available: false, php_versions: [], reason: '' })
const sites = ref([])

const phpVersions = computed(() => status.value.php_versions || [])
const available = computed(() => !!status.value.available)
const reasonText = computed(() => status.value.reason || (available.value ? '' : '—'))

function typeLabel(type) {
  if (type === 'static') return '静态网址'
  if (type === 'subsite') return '子网站'
  if (type === 'proxy') return '反向代理'
  if (type === 'tcpudp') return 'TCP/UDP代理'
  return type || '—'
}

async function loadStatus() {
  loading.value = true
  try {
    const r = await phpversionsApi.status()
    status.value = r || { available: false, php_versions: [], reason: '' }
  } catch (e) {
    alert('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadSites() {
  siteLoading.value = true
  try {
    const r = await phpversionsApi.sites()
    sites.value = (r && r.sites) || []
  } catch (e) {
    alert('加载站点失败：' + (e.response?.data?.detail || e.message))
  } finally {
    siteLoading.value = false
  }
}

async function loadAll() {
  await Promise.all([loadStatus(), loadSites()])
}

// 选择 / 清除站点的 PHP 版本（setPhp）
async function onSelectSitePhp(s, version) {
  busy.value = true
  try {
    await phpversionsApi.setPhp(s.id, version)
    await loadSites()
  } catch (e) {
    alert('设置失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.phpversions-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; overflow: auto; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.status-badge.ok { background: #d1fae5; color: #065f46; }
.status-badge.off { background: #f3f4f6; color: #6b7280; }
.hint { color: #6e6e73; font-size: 12px; }
.toolbar-actions { display: flex; gap: 8px; }

.section-title { font-size: 13px; font-weight: 600; color: #1d1d1f; margin: 8px 0 6px; }

.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; margin-bottom: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; }
.sub { font-size: 10px; color: #888; }
.actions-cell { display: flex; gap: 4px; }
.table-hint { margin: 6px 2px; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }

.php-select { padding: 5px 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12.5px; font-family: inherit; background: #fff; }
.php-select:focus { outline: none; border-color: #0a84ff; }
.php-select:disabled { opacity: 0.5; cursor: not-allowed; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.mini { padding: 3px 8px; font-size: 11.5px; }

.empty { text-align: center; color: #9ca3af; padding: 30px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
</style>