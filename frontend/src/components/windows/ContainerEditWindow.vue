<!--
  ContainerEditWindow.vue — 容器编辑/重建窗口
  ==========================================================
  业务作用：
    修改一个 Docker 容器的运行配置，分四个区块：
    1) CPU/内存限制（立即生效，经 docker update）；
    2) 环境变量编辑（需重建生效）；
    3) 端口映射编辑（需重建生效）；
    4) 按新配置重建容器（高风险操作，需输入面板密码二次确认）。
  后端模块：
    /api/docker 的 containers（容器列表，供选择器）；
    /api/containeredit 的 info / updateLimits / rebuild。
  关键状态：
    - containerId 当前编辑的容器（优先 props.id，否则手动选择）
    - info        容器当前配置
    - envRows / portRows 环境变量与端口映射的行编辑器
    - confirm     重建容器的密码二次确认
  打开方式：
    由 Docker 管理窗口的容器「编辑」按钮打开（props 传入 id/name）；
    也可从桌面快捷方式进入后手动选择容器。
-->
<template>
  <div class="containeredit-window">
    <!-- 容器选择（未通过 props 指定容器时，从桌面快捷方式进入需要手动选择） -->
    <div v-if="!props.id" class="selector-bar">
      <select v-model="containerId" class="selector" @change="onSelectContainer">
        <option value="">{{ $t('containeredit.selectContainer') }}</option>
        <option v-for="c in containers" :key="c.id" :value="c.id">{{ c.name }}（{{ c.id }}）</option>
      </select>
      <button class="btn" :disabled="loading" @click="loadContainers"><RefreshCw :size="13" /> {{ $t('containeredit.refresh') }}</button>
    </div>

    <!-- 容器基本信息 -->
    <div class="info-bar">
      <div class="info-name" :title="info?.name || containerName">{{ info?.name || containerName || '—' }}</div>
      <div class="info-meta">
        <span class="state-badge" :class="stateClass">{{ info?.state || '—' }}</span>
        <span class="img" :title="info?.image">{{ info?.image || '—' }}</span>
      </div>
    </div>

    <div v-if="loading" class="empty">{{ $t('containeredit.loading') }}</div>
    <div v-else-if="loadErr" class="empty err">{{ loadErr }}</div>

    <template v-else>
      <!-- Section 1: CPU / 内存限制 -->
      <div class="section">
        <div class="section-title">{{ $t('containeredit.resourceLimits') }}</div>
        <div class="section-body">
          <div class="field-row">
            <label class="field">
              <span class="label">{{ $t('containeredit.cpuCores') }}</span>
              <input type="number" step="0.1" min="0.1" max="64" v-model.number="limits.cpus" />
            </label>
            <label class="field">
              <span class="label">{{ $t('containeredit.memoryMB') }}</span>
              <input type="number" min="0" max="262144" v-model.number="limits.memory_mb" :disabled="limits.memory_unlimited" />
            </label>
            <label class="field check">
              <input type="checkbox" v-model="limits.memory_unlimited" />
              <span>{{ $t('containeredit.memoryUnlimited') }}</span>
            </label>
          </div>
          <div class="actions-row">
            <button class="btn primary" :disabled="savingLimits || !containerId" @click="saveLimits">
              {{ savingLimits ? $t('containeredit.savingLimits') : $t('containeredit.saveLimits') }}
            </button>
            <span class="hint">{{ $t('containeredit.limitsHint') }}</span>
          </div>
          <div v-if="limitsMsg" class="msg" :class="{ err: limitsMsgErr }">{{ limitsMsg }}</div>
        </div>
      </div>

      <!-- Section 2: 环境变量 -->
      <div class="section">
        <div class="section-title">
          {{ $t('containeredit.envVars') }}
          <button class="btn mini" :disabled="!containerId" @click="addEnvRow"><Plus :size="13" /> {{ $t('containeredit.addEnv') }}</button>
        </div>
        <div class="section-body">
          <div v-if="envRows.length === 0" class="empty">{{ $t('containeredit.noEnv') }}</div>
          <div v-else class="row-list">
            <div v-for="(row, idx) in envRows" :key="idx" class="row-item">
              <input v-model.trim="row.key" class="row-key" :placeholder="$t('containeredit.envKey')" spellcheck="false" />
              <input v-model="row.value" class="row-val" :placeholder="$t('containeredit.envValue')" spellcheck="false" />
              <button class="btn mini danger-text" @click="envRows.splice(idx, 1)"><Trash2 :size="13" /> {{ $t('containeredit.remove') }}</button>
            </div>
          </div>
          <div class="hint">{{ $t('containeredit.envRebuildHint') }}</div>
        </div>
      </div>

      <!-- Section 3: 端口映射 -->
      <div class="section">
        <div class="section-title">
          {{ $t('containeredit.ports') }}
          <button class="btn mini" :disabled="!containerId" @click="addPortRow"><Plus :size="13" /> {{ $t('containeredit.addPort') }}</button>
        </div>
        <div class="section-body">
          <div v-if="portRows.length === 0" class="empty">{{ $t('containeredit.noPorts') }}</div>
          <div v-else class="row-list">
            <div v-for="(row, idx) in portRows" :key="idx" class="row-item">
              <input v-model.trim="row.host_port" class="row-port" type="number" min="1" max="65535" :placeholder="$t('containeredit.hostPort')" />
              <span class="sep">→</span>
              <input v-model.trim="row.container_port" class="row-port" type="number" min="1" max="65535" :placeholder="$t('containeredit.containerPort')" />
              <select v-model="row.protocol" class="row-proto">
                <option value="tcp">tcp</option>
                <option value="udp">udp</option>
              </select>
              <button class="btn mini danger-text" @click="portRows.splice(idx, 1)"><Trash2 :size="13" /> {{ $t('containeredit.remove') }}</button>
            </div>
          </div>
          <div class="hint">{{ $t('containeredit.portRebuildHint') }}</div>
        </div>
      </div>

      <!-- Section 4: 重建容器 -->
      <div class="section">
        <div class="section-title">{{ $t('containeredit.rebuildSection') }}</div>
        <div class="section-body">
          <p class="rebuild-warn">{{ $t('containeredit.rebuildWarn') }}</p>
          <div class="actions-row">
            <button class="btn danger" :disabled="rebuilding || !containerId" @click="confirmRebuild">
              {{ rebuilding ? $t('containeredit.rebuilding') : $t('containeredit.rebuild') }}
            </button>
          </div>
          <div v-if="rebuildMsg" class="msg" :class="{ err: rebuildMsgErr }">{{ rebuildMsg }}</div>
        </div>
      </div>
    </template>

    <!-- 高风险操作二次确认：重建容器需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="$t('containeredit.rebuildConfirmTitle')"
      :message="`${$t('containeredit.rebuildConfirm', { name: info?.name || containerName || '' })}\n${$t('containeredit.rebuildConfirmPwd')}`"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      :confirm-label="$t('containeredit.rebuild')"
      @confirm="doRebuild"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'   // 状态/表单/派生徽标/挂载初始化
import { useI18n } from 'vue-i18n'   // 翻译函数
import { RefreshCw, Plus, Trash2 } from 'lucide-vue-next'   // 工具栏/行编辑图标
import { dockerApi, containereditApi } from '../../api'   // /api/docker 容器列表 + /api/containeredit 编辑接口
import ConfirmDialog from '../ConfirmDialog.vue'   // 重建容器的密码二次确认对话框

const { t } = useI18n()

const props = defineProps({
  id: { type: String, default: '' },   // 从 Docker 管理窗口进入时传入的容器 id
  name: { type: String, default: '' },   // 容器显示名称
})

const loading = ref(false)   // 配置加载中
const loadErr = ref('')   // 加载失败提示
const info = ref(null)   // 容器当前配置
const containers = ref([])   // 容器列表（仅未传 id 时用于选择器）

// 当前编辑的容器（优先 props.id，其次手动选择）
const containerId = ref(props.id || '')
const containerName = ref(props.name || '')

// Section 1 状态
const savingLimits = ref(false)   // 限制保存中
const limitsMsg = ref('')   // 保存结果反馈
const limitsMsgErr = ref(false)   // 反馈是否为错误
const limits = reactive({ cpus: 0, memory_mb: 0, memory_unlimited: true })   // 资源限制表单（勾选不限制时 memory_mb 提交为 0）

// Section 2 / 3 状态
const envRows = ref([])   // 环境变量行编辑器
const portRows = ref([])   // 端口映射行编辑器

// Section 4 状态
const rebuilding = ref(false)   // 重建进行中
const rebuildMsg = ref('')   // 重建结果反馈
const rebuildMsgErr = ref(false)   // 反馈是否为错误
const confirm = ref({ show: false })   // 密码二次确认弹窗

// 状态徽标配色：running 绿 / exited、stopped 红 / 其余灰
const stateClass = computed(() => {
  const s = info.value?.state || ''
  if (s === 'running') return 'running'
  if (s === 'exited' || s === 'stopped') return 'stopped'
  return 'other'
})

// ---------- 容器列表（桌面快捷方式无 props.id 时展示选择器） ----------
async function loadContainers() {
  try {
    containers.value = await dockerApi.containers()
  } catch (e) {
    loadErr.value = e.response?.data?.detail || e.message
  }
}

// 手动选择容器后加载其配置
function onSelectContainer() {
  const c = containers.value.find((x) => x.id === containerId.value)
  containerName.value = c?.name || ''
  loadErr.value = ''
  loadInfo()
}

// ---------- 加载编辑配置 ----------
async function loadInfo() {
  if (!containerId.value) return   // 未选容器直接返回
  loading.value = true
  loadErr.value = ''
  try {
    const d = await containereditApi.info(containerId.value)
    info.value = d
    containerName.value = d.name || props.name
    limits.cpus = d.cpu_cores || 0
    limits.memory_mb = d.memory_mb || 0
    limits.memory_unlimited = !!d.memory_unlimited
    envRows.value = (d.env || []).map((e) => ({ key: e.key, value: e.value }))
    portRows.value = (d.ports || []).map((p) => ({
      ip: p.ip || '', host_port: p.host_port, container_port: p.container_port, protocol: p.protocol || 'tcp',
    }))
    limitsMsg.value = ''
    rebuildMsg.value = ''
  } catch (e) {
    loadErr.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

// ---------- Section 1：保存资源限制 ----------
async function saveLimits() {
  if (savingLimits.value || !containerId.value) return   // 防重复提交
  limitsMsg.value = ''
  limitsMsgErr.value = false
  const cpus = Number(limits.cpus)
  const memory_mb = limits.memory_unlimited ? 0 : Number(limits.memory_mb)   // 勾选不限制时提交 0
  if (!(cpus >= 0.1 && cpus <= 64)) {   // CPU 核数范围校验（0.1~64）
    limitsMsgErr.value = true
    limitsMsg.value = t('containeredit.cpuInvalid')
    return
  }
  if (memory_mb !== 0 && !(memory_mb >= 32 && memory_mb <= 262144)) {   // 内存范围校验（32MB~256GB）
    limitsMsgErr.value = true
    limitsMsg.value = t('containeredit.memoryInvalid')
    return
  }
  savingLimits.value = true
  try {
    await containereditApi.updateLimits(containerId.value, { cpus, memory_mb })
    limitsMsg.value = t('containeredit.limitsSaved')
    await loadInfo() // 重新读取最新生效值
  } catch (e) {
    limitsMsgErr.value = true
    limitsMsg.value = t('containeredit.limitsFailed', { error: e.response?.data?.detail || e.message })
  } finally {
    savingLimits.value = false
  }
}

// ---------- Section 2：环境变量 ----------
function addEnvRow() {
  envRows.value.push({ key: '', value: '' })
}

// ---------- Section 3：端口映射 ----------
function addPortRow() {
  portRows.value.push({ host_port: '', container_port: '', protocol: 'tcp', ip: '' })
}

// ---------- Section 4：重建容器 ----------
function confirmRebuild() {
  if (!containerId.value) return
  confirm.value = { show: true }   // 弹密码确认框
}

// --- 按新环境变量/端口配置重建容器（需密码确认通过） ---
async function doRebuild() {
  confirm.value.show = false
  rebuilding.value = true
  rebuildMsg.value = ''
  rebuildMsgErr.value = false
  try {
    const body = {
      // 只提交 key 非空的环境变量行；端口行要求宿主与容器端口都填写
      env: envRows.value.filter((r) => r.key && r.key.trim()).map((r) => ({ key: r.key.trim(), value: r.value })),
      ports: portRows.value
        .filter((r) => r.host_port && r.container_port)
        .map((r) => ({
          host_port: String(r.host_port),
          container_port: String(r.container_port),
          protocol: r.protocol || 'tcp',
        })),
    }
    const r = await containereditApi.rebuild(containerId.value, body)
    rebuildMsg.value = t('containeredit.rebuildSuccess') + (r.new_container_id ? t('containeredit.rebuildSuccessId', { id: r.new_container_id }) : '')
    await loadInfo() // 重建后刷新最新配置
  } catch (e) {
    rebuildMsgErr.value = true
    rebuildMsg.value = t('containeredit.rebuildFailed', { error: e.response?.data?.detail || e.message })
  } finally {
    rebuilding.value = false
  }
}

onMounted(() => {
  if (props.id) {
    loadInfo()   // 有传入容器：直接加载
  } else {
    loadContainers()   // 无传入容器：先加载列表供选择
  }
})
</script>

<style scoped>
.containeredit-window { padding: 12px; display: flex; flex-direction: column; gap: 12px; height: 100%; box-sizing: border-box; overflow: auto; }

/* 容器选择器 */
.selector-bar { display: flex; align-items: center; gap: 8px; }
.selector { flex: 1; padding: 7px 10px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 12.5px; background: #fff; color: #111827; }

/* 基本信息 */
.info-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 12px; background: #fafafa; }
.info-name { font-weight: 600; font-size: 14px; }
.info-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.state-badge { display: inline-flex; align-items: center; padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.state-badge.running { background: #d1fae5; color: #065f46; }
.state-badge.stopped { background: #fee2e2; color: #b91c1c; }
.state-badge.other { background: #f3f4f6; color: #6b7280; }
.img { font-size: 11.5px; color: #6b7280; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, Menlo, Consolas, monospace; }

/* 分区：不限制卡片高度，由外层整体滚动（同应用商店的上下滑查看体验） */
.section { border: 1px solid #e5e7eb; border-radius: 10px; background: #fff; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 9px 12px; background: #f9fafb; font-size: 12.5px; font-weight: 600; border-bottom: 1px solid #eef0f2; }
.section-body { padding: 12px; }

.field-row { display: flex; gap: 12px; flex-wrap: wrap; }
.field { display: block; flex: 1; min-width: 140px; margin-bottom: 10px; }
.field .label { display: block; font-size: 11px; color: #1d1d1f; font-weight: 600; margin-bottom: 5px; }
.field input { width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; font-family: inherit; }
.field input:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.field.check { display: flex; align-items: center; justify-content: flex-end; gap: 6px; min-width: 120px; cursor: pointer; font-size: 12.5px; }
.field.check input { width: auto; }

.actions-row { display: flex; align-items: center; gap: 10px; margin-top: 2px; flex-wrap: wrap; }
.hint { color: #6e6e73; font-size: 11.5px; }
.msg { margin-top: 8px; font-size: 12px; color: #2a8f3c; }
.msg.err { color: #b91c1c; }

/* 行编辑器（环境变量 / 端口） */
.row-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px; }
.row-item { display: flex; align-items: center; gap: 6px; }
.row-key { flex: 1; min-width: 0; padding: 7px 10px; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; font-size: 12.5px; font-family: ui-monospace, Menlo, Consolas, monospace; }
.row-val { flex: 1.6; min-width: 0; padding: 7px 10px; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; font-size: 12.5px; font-family: ui-monospace, Menlo, Consolas, monospace; }
.row-port { width: 110px; padding: 7px 10px; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; font-size: 12.5px; font-family: ui-monospace, Menlo, Consolas, monospace; }
.row-proto { padding: 7px 6px; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; font-size: 12.5px; background: #fff; }
.row-item input:focus, .row-proto:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.sep { color: #9ca3af; }

.rebuild-warn { margin: 0 0 10px; font-size: 12px; color: #b45309; background: #fef3c7; border: 1px solid #fde68a; border-radius: 8px; padding: 8px 10px; }

/* 通用按钮 */
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.mini { padding: 3px 8px; font-size: 11.5px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.btn.danger { background: #dc2626; color: #fff; border-color: #dc2626; }
.btn.danger:hover:not(:disabled) { background: #b91c1c; }
.danger-text { color: #b91c1c; }

.empty { text-align: center; color: #9ca3af; padding: 16px; font-size: 12.5px; }
.empty.err { color: #b91c1c; }
</style>
