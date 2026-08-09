<template>
  <div class="fw-window">
    <div class="toolbar">
      <span class="badge" :class="enabled ? 'ok' : 'off'">防火墙 {{ enabled ? '已启用' : '已停用' }}</span>
      <button class="btn" @click="toggle">{{ enabled ? '停用' : '启用' }}</button>
      <button class="btn primary" @click="showPortModal=true">添加端口规则</button>
      <button class="btn primary" @click="showIpModal=true">添加IP规则</button>
      <span class="hint">平台: {{ platform }}</span>
    </div>

    <h4>端口规则</h4>
    <div class="table-wrap">
      <table>
        <thead><tr><th>端口</th><th>协议</th><th>动作</th><th>备注</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="r in portRules" :key="r.id"><td>{{ r.port }}</td><td>{{ r.protocol }}</td>
            <td><span class="badge" :class="r.action==='allow'?'ok':'warn'">{{ r.action }}</span></td>
            <td>{{ r.comment }}</td>
            <td><button class="iconbtn danger" @click="delPort(r.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="portRules.length===0"><td colspan="5" class="empty">暂无端口规则</td></tr>
        </tbody>
      </table>
    </div>

    <h4>IP 规则</h4>
    <div class="table-wrap">
      <table>
        <thead><tr><th>IP</th><th>动作</th><th>备注</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="r in ipRules" :key="r.id"><td>{{ r.ip }}</td>
            <td><span class="badge" :class="r.action==='allow'?'ok':'warn'">{{ r.action }}</span></td>
            <td>{{ r.comment }}</td>
            <td><button class="iconbtn danger" @click="delIp(r.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="ipRules.length===0"><td colspan="4" class="empty">暂无IP规则</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="showPortModal" class="modal-overlay" @click.self="showPortModal=false">
      <div class="modal">
        <h3>添加端口规则</h3>
        <div class="form">
          <label>端口</label><input v-model.number="portForm.port" type="number" />
          <label>协议</label>
          <select v-model="portForm.protocol"><option value="tcp">TCP</option><option value="udp">UDP</option></select>
          <label>动作</label>
          <select v-model="portForm.action"><option value="allow">允许</option><option value="deny">拒绝</option></select>
          <label>备注</label><input v-model="portForm.comment" />
          <div class="actions">
            <button class="btn" @click="showPortModal=false">取消</button>
            <button class="btn primary" @click="addPort">保存</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showIpModal" class="modal-overlay" @click.self="showIpModal=false">
      <div class="modal">
        <h3>添加IP规则</h3>
        <div class="form">
          <label>IP / CIDR</label><input v-model="ipForm.ip" placeholder="如: 192.168.1.0/24" />
          <label>动作</label>
          <select v-model="ipForm.action"><option value="allow">允许</option><option value="deny">拒绝</option></select>
          <label>备注</label><input v-model="ipForm.comment" />
          <div class="actions">
            <button class="btn" @click="showIpModal=false">取消</button>
            <button class="btn primary" @click="addIp">保存</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { firewallApi } from '../../api'
import { Trash2 } from 'lucide-vue-next'

const enabled = ref(true)
const platform = ref('')
const portRules = ref([])
const ipRules = ref([])
const showPortModal = ref(false)
const showIpModal = ref(false)
const portForm = ref({ port: 80, protocol: 'tcp', action: 'allow', comment: '' })
const ipForm = ref({ ip: '', action: 'allow', comment: '' })

async function load() {
  const s = await firewallApi.status()
  enabled.value = s.enabled
  platform.value = s.platform
  const r = await firewallApi.rules()
  portRules.value = r.port_rules || []
  ipRules.value = r.ip_rules || []
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

async function delPort(id) {
  if (!confirm('删除此端口规则？')) return
  await firewallApi.delPort(id)
  await load()
}

async function addIp() {
  await firewallApi.addIp(ipForm.value)
  showIpModal.value = false
  await load()
}

async function delIp(id) {
  if (!confirm('删除此IP规则？')) return
  await firewallApi.delIp(id)
  await load()
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
