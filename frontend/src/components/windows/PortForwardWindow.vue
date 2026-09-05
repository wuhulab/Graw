<!--
  SSH 端口转发窗口（PortForwardWindow）
  业务：把「远程节点的某个端口」转发到「面板所在主机的本地端口」，本地工具
        （Navicat / redis-cli）即可直连远程服务，免开防火墙/跳板。
  后端模块：portforwardApi（list/running/create/toggle/remove）
  关键状态：items（已配置条目）、runningMap（id→运行态）、form（新建弹窗）
  打开方式：桌面「端口转发」入口（管理员）
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <button class="btn primary" @click="openForm">{{ $t('pf.create') }}</button>
      <button class="btn" @click="loadAll">{{ $t('common.refresh') }}</button>
      <span style="margin-left:auto; color:#888; font-size:11px;">{{ $t('pf.localHint') }}</span>
    </div>

    <div style="flex:1; overflow:auto; padding: 12px;">
      <div v-if="items.length === 0" style="text-align:center;color:#999;padding:40px;font-size:12px;">
        {{ $t('pf.empty') }}
      </div>
      <div v-for="it in items" :key="it.id" class="row">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          <b style="font-size:13px;">{{ it.name || it.id }}</b>
          <span class="badge" :class="runStatus(it).active ? 'ok' : 'off'">
            {{ runStatus(it).active ? $t('pf.running') : $t('pf.stopped') }}
          </span>
        </div>
        <div style="font-size:12px; color:#455; margin-top:4px;">
          <span class="mono">127.0.0.1:{{ it.local_port }}</span>
          <span style="color:#999;"> → </span>
          <span class="mono">{{ it.remote_host }}:{{ it.remote_port }}</span>
          <span style="margin-left:10px;">{{ $t('pf.node') }}: {{ it.node_id }}</span>
        </div>
        <div v-if="runStatus(it).stats" style="font-size:11px; color:#888; margin-top:2px;">
          {{ $t('pf.conns') }}: {{ runStatus(it).stats.conns }} · {{ $t('pf.traffic') }}: {{ formatBytes(runStatus(it).stats.bytes_in) }} ↑ / {{ formatBytes(runStatus(it).stats.bytes_out) }} ↓
        </div>
        <div style="margin-top:8px; display:flex; gap:6px;">
          <button class="btn" @click="doToggle(it)">{{ runStatus(it).active ? $t('pf.stop') : $t('pf.start') }}</button>
          <button class="btn danger" @click="doRemove(it)">{{ $t('common.delete') }}</button>
        </div>
      </div>
    </div>

    <!-- 新建弹窗 -->
    <div v-if="form.show" class="modal-mask" @click.self="form.show = false">
      <div class="modal">
        <h3 style="margin-top:0;">{{ $t('pf.create') }}</h3>
        <label class="fld"><span>{{ $t('pf.name') }}</span><input v-model="form.name" type="text" /></label>
        <label class="fld">
          <span>{{ $t('pf.node') }}（SSH）</span>
          <select v-model="form.node_id">
            <option v-for="n in sshNodes" :key="n.id" :value="n.id">{{ n.name }}（{{ n.host }}）</option>
          </select>
        </label>
        <label class="fld"><span>{{ $t('pf.localPort') }}</span><input v-model.number="form.local_port" type="number" min="1024" max="65535" /></label>
        <label class="fld"><span>{{ $t('pf.remoteHost') }}</span><input v-model="form.remote_host" type="text" placeholder="127.0.0.1 / db.example.com" /></label>
        <label class="fld"><span>{{ $t('pf.remotePort') }}</span><input v-model.number="form.remote_port" type="number" min="1" max="65535" /></label>
        <div class="modal-actions">
          <button class="btn" @click="form.show = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="saving" @click="doCreate">{{ saving ? $t('common.loading') : $t('common.save') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'                 // 响应式 + 挂载刷新
import { useI18n } from 'vue-i18n'                              // 国际化
import { portforwardApi, formatBytes } from '../../api'         // 接口 + 字节格式化
import { nodes, refreshNodes } from '../../store/nodes'         // 节点列表（筛选 SSH 节点）

const { t } = useI18n()
const items = ref([])         // 已配置条目
const runningMap = ref({})    // 运行中隧道：id → tunnel 状态
const form = ref({ show: false })
const saving = ref(false)

const sshNodes = computed(() => nodes.list.filter(n => n.type === 'ssh'))

// 取某条目的运行状态（无则 stopped）
function runStatus(it) {
  return runningMap.value[it.id] || { active: false }
}

async function loadAll() {
  try {
    const [it, run] = await Promise.all([portforwardApi.list(), portforwardApi.running()])
    items.value = it.items || []
    const map = {}
    for (const x of run.tunnels || []) map[x.id] = x
    runningMap.value = map
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

function openForm() {
  form.value = {
    show: true, name: '', node_id: sshNodes.value[0]?.id || '',
    local_port: '', remote_host: '127.0.0.1', remote_port: ''
  }
}

async function doCreate() {
  const f = form.value
  if (!f.local_port || !f.remote_port) {
    alert(t('pf.needPorts'))
    return
  }
  saving.value = true
  try {
    await portforwardApi.create({
      name: f.name, node_id: f.node_id, local_port: Number(f.local_port),
      remote_host: f.remote_host.trim(), remote_port: Number(f.remote_port)
    })
    form.value.show = false
    await loadAll()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  } finally {
    saving.value = false
  }
}

async function doToggle(it) {
  try {
    await portforwardApi.toggle(it.id)
    await loadAll()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

async function doRemove(it) {
  if (!confirm(t('pf.deleteConfirm'))) return
  try {
    await portforwardApi.remove(it.id)
    await loadAll()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

onMounted(async () => {
  await refreshNodes()
  await loadAll()
})
</script>

<style scoped>
.row { border: 1px solid #e4e7f0; border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; background: #fafbfe; }
.badge { font-size: 11px; padding: 1px 8px; border-radius: 10px; color: #fff; }
.badge.ok { background: #27ae60; }
.badge.off { background: #7f8c8d; }
.mono { font-family: Consolas, monospace; color: #0a3d7a; }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex; align-items: center; justify-content: center; z-index: 9999; }
.modal { background: #fff; border-radius: 10px; padding: 18px; width: 420px; }
.fld { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }
.fld > span { font-size: 12px; color: #0a3d7a; font-weight: 600; }
.fld input, .fld select { font-size: 12px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
</style>