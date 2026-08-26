<!--
  防火墙窗口（后端 /api/firewall 模块）
  作用：启停系统防火墙，管理端口 / IP 的放行与拒绝规则，并提供「屏蔽未开放端口」批量保护。
        规则分三组展示：未开放端口屏蔽（NOLP）、端口规则、IP 规则。
  后端模块：/api/firewall（status 状态、rules 规则列表、add_port/add_ip、del_port/del_ip、
            toggle 启停、block_unopened 屏蔽未开放端口、clear 清空全部）。
  关键状态：enabled/platform（防火墙状态）、portRules/ipRules/nolpRules（规则列表）、
            portForm/ipForm（新增规则表单）、blockUnopenedMsg（批量屏蔽结果）、confirm（二次确认）。
  删除 / 清空 / 批量屏蔽等破坏性操作需输入面板密码（ConfirmDialog）确认。
  打开方式：桌面「防火墙」卡片（内嵌于 ShunX 保护聚合窗口时外边距由父容器统一提供）。
-->
<template>
  <div class="fw-window">
    <!-- 工具栏：状态徽标 + 启停 + 新增规则 + 清空 -->
    <div class="toolbar">
      <span class="badge" :class="enabled ? 'ok' : 'off'">{{ $t('firewall.title') }} {{ $t(enabled ? 'firewall.enabled' : 'firewall.disabled') }}</span>
      <button class="btn" @click="toggle">{{ $t(enabled ? 'firewall.disable' : 'firewall.enable') }}</button>
      <button class="btn primary" @click="showPortModal=true">{{ $t('firewall.addPortRule') }}</button>
      <button class="btn primary" @click="showIpModal=true">{{ $t('firewall.addIpRule') }}</button>
      <button class="btn danger-text" :disabled="portRules.length + ipRules.length === 0" @click="doClear">{{ $t('firewall.clearAll') }}</button>
      <span class="hint">{{ $t('firewall.platform', { platform }) }}</span>
    </div>

    <!-- ===== 未开放端口屏蔽（NOLP）规则表 ===== -->
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

    <!-- ===== 端口规则表 ===== -->
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

    <!-- ===== IP 规则表 ===== -->
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

    <!-- 新增端口规则弹窗 -->
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

    <!-- 新增 IP 规则弹窗 -->
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
// 响应式状态与生命周期钩子
import { ref, onMounted } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 防火墙 API：status/rules/add_port/add_ip/del_port/del_ip/toggle/block_unopened/clear
import { firewallApi } from '../../api'
// 图标（删除规则按钮）
import { Trash2 } from 'lucide-vue-next'
// 高风险操作「输入面板密码」二次确认弹窗
import ConfirmDialog from '../ConfirmDialog.vue'

const { t } = useI18n()

const enabled = ref(true)                 // 防火墙是否启用
const platform = ref('')                  // 底层平台（如 nftables/iptables/win）
const portRules = ref([])                 // 端口规则（放行/拒绝）
const ipRules = ref([])                   // IP 规则（CIDR + 动作）
const nolpRules = ref([])                 // 未开放端口屏蔽规则（即 action=deny 的端口规则子集）
const blockUnopenedMsg = ref('')          // 「屏蔽未开放端口」操作结果提示
const showPortModal = ref(false)          // 新增端口规则弹窗显隐
const showIpModal = ref(false)            // 新增 IP 规则弹窗显隐
const portForm = ref({ port: 80, protocol: 'tcp', action: 'allow', comment: '' })
const ipForm = ref({ ip: '', action: 'allow', comment: '' })
// 高风险操作二次确认状态
const confirm = ref({ show: false, title: '', message: '', action: null })

// --- 动作：拉取防火墙状态与全部规则 ---
async function load() {
  const s = await firewallApi.status()   // 调用 /api/firewall/status
  enabled.value = s.enabled
  platform.value = s.platform
  const r = await firewallApi.rules()    // 调用 /api/firewall/rules
  portRules.value = r.port_rules || []
  ipRules.value = r.ip_rules || []
  // NOLP 即端口规则中 action=deny 的部分，专门展示「屏蔽未开放端口」结果
  nolpRules.value = (r.port_rules || []).filter(x => x.action === 'deny')
}

// --- 动作：批量屏蔽未开放端口（破坏性操作，先弹密码确认框） ---
async function doBlockUnopened() {
  // 高风险操作：批量屏蔽未放行端口需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.blockUnopenedTitle'),
    message: t('confirmDanger.blockUnopenedMsg'),
    action: { type: 'blockUnopened' }
  }
}

// --- 动作：密码校验通过后真正执行屏蔽并提示结果 ---
async function execBlockUnopened() {
  try {
    const d = await firewallApi.blockUnopened()   // 调用 /api/firewall/block_unopened
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

// --- 动作：启用/停用防火墙 ---
async function toggle() {
  const data = await firewallApi.toggle(!enabled.value)   // 调用 /api/firewall/toggle
  enabled.value = data.enabled
}

// --- 动作：新增端口规则并刷新列表 ---
async function addPort() {
  await firewallApi.addPort(portForm.value)   // 调用 /api/firewall/add_port
  showPortModal.value = false
  await load()
}

// 删除端口规则：高风险操作，先弹密码确认框
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

// --- 动作：密码校验通过后按 action 类型执行对应删除/清空/屏蔽 ---
async function doConfirm() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return   // 无待执行动作则提前返回
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

// 清空全部规则：高风险操作，先弹密码确认框
function doClear() {
  // 高风险操作：清空全部防火墙规则需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.clearFirewallTitle'),
    message: t('confirmDanger.clearFirewallMsg'),
    action: { type: 'clear' }
  }
}

// --- 动作：新增 IP 规则并刷新列表 ---
async function addIp() {
  await firewallApi.addIp(ipForm.value)   // 调用 /api/firewall/add_ip
  showIpModal.value = false
  await load()
}

// 删除 IP 规则：高风险操作，先弹密码确认框
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

onMounted(load)   // 打开即加载防火墙状态与规则
</script>

<style scoped>
.fw-window { padding: 0; } /* 内嵌于 ShunX 保护机制聚合窗口，外边距由父容器统一提供，上栏可与父窗口边缘平齐 */
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
