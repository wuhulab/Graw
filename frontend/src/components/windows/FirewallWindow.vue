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
    <!-- 工具栏：状态徽标 + 启停 + 新增规则（独立窗口）+ 清空 -->
    <div class="ui-toolbar">
      <span class="ui-badge" :class="enabled ? 'ok' : 'off'">{{ $t('firewall.title') }} {{ $t(enabled ? 'firewall.enabled' : 'firewall.disabled') }}</span>
      <button class="ui-btn" @click="toggle">{{ $t(enabled ? 'firewall.disable' : 'firewall.enable') }}</button>
      <button class="ui-btn primary" @click="emit('openFirewallRuleForm', { mode: 'port' })">{{ $t('firewall.addPortRule') }}</button>
      <button class="ui-btn primary" @click="emit('openFirewallRuleForm', { mode: 'ip' })">{{ $t('firewall.addIpRule') }}</button>
      <button class="ui-btn ghost danger-text" :disabled="portRules.length + ipRules.length === 0" @click="doClear">{{ $t('firewall.clearAll') }}</button>
      <span class="ui-hint right">{{ $t('firewall.platform', { platform }) }}</span>
    </div>

    <!-- ===== 未开放端口屏蔽（NOLP）规则表 ===== -->
    <h4>{{ $t('firewall.nolpRules') }}</h4>
    <div class="ui-table-wrap">
      <table>
        <thead><tr><th>{{ $t('firewall.port') }}</th><th>{{ $t('firewall.action') }}</th><th>{{ $t('firewall.remark') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="r in nolpRules" :key="r.id"><td>{{ r.port }}</td>
            <td><span class="ui-badge danger">{{ $t('firewall.deny') }}</span></td>
            <td>{{ r.comment }}</td>
            <td><button class="iconbtn danger" @click="delPort(r.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="nolpRules.length===0"><td colspan="4" class="ui-empty">{{ $t('firewall.noLpRules') }}</td></tr>
        </tbody>
      </table>
      <div class="block-unopened-bar">
        <span v-if="blockUnopenedMsg" class="block-msg">{{ blockUnopenedMsg }}</span>
        <button class="ui-btn danger" style="margin-left:auto;" @click="doBlockUnopened">{{ $t('firewall.blockUnopened') }}</button>
      </div>
    </div>

    <!-- ===== 端口规则表 ===== -->
    <h4>{{ $t('firewall.portRules') }}</h4>
    <div class="ui-table-wrap">
      <table>
        <thead><tr><th>{{ $t('firewall.port') }}</th><th>{{ $t('firewall.protocol') }}</th><th>{{ $t('firewall.action') }}</th><th>{{ $t('firewall.remark') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="r in portRules" :key="r.id"><td>{{ r.port }}</td><td>{{ r.protocol }}</td>
            <td><span class="ui-badge" :class="r.action==='allow'?'ok':'danger'">{{ r.action === 'allow' ? $t('firewall.allow') : $t('firewall.deny') }}</span></td>
            <td>{{ r.comment }}</td>
            <td><button class="iconbtn danger" @click="delPort(r.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="portRules.length===0"><td colspan="5" class="ui-empty">{{ $t('firewall.noPortRules') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- ===== IP 规则表 ===== -->
    <h4>{{ $t('firewall.ipRules') }}</h4>
    <div class="ui-table-wrap">
      <table>
        <thead><tr><th>{{ $t('firewall.ip') }}</th><th>{{ $t('firewall.action') }}</th><th>{{ $t('firewall.remark') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="r in ipRules" :key="r.id"><td>{{ r.ip }}</td>
            <td><span class="ui-badge" :class="r.action==='allow'?'ok':'danger'">{{ r.action === 'allow' ? $t('firewall.allow') : $t('firewall.deny') }}</span></td>
            <td>{{ r.comment }}</td>
            <td><button class="iconbtn danger" @click="delIp(r.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="ipRules.length===0"><td colspan="4" class="ui-empty">{{ $t('firewall.noIpRules') }}</td></tr>
        </tbody>
      </table>
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
import { ref, onMounted, watch } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 防火墙 API：status/rules/add_port/add_ip/del_port/del_ip/toggle/block_unopened/clear
import { firewallApi } from '../../api'
// 图标（删除规则按钮）
import { Trash2 } from 'lucide-vue-next'
// 高风险操作「输入面板密码」二次确认弹窗
import ConfirmDialog from '../ConfirmDialog.vue'
// 表单保存信号：独立「新增规则」窗口保存成功后刷新本列表
import { formBus } from '../../store/formBus'

const { t } = useI18n()

const emit = defineEmits(['openFirewallRuleForm'])   // 打开独立「新增规则」窗口（mode: port/ip）

const enabled = ref(true)                 // 防火墙是否启用
const platform = ref('')                  // 底层平台（如 nftables/iptables/win）
const portRules = ref([])                 // 端口规则（放行/拒绝）
const ipRules = ref([])                   // IP 规则（CIDR + 动作）
const nolpRules = ref([])                 // 未开放端口屏蔽规则（即 action=deny 的端口规则子集）
const blockUnopenedMsg = ref('')          // 「屏蔽未开放端口」操作结果提示
// 高风险操作二次确认状态
const confirm = ref({ show: false, title: '', message: '', action: null })

// 新增规则改由独立窗口承载：保存成功后 bumpForm('firewall') 触发此处重载
watch(() => formBus.firewall, load)

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
h4 { margin: 12px 0 6px; font-size: 14px; }
.block-unopened-bar { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-top: 1px solid #f0f0f0; }
.block-msg { font-size: 12px; color: #6e6e73; flex: 1; }
.danger-text { color: #dc2626; }
.danger-text:hover { background: #fee2e2; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
</style>
