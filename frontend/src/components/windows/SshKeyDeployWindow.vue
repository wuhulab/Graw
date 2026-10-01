<!--
  SshKeyDeployWindow.vue — SSH 公钥部署到节点表单（独立窗口）
  ==========================================================
  业务作用：原内嵌于 SSHKeysWindow 的「部署到节点」modal 独立为桌面窗口，
  避免误触灰色遮罩丢失已选的目标节点。打开时自行拉取节点列表。
  后端模块：/api/sshkeys 的 nodes / deploy（把公钥追加到目标 authorized_keys，幂等）。
-->
<template>
  <div class="deploy-key-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <p class="hint-text">将「{{ key?.name }}」的公钥追加到目标节点 ~/.ssh/authorized_keys（幂等，已存在则跳过）。</p>

    <label class="ui-field">
      <span class="ui-label">目标节点</span>
      <select class="ui-select" v-model="node">
        <option value="">请选择节点…</option>
        <option v-for="n in nodes" :key="n.id" :value="n.id">
          {{ n.name }}{{ n.type === 'ssh' ? `（${n.user}@${n.host}）` : '（本机）' }}
        </option>
      </select>
      <span v-if="nodes.length === 0" class="ui-hint">暂无可用节点，请先在「多节点管理」中添加节点</span>
    </label>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="busy" @click="emit('close')">取消</button>
      <button class="ui-btn primary" :disabled="busy || !node" @click="deploy">{{ busy ? '部署中…' : '部署' }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { sshkeysApi } from '../../api'
import { bumpForm } from '../../store/formBus'

const props = defineProps({
  key: { type: Object, default: null }   // 要部署的公钥对应的密钥记录
})
const emit = defineEmits(['close'])

const busy = ref(false)
const error = ref('')
const node = ref('')       // 选中的目标节点 id
const nodes = ref([])      // 可部署的节点列表（含本机与 SSH 节点）

// --- 打开即拉取节点列表 ---
async function loadNodes() {
  try {
    const r = await sshkeysApi.nodes()
    nodes.value = (r && r.nodes) || []
  } catch (e) {
    error.value = '加载节点失败：' + (e.response?.data?.detail || e.message)
  }
}

async function deploy() {
  if (busy.value || !node.value || !props.key) return   // 未选目标节点或请求进行中则不发请求
  busy.value = true
  error.value = ''
  try {
    const r = await sshkeysApi.deploy(props.key.id, node.value)   // 后端幂等追加公钥
    alert(`已部署到节点「${r.node_name}」`)
    bumpForm('sshkeys')
    emit('close')
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

onMounted(loadNodes)
</script>

<style scoped>
.deploy-key-window { padding: 14px; }
.hint-text { font-size: 12.5px; color: #6e6e73; margin-bottom: 12px; line-height: 1.6; }
.error-box { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; word-break: break-all; }
</style>