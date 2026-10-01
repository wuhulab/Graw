<!--
  ShunX 安全中心聚合窗口
  业务：单窗口内切换并聚合多个安全相关子应用——防火墙、应用防火墙(WAF)、网页防篡改、数据库保护、备份中心、通知中心、SSH 密钥、系统体检、面板备份。
  后端模块：/api/shunx、/api/waf、/api/tamper、/api/protected、/api/backup、/api/notify、/api/sshkeys、/api/healthcheck、/api/panelbackup
  关键状态：mode（当前子视图标签）
  打开方式：桌面「ShunX 安全」入口挂载
-->
<template>
  <div class="shunx-security-window">
    <!-- 视图切换：防火墙 / 应用防火墙 / 防篡改 / 数据库保护 / 备份 / 通知 / SSH密钥 / 系统体检 / 面板备份 -->
    <div class="toolbar">
      <div class="mode-tabs">
        <button class="tab" :class="{ active: mode === 'firewall' }" @click="switchMode('firewall')">{{ $t('shunx.modeFirewall') }}</button>
        <button class="tab" :class="{ active: mode === 'waf' }" @click="switchMode('waf')">{{ $t('shunx.modeWaf') }}</button>
        <button class="tab" :class="{ active: mode === 'tamper' }" @click="switchMode('tamper')">{{ $t('shunx.modeTamper') }}</button>
        <button class="tab" :class="{ active: mode === 'protection' }" @click="switchMode('protection')">{{ $t('shunx.modeProtection') }}</button>
        <button class="tab" :class="{ active: mode === 'backup' }" @click="switchMode('backup')">{{ $t('shunx.modeBackup') }}</button>
        <button class="tab" :class="{ active: mode === 'notify' }" @click="switchMode('notify')">{{ $t('shunx.modeNotify') }}</button>
        <button class="tab" :class="{ active: mode === 'sshkeys' }" @click="switchMode('sshkeys')">{{ $t('shunx.modeSshkeys') }}</button>
        <button class="tab" :class="{ active: mode === 'healthcheck' }" @click="switchMode('healthcheck')">{{ $t('shunx.modeHealthcheck') }}</button>
        <button class="tab" :class="{ active: mode === 'panelbackup' }" @click="switchMode('panelbackup')">{{ $t('shunx.modePanelbackup') }}</button>
      </div>
    </div>

    <!-- 无权限标签：按约定不隐藏标签，切到无权限模块时仅在标签内提示，避免吃 403 -->
    <div v-if="!tabAllowed(mode)" class="hub-body perm-denied">{{ $t('common.noModulePerm') }}</div>

    <!-- 防火墙视图（合并自独立的「防火墙」应用） -->
    <div v-else-if="mode === 'firewall'" class="hub-body">
      <FirewallWindow @openFirewallRuleForm="emit('openFirewallRuleForm', $event)" />
    </div>

    <!-- 应用防火墙视图（合并自独立的「应用防火墙」应用） -->
    <div v-else-if="mode === 'waf'" class="hub-body">
      <WafWindow @openWafAclForm="emit('openWafAclForm', $event)" />
    </div>

    <!-- 网页防篡改视图（合并自独立的「ShunX网页防篡改」应用） -->
    <div v-else-if="mode === 'tamper'" class="hub-body">
      <TamperWindow @openTamperForm="emit('openTamperForm', $event)" />
    </div>

    <!-- 数据库保护视图（合并自独立的「Graw数据库保护机制」应用） -->
    <div v-else-if="mode === 'protection'" class="hub-body">
      <ProtectionWindow />
    </div>

    <!-- 备份中心视图（合并自独立的「备份中心」应用） -->
    <div v-else-if="mode === 'backup'" class="hub-body">
      <BackupWindow @openBackupTaskForm="emit('openBackupTaskForm', $event)" @openBackupRemoteForm="emit('openBackupRemoteForm', $event)" />
    </div>

    <!-- 通知中心视图（合并自独立的「通知中心」应用） -->
    <div v-else-if="mode === 'notify'" class="hub-body">
      <NotifyWindow @openNotifyChannelForm="emit('openNotifyChannelForm', $event)" @openNotifyRuleForm="emit('openNotifyRuleForm', $event)" />
    </div>

    <!-- SSH密钥视图（合并自独立的「SSH 密钥」应用） -->
    <div v-else-if="mode === 'sshkeys'" class="hub-body">
      <SSHKeysWindow @openSshKeyGen="emit('openSshKeyGen', $event)" @openSshKeyImport="emit('openSshKeyImport', $event)" @openSshKeyDeploy="emit('openSshKeyDeploy', $event)" />
    </div>

    <!-- 系统体检视图（合并自独立的「系统体检」应用） -->
    <div v-else-if="mode === 'healthcheck'" class="hub-body">
      <HealthCheckWindow />
    </div>

    <!-- 面板备份视图（合并自独立的「面板备份」应用） -->
    <div v-else-if="mode === 'panelbackup'" class="hub-body">
      <PanelBackupWindow />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'                        // Composition API：响应式（当前子视图）
import { hasPerm, isFullAdmin } from '../../store/auth'  // 模块权限判定（受限管理员）
import FirewallWindow from './FirewallWindow.vue' // 子应用：防火墙
import WafWindow from './WafWindow.vue'           // 子应用：应用防火墙
import TamperWindow from './TamperWindow.vue'     // 子应用：网页防篡改
import ProtectionWindow from './ProtectionWindow.vue'   // 子应用：数据库保护
import BackupWindow from './BackupWindow.vue'     // 子应用：备份中心
import NotifyWindow from './NotifyWindow.vue'     // 子应用：通知中心
import SSHKeysWindow from './SSHKeysWindow.vue'   // 子应用：SSH 密钥
import HealthCheckWindow from './HealthCheckWindow.vue'  // 子应用：系统体检
import PanelBackupWindow from './PanelBackupWindow.vue'  // 子应用：面板备份

// 冒泡到桌面（App.vue）的独立表单窗口事件：把各子应用弹出的「表单窗口」事件转发给桌面统一打开
const emit = defineEmits([
  'openFirewallRuleForm',
  'openWafAclForm',
  'openTamperForm',
  'openBackupTaskForm',
  'openBackupRemoteForm',
  'openNotifyChannelForm',
  'openNotifyRuleForm',
  'openSshKeyGen',
  'openSshKeyImport',
  'openSshKeyDeploy'
])

// 视图模式：firewall / waf / tamper / protection / backup / notify / sshkeys / healthcheck / panelbackup
const mode = ref('firewall')

// 标签 → 权限门：模块 key 数组（任一命中即放行），'full' 表示仅完整管理员
// （SSH 密钥 / 面板备份属「面板自身安全边界」，不在模块白名单内）。
// 与后端 require_perm 的模块划分保持一致：WAF 归 sites、数据库保护归 firewall。
const TAB_GATE = {
  firewall: ['firewall'],
  waf: ['sites'],
  tamper: ['tamper'],
  protection: ['firewall'],
  backup: ['backup'],
  notify: ['notify'],
  sshkeys: 'full',
  healthcheck: ['healthcheck'],
  panelbackup: 'full',
}

// 当前用户是否可访问该标签：不隐藏标签，无权限时标签内提示（避免点开吃 403）
function tabAllowed(m) {
  const gate = TAB_GATE[m]
  if (!gate) return true
  if (gate === 'full') return isFullAdmin()
  return hasPerm(...gate)
}

function switchMode(m) {
  mode.value = m
}
</script>

<style scoped>
.shunx-security-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.toolbar { display: flex; align-items: center; }
.mode-tabs { display: inline-flex; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; flex-wrap: wrap; }
.mode-tabs .tab { padding: 6px 14px; font-size: 13px; background: #fff; border: none; cursor: pointer; color: #6b7280; }
.mode-tabs .tab + .tab { border-left: 1px solid #e5e7eb; }
.mode-tabs .tab.active { background: #111827; color: #fff; }
.hub-body { flex: 1; min-height: 0; }
/* 无权限标签：标签内居中提示，不隐藏标签本身 */
.hub-body.perm-denied {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #9ca3af;
  font-size: 14px;
}
</style>