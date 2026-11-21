<!--
  AiWindow.vue — AI（模型账号 / GPU / MCP）
  ==========================================================
  业务作用：
    轻量 AI 能力入口：管理 OpenAI 兼容模型账号并做对话测试、查看 NVIDIA GPU、
    登记 MCP HTTP 端点并测试连通性。不做智能体编排/向量库/模型下载。
  后端模块：
    /api/ai/*（handlers_ai.go）。
  打开方式：
    桌面/侧边栏「AI 助手」快捷方式（perm=ai）。
-->
<template>
  <div class="ai-window">
    <div class="tabs">
      <button class="tab" :class="{ active: tab === 'accounts' }" @click="tab = 'accounts'">{{ $t('ai.tabAccounts') }}</button>
      <button class="tab" :class="{ active: tab === 'gpu' }" @click="switchTab('gpu')">{{ $t('ai.tabGpu') }}</button>
      <button class="tab" :class="{ active: tab === 'mcp' }" @click="switchTab('mcp')">{{ $t('ai.tabMcp') }}</button>
      <span v-if="err" class="err">{{ err }}</span>
    </div>

    <!-- 模型账号 -->
    <div v-if="tab === 'accounts'" class="body">
      <div class="toolbar">
        <button class="btn" :disabled="loading" @click="loadAccounts">{{ loading ? $t('common.loading') : $t('common.refresh') }}</button>
        <button class="btn primary" @click="openAccountForm"><Plus :size="13" /> {{ $t('ai.addAccount') }}</button>
        <span class="hint">{{ $t('ai.accountsHint') }}</span>
      </div>
      <table class="tbl">
        <thead><tr><th>{{ $t('common.name') }}</th><th>Base URL</th><th>{{ $t('ai.model') }}</th><th>{{ $t('ai.apiKey') }}</th><th>{{ $t('common.status') }}</th><th style="width:190px;">{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="a in accounts" :key="a.id">
            <td>{{ a.name }}</td><td class="mono">{{ a.base_url }}</td><td class="mono">{{ a.model || '-' }}</td>
            <td>{{ a.api_key_set ? '已配置' : '未配置' }}</td>
            <td>{{ a.enabled ? $t('common.enabled') : $t('common.disabled') }}</td>
            <td>
              <button class="btn sm" @click="openTest(a)">{{ $t('ai.test') }}</button>
              <button class="btn sm danger" @click="removeAccount(a)">{{ $t('common.delete') }}</button>
            </td>
          </tr>
          <tr v-if="!accounts.length"><td colspan="6" class="muted">{{ $t('ai.noAccounts') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- GPU -->
    <div v-else-if="tab === 'gpu'" class="body">
      <div class="toolbar">
        <button class="btn" :disabled="loading" @click="loadGPU">{{ $t('common.refresh') }}</button>
        <span class="hint">{{ $t('ai.gpuHint') }}</span>
      </div>
      <div v-if="!gpu.available" class="empty">{{ gpu.reason || $t('ai.noGpu') }}</div>
      <table v-else class="tbl">
        <thead><tr><th>#</th><th>{{ $t('common.name') }}</th><th>{{ $t('ai.vram') }}</th><th>{{ $t('ai.utilization') }}</th><th>{{ $t('ai.temperature') }}</th></tr></thead>
        <tbody>
          <tr v-for="g in gpu.gpus" :key="g.index">
            <td>{{ g.index }}</td><td>{{ g.name }}</td>
            <td>{{ g.memory_used_mb }} / {{ g.memory_total_mb }} MB</td>
            <td>{{ g.utilization }}%</td><td>{{ g.temperature }}°C</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- MCP -->
    <div v-else class="body">
      <div class="toolbar">
        <button class="btn" :disabled="loading" @click="loadMCP">{{ $t('common.refresh') }}</button>
        <button class="btn primary" @click="openMCPForm"><Plus :size="13" /> {{ $t('ai.addMcp') }}</button>
        <span class="hint">{{ $t('ai.mcpHint') }}</span>
      </div>
      <table class="tbl">
        <thead><tr><th>{{ $t('common.name') }}</th><th>URL</th><th>{{ $t('ai.headers') }}</th><th>{{ $t('common.status') }}</th><th style="width:160px;">{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="m in mcpServers" :key="m.id">
            <td>{{ m.name }}</td><td class="mono">{{ m.url }}</td><td class="mono">{{ (m.header_keys || []).join(', ') || '-' }}</td>
            <td>{{ m.enabled ? $t('common.enabled') : $t('common.disabled') }}</td>
            <td>
              <button class="btn sm" @click="testMCP(m)">{{ $t('ai.test') }}</button>
              <button class="btn sm danger" @click="removeMCP(m)">{{ $t('common.delete') }}</button>
            </td>
          </tr>
          <tr v-if="!mcpServers.length"><td colspan="5" class="muted">{{ $t('ai.noMcp') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 新增模型账号 -->
    <div v-if="accountForm.show" class="mask" @click.self="accountForm.show = false">
      <div class="dialog">
        <div class="dialog-title">{{ $t('ai.addAccount') }}</div>
        <div class="dialog-body">
          <label class="field">{{ $t('common.name') }}<input v-model.trim="accountForm.name" class="inp" /></label>
          <label class="field">Base URL<input v-model.trim="accountForm.base_url" class="inp" placeholder="https://api.openai.com/v1" /></label>
          <label class="field">{{ $t('ai.model') }}<input v-model.trim="accountForm.model" class="inp" placeholder="gpt-4o-mini" /></label>
          <label class="field">API Key<input v-model.trim="accountForm.api_key" class="inp" type="password" /></label>
          <label class="check"><input type="checkbox" v-model="accountForm.enabled" /> {{ $t('common.enabled') }}</label>
          <div v-if="accountForm.err" class="err">{{ accountForm.err }}</div>
        </div>
        <div class="dialog-foot">
          <button class="btn" @click="accountForm.show = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="accountForm.busy" @click="saveAccount">{{ $t('common.save') }}</button>
        </div>
      </div>
    </div>

    <!-- 对话测试 -->
    <div v-if="testDialog.show" class="mask" @click.self="testDialog.show = false">
      <div class="dialog">
        <div class="dialog-title">{{ $t('ai.testTitle', { name: testDialog.name }) }}</div>
        <div class="dialog-body">
          <label class="field">{{ $t('ai.prompt') }}<textarea v-model="testDialog.prompt" class="area" rows="3" /></label>
          <div v-if="testDialog.busy" class="muted">{{ $t('common.loading') }}</div>
          <pre v-if="testDialog.result" class="result">{{ testDialog.result }}</pre>
          <div v-if="testDialog.err" class="err">{{ testDialog.err }}</div>
        </div>
        <div class="dialog-foot">
          <button class="btn" @click="testDialog.show = false">{{ $t('common.close') }}</button>
          <button class="btn primary" :disabled="testDialog.busy" @click="runTest">{{ $t('ai.send') }}</button>
        </div>
      </div>
    </div>

    <!-- 新增 MCP -->
    <div v-if="mcpForm.show" class="mask" @click.self="mcpForm.show = false">
      <div class="dialog">
        <div class="dialog-title">{{ $t('ai.addMcp') }}</div>
        <div class="dialog-body">
          <label class="field">{{ $t('common.name') }}<input v-model.trim="mcpForm.name" class="inp" /></label>
          <label class="field">URL<input v-model.trim="mcpForm.url" class="inp" placeholder="https://mcp.example.com/sse" /></label>
          <label class="field">{{ $t('ai.headers') }}<textarea v-model="mcpForm.headers" class="area" rows="3" placeholder="Authorization: Bearer xxx" /></label>
          <label class="check"><input type="checkbox" v-model="mcpForm.enabled" /> {{ $t('common.enabled') }}</label>
          <div v-if="mcpForm.err" class="err">{{ mcpForm.err }}</div>
        </div>
        <div class="dialog-foot">
          <button class="btn" @click="mcpForm.show = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="mcpForm.busy" @click="saveMCP">{{ $t('common.save') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { aiApi } from '../../api'
import { Plus } from 'lucide-vue-next'

const tab = ref('accounts')
const loading = ref(false)
const err = ref('')
const accounts = ref([])
const gpu = reactive({ available: false, reason: '', gpus: [] })
const mcpServers = ref([])
const accountForm = reactive({ show: false, name: '', base_url: '', model: '', api_key: '', enabled: true, busy: false, err: '' })
const testDialog = reactive({ show: false, id: '', name: '', prompt: '', result: '', busy: false, err: '' })
const mcpForm = reactive({ show: false, name: '', url: '', headers: '', enabled: true, busy: false, err: '' })

function apiErr(e) { return e?.response?.data?.detail || e.message }

async function loadAccounts() {
  loading.value = true
  err.value = ''
  try {
    const r = await aiApi.accounts()
    accounts.value = r.accounts || []
  } catch (e) { err.value = apiErr(e) } finally { loading.value = false }
}

async function loadGPU() {
  loading.value = true
  try {
    const r = await aiApi.gpu()
    Object.assign(gpu, r)
  } catch (e) { err.value = apiErr(e) } finally { loading.value = false }
}

async function loadMCP() {
  loading.value = true
  try {
    const r = await aiApi.mcp()
    mcpServers.value = r.servers || []
  } catch (e) { err.value = apiErr(e) } finally { loading.value = false }
}

function switchTab(t) {
  tab.value = t
  if (t === 'gpu') loadGPU()
  if (t === 'mcp') loadMCP()
}

function openAccountForm() {
  Object.assign(accountForm, { show: true, name: '', base_url: '', model: '', api_key: '', enabled: true, busy: false, err: '' })
}

async function saveAccount() {
  accountForm.busy = true
  accountForm.err = ''
  try {
    await aiApi.createAccount({
      name: accountForm.name, base_url: accountForm.base_url, model: accountForm.model,
      api_key: accountForm.api_key, enabled: accountForm.enabled,
    })
    accountForm.show = false
    await loadAccounts()
  } catch (e) { accountForm.err = apiErr(e) } finally { accountForm.busy = false }
}

async function removeAccount(a) {
  if (!window.confirm(`删除模型账号「${a.name}」？`)) return
  try { await aiApi.deleteAccount(a.id); await loadAccounts() } catch (e) { alert(apiErr(e)) }
}

function openTest(a) {
  testDialog.show = true
  testDialog.id = a.id
  testDialog.name = a.name
  testDialog.prompt = '你好，请用一句话介绍你自己。'
  testDialog.result = ''
  testDialog.err = ''
}

async function runTest() {
  testDialog.busy = true
  testDialog.err = ''
  testDialog.result = ''
  try {
    const r = await aiApi.testAccount(testDialog.id, testDialog.prompt)
    testDialog.result = r.content || ''
  } catch (e) { testDialog.err = apiErr(e) } finally { testDialog.busy = false }
}

function openMCPForm() {
  Object.assign(mcpForm, { show: true, name: '', url: '', headers: '', enabled: true, busy: false, err: '' })
}

async function saveMCP() {
  mcpForm.busy = true
  mcpForm.err = ''
  try {
    const headers = {}
    for (const line of mcpForm.headers.split('\n')) {
      const i = line.indexOf(':')
      if (i > 0) headers[line.slice(0, i).trim()] = line.slice(i + 1).trim()
    }
    await aiApi.createMCP({ name: mcpForm.name, url: mcpForm.url, headers, enabled: mcpForm.enabled })
    mcpForm.show = false
    await loadMCP()
  } catch (e) { mcpForm.err = apiErr(e) } finally { mcpForm.busy = false }
}

async function testMCP(m) {
  try {
    const r = await aiApi.testMCP(m.id)
    alert(r.ok ? `连通正常（HTTP ${r.status}，${r.latency_ms}ms）` : `连接失败：${r.error || 'HTTP ' + r.status}`)
  } catch (e) { alert(apiErr(e)) }
}

async function removeMCP(m) {
  if (!window.confirm(`删除 MCP 端点「${m.name}」？`)) return
  try { await aiApi.deleteMCP(m.id); await loadMCP() } catch (e) { alert(apiErr(e)) }
}

onMounted(loadAccounts)
</script>

<style scoped>
.ai-window { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.tabs { display: flex; align-items: center; gap: 4px; padding: 8px 12px; border-bottom: 1px solid var(--g-border); }
.tab { border: none; background: transparent; color: var(--g-text-muted); font-size: 12px; padding: 4px 10px; border-radius: 7px; cursor: pointer; }
.tab.active { background: var(--g-surface-soft); color: var(--g-text); font-weight: 600; }
.err { color: #b91c1c; font-size: 12px; margin-left: auto; }
.body { flex: 1; overflow: auto; padding: 10px 12px; }
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.hint { font-size: 11.5px; color: var(--g-text-muted); }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th, .tbl td { border-bottom: 1px solid var(--g-border); padding: 6px 7px; text-align: left; }
.tbl th { color: var(--g-text-muted); font-weight: 600; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.muted { color: var(--g-text-muted); }
.empty { color: var(--g-text-muted); font-size: 13px; padding: 30px; text-align: center; }
.mask { position: fixed; inset: 0; background: rgba(15,23,42,.55); display: flex; align-items: center; justify-content: center; z-index: 80; padding: 24px; }
.dialog { width: 100%; max-width: 520px; max-height: 90%; background: var(--g-surface); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; }
.dialog-title { padding: 11px 14px; border-bottom: 1px solid var(--g-border); font-weight: 700; font-size: 13.5px; background: var(--g-surface-muted); }
.dialog-body { padding: 12px 14px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
.dialog-foot { padding: 10px 14px; border-top: 1px solid var(--g-border); display: flex; justify-content: flex-end; gap: 8px; background: var(--g-surface-muted); }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.inp, .area { padding: 6px 8px; border: 1px solid var(--g-border); border-radius: 8px; background: var(--g-surface); color: var(--g-text); font-size: 12px; }
.area { resize: vertical; font-family: ui-monospace, monospace; }
.check { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.result { background: #0f1722; color: #d7e3f4; border-radius: 8px; padding: 8px; font-size: 12px; white-space: pre-wrap; max-height: 240px; overflow: auto; margin: 0; }
</style>
