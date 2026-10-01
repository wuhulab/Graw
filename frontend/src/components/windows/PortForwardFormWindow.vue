<!--
  PortForwardFormWindow.vue — SSH 端口转发 新建 表单（独立窗口）
  ==========================================================
  业务作用：原内嵌于 PortForwardWindow 的「新建隧道」modal 弹窗独立为
  桌面窗口，避免误触灰色遮罩丢失已填写的端口/目标等配置。选择 SSH 节点、
  本地端口、远端主机与端口。
  后端模块：portforwardApi 的 create。
  打开方式：由 App.vue 的 openPortForwardForm(payload) 打开，props 传入
  { sshNodes: 可选 SSH 节点列表 }（若未传则本窗口自行拉取节点）。
  保存成功后 emit('close')，并经 formBus 通知 PortForwardWindow 刷新。
-->
<template>
  <div class="pf-form-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">{{ $t('pf.name') }}</span>
      <input class="ui-input" v-model.trim="form.name" type="text" />
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('pf.node') }}（SSH）</span>
      <select class="ui-select" v-model="form.node_id">
        <option v-for="n in sshNodes" :key="n.id" :value="n.id">{{ n.name }}（{{ n.host }}）</option>
      </select>
      <span v-if="sshNodes.length === 0" class="ui-hint">暂无 SSH 节点，请先在「多节点管理」中添加</span>
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('pf.localPort') }}</span>
      <input class="ui-input" v-model.number="form.local_port" type="number" min="1024" max="65535" />
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('pf.remoteHost') }}</span>
      <input class="ui-input" v-model.trim="form.remote_host" type="text" placeholder="127.0.0.1 / db.example.com" />
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('pf.remotePort') }}</span>
      <input class="ui-input" v-model.number="form.remote_port" type="number" min="1" max="65535" />
    </label>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="create">{{ saving ? $t('common.loading') : $t('common.save') }}</button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与 props
import { ref, reactive, computed, onMounted } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 接口：创建端口转发 + 节点列表
import { portforwardApi } from '../../api'
import { nodes, refreshNodes } from '../../store/nodes'
// 表单保存信号：通知端口转发窗口刷新列表
import { bumpForm } from '../../store/formBus'

const { t } = useI18n()

const props = defineProps({
  sshNodes: { type: Array, default: null }   // 父窗口传入的 SSH 节点（未传则自行拉取）
})
const emit = defineEmits(['close'])

const saving = ref(false)
const error = ref('')

// 可选 SSH 节点：优先用父窗口传入，否则取全局节点 store 中的 SSH 节点
const sshNodes = computed(() => {
  if (Array.isArray(props.sshNodes)) return props.sshNodes
  return nodes.list.filter((n) => n.type === 'ssh')
})

const form = reactive({
  name: '',
  node_id: '',
  local_port: '',
  remote_host: '127.0.0.1',
  remote_port: ''
})

// 首次打开若未传节点，则拉取一次全局节点
onMounted(async () => {
  if (!Array.isArray(props.sshNodes)) {
    await refreshNodes()
  }
  if (!form.node_id && sshNodes.value.length) form.node_id = sshNodes.value[0].id
})

// --- 创建：本地/远端端口必填，成功后通知父窗口刷新并自关 ---
async function create() {
  if (saving.value) return
  error.value = ''
  if (!form.local_port || !form.remote_port) {
    error.value = t('pf.needPorts')
    return
  }
  saving.value = true
  try {
    await portforwardApi.create({
      name: form.name,
      node_id: form.node_id,
      local_port: Number(form.local_port),
      remote_host: form.remote_host.trim(),
      remote_port: Number(form.remote_port)
    })
    bumpForm('portforward')   // 通知端口转发窗口刷新列表
    emit('close')
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.pf-form-window { padding: 14px; }
.error-box { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; word-break: break-all; }
</style>