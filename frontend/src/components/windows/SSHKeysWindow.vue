<template>
  <div class="sshkeys-window">
    <!-- 工具栏 -->
    <div class="toolbar">
      <button class="btn primary" :disabled="busy" @click="openCreate">
        <KeyRound :size="14" /> 生成密钥
      </button>
      <button class="btn" :disabled="busy" @click="openImport">
        <FileUp :size="14" /> 导入私钥
      </button>
      <span class="hint">密钥生成后可一键部署到节点，配合「设置-多机管理」的密钥认证实现免密登录</span>
      <button class="btn" :disabled="busy" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
    </div>

    <!-- 空状态 -->
    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="keys.length === 0" class="empty">
      <KeyRound :size="40" style="color:#9ca3af;" />
      <div>还没有 SSH 密钥，点击「生成密钥」创建一对新密钥，或「导入私钥」使用已有密钥</div>
    </div>

    <!-- 密钥列表 -->
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>指纹</th>
            <th>备注</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="k in keys" :key="k.id">
            <td>{{ k.name }}</td>
            <td><span class="tag">{{ typeLabel(k.key_type) }}</span></td>
            <td class="mono">{{ k.fingerprint }}</td>
            <td class="mono" :title="k.comment">{{ k.comment || '—' }}</td>
            <td class="mono">{{ fmtTime(k.created_at) }}</td>
            <td class="actions-cell">
              <button class="btn mini" :disabled="busy" @click="showPublic(k)">公钥</button>
              <button class="btn mini" :disabled="busy" @click="openDeploy(k)">部署</button>
              <button class="btn mini danger-text" :disabled="busy" @click="doDelete(k)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 生成密钥弹窗 -->
    <div v-if="createOpen" class="modal-overlay" @click.self="createOpen = false">
      <div class="modal">
        <h3><KeyRound :size="16" /> 生成密钥对</h3>
        <label class="field">
          <span class="label">名称</span>
          <input v-model.trim="createForm.name" maxlength="64" placeholder="如：生产服务器" />
        </label>
        <label class="field">
          <span class="label">算法类型</span>
          <select v-model="createForm.key_type">
            <option value="ed25519">Ed25519（推荐，更安全更快）</option>
            <option value="rsa">RSA 3072（兼容老系统）</option>
          </select>
        </label>
        <label class="field">
          <span class="label">备注（可选，将作为公钥注释便于识别）</span>
          <input v-model.trim="createForm.comment" maxlength="128" placeholder="如：root@graw-panel" />
        </label>
        <p v-if="formError" class="err">{{ formError }}</p>
        <div class="actions">
          <button class="btn" :disabled="saving" @click="createOpen = false">取消</button>
          <button class="btn primary" :disabled="saving || !createForm.name.trim()" @click="doCreate">
            {{ saving ? '生成中…' : '生成' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 导入私钥弹窗 -->
    <div v-if="importOpen" class="modal-overlay" @click.self="importOpen = false">
      <div class="modal wide">
        <h3><FileUp :size="16" /> 导入私钥</h3>
        <label class="field">
          <span class="label">名称</span>
          <input v-model.trim="importForm.name" maxlength="64" placeholder="如：云服务器密钥" />
        </label>
        <label class="field">
          <span class="label">私钥内容（PEM 格式，支持 OpenSSH 私钥）</span>
          <textarea v-model="importForm.private_key" rows="8" class="mono" spellcheck="false"
            placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;..." />
        </label>
        <label class="field">
          <span class="label">私钥密码（可选，加密私钥才需要）</span>
          <input v-model="importForm.passphrase" type="password" maxlength="128" placeholder="加密私钥的 passphrase" />
        </label>
        <p v-if="formError" class="err">{{ formError }}</p>
        <div class="actions">
          <button class="btn" :disabled="saving" @click="importOpen = false">取消</button>
          <button class="btn primary" :disabled="saving || !importForm.name.trim() || !importForm.private_key.trim()" @click="doImport">
            {{ saving ? '导入中…' : '导入' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 公钥查看弹窗 -->
    <div v-if="pubOpen" class="modal-overlay" @click.self="pubOpen = false">
      <div class="modal wide">
        <h3><FileKey :size="16" /> 公钥内容（authorized_keys 格式）</h3>
        <p class="hint">复制下面内容，添加到目标服务器 ~/.ssh/authorized_keys，或直接用「部署」按钮推送到节点。</p>
        <textarea :value="publicKey" readonly rows="4" class="mono pub-box" spellcheck="false" @click="selectText" />
        <p class="mono fp">指纹：{{ pubFingerprint }}</p>
        <div class="actions">
          <button class="btn" @click="copyPublic">复制</button>
          <button class="btn primary" @click="pubOpen = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- 部署弹窗 -->
    <div v-if="deployOpen" class="modal-overlay" @click.self="deployOpen = false">
      <div class="modal">
        <h3><Send :size="16" /> 部署到节点</h3>
        <p class="hint">将「{{ deployKey?.name }}」的公钥追加到目标节点 ~/.ssh/authorized_keys（幂等，已存在则跳过）。</p>
        <label class="field">
          <span class="label">目标节点</span>
          <select v-model="deployNode">
            <option value="" disabled>请选择 SSH 节点</option>
            <option v-for="n in sshNodes" :key="n.id" :value="n.id" :disabled="n.type !== 'ssh'">
              {{ n.name }}（{{ n.type === 'ssh' ? n.host + ':' + n.port : '本机' }}）
            </option>
          </select>
        </label>
        <p v-if="!hasSshNode" class="hint">当前没有 SSH 节点，请先在「设置-多机管理」中添加。</p>
        <p v-if="formError" class="err">{{ formError }}</p>
        <div class="actions">
          <button class="btn" :disabled="busy" @click="deployOpen = false">取消</button>
          <button class="btn primary" :disabled="busy || !deployNode" @click="doDeploy">
            {{ busy ? '部署中…' : '部署' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除密钥需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      title="删除密钥确认"
      :message="`删除密钥「${confirm.target?.name || ''}」后无法恢复（已部署到节点的公钥不受影响）。\n请输入面板密码以确认。`"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      confirm-label="删除"
      @confirm="doDeleteConfirmed"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { KeyRound, FileUp, FileKey, Send, RefreshCw } from 'lucide-vue-next'
import { sshkeysApi } from '../../api'
import ConfirmDialog from '../ConfirmDialog.vue'

const loading = ref(false)
const busy = ref(false)
const saving = ref(false)
const keys = ref([])
const formError = ref('')

// 高风险操作二次确认状态（删除密钥需输入面板密码）
const confirm = ref({ show: false, target: null })

// 生成 / 导入表单
const createOpen = ref(false)
const createForm = ref({ name: '', key_type: 'ed25519', comment: '' })
const importOpen = ref(false)
const importForm = ref({ name: '', private_key: '', passphrase: '' })

// 公钥查看
const pubOpen = ref(false)
const publicKey = ref('')
const pubFingerprint = ref('')

// 部署
const deployOpen = ref(false)
const deployKey = ref(null)
const deployNode = ref('')
const sshNodes = ref([])
const hasSshNode = computed(() => sshNodes.value.some(n => n.type === 'ssh'))

const typeLabel = (t) => ({ ed25519: 'Ed25519', rsa: 'RSA', ecdsa: 'ECDSA', unknown: '未知' }[t] || t)

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function loadAll() {
  loading.value = true
  try {
    const r = await sshkeysApi.list()
    keys.value = (r && r.keys) || []
  } catch (e) {
    alert('加载密钥失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function loadNodes() {
  try {
    const r = await sshkeysApi.nodes()
    sshNodes.value = (r && r.nodes) || []
    // 加载成功清除错误提示；避免残留上一次的报错误导
    if (!deployOpen.value) formError.value = ''
  } catch (e) {
    sshNodes.value = []
    // 加载失败不再静默，弹窗内提示，方便排查节点列表为空的原因
    if (deployOpen.value) {
      formError.value = '加载节点失败：' + (e.response?.data?.detail || e.message)
    }
  }
}

// ---- 生成 ----
function openCreate() {
  createForm.value = { name: '', key_type: 'ed25519', comment: '' }
  formError.value = ''
  createOpen.value = true
}

async function doCreate() {
  if (saving.value) return
  saving.value = true
  formError.value = ''
  try {
    await sshkeysApi.create({
      name: createForm.value.name.trim(),
      key_type: createForm.value.key_type,
      comment: createForm.value.comment.trim()
    })
    createOpen.value = false
    await loadAll()
  } catch (e) {
    formError.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

// ---- 导入 ----
function openImport() {
  importForm.value = { name: '', private_key: '', passphrase: '' }
  formError.value = ''
  importOpen.value = true
}

async function doImport() {
  if (saving.value) return
  saving.value = true
  formError.value = ''
  try {
    await sshkeysApi.importKey({
      name: importForm.value.name.trim(),
      private_key: importForm.value.private_key,
      passphrase: importForm.value.passphrase || undefined
    })
    importOpen.value = false
    await loadAll()
  } catch (e) {
    formError.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

// ---- 公钥查看 / 复制 ----
async function showPublic(k) {
  busy.value = true
  try {
    const r = await sshkeysApi.publicKey(k.id)
    publicKey.value = r.public_key
    pubFingerprint.value = r.fingerprint
    pubOpen.value = true
  } catch (e) {
    alert('获取公钥失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

function selectText(e) {
  e.target && e.target.select()
}

async function copyPublic() {
  try {
    await navigator.clipboard.writeText(publicKey.value)
    alert('公钥已复制到剪贴板')
  } catch (e) {
    alert('复制失败，请手动选择复制')
  }
}

// ---- 部署 ----
async function openDeploy(k) {
  deployKey.value = k
  deployNode.value = ''
  formError.value = ''
  // 每次打开都重新拉取节点，确保能弹出最新已配置的节点（避免首次为空后不再刷新）
  await loadNodes()
  deployOpen.value = true
}

async function doDeploy() {
  if (busy.value || !deployNode.value || !deployKey.value) return
  busy.value = true
  formError.value = ''
  try {
    const r = await sshkeysApi.deploy(deployKey.value.id, deployNode.value)
    alert(`已部署到节点「${r.node_name}」`)
    deployOpen.value = false
  } catch (e) {
    formError.value = e.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

// ---- 删除（二次确认）----
function doDelete(k) {
  confirm.value = { show: true, target: k }
}

async function doDeleteConfirmed() {
  const k = confirm.value.target
  confirm.value.show = false
  if (!k) return
  busy.value = true
  try {
    await sshkeysApi.delete(k.id)
    await loadAll()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.sshkeys-window { padding: 0; display: flex; flex-direction: column; gap: 10px; height: 100%; overflow: hidden; } /* 内嵌聚合窗口：外边距由父容器提供 */
.toolbar { display: flex; align-items: center; gap: 10px; }
.hint { color: #6e6e73; font-size: 12px; flex: 1; }
.table-wrap { overflow: auto; flex: 1; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
th { background: #f9fafb; position: sticky; top: 0; }
.mono { font-family: ui-monospace, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
.tag { background: #eef2ff; color: #4338ca; border-radius: 4px; padding: 2px 6px; font-size: 11px; }
.actions-cell { white-space: nowrap; }
.empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: #9ca3af; font-size: 13px; text-align: center; padding: 20px; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 460px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal.wide { width: 620px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.field { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
.label { font-size: 12px; color: #374151; }
.field input, .field select, .field textarea { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; width: 100%; box-sizing: border-box; }
.field input:focus, .field select:focus, .field textarea:focus { outline: none; border-color: #2563eb; }
.err { color: #dc2626; font-size: 12px; margin: 0 0 8px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.btn { padding: 6px 14px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 6px; }
.btn.primary { background: #2563eb; color: #fff; border-color: #2563eb; }
.btn.primary:hover:not(:disabled) { background: #1d4ed8; }
.btn.mini { padding: 3px 8px; font-size: 12px; }
.btn.danger-text { color: #dc2626; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.pub-box { resize: none; background: #f9fafb; }
.fp { color: #6e6e73; font-size: 12px; margin: 8px 0; }
</style>
