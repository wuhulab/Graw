<!--
  SyncWindow.vue — 资源 / 证书同步
  ==========================================================
  业务作用：
    把当前节点的站点、证书（含 PEM）与编排模板导出为 JSON bundle；
    可导入当前节点，或推送到多个已启用 Agent 的子节点。
    导入的站点默认停用，避免同名域名立即抢流量。
  后端模块：
    /api/sync/export、/api/sync/import、/api/nodes/sync。
  打开方式：
    桌面/侧边栏「资源同步」快捷方式（perm=sync；跨节点推送需完整管理员）。
-->
<template>
  <div class="sync-window">
    <div class="toolbar">
      <button class="btn primary" :disabled="busy" @click="doExport">{{ busy ? $t('common.loading') : $t('sync.export') }}</button>
      <button class="btn" :disabled="busy || !bundleText" @click="doImport">{{ $t('sync.importLocal') }}</button>
      <label class="check"><input type="checkbox" v-model="enableSites" /> {{ $t('sync.enableSites') }}</label>
      <span v-if="msg" class="msg">{{ msg }}</span>
      <span v-if="err" class="err">{{ err }}</span>
    </div>

    <div class="grid">
      <div class="pane">
        <div class="title">{{ $t('sync.resources') }}</div>
        <label class="check"><input type="checkbox" v-model="resources" value="sites" /> {{ $t('sync.sites') }}</label>
        <label class="check"><input type="checkbox" v-model="resources" value="certs" /> {{ $t('sync.certs') }}</label>
        <label class="check"><input type="checkbox" v-model="resources" value="templates" /> {{ $t('sync.templates') }}</label>
        <div class="hint">{{ $t('sync.exportHint') }}</div>
        <button class="btn sm" :disabled="!bundleText" @click="downloadBundle">{{ $t('sync.download') }}</button>

        <div class="title" style="margin-top:14px;">{{ $t('sync.pushTitle') }}</div>
        <div class="nodes">
          <label v-for="n in nodes" :key="n.id" class="check">
            <input type="checkbox" v-model="nodeIDs" :value="n.id" /> {{ n.name }}<span v-if="n.type === 'ssh'" class="mono"> · {{ n.host }}</span>
          </label>
          <div v-if="!nodes.length" class="hint">{{ $t('sync.noNodes') }}</div>
        </div>
        <button class="btn primary sm" :disabled="busy || !nodeIDs.length" @click="doPush">{{ $t('sync.push') }}</button>
        <pre v-if="pushResult" class="result">{{ pushResult }}</pre>
      </div>

      <div class="pane">
        <div class="title">{{ $t('sync.bundle') }}</div>
        <textarea v-model="bundleText" class="bundle" spellcheck="false" :placeholder="$t('sync.bundleHint')" />
        <div v-if="importResult" class="result">{{ importResult }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { syncApi, nodesApi } from '../../api'

const resources = ref(['sites', 'certs', 'templates'])
const enableSites = ref(false)
const bundleText = ref('')
const importResult = ref('')
const pushResult = ref('')
const nodes = ref([])
const nodeIDs = ref([])
const busy = ref(false)
const msg = ref('')
const err = ref('')

function apiErr(e) { return e?.response?.data?.detail || e.message }

async function loadNodes() {
  try {
    const r = await nodesApi.list()
    nodes.value = (r.nodes || []).filter(n => n.type === 'ssh')
  } catch { nodes.value = [] }
}

async function doExport() {
  busy.value = true
  err.value = ''
  msg.value = ''
  try {
    const r = await syncApi.exportBundle({ resources: resources.value })
    bundleText.value = JSON.stringify(r, null, 2)
    const counts = [r.sites?.length || 0, r.certs?.length || 0, r.templates?.length || 0]
    msg.value = `导出完成：站点 ${counts[0]} / 证书 ${counts[1]} / 模板 ${counts[2]}`
  } catch (e) { err.value = apiErr(e) } finally { busy.value = false }
}

async function doImport() {
  if (!window.confirm('确定把同步包导入当前节点？站点将默认停用。')) return
  busy.value = true
  err.value = ''
  importResult.value = ''
  try {
    const bundle = JSON.parse(bundleText.value)
    const r = await syncApi.importBundle(bundle, enableSites.value)
    importResult.value = JSON.stringify(r, null, 2)
  } catch (e) { err.value = apiErr(e) } finally { busy.value = false }
}

async function doPush() {
  busy.value = true
  err.value = ''
  pushResult.value = ''
  try {
    const r = await syncApi.pushNodes({
      node_ids: nodeIDs.value, resources: resources.value, enable_sites: enableSites.value,
    })
    pushResult.value = JSON.stringify(r.results || r, null, 2)
  } catch (e) { err.value = apiErr(e) } finally { busy.value = false }
}

function downloadBundle() {
  const blob = new Blob([bundleText.value], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'graw-sync-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.json'
  a.click()
  URL.revokeObjectURL(a.href)
}

onMounted(loadNodes)
</script>

<style scoped>
.sync-window { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--g-border); flex-wrap: wrap; }
.check { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; }
.msg { font-size: 12px; color: #047857; }
.err { font-size: 12px; color: #b91c1c; }
.grid { flex: 1; display: grid; grid-template-columns: 320px 1fr; gap: 12px; padding: 12px; min-height: 0; }
.pane { border: 1px solid var(--g-border); border-radius: 10px; background: var(--g-surface); padding: 10px 12px; display: flex; flex-direction: column; gap: 7px; min-height: 0; }
.title { font-weight: 700; font-size: 12.5px; }
.hint { font-size: 11.5px; color: var(--g-text-muted); }
.nodes { display: flex; flex-direction: column; gap: 5px; max-height: 180px; overflow: auto; border: 1px solid var(--g-border); border-radius: 8px; padding: 6px; }
.mono { font-family: ui-monospace, monospace; color: var(--g-text-muted); font-size: 11px; }
.bundle { flex: 1; min-height: 0; resize: none; padding: 8px; border: 1px solid var(--g-border); border-radius: 8px; background: #0f1722; color: #d7e3f4; font-family: ui-monospace, monospace; font-size: 11.5px; }
.result { background: var(--g-surface-soft); border-radius: 8px; padding: 8px; font-size: 11.5px; max-height: 180px; overflow: auto; white-space: pre-wrap; margin: 0; }
</style>
