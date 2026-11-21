<!--
  Fail2banPanel.vue — 工具箱「Fail2ban」
  ==========================================================
  业务作用：
    查看 fail2ban 运行状态与各 jail 的封禁 IP，执行启停/重启，
    并支持手动封禁 / 解封 IP。
  后端模块：
    /api/toolbox/fail2ban（GET 状态 / POST 动作）。
  打开方式：
    ToolboxWindow 的「Fail2ban」页签。
-->
<template>
  <div class="f2b-panel">
    <div class="toolbar">
      <button class="btn" :disabled="loading" @click="load">{{ loading ? '加载中…' : '刷新' }}</button>
      <template v-if="status.installed">
        <button class="btn" :disabled="busy" @click="act('start')">启动</button>
        <button class="btn" :disabled="busy" @click="act('stop')">停止</button>
        <button class="btn" :disabled="busy" @click="act('restart')">重启</button>
        <span class="state" :class="status.running ? 'ok' : 'off'">{{ status.running ? '运行中' : '未运行' }}</span>
      </template>
      <span v-if="err" class="err">{{ err }}</span>
      <span v-if="msg" class="msg">{{ msg }}</span>
    </div>

    <div v-if="!loading && !status.installed" class="empty">
      未检测到 fail2ban-client。请先在系统上安装 fail2ban 后刷新。
    </div>

    <template v-else-if="status.installed">
      <div class="ban-box">
        <input v-model.trim="banJail" class="inp" placeholder="Jail 名称" spellcheck="false" />
        <input v-model.trim="banIP" class="inp" placeholder="IP 地址" spellcheck="false" />
        <button class="btn primary" :disabled="busy || !banJail || !banIP" @click="act('ban')">封禁</button>
      </div>

      <div v-for="j in status.jails || []" :key="j.name" class="jail">
        <div class="jail-head">
          <strong>{{ j.name }}</strong>
          <span class="muted">封禁 {{ (j.banned || []).length }} 个 IP</span>
        </div>
        <div v-if="(j.banned || []).length" class="ips">
          <span v-for="ip in j.banned" :key="ip" class="ip-chip">
            {{ ip }}
            <button class="unban" @click="unban(j.name, ip)">×</button>
          </span>
        </div>
        <pre v-else class="raw">{{ j.raw }}</pre>
      </div>
      <div v-if="!(status.jails || []).length && status.running" class="muted">当前没有启用的 jail。</div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { toolboxApi } from '../../api'

const status = reactive({ installed: false, running: false, jails: [] })
const loading = ref(false)
const busy = ref(false)
const err = ref('')
const msg = ref('')
const banJail = ref('sshd')
const banIP = ref('')

async function load() {
  loading.value = true
  err.value = ''
  try {
    Object.assign(status, await toolboxApi.fail2ban())
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function act(action) {
  busy.value = true
  err.value = ''
  msg.value = ''
  try {
    const r = await toolboxApi.fail2banAction({ action, jail: banJail.value, ip: banIP.value })
    if (r.status) Object.assign(status, r.status)
    msg.value = action + ' 完成'
    banIP.value = ''
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

async function unban(jail, ip) {
  try {
    const r = await toolboxApi.fail2banAction({ action: 'unban', jail, ip })
    if (r.status) Object.assign(status, r.status)
    msg.value = '已解封 ' + ip
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  }
}

onMounted(load)
</script>

<style scoped>
.f2b-panel { padding: 10px; height: 100%; overflow: auto; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
.state { font-size: 11.5px; padding: 2px 8px; border-radius: 999px; }
.state.ok { background: #ecfdf5; color: #047857; }
.state.off { background: #fef2f2; color: #b91c1c; }
.err { color: #b91c1c; font-size: 12px; }
.msg { color: #047857; font-size: 12px; }
.empty { color: var(--g-text-muted); font-size: 13px; padding: 30px; text-align: center; }
.ban-box { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.inp { padding: 6px 8px; border: 1px solid var(--g-border); border-radius: 8px; background: var(--g-surface); color: var(--g-text); font-size: 12px; width: 180px; }
.jail { border: 1px solid var(--g-border); border-radius: 10px; background: var(--g-surface); padding: 10px 12px; margin-bottom: 8px; }
.jail-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.muted { color: var(--g-text-muted); font-size: 11.5px; }
.ips { display: flex; flex-wrap: wrap; gap: 6px; }
.ip-chip { display: inline-flex; align-items: center; gap: 4px; background: var(--g-surface-soft); border-radius: 999px; padding: 2px 8px; font-size: 11.5px; font-family: ui-monospace, monospace; }
.unban { border: none; background: transparent; color: #b91c1c; cursor: pointer; font-size: 13px; line-height: 1; padding: 0; }
.raw { margin: 0; font-size: 11px; color: var(--g-text-muted); white-space: pre-wrap; max-height: 120px; overflow: auto; }
</style>
