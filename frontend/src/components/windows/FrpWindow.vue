<template>
  <div class="frp-window">
    <!-- 顶部工具条：模式切换 + 状态 + 进程控制 -->
    <div class="toolbar">
      <label class="mode-label">{{ $t('frp.mode') }}</label>
      <select v-model="mode" @change="onModeChange">
        <option value="server">{{ $t('frp.modeServer') }}</option>
        <option value="client">{{ $t('frp.modeClient') }}</option>
      </select>

      <span class="badge" :class="installed ? 'ok' : 'warn'">{{ $t(installed ? 'frp.installed' : 'frp.notInstalled') }}</span>
      <span class="badge" :class="running ? 'ok' : 'off'">{{ $t(running ? 'frp.running' : 'frp.stopped') }}</span>
      <span class="badge neutral">{{ cfgPath }}</span>

      <div class="spacer"></div>
      <button class="btn" title="刷新" @click="load"><RotateCw :size="14" /></button>
      <button class="btn primary" @click="preview">{{ $t('frp.preview') }}</button>
      <button class="btn primary" @click="save">{{ $t('common.save') }}</button>
      <button class="btn" @click="proc('start')" :disabled="running"><Play :size="14" /> {{ $t('frp.start') }}</button>
      <button class="btn" @click="proc('stop')" :disabled="!running"><Square :size="14" /> {{ $t('frp.stop') }}</button>
      <button class="btn warn" @click="proc('restart')"><RotateCw :size="14" /> {{ $t('frp.restart') }}</button>
    </div>

    <p v-if="statusMsg" class="status-msg">{{ statusMsg }}</p>

    <!-- 可执行文件路径（frps / frpc）：不填则从 PATH 探测 -->
    <div class="bin-row">
      <span class="bin-label">{{ $t('frp.binPath') }}</span>
      <input class="bin-input" v-model="bins.serverBin" :placeholder="$t('frp.binServerPlaceholder')" />
      <input class="bin-input" v-model="bins.clientBin" :placeholder="$t('frp.binClientPlaceholder')" />
    </div>

    <!-- 服务端模式配置 -->
    <section v-if="mode === 'server'">
      <h4>{{ $t('frp.serverConfig') }}</h4>
      <div class="form-grid">
        <div class="field"><label>{{ $t('frp.bindAddr') }}</label><input v-model="server.bindAddr" /></div>
        <div class="field"><label>{{ $t('frp.bindPort') }}</label><input v-model.number="server.bindPort" type="number" /></div>
        <div class="field"><label>{{ $t('frp.token') }}</label><input v-model="server.token" :placeholder="$t('frp.tokenPlaceholder')" /></div>
        <div class="field"><label>{{ $t('frp.configPath') }}</label><input v-model="server.configPath" :placeholder="$t('frp.configPathPlaceholder', { p: '/etc/frp/frps.toml' })" /></div>
        <div class="field"><label>{{ $t('frp.logLevel') }}</label>
          <select v-model="server.logLevel"><option v-for="l in logLevels" :key="l" :value="l">{{ l }}</option></select>
        </div>
        <div class="field"><label>{{ $t('frp.dashboardPort') }}</label><input v-model.number="server.dashboardPort" type="number" /></div>
        <div class="field"><label>{{ $t('frp.dashboardAddr') }}</label><input v-model="server.dashboardAddr" /></div>
        <div class="field"><label>{{ $t('frp.dashboardUser') }}</label><input v-model="server.dashboardUser" /></div>
        <div class="field"><label>{{ $t('frp.dashboardPwd') }}</label><input v-model="server.dashboardPwd" /></div>
      </div>
    </section>

    <!-- 客户端模式配置 -->
    <section v-else>
      <h4>{{ $t('frp.clientConfig') }}</h4>
      <div class="form-grid">
        <div class="field"><label>{{ $t('frp.serverAddr') }}</label><input v-model="client.serverAddr" :placeholder="$t('frp.serverAddrPlaceholder')" /></div>
        <div class="field"><label>{{ $t('frp.serverPort') }}</label><input v-model.number="client.serverPort" type="number" /></div>
        <div class="field"><label>{{ $t('frp.token') }}</label><input v-model="client.token" :placeholder="$t('frp.tokenPlaceholder')" /></div>
        <div class="field"><label>{{ $t('frp.configPath') }}</label><input v-model="client.configPath" :placeholder="$t('frp.configPathPlaceholder', { p: '/etc/frp/frpc.toml' })" /></div>
        <div class="field"><label>{{ $t('frp.logLevel') }}</label>
          <select v-model="client.logLevel"><option v-for="l in logLevels" :key="l" :value="l">{{ l }}</option></select>
        </div>
        <div class="field toggle"><label><input type="checkbox" v-model="client.loginFailExit" /> {{ $t('frp.loginFailExit') }}</label></div>
      </div>

      <div class="section-head">
        <h4>{{ $t('frp.proxies') }}</h4>
        <button class="btn primary" @click="openProxyModal()"><Plus :size="14" /> {{ $t('frp.addProxy') }}</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{{ $t('frp.name') }}</th><th>{{ $t('frp.type') }}</th><th>{{ $t('frp.localEndpoint') }}</th>
              <th>{{ $t('frp.remote') }}</th><th>{{ $t('frp.remark') }}</th><th>{{ $t('frp.status') }}</th><th>{{ $t('common.action') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in proxies" :key="p.id" :class="{ muted: !p.enabled }">
              <td>{{ p.name }}</td><td>{{ p.type }}</td>
              <td>{{ p.localIp }}:{{ p.localPort }}</td>
              <td>
                <span v-if="p.type==='tcp'||p.type==='udp'">{{ p.remotePort }}</span>
                <span v-else>{{ p.customDomains }}</span>
              </td>
              <td>{{ p.remark }}</td>
              <td><span class="badge" :class="p.enabled ? 'ok' : 'off'">{{ $t(p.enabled ? 'frp.enabled' : 'frp.disabled') }}</span></td>
              <td class="ops">
                <button class="iconbtn" :title="$t('frp.toggle')" @click="toggleProxy(p)"><Power :size="14" /></button>
                <button class="iconbtn" :title="$t('common.edit')" @click="openProxyModal(p)"><Pencil :size="14" /></button>
                <button class="iconbtn danger" :title="$t('common.delete')" @click="delProxy(p)"><Trash2 :size="14" /></button>
              </td>
            </tr>
            <tr v-if="proxies.length === 0"><td colspan="7" class="empty">{{ $t('frp.noProxies') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 配置预览弹窗 -->
    <div v-if="showPreview" class="modal-overlay" @click.self="showPreview=false">
      <div class="modal wide">
        <h3>{{ $t('frp.preview') }}</h3>
        <pre class="toml">{{ previewText }}</pre>
        <div class="actions"><button class="btn primary" @click="showPreview=false">{{ $t('common.close') }}</button></div>
      </div>
    </div>

    <!-- 代理编辑弹窗 -->
    <div v-if="showProxyModal" class="modal-overlay" @click.self="showProxyModal=false">
      <div class="modal">
        <h3>{{ proxyForm.id ? $t('frp.editProxy') : $t('frp.addProxy') }}</h3>
        <div class="form">
          <label>{{ $t('frp.name') }}</label><input v-model="proxyForm.name" />
          <label>{{ $t('frp.type') }}</label>
          <select v-model="proxyForm.type">
            <option v-for="t in proxyTypes" :key="t" :value="t">{{ t }}</option>
          </select>
          <label>{{ $t('frp.localIp') }}</label><input v-model="proxyForm.localIp" />
          <label>{{ $t('frp.localPort') }}</label><input v-model.number="proxyForm.localPort" type="number" />
          <template v-if="isPortType">
            <label>{{ $t('frp.remotePort') }}</label>
            <input v-model.number="proxyForm.remotePort" type="number" />
          </template>
          <template v-else>
            <label>{{ $t('frp.customDomains') }}</label>
            <input v-model="proxyForm.customDomains" :placeholder="$t('frp.customDomainsPlaceholder')" />
          </template>
          <label>{{ $t('frp.remark') }}</label><input v-model="proxyForm.remark" />
          <div class="row">
            <label class="inline"><input type="checkbox" v-model="proxyForm.useEncryption" /> {{ $t('frp.useEncryption') }}</label>
            <label class="inline"><input type="checkbox" v-model="proxyForm.useCompression" /> {{ $t('frp.useCompression') }}</label>
            <label class="inline"><input type="checkbox" v-model="proxyForm.enabled" /> {{ $t('frp.enabled') }}</label>
          </div>
          <div class="actions">
            <button class="btn" @click="showProxyModal=false">{{ $t('common.cancel') }}</button>
            <button class="btn primary" @click="saveProxy">{{ $t('common.save') }}</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { frpApi } from '../../api'
import { RotateCw, Play, Square, Plus, Trash2, Pencil, Power } from 'lucide-vue-next'

const { t } = useI18n()

// tcp/udp 代理使用远程端口，http/https 使用自定义域名
const isPortType = computed(() => proxyForm.value.type === 'tcp' || proxyForm.value.type === 'udp')

const mode = ref('server')
const installed = ref(false)
const running = ref(false)
const cfgPath = ref('')
const statusMsg = ref('')
const bins = ref({ serverBin: '', clientBin: '' })
const server = ref({ bindAddr: '0.0.0.0', bindPort: 7000, token: '', configPath: '', dashboardAddr: '127.0.0.1', dashboardPort: 0, dashboardUser: 'admin', dashboardPwd: '', logLevel: 'info' })
const client = ref({ serverAddr: '', serverPort: 7000, token: '', configPath: '', loginFailExit: true, logLevel: 'info' })
const proxies = ref([])
const logLevels = ['trace', 'debug', 'info', 'warn', 'error']
const proxyTypes = ['tcp', 'udp', 'http', 'https']

const showPreview = ref(false)
const previewText = ref('')
const showProxyModal = ref(false)
const proxyForm = ref(newProxy())

function newProxy() {
  return { id: '', name: '', type: 'tcp', localIp: '127.0.0.1', localPort: 80, remotePort: 8080, customDomains: '', useEncryption: false, useCompression: false, enabled: true, remark: '' }
}

function applyServer(d) {
  server.value = { ...server.value, ...(d.server || {}) }
}
function applyClient(d) {
  client.value = { ...client.value, ...(d.client || {}) }
  proxies.value = (d.client && d.client.proxies) || []
}
function applyAll(d) {
  mode.value = d.mode || 'server'
  bins.value.serverBin = d.serverBin || ''
  bins.value.clientBin = d.clientBin || ''
  applyServer(d)
  applyClient(d)
}

async function load() {
  statusMsg.value = ''
  try {
    const s = await frpApi.status()
    installed.value = s.installed
    running.value = s.running
    cfgPath.value = s.configPath || ''
    const cfg = await frpApi.config()
    applyAll(cfg)
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

async function save() {
  try {
    const payload = {
      mode: mode.value,
      serverBin: bins.value.serverBin,
      clientBin: bins.value.clientBin,
      server: server.value,
      client: { ...client.value, proxies: proxies.value }
    }
    const d = await frpApi.save(payload)
    applyAll(d)
    statusMsg.value = t('frp.saved')
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

async function onModeChange() {
  try {
    const d = await frpApi.switchMode(mode.value)
    applyAll(d)
    await load()
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

async function proc(action) {
  statusMsg.value = ''
  try {
    const fn = frpApi[action]
    const d = await fn()
    running.value = d.running
    statusMsg.value = d.message || t('frp.done')
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

async function preview() {
  try {
    const p = await frpApi.preview()
    previewText.value = p.toml
    showPreview.value = true
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

function openProxyModal(p) {
  proxyForm.value = p ? { ...p } : newProxy()
  showProxyModal.value = true
}

async function saveProxy() {
  try {
    const payload = {
      name: proxyForm.value.name,
      type: proxyForm.value.type,
      localIp: proxyForm.value.localIp,
      localPort: proxyForm.value.localPort,
      remotePort: proxyForm.value.remotePort || null,
      customDomains: proxyForm.value.customDomains || '',
      useEncryption: proxyForm.value.useEncryption,
      useCompression: proxyForm.value.useCompression,
      enabled: proxyForm.value.enabled,
      remark: proxyForm.value.remark
    }
    if (proxyForm.value.id) {
      await frpApi.updateProxy(proxyForm.value.id, payload)
    } else {
      await frpApi.addProxy(payload)
    }
    showProxyModal.value = false
    const cfg = await frpApi.config()
    applyAll(cfg)
    statusMsg.value = t('frp.saved')
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

async function toggleProxy(p) {
  try {
    await frpApi.toggleProxy(p.id, !p.enabled)
    const cfg = await frpApi.config()
    applyAll(cfg)
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

// 删除代理：高风险操作，先弹出密码二次确认框
function delProxy(p) {
  confirm.value = { show: true, target: p }
}

// 面板密码校验通过后真正执行删除
async function doDeleteProxy() {
  const p = confirm.value.target
  confirm.value.show = false
  if (!p) return
  try {
    await frpApi.deleteProxy(p.id)
    const cfg = await frpApi.config()
    applyAll(cfg)
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

onMounted(load)
</script>

<style scoped>
.frp-window { padding: 10px; }
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.mode-label { font-size: 13px; color: #374151; }
.spacer { flex: 1; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.warn { background: #fef3c7; color: #92400e; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.badge.neutral { background: #eef2ff; color: #3730a3; }
.status-msg { color: #155e75; font-size: 12px; margin: 4px 0 8px; }
.bin-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; background: #f9fafb; padding: 8px 10px; border-radius: 8px; }
.bin-label { font-size: 12px; color: #374151; white-space: nowrap; }
.bin-input { flex: 1; min-width: 220px; }
h4 { margin: 12px 0 8px; font-size: 14px; }
.section-head { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field label { font-size: 12px; color: #374151; }
.field.toggle { justify-content: flex-end; }
.field.toggle label { display: flex; align-items: center; gap: 6px; padding-bottom: 8px; }
input, select { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.table-wrap { overflow: auto; max-height: 300px; border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tr.muted { opacity: 0.55; }
.empty { text-align: center; color: #9ca3af; padding: 20px; }
.ops { display: flex; gap: 6px; }
.btn { display: inline-flex; align-items: center; gap: 4px; padding: 6px 10px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.warn { background: #fff7ed; color: #c2410c; border-color: #fdba74; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; display: inline-flex; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 440px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal.wide { width: 640px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.row { display: flex; gap: 14px; flex-wrap: wrap; }
.inline { display: flex; align-items: center; gap: 5px; font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
.toml { background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px; max-height: 60vh; overflow: auto; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-all; }
</style>