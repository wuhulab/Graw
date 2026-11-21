<!--
  VmWindow.vue — 虚拟机管理（libvirt/virsh）
  ==========================================================
  业务作用：
    发现宿主机上的 libvirt 虚拟机，展示状态/CPU/内存/自启，支持启动、关机、
    重启、强制停止、挂起/恢复与自启开关，并可查看 XML 与 VNC 地址。
    不创建/克隆虚拟机（需要磁盘与网络模板，属后续扩展）。
  后端模块：
    /api/vms/*（handlers_vm.go）。
  打开方式：
    桌面/侧边栏「虚拟机」快捷方式（perm=vms）。
-->
<template>
  <div class="vm-window">
    <div class="toolbar">
      <button class="btn" :disabled="loading" @click="load">{{ loading ? $t('common.loading') : $t('common.refresh') }}</button>
      <span v-if="status.version" class="hint">{{ status.version }}</span>
      <span v-if="err" class="err">{{ err }}</span>
    </div>

    <div v-if="!loading && !status.available" class="empty">
      <Server :size="36" />
      <div>{{ $t('vms.unavailable') }}</div>
      <div class="hint">{{ status.reason }}</div>
      <div class="hint">{{ $t('vms.installHint') }}</div>
    </div>

    <table v-else class="tbl">
      <thead><tr>
        <th>{{ $t('common.name') }}</th><th>{{ $t('common.status') }}</th><th>vCPU</th><th>{{ $t('vms.memory') }}</th>
        <th>{{ $t('vms.autostart') }}</th><th style="width:320px;">{{ $t('common.action') }}</th>
      </tr></thead>
      <tbody>
        <tr v-for="v in vms" :key="v.name">
          <td><button class="link" @click="openDetail(v)">{{ v.name }}</button></td>
          <td><span :class="['state', stateClass(v.state)]">{{ v.state }}</span></td>
          <td>{{ v.vcpus || '-' }}</td>
          <td>{{ v.memory_kb ? Math.round(v.memory_kb / 1024) + ' MB' : '-' }}</td>
          <td>{{ v.autostart ? $t('common.yes') : $t('common.no') }}</td>
          <td>
            <button class="btn sm" :disabled="busy === v.name" @click="act(v, 'start')">{{ $t('common.start') }}</button>
            <button class="btn sm" :disabled="busy === v.name" @click="act(v, 'shutdown')">{{ $t('vms.shutdown') }}</button>
            <button class="btn sm" :disabled="busy === v.name" @click="act(v, 'reboot')">{{ $t('common.restart') }}</button>
            <button class="btn sm" :disabled="busy === v.name" @click="act(v, 'suspend')">{{ $t('vms.suspend') }}</button>
            <button class="btn sm" :disabled="busy === v.name" @click="act(v, 'resume')">{{ $t('vms.resume') }}</button>
            <button class="btn sm" :disabled="busy === v.name" @click="act(v, v.autostart ? 'autostart_off' : 'autostart_on')">
              {{ v.autostart ? $t('vms.autostartOff') : $t('vms.autostartOn') }}
            </button>
            <button class="btn sm danger" :disabled="busy === v.name" @click="act(v, 'destroy')">{{ $t('vms.forceStop') }}</button>
          </td>
        </tr>
        <tr v-if="!vms.length"><td colspan="6" class="muted">{{ $t('vms.noVms') }}</td></tr>
      </tbody>
    </table>

    <div v-if="detail" class="mask" @click.self="detail = null">
      <div class="dialog">
        <div class="dialog-title">{{ detail.name }}</div>
        <div class="dialog-body">
          <div class="kv"><span>{{ $t('common.status') }}</span><b>{{ detail.state }}</b></div>
          <div class="kv"><span>UUID</span><span class="mono">{{ detail.uuid || '-' }}</span></div>
          <div class="kv"><span>VNC</span><span class="mono">{{ detail.vnc || '-' }}</span></div>
          <pre class="xml">{{ detail.xml || '' }}</pre>
        </div>
        <div class="dialog-foot"><button class="btn" @click="detail = null">{{ $t('common.close') }}</button></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { vmApi } from '../../api'
import { Server } from 'lucide-vue-next'

const loading = ref(true)
const err = ref('')
const busy = ref('')
const vms = ref([])
const detail = ref(null)
const status = reactive({ available: false, reason: '', version: '' })

function apiErr(e) { return e?.response?.data?.detail || e.message }
function stateClass(s) {
  const v = String(s || '').toLowerCase()
  if (v.includes('running')) return 'ok'
  if (v.includes('paused') || v.includes('suspend')) return 'warn'
  return 'off'
}

async function load() {
  loading.value = true
  err.value = ''
  try {
    const s = await vmApi.status()
    Object.assign(status, s)
    if (s.available) {
      const r = await vmApi.list()
      vms.value = r.vms || []
    } else {
      vms.value = []
    }
  } catch (e) { err.value = apiErr(e) } finally { loading.value = false }
}

async function act(v, action) {
  if (action === 'destroy' && !window.confirm(`强制停止虚拟机「${v.name}」？可能丢失未保存数据。`)) return
  busy.value = v.name
  try {
    await vmApi.action(v.name, action)
    await load()
  } catch (e) { alert(apiErr(e)) } finally { busy.value = '' }
}

async function openDetail(v) {
  try {
    const r = await vmApi.detail(v.name)
    detail.value = r.vm
  } catch (e) { alert(apiErr(e)) }
}

onMounted(load)
</script>

<style scoped>
.vm-window { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--g-border); }
.hint { font-size: 11.5px; color: var(--g-text-muted); }
.err { color: #b91c1c; font-size: 12px; }
.tbl { flex: 1; width: 100%; border-collapse: collapse; font-size: 12px; overflow: auto; }
.tbl th, .tbl td { border-bottom: 1px solid var(--g-border); padding: 7px 8px; text-align: left; }
.tbl th { color: var(--g-text-muted); font-weight: 600; position: sticky; top: 0; background: var(--g-surface); }
.link { border: none; background: transparent; color: #2563eb; cursor: pointer; padding: 0; font-size: 12.5px; }
.state { font-size: 11px; padding: 1px 7px; border-radius: 999px; background: var(--g-surface-soft); }
.state.ok { background: #ecfdf5; color: #047857; }
.state.warn { background: #fffbeb; color: #b45309; }
.state.off { background: #fef2f2; color: #b91c1c; }
.empty { margin: auto; display: flex; flex-direction: column; align-items: center; gap: 8px; color: var(--g-text-muted); font-size: 13px; padding: 30px; }
.muted { color: var(--g-text-muted); text-align: center; }
.mask { position: fixed; inset: 0; background: rgba(15,23,42,.55); display: flex; align-items: center; justify-content: center; z-index: 80; padding: 24px; }
.dialog { width: 100%; max-width: 720px; max-height: 90%; background: var(--g-surface); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; }
.dialog-title { padding: 11px 14px; border-bottom: 1px solid var(--g-border); font-weight: 700; font-size: 13.5px; background: var(--g-surface-muted); }
.dialog-body { padding: 12px 14px; overflow: auto; display: flex; flex-direction: column; gap: 6px; }
.dialog-foot { padding: 10px 14px; border-top: 1px solid var(--g-border); display: flex; justify-content: flex-end; background: var(--g-surface-muted); }
.kv { display: flex; gap: 10px; font-size: 12px; }
.kv span:first-child { width: 90px; color: var(--g-text-muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.xml { background: #0f1722; color: #d7e3f4; border-radius: 8px; padding: 8px; font-size: 11px; max-height: 420px; overflow: auto; white-space: pre-wrap; margin: 0; }
</style>
