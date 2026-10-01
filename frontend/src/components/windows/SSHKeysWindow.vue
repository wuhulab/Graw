<!--
  SSH 密钥窗口（SSH Keys）

  这个窗口做什么：
    面板「设置」里的 SSH 密钥管理页。它负责生成 / 导入 SSH 密钥对，
    并把公钥一键部署到已配置的 SSH 节点上（配合「设置-多机管理」的密钥认证
    实现免密登录）；同时支持查看公钥内容、删除密钥。删除属于高风险操作，
    需输入面板密码二次确认。

  用到的后端模块：
    /api/sshkeys/*（管理员权限）——list 密钥列表、create 生成密钥对、
    import 导入私钥、{id}/public 取公钥、{id}/deploy 部署到节点、
    nodes 取可部署的节点列表、{id} 删除密钥。

  关键状态：
    keys         密钥列表，表格数据源
    createForm / importForm   生成 / 导入表单
    pubOpen / publicKey       公钥查看弹窗
    deployOpen / deployNode / sshNodes   部署弹窗与目标节点
    confirm      删除密钥的二次确认（需输入面板密码）

  怎么被打开：
    从「设置」窗口（SettingsWindow）内嵌挂载，不是桌面独立应用。
-->
<template>
  <div class="sshkeys-window">
    <!-- 工具栏：生成/导入打开独立表单窗口 -->
    <div class="ui-toolbar">
      <button class="ui-btn primary" :disabled="busy" @click="emit('openSshKeyGen')">
        <KeyRound :size="14" /> 生成密钥
      </button>
      <button class="ui-btn" :disabled="busy" @click="emit('openSshKeyImport')">
        <FileUp :size="14" /> 导入私钥
      </button>
      <span class="ui-hint">密钥生成后可一键部署到节点，配合「设置-多机管理」的密钥认证实现免密登录</span>
      <button class="ui-btn right" :disabled="busy" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
    </div>

    <!-- 空状态 -->
    <div v-if="loading" class="ui-empty">加载中…</div>
    <div v-else-if="keys.length === 0" class="ui-empty">
      <KeyRound :size="40" style="color:#9ca3af;" />
      <div>还没有 SSH 密钥，点击「生成密钥」创建一对新密钥，或「导入私钥」使用已有密钥</div>
    </div>

    <!-- 密钥列表 -->
    <div v-else class="ui-table-wrap">
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
            <td class="ui-mono">{{ k.fingerprint }}</td>
            <td class="ui-mono" :title="k.comment">{{ k.comment || '—' }}</td>
            <td class="ui-mono">{{ fmtTime(k.created_at) }}</td>
            <td class="actions-cell">
              <button class="ui-btn mini" :disabled="busy" @click="showPublic(k)">公钥</button>
              <button class="ui-btn mini" :disabled="busy" @click="emit('openSshKeyDeploy', { key: k })">部署</button>
              <button class="ui-btn mini danger-text" :disabled="busy" @click="doDelete(k)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 生成/导入/部署已拆分为独立窗口（SshKeyGenWindow / SshKeyImportWindow /
         SshKeyDeployWindow），避免误触遮罩丢失已填内容 -->

    <!-- 公钥查看弹窗（只读展示 + 复制，保留内嵌） -->
    <div v-if="pubOpen" class="ui-modal-overlay" @click.self="pubOpen = false">
      <div class="ui-modal wide">
        <h3><FileKey :size="16" /> 公钥内容（authorized_keys 格式）</h3>
        <p class="ui-hint">复制下面内容，添加到目标服务器 ~/.ssh/authorized_keys，或直接用「部署」按钮推送到节点。</p>
        <textarea :value="publicKey" readonly rows="4" class="ui-textarea mono pub-box" spellcheck="false" @click="selectText" />
        <p class="mono fp">指纹：{{ pubFingerprint }}</p>
        <div class="ui-actions">
          <button class="ui-btn" @click="copyPublic">复制</button>
          <button class="ui-btn primary" @click="pubOpen = false">关闭</button>
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
import { ref, onMounted, watch } from 'vue'   // 响应式状态、挂载钩子、信号监听
import { KeyRound, FileUp, FileKey, Send, RefreshCw } from 'lucide-vue-next'   // 工具栏 / 各弹窗用到的图标
import { sshkeysApi } from '../../api'   // SSH 密钥后端能力：/api/sshkeys/* 的封装
import ConfirmDialog from '../ConfirmDialog.vue'   // 高风险操作确认框（删除密钥要求输入面板密码）
import { formBus } from '../../store/formBus'   // 表单保存信号：独立表单窗口保存成功后刷新

// 生成 / 导入 / 部署已拆分为独立窗口（SshKeyGenWindow / SshKeyImportWindow / SshKeyDeployWindow）
const emit = defineEmits(['openSshKeyGen', 'openSshKeyImport', 'openSshKeyDeploy'])

const loading = ref(false)   // 列表加载中（首屏加载与空状态判断）
const busy = ref(false)      // 行内操作（查看公钥 / 删除）进行中，用于禁用按钮
const keys = ref([])         // 密钥列表，表格数据源

// 高风险操作二次确认状态（删除密钥需输入面板密码）
const confirm = ref({ show: false, target: null })

// 生成/导入/部署表单窗口保存成功后 bumpForm('sshkeys') 触发此处重载
watch(() => formBus.sshkeys, loadAll)

// 公钥查看（只读弹窗保留）
const pubOpen = ref(false)        // 公钥查看弹窗是否展开
const publicKey = ref('')         // 公钥内容（authorized_keys 格式）
const pubFingerprint = ref('')    // 公钥指纹，用于人工核验

// --- 把后端密钥类型码翻译成界面显示名（未收录的类型原样展示） ---
const typeLabel = (t) => ({ ed25519: 'Ed25519', rsa: 'RSA', ecdsa: 'ECDSA', unknown: '未知' }[t] || t)

// --- 时间格式化：ISO 时间串 → yyyy-MM-dd HH:mm（非法值原样返回） ---
function fmtTime(iso) {
  if (!iso) return '—'                    // 空值显示占位符
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso      // 解析失败直接返回原文，避免显示 NaN
  const p = (n) => String(n).padStart(2, '0')    // 月/日/时/分补零到两位数
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

// --- 拉取密钥列表 ---
async function loadAll() {
  loading.value = true
  try {
    const r = await sshkeysApi.list()
    keys.value = (r && r.keys) || []    // 后端无 keys 字段时兜空数组，避免表格渲染报错
  } catch (e) {
    alert('加载密钥失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// ---- 公钥查看 / 复制 ----
async function showPublic(k) {
  busy.value = true
  try {
    const r = await sshkeysApi.publicKey(k.id)
    publicKey.value = r.public_key
    pubFingerprint.value = r.fingerprint
    pubOpen.value = true   // 数据取回后才开弹窗，避免先弹出空白框
  } catch (e) {
    alert('获取公钥失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// --- 点击公钥框时全选文本，方便一键复制 ---
function selectText(e) {
  e.target && e.target.select()
}

// --- 复制公钥到剪贴板（浏览器受限时引导手动选择） ---
async function copyPublic() {
  try {
    await navigator.clipboard.writeText(publicKey.value)
    alert('公钥已复制到剪贴板')
  } catch (e) {
    alert('复制失败，请手动选择复制')
  }
}

// ---- 删除（二次确认）----
function doDelete(k) {
  confirm.value = { show: true, target: k }
}

// --- 删除密钥第二步：面板密码校验通过后真正下发删除 ---
async function doDeleteConfirmed() {
  const k = confirm.value.target
  confirm.value.show = false   // 先收起确认框，避免删除期间重复触发
  if (!k) return               // 无待删目标（异常触发）时直接退出
  busy.value = true
  try {
    await sshkeysApi.delete(k.id)   // 后端同时移除本地私钥 / 公钥文件
    await loadAll()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)   // 窗口一打开就拉一次密钥列表
</script>

<style scoped>
.sshkeys-window { padding: 0; display: flex; flex-direction: column; gap: 10px; height: 100%; overflow: hidden; } /* 内嵌聚合窗口：外边距由父容器提供 */
.mono { font-family: ui-monospace, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
.tag { background: #eef2ff; color: #4338ca; border-radius: 4px; padding: 2px 6px; font-size: 11px; }
.actions-cell { white-space: nowrap; }
.danger-text { color: #dc2626; }
.pub-box { resize: none; background: #f9fafb; }
.fp { color: #6e6e73; font-size: 12px; margin: 8px 0; }
</style>
