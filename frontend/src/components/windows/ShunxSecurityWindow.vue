<template>
  <div class="shunx-security-window">
    <!-- 视图切换：防火墙 / 应用防火墙 / 防篡改 / 数据库保护 / 备份 / 通知 / SSH密钥 -->
    <div class="toolbar">
      <div class="mode-tabs">
        <button class="tab" :class="{ active: mode === 'firewall' }" @click="switchMode('firewall')">{{ $t('shunx.modeFirewall') }}</button>
        <button class="tab" :class="{ active: mode === 'waf' }" @click="switchMode('waf')">{{ $t('shunx.modeWaf') }}</button>
        <button class="tab" :class="{ active: mode === 'tamper' }" @click="switchMode('tamper')">{{ $t('shunx.modeTamper') }}</button>
        <button class="tab" :class="{ active: mode === 'protection' }" @click="switchMode('protection')">{{ $t('shunx.modeProtection') }}</button>
        <button class="tab" :class="{ active: mode === 'backup' }" @click="switchMode('backup')">{{ $t('shunx.modeBackup') }}</button>
        <button class="tab" :class="{ active: mode === 'notify' }" @click="switchMode('notify')">{{ $t('shunx.modeNotify') }}</button>
        <button class="tab" :class="{ active: mode === 'sshkeys' }" @click="switchMode('sshkeys')">{{ $t('shunx.modeSshkeys') }}</button>
      </div>
    </div>

    <!-- 防火墙视图（合并自独立的「防火墙」应用） -->
    <div v-if="mode === 'firewall'" class="hub-body">
      <FirewallWindow />
    </div>

    <!-- 应用防火墙视图（合并自独立的「应用防火墙」应用） -->
    <div v-else-if="mode === 'waf'" class="hub-body">
      <WafWindow />
    </div>

    <!-- 网页防篡改视图（合并自独立的「ShunX网页防篡改」应用） -->
    <div v-else-if="mode === 'tamper'" class="hub-body">
      <TamperWindow />
    </div>

    <!-- 数据库保护视图（合并自独立的「Graw数据库保护机制」应用） -->
    <div v-else-if="mode === 'protection'" class="hub-body">
      <ProtectionWindow />
    </div>

    <!-- 备份中心视图（合并自独立的「备份中心」应用） -->
    <div v-else-if="mode === 'backup'" class="hub-body">
      <BackupWindow />
    </div>

    <!-- 通知中心视图（合并自独立的「通知中心」应用） -->
    <div v-else-if="mode === 'notify'" class="hub-body">
      <NotifyWindow />
    </div>

    <!-- SSH密钥视图（合并自独立的「SSH 密钥」应用） -->
    <div v-else class="hub-body">
      <SSHKeysWindow />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import FirewallWindow from './FirewallWindow.vue'
import WafWindow from './WafWindow.vue'
import TamperWindow from './TamperWindow.vue'
import ProtectionWindow from './ProtectionWindow.vue'
import BackupWindow from './BackupWindow.vue'
import NotifyWindow from './NotifyWindow.vue'
import SSHKeysWindow from './SSHKeysWindow.vue'

// 视图模式：firewall / waf / tamper / protection / backup / notify / sshkeys
const mode = ref('firewall')

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
</style>