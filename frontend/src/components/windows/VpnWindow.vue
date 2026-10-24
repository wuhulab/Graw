<!--
  VPN 转发策略窗口（VpnWindow）
  业务：把面板托管的 VPN/代理节点生成「兼容 Clash 订阅格式」的订阅链接；
        转发策略可选「端口转发」（仅指定端口走 VPN）/「全局代理」（全部流量走 VPN）；
        「低占用模式」默认开启——核心空闲超过阈值自动关闭，下次初次请求
        （拉取订阅 / 唤醒接口 / 手动启动）自动重新拉起。
  后端模块：vpnApi（get/status/saveConfig/start/stop/wake/rotateToken/detect/preview/log）
  组成：策略选项卡 / 订阅链接卡 / 节点管理卡（内联表单）/ 端口转发条目卡 / 核心设置卡。
-->
<template>
  <div class="vpn-window">
    <div class="ui-toolbar">
      <label class="vpn-switch">
        <input type="checkbox" v-model="form.enabled" />
        <b>{{ $t('vpn.enabled') }}</b>
      </label>
      <span class="ui-badge" :class="statusBadge.cls">{{ $t(statusBadge.key) }}</span>
      <button class="ui-btn" @click="startCore" :disabled="!form.enabled || status.running">{{ $t('vpn.start') }}</button>
      <button class="ui-btn" @click="stopCore" :disabled="!status.running">{{ $t('vpn.stop') }}</button>
      <button class="ui-btn" @click="loadAll">{{ $t('common.refresh') }}</button>
      <button class="ui-btn primary" @click="save" :disabled="saving">{{ $t('common.save') }}</button>
      <span class="ui-hint right" v-if="status.lowPower && status.enabled">
        {{ $t('vpn.idleNow', { m: status.idleMinutes, s: Math.floor((status.idleSeconds || 0) / 60) }) }}
      </span>
    </div>
    <p v-if="statusMsg" class="vpn-msg" :class="{ warn: statusWarn }">{{ statusMsg }}</p>

    <div class="vpn-body">
      <!-- 转发策略 -->
      <div class="ui-card vpn-section">
        <div class="vpn-sec-title">{{ $t('vpn.policy') }}</div>
        <div class="vpn-checks">
          <label class="vpn-check">
            <input type="checkbox" v-model="form.portForward" />
            <span><b>{{ $t('vpn.portForward') }}</b><br /><span class="ui-hint">{{ $t('vpn.portForwardDesc') }}</span></span>
          </label>
          <label class="vpn-check">
            <input type="checkbox" v-model="form.globalProxy" />
            <span><b>{{ $t('vpn.globalProxy') }}</b><br /><span class="ui-hint">{{ $t('vpn.globalProxyDesc') }}</span></span>
          </label>
          <label class="vpn-check">
            <input type="checkbox" v-model="form.lowPower" />
            <span><b>{{ $t('vpn.lowPower') }}</b><br /><span class="ui-hint">{{ $t('vpn.lowPowerDesc') }}</span></span>
          </label>
        </div>
        <div class="vpn-inline">
          <div class="field" v-if="form.globalProxy">
            <label>{{ $t('vpn.mixedPort') }}</label>
            <input class="ui-input" type="number" min="1024" max="65535" v-model.number="form.mixedPort" />
          </div>
          <div class="field" v-if="form.lowPower">
            <label>{{ $t('vpn.idleMinutes') }}</label>
            <input class="ui-input" type="number" min="1" max="1440" v-model.number="form.idleMinutes" />
          </div>
        </div>
      </div>

      <!-- 订阅链接 -->
      <div class="ui-card vpn-section">
        <div class="vpn-sec-title">{{ $t('vpn.subscription') }}</div>
        <div class="vpn-sub-row">
          <input class="ui-input ui-mono vpn-sub-url" :value="subUrl" readonly :type="showToken ? 'text' : 'password'" />
          <button class="ui-btn mini" @click="showToken = !showToken">{{ showToken ? $t('vpn.hideToken') : $t('vpn.showToken') }}</button>
          <button class="ui-btn mini" @click="copySub">{{ $t('common.copy') }}</button>
          <button class="ui-btn mini" @click="rotateToken">{{ $t('vpn.rotateToken') }}</button>
          <button class="ui-btn mini" @click="openPreview">{{ $t('vpn.preview') }}</button>
        </div>
        <div class="ui-hint">{{ $t('vpn.subHint') }}</div>
      </div>

      <!-- 节点列表 -->
      <div class="ui-card vpn-section">
        <div class="vpn-sec-title">
          {{ $t('vpn.nodes') }}（{{ form.nodes.length }}）
          <button class="ui-btn mini primary" @click="addNode">{{ $t('vpn.addNode') }}</button>
        </div>
        <div v-if="form.nodes.length === 0" class="ui-empty">{{ $t('vpn.noNodes') }}</div>
        <div class="vpn-row" v-for="(n, i) in form.nodes" :key="n.id">
          <b class="vpn-row-name">{{ n.name }}</b>
          <span class="ui-badge neutral">{{ n.type }}</span>
          <span class="ui-mono">{{ n.server }}:{{ n.port }}</span>
          <span class="ui-badge" :class="n.tls ? 'ok' : 'off'" v-if="nodeHasTls(n)">TLS</span>
          <span class="vpn-row-actions">
            <button class="ui-btn mini" @click="editNode(i)">{{ $t('common.edit') }}</button>
            <button class="ui-btn mini danger" @click="removeNode(i)">{{ $t('common.delete') }}</button>
          </span>
        </div>
        <!-- 节点编辑表单（内联展开） -->
        <div v-if="nodeForm !== null" class="vpn-form">
          <div class="vpn-grid">
            <div class="field"><label>{{ $t('vpn.nodeType') }}</label>
              <select class="ui-select" v-model="nodeForm.type">
                <option v-for="ty in nodeTypes" :key="ty" :value="ty">{{ ty }}</option>
              </select>
            </div>
            <div class="field"><label>{{ $t('common.name') }}</label><input class="ui-input" v-model="nodeForm.name" /></div>
            <div class="field"><label>{{ $t('vpn.server') }}</label><input class="ui-input" v-model="nodeForm.server" /></div>
            <div class="field"><label>{{ $t('vpn.port') }}</label><input class="ui-input" type="number" min="1" max="65535" v-model.number="nodeForm.port" /></div>
            <div class="field" v-if="hasField('uuid')"><label>UUID</label><input class="ui-input" v-model="nodeForm.uuid" /></div>
            <div class="field" v-if="hasField('cipher')"><label>{{ $t('vpn.cipher') }}</label>
              <select class="ui-select" v-model="nodeForm.cipher">
                <option v-for="c in ciphers" :key="c" :value="c">{{ c }}</option>
              </select>
            </div>
            <div class="field" v-if="hasField('password')"><label>{{ $t('vpn.password') }}</label><input class="ui-input" v-model="nodeForm.password" /></div>
            <div class="field" v-if="hasField('username')"><label>{{ $t('vpn.username') }}</label><input class="ui-input" v-model="nodeForm.username" /></div>
            <div class="field" v-if="hasField('sni')"><label>SNI</label><input class="ui-input" v-model="nodeForm.sni" /></div>
            <div class="field" v-if="nodeForm.type === 'vless'"><label>Flow</label><input class="ui-input" v-model="nodeForm.flow" /></div>
          </div>
          <div class="vpn-switches">
            <label class="vpn-switch" v-if="nodeHasTls(nodeForm)"><input type="checkbox" v-model="nodeForm.tls" /> TLS</label>
            <label class="vpn-switch" v-if="nodeHasTls(nodeForm) && nodeForm.tls"><input type="checkbox" v-model="nodeForm.skipCertVerify" /> {{ $t('vpn.skipCertVerify') }}</label>
            <label class="vpn-switch" v-if="hasField('udp')"><input type="checkbox" v-model="nodeForm.udp" /> UDP</label>
          </div>
          <div class="vpn-form-actions">
            <button class="ui-btn primary" @click="saveNode">{{ $t('common.save') }}</button>
            <button class="ui-btn" @click="nodeForm = null">{{ $t('common.cancel') }}</button>
          </div>
        </div>
      </div>

      <!-- 端口转发条目（勾选端口转发策略时展示） -->
      <div class="ui-card vpn-section" v-if="form.portForward">
        <div class="vpn-sec-title">
          {{ $t('vpn.forwards') }}（{{ form.forwards.length }}）
          <button class="ui-btn mini primary" @click="addForward">{{ $t('vpn.addForward') }}</button>
        </div>
        <div v-if="form.forwards.length === 0" class="ui-empty">{{ $t('vpn.noForwards') }}</div>
        <div class="vpn-row" v-for="(f, i) in form.forwards" :key="f.id">
          <b class="vpn-row-name">{{ f.name }}</b>
          <span class="ui-mono">127.0.0.1:{{ f.listenPort }}</span>
          <span class="vpn-arrow">→</span>
          <span class="ui-mono">{{ f.targetHost }}:{{ f.targetPort }}</span>
          <span class="ui-badge" :class="f.viaProxy ? 'ok' : 'off'">{{ f.viaProxy ? $t('vpn.viaProxyOn') : $t('vpn.viaProxyOff') }}</span>
          <span class="vpn-row-actions">
            <button class="ui-btn mini danger" @click="removeForward(i)">{{ $t('common.delete') }}</button>
          </span>
        </div>
        <!-- 转发条目新增表单（内联展开） -->
        <div v-if="forwardForm !== null" class="vpn-form">
          <div class="vpn-grid">
            <div class="field"><label>{{ $t('common.name') }}</label><input class="ui-input" v-model="forwardForm.name" /></div>
            <div class="field"><label>{{ $t('vpn.listenPort') }}</label><input class="ui-input" type="number" min="1024" max="65535" v-model.number="forwardForm.listenPort" /></div>
            <div class="field"><label>{{ $t('vpn.targetHost') }}</label><input class="ui-input" v-model="forwardForm.targetHost" /></div>
            <div class="field"><label>{{ $t('vpn.targetPort') }}</label><input class="ui-input" type="number" min="1" max="65535" v-model.number="forwardForm.targetPort" /></div>
          </div>
          <div class="vpn-switches">
            <label class="vpn-switch"><input type="checkbox" v-model="forwardForm.viaProxy" /> {{ $t('vpn.viaProxyOn') }}</label>
          </div>
          <div class="vpn-form-actions">
            <button class="ui-btn primary" @click="saveForward">{{ $t('common.confirm') }}</button>
            <button class="ui-btn" @click="forwardForm = null">{{ $t('common.cancel') }}</button>
          </div>
        </div>
      </div>

      <!-- 核心设置 -->
      <div class="ui-card vpn-section">
        <div class="vpn-sec-title">{{ $t('vpn.core') }}</div>
        <div class="vpn-inline">
          <div class="field grow">
            <label>{{ $t('vpn.coreBinary') }}</label>
            <input class="ui-input ui-mono" v-model="form.binPath" :placeholder="$t('vpn.coreBinaryHint')" />
          </div>
          <div class="field">
            <label>{{ $t('vpn.controllerPort') }}</label>
            <input class="ui-input" type="number" min="1024" max="65535" v-model.number="form.controllerPort" />
          </div>
          <button class="ui-btn mini vpn-detect" @click="detectBin">{{ $t('vpn.detect') }}</button>
        </div>
        <div class="ui-hint" v-if="status.binary">{{ $t('vpn.detectedBinary') }}: <span class="ui-mono">{{ status.binary }}</span></div>
        <div class="ui-hint">{{ $t('vpn.wakeHint') }}</div>
        <button class="ui-btn mini" @click="toggleLog">{{ $t('vpn.viewLog') }}</button>
        <pre v-if="showLog" class="vpn-log">{{ coreLog || $t('vpn.logEmpty') }}</pre>
      </div>
    </div>

    <!-- 订阅 YAML 预览弹层 -->
    <div v-if="previewOpen" class="vpn-modal" @click.self="previewOpen = false">
      <div class="vpn-modal-box">
        <div class="vpn-modal-head">
          <b>{{ $t('vpn.previewTitle') }}</b>
          <button class="ui-btn mini" @click="previewOpen = false">{{ $t('common.close') }}</button>
        </div>
        <pre class="vpn-yaml">{{ previewYaml }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useI18n } from 'vue-i18n'
import { vpnApi } from '../../api'

const { t } = useI18n()

const form = ref({
  enabled: false,
  portForward: true,
  globalProxy: false,
  lowPower: true,          // 低占用模式：默认勾选
  idleMinutes: 15,
  mixedPort: 7890,
  controllerPort: 9097,
  binPath: '',
  subToken: '',
  nodes: [],
  forwards: [],
})
const status = ref({})            // 后端运行状态（轮询刷新）
const statusMsg = ref('')
const statusWarn = ref(false)
const saving = ref(false)
const showToken = ref(false)
const previewOpen = ref(false)
const previewYaml = ref('')
const showLog = ref(false)
const coreLog = ref('')
const nodeForm = ref(null)        // null=收起；对象=新增/编辑中的节点
const nodeEditIdx = ref(-1)
const forwardForm = ref(null)

const nodeTypes = ['ss', 'vmess', 'vless', 'trojan', 'hysteria2', 'socks5', 'http']
const ciphers = ['aes-128-gcm', 'aes-256-gcm', 'chacha20-ietf-poly1305', 'aes-128-cfb', 'rc4-md5', 'none']

// 各类型在表单中需要展示的字段
const TYPE_FIELDS = {
  ss: ['cipher', 'password', 'udp'],
  vmess: ['uuid', 'sni', 'udp'],
  vless: ['uuid', 'sni', 'udp'],
  trojan: ['password', 'sni', 'udp'],
  hysteria2: ['password', 'sni'],
  socks5: ['username', 'password', 'udp'],
  http: ['username', 'password'],
}
function hasField(f) {
  return nodeForm.value && (TYPE_FIELDS[nodeForm.value.type] || []).includes(f)
}
// TLS 相关控件仅对支持 TLS 的类型展示
function nodeHasTls(n) {
  return n && ['vmess', 'vless', 'trojan', 'hysteria2', 'socks5', 'http'].includes(n.type)
}

const statusBadge = computed(() => {
  const s = status.value.status || 'disabled'
  if (s === 'running') return { key: 'vpn.statusRunning', cls: 'ok' }
  if (s === 'sleeping') return { key: 'vpn.statusSleeping', cls: 'warn' }
  if (s === 'disabled') return { key: 'vpn.statusDisabled', cls: 'off' }
  return { key: 'vpn.statusStopped', cls: 'off' }
})

// 订阅链接：面板地址 + 令牌（Clash 客户端直接导入）
const subUrl = computed(() => {
  if (!form.value.subToken) return ''
  return `${window.location.origin}/api/vpn/sub?token=${form.value.subToken}`
})

function flash(msg, warn = false) {
  statusMsg.value = msg
  statusWarn.value = warn
  if (msg) setTimeout(() => { if (statusMsg.value === msg) statusMsg.value = '' }, 6000)
}

async function loadAll() {
  try {
    const data = await vpnApi.get()
    form.value = { ...form.value, ...data.config }
    status.value = data.status || {}
  } catch (e) {
    flash(e?.response?.data?.detail || String(e), true)
  }
}

async function pollStatus() {
  try { status.value = await vpnApi.status() } catch (e) { /* 轮询失败静默，下轮重试 */ }
}

async function save() {
  saving.value = true
  try {
    const payload = { ...form.value }
    delete payload.subToken // 令牌不经保存接口修改（rotateToken 专用）
    const data = await vpnApi.saveConfig(payload)
    form.value = { ...form.value, ...data.config }
    status.value = data.status || {}
    flash(data.warning ? `${t('vpn.saved')} · ${data.warning}` : t('vpn.saved'), !!data.warning)
  } catch (e) {
    flash(e?.response?.data?.detail || String(e), true)
  } finally {
    saving.value = false
  }
}

async function startCore() {
  try {
    const data = await vpnApi.start()
    status.value = data.status || {}
  } catch (e) {
    flash(e?.response?.data?.detail || String(e), true)
  }
}

async function stopCore() {
  try {
    const data = await vpnApi.stop()
    status.value = data.status || {}
  } catch (e) {
    flash(e?.response?.data?.detail || String(e), true)
  }
}

async function rotateToken() {
  if (!confirm(t('vpn.rotateConfirm'))) return
  try {
    const data = await vpnApi.rotateToken()
    form.value.subToken = data.subToken
    flash(t('vpn.rotateDone'))
  } catch (e) {
    flash(e?.response?.data?.detail || String(e), true)
  }
}

async function detectBin() {
  try {
    const data = await vpnApi.detect()
    status.value = { ...status.value, binary: data.binary }
    form.value.binPath = form.value.binPath || data.binary
    flash(data.binary ? `${t('vpn.detectedBinary')}: ${data.binary}` : t('vpn.noBinary'))
  } catch (e) {
    flash(e?.response?.data?.detail || String(e), true)
  }
}

async function copySub() {
  if (!subUrl.value) return
  try {
    await navigator.clipboard.writeText(subUrl.value)
    flash(t('vpn.copied'))
  } catch (e) {
    flash(String(e), true)
  }
}

async function openPreview() {
  try {
    previewYaml.value = await vpnApi.preview()
    previewOpen.value = true
  } catch (e) {
    flash(e?.response?.data?.detail || String(e), true)
  }
}

async function toggleLog() {
  showLog.value = !showLog.value
  if (showLog.value) {
    try { coreLog.value = (await vpnApi.log()).log } catch (e) { coreLog.value = '' }
  }
}

// ---- 节点管理（内联表单） ----
function blankNode() {
  return { id: '', name: '', type: 'ss', server: '', port: 8388, uuid: '', password: '', cipher: 'aes-256-gcm', username: '', sni: '', flow: '', udp: true, tls: false, skipCertVerify: false }
}
function addNode() {
  nodeEditIdx.value = -1
  nodeForm.value = blankNode()
}
function editNode(i) {
  nodeEditIdx.value = i
  nodeForm.value = { ...blankNode(), ...form.value.nodes[i] }
}
function saveNode() {
  const n = { ...nodeForm.value }
  if (!n.name || !n.server || !n.port) { flash(t('vpn.nodeIncomplete'), true); return }
  if (!n.id) n.id = Math.random().toString(36).slice(2, 12)
  if (nodeEditIdx.value >= 0) form.value.nodes.splice(nodeEditIdx.value, 1, n)
  else form.value.nodes.push(n)
  nodeForm.value = null
}
function removeNode(i) {
  if (!confirm(t('vpn.deleteNodeConfirm', { name: form.value.nodes[i].name }))) return
  form.value.nodes.splice(i, 1)
}

// ---- 端口转发条目（内联表单） ----
function addForward() {
  forwardForm.value = { id: '', name: '', listenPort: 13306, targetHost: '', targetPort: 3306, viaProxy: true }
}
function saveForward() {
  const f = { ...forwardForm.value }
  if (!f.listenPort || !f.targetHost || !f.targetPort) { flash(t('vpn.forwardIncomplete'), true); return }
  if (!f.id) f.id = Math.random().toString(36).slice(2, 12)
  if (!f.name) f.name = `fw-${f.listenPort}`
  form.value.forwards.push(f)
  forwardForm.value = null
}
function removeForward(i) {
  if (!confirm(t('vpn.deleteForwardConfirm', { port: form.value.forwards[i].listenPort }))) return
  form.value.forwards.splice(i, 1)
}

let pollTimer = null
onMounted(async () => {
  await loadAll()
  pollTimer = setInterval(pollStatus, 10000)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.vpn-window { display: flex; flex-direction: column; height: 100%; padding: 10px; box-sizing: border-box; }
.vpn-body { flex: 1; overflow: auto; padding: 8px 4px 12px; display: flex; flex-direction: column; gap: 10px; }
.vpn-msg { margin: 6px 2px 0; font-size: 12px; color: #166534; }
.vpn-msg.warn { color: #b45309; }
.vpn-section { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.vpn-sec-title { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.vpn-checks { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px; }
.vpn-check { display: flex; align-items: flex-start; gap: 8px; padding: 8px 10px; border: 1px solid var(--ui-border, #e5e7eb); border-radius: 8px; cursor: pointer; }
.vpn-check input { margin-top: 3px; }
.vpn-check b { font-size: 12.5px; }
.vpn-inline { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.vpn-inline .field { display: flex; flex-direction: column; gap: 4px; }
.vpn-inline .field label { font-size: 11.5px; color: var(--ui-text-secondary, #667); }
.vpn-inline .field input, .vpn-inline .field select { width: 130px; }
.vpn-inline .field.grow { flex: 1; min-width: 260px; }
.vpn-inline .field.grow input { width: 100%; }
.vpn-detect { margin-bottom: 2px; }
.vpn-sub-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.vpn-sub-url { flex: 1; min-width: 260px; }
.vpn-row { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border: 1px solid var(--ui-border, #e5e7eb); border-radius: 8px; flex-wrap: wrap; }
.vpn-row-name { font-size: 12.5px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vpn-row-actions { margin-left: auto; display: flex; gap: 6px; }
.vpn-arrow { color: #999; }
.vpn-form { border: 1px dashed var(--ui-border, #d1d5db); border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
.vpn-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; }
.vpn-grid .field { display: flex; flex-direction: column; gap: 4px; }
.vpn-grid .field label { font-size: 11.5px; color: var(--ui-text-secondary, #667); }
.vpn-switches { display: flex; gap: 16px; flex-wrap: wrap; }
.vpn-switch { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; cursor: pointer; }
.vpn-form-actions { display: flex; gap: 8px; }
.vpn-log { max-height: 180px; overflow: auto; font-size: 11px; background: rgba(0, 0, 0, 0.04); border-radius: 6px; padding: 8px; margin: 0; white-space: pre-wrap; }
.vpn-modal { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); display: flex; align-items: center; justify-content: center; z-index: 60; }
.vpn-modal-box { width: min(760px, 92vw); max-height: 84vh; background: var(--ui-bg, #fff); border-radius: 12px; padding: 14px; display: flex; flex-direction: column; gap: 10px; box-shadow: 0 18px 50px rgba(0, 0, 0, 0.25); }
.vpn-modal-head { display: flex; align-items: center; justify-content: space-between; }
.vpn-yaml { margin: 0; overflow: auto; flex: 1; font-size: 11.5px; background: rgba(0, 0, 0, 0.04); border-radius: 8px; padding: 10px; }
</style>
