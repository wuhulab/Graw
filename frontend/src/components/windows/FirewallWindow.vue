<template>
  <div class="fw-window">
    <div class="toolbar">
      <span class="badge" :class="enabled ? 'ok' : 'off'">{{ $t('firewall.title') }} {{ $t(enabled ? 'firewall.enabled' : 'firewall.disabled') }}</span>
      <button class="btn" @click="toggle">{{ $t(enabled ? 'firewall.disable' : 'firewall.enable') }}</button>
      <button class="btn primary" @click="showPortModal=true">{{ $t('firewall.addPortRule') }}</button>
      <button class="btn primary" @click="showIpModal=true">{{ $t('firewall.addIpRule') }}</button>
      <button class="btn danger-text" :disabled="portRules.length + ipRules.length === 0" @click="doClear">{{ $t('firewall.clearAll') }}</button>
      <span class="hint">{{ $t('firewall.platform', { platform }) }}</span>
    </div>

    <h4>{{ $t('firewall.nolpRules') }}</h4>
    <div class="table-wrap">
      <table>
        <thead><tr><th>{{ $t('firewall.port') }}</th><th>{{ $t('firewall.action') }}</th><th>{{ $t('firewall.remark') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="r in nolpRules" :key="r.id"><td>{{ r.port }}</td>
            <td><span class="badge warn">{{ $t('firewall.deny') }}</span></td>
            <td>{{ r.comment }}</td>
            <td><button class="iconbtn danger" @click="delPort(r.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="nolpRules.length===0"><td colspan="4" class="empty">{{ $t('firewall.noLpRules') }}</td></tr>
        </tbody>
      </table>
      <div class="block-unopened-bar">
        <span v-if="blockUnopenedMsg" class="block-msg">{{ blockUnopenedMsg }}</span>
        <button class="btn danger" style="margin-left:auto;" @click="doBlockUnopened">{{ $t('firewall.blockUnopened') }}</button>
      </div>
    </div>

    <h4>{{ $t('firewall.portRules') }}</h4>
    <div class="table-wrap">
      <table>
        <thead><tr><th>{{ $t('firewall.port') }}</th><th>{{ $t('firewall.protocol') }}</th><th>{{ $t('firewall.action') }}</th><th>{{ $t('firewall.remark') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="r in portRules" :key="r.id"><td>{{ r.port }}</td><td>{{ r.protocol }}</td>
            <td><span class="badge" :class="r.action==='allow'?'ok':'warn'">{{ r.action === 'allow' ? $t('firewall.allow') : $t('firewall.deny') }}</span></td>
            <td>{{ r.comment }}</td>
            <td><button class="iconbtn danger" @click="delPort(r.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="portRules.length===0"><td colspan="5" class="empty">{{ $t('firewall.noPortRules') }}</td></tr>
        </tbody>
      </table>
    </div>

    <h4>{{ $t('firewall.ipRules') }}</h4>
    <div class="table-wrap">
      <table>
        <thead><tr><th>{{ $t('firewall.ip') }}</th><th>{{ $t('firewall.action') }}</th><th>{{ $t('firewall.remark') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="r in ipRules" :key="r.id"><td>{{ r.ip }}</td>
            <td><span class="badge" :class="r.action==='allow'?'ok':'warn'">{{ r.action === 'allow' ? $t('firewall.allow') : $t('firewall.deny') }}</span></td>
            <td>{{ r.comment }}</td>
            <td><button class="iconbtn danger" @click="delIp(r.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="ipRules.length===0"><td colspan="4" class="empty">{{ $t('firewall.noIpRules') }}</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="showPortModal" class="modal-overlay" @click.self="showPortModal=false">
      <div class="modal">
        <h3>{{ $t('firewall.addPortRule') }}</h3>
        <div class="form">
          <label>{{ $t('firewall.port') }}</label><input v-model.number="portForm.port" type="number" />
          <label>{{ $t('firewall.protocol') }}</label>
          <select v-model="portForm.protocol"><option value="tcp">TCP</option><option value="udp">UDP</option></select>
          <label>{{ $t('firewall.action') }}</label>
          <select v-model="portForm.action"><option value="allow">{{ $t('firewall.allow') }}</option><option value="deny">{{ $t('firewall.deny') }}</option></select>
          <label>{{ $t('firewall.remark') }}</label><input v-model="portForm.comment" />
          <div class="actions">
            <button class="btn" @click="showPortModal=false">{{ $t('common.cancel') }}</button>
            <button class="btn primary" @click="addPort">{{ $t('common.save') }}</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showIpModal" class="modal-overlay" @click.self="showIpModal=false">
      <div class="modal">
        <h3>{{ $t('firewall.addIpRule') }}</h3>
        <div class="form">
          <label>{{ $t('firewall.ipCidr') }}</label><input v-model="ipForm.ip" placeholder="如: 192.168.1.0/24" />
          <label>{{ $t('firewall.action') }}</label>
          <select v-model="ipForm.action"><option value="allow">{{ $t('firewall.allow') }}</option><option value="deny">{{ $t('firewall.deny') }}</option></select>
          <label>{{ $t('firewall.remark') }}</label><input v-model="ipForm.comment" />
          <div class="actions">
            <button class="btn" @click="showIpModal=false">{{ $t('common.cancel') }}</button>
            <button class="btn primary" @click="addIp">{{ $t('common.save') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除防火墙规则需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="confirm.title"
      :message="confirm.message"
      :input-label="t('confirmDanger.inputPwdLabel')"
      :placeholder="t('confirmDanger.inputPwdPlaceholder')"
      :confirm-label="$t('common.delete')"
      @confirm="doConfirm"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { firewallApi } from '../../api'
import { Trash2 } from 'lucide-vue-next'
import ConfirmDialog from '../ConfirmDialog.vue'

const { t } = useI18n()

const enabled = ref(true)
const platform = ref('')
const portRules = ref([])
const ipRules = ref([])
const nolpRules = ref([])
const blockUnopenedMsg = ref('')
const showPortModal = ref(false)
const showIpModal = ref(false)
const portForm = ref({ port: 80, protocol: 'tcp', action: 'allow', comment: '' })
const ipForm = ref({ ip: '', action: 'allow', comment: '' })
// 高风险操作二次确认状态
const confirm = ref({ show: false, title: '', message: '', action: null })

async function load() {
  const s = await firewallApi.status()
  enabled.value = s.enabled
  platform.value = s.platform
  const r = await firewallApi.rules()
  portRules.value = r.port_rules || []
  ipRules.value = r.ip_rules || []
  nolpRules.value = (r.port_rules || []).filter(x => x.action === 'deny')
}

async function doBlockUnopened() {
  // 高风险操作：批量屏蔽未放行端口需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.blockUnopenedTitle'),
    message: t('confirmDanger.blockUnopenedMsg'),
    action: { type: 'blockUnopened' }
  }
}

async function execBlockUnopened() {
  try {
    const d = await firewallApi.blockUnopened()
    if (d.created > 0) {
      blockUnopenedMsg.value = t('firewall.blockUnopenedDone', { count: d.created, ports: d.ports.join(', ') })
    } else {
      const skipped = (d.skipped_protected || []).join(', ')
      blockUnopenedMsg.value = skipped
        ? t('firewall.blockUnopenedSkip', { skipped })
        : t('firewall.blockUnopenedNone')
    }
  } catch (e) {
    blockUnopenedMsg.value = t('firewall.blockUnopenedFail', { error: e.response?.data?.detail || e.message })
  }
  await load()
}

async function toggle() {
  const data = await firewallApi.toggle(!enabled.value)
  enabled.value = data.enabled
}

async function addPort() {
  await firewallApi.addPort(portForm.value)
  showPortModal.value = false
  await load()
}

function delPort(id) {
  const rule = portRules.value.find(r => r.id === id)
  // 高风险操作：删除端口规则需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.deleteFirewallPortTitle'),
    message: t('confirmDanger.deleteFirewallPortMsg', { port: rule?.port ?? id }),
    action: { type: 'port', id }
  }
}

async function doConfirm() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return
  if (a.type === 'port') {
    await firewallApi.delPort(a.id)
  } else if (a.type === 'ip') {
    await firewallApi.delIp(a.id)
  } else if (a.type === 'clear') {
    await firewallApi.clear()
  } else if (a.type === 'blockUnopened') {
    await execBlockUnopened()
  }
  await load()
}

function doClear() {
  // 高风险操作：清空全部防火墙规则需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.clearFirewallTitle'),
    message: t('confirmDanger.clearFirewallMsg'),
    action: { type: 'clear' }
  }
}

async function addIp() {
  await firewallApi.addIp(ipForm.value)
  showIpModal.value = false
  await load()
}

function delIp(id) {
  const rule = ipRules.value.find(r => r.id === id)
  // 高风险操作：删除 IP 规则需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.deleteFirewallIpTitle'),
    message: t('confirmDanger.deleteFirewallIpMsg', { ip: rule?.ip ?? id }),
    action: { type: 'ip', id }
  }
}

onMounted(load)
</script>

<style scoped>
.fw-window { padding: 10px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
.hint { color: #6e6e73; font-size: 12px; margin-left: auto; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.warn { background: #fee2e2; color: #991b1b; }
.badge.off { background: #f3f4f6; color: #6b7280; }
h4 { margin: 12px 0 6px; font-size: 14px; }
.table-wrap { overflow: auto; max-height: 260px; border: 1px solid #e5e7eb; border-radius: 8px; margin-bottom: 10px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
th { background: #f9fafb; position: sticky; top: 0; }
.empty { text-align: center; color: #9ca3af; padding: 20px; }
.block-unopened-bar { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-top: 1px solid #f0f0f0; }
.block-msg { font-size: 12px; color: #6e6e73; flex: 1; }
.btn.danger { border-color: #fca5a5; color: #dc2626; }
.btn.danger-text { border-color: transparent; color: #dc2626; background: transparent; }
.btn.danger-text:hover { border-color: #fca5a5; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 420px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input, .form select { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
