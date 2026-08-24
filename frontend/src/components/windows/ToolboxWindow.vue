<template>
  <div class="toolbox-window">
    <!-- 顶部：功能页签切换 -->
    <div class="tabs">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="tab"
        :class="{ active: tab === t.key }"
        @click="tab = t.key"
      >
        <component :is="t.icon" :size="14" /> {{ t.label }}
      </button>
    </div>

    <!-- Base64 编解码 -->
    <div v-if="tab === 'base64'" class="tab-body">
      <div class="field-row">
        <label class="field">
          <span class="label">操作</span>
          <select v-model="b64.mode">
            <option value="encode">编码（文本 → Base64）</option>
            <option value="decode">解码（Base64 → 文本）</option>
          </select>
        </label>
      </div>
      <label class="field">
        <span class="label">{{ b64.mode === 'encode' ? '待编码文本' : 'Base64 内容' }}</span>
        <textarea v-model.trim="b64.input" rows="4" placeholder="输入内容" spellcheck="false"></textarea>
      </label>
      <div class="actions">
        <button class="btn primary" :disabled="busy" @click="runBase64">{{ busy ? '处理中…' : '转换' }}</button>
      </div>
    </div>

    <!-- 哈希 -->
    <div v-if="tab === 'hash'" class="tab-body">
      <div class="field-row">
        <label class="field">
          <span class="label">算法</span>
          <select v-model="hashAlgo">
            <option value="md5">MD5</option>
            <option value="sha1">SHA1</option>
            <option value="sha256">SHA256</option>
          </select>
        </label>
      </div>
      <label class="field">
        <span class="label">待计算文本</span>
        <textarea v-model.trim="hashInput" rows="4" placeholder="输入要计算哈希的文本" spellcheck="false"></textarea>
      </label>
      <div class="actions">
        <button class="btn primary" :disabled="busy" @click="runHash">{{ busy ? '计算中…' : '计算' }}</button>
      </div>
    </div>

    <!-- 时间戳 <-> 时间 -->
    <div v-if="tab === 'timestamp'" class="tab-body">
      <div class="field-row">
        <label class="field">
          <span class="label">方向</span>
          <select v-model="tsMode">
            <option value="to-datetime">时间戳 → 时间</option>
            <option value="to-timestamp">时间 → 时间戳</option>
          </select>
        </label>
      </div>
      <label v-if="tsMode === 'to-datetime'" class="field">
        <span class="label">时间戳（秒）</span>
        <input v-model.trim="tsTimestamp" placeholder="如 1755648000" spellcheck="false" />
      </label>
      <label v-else class="field">
        <span class="label">日期时间</span>
        <input v-model.trim="tsDatetime" placeholder="如 2026-08-20 12:00:00" spellcheck="false" />
      </label>
      <div class="actions">
        <button class="btn primary" :disabled="busy" @click="runTimestamp">{{ busy ? '转换中…' : '转换' }}</button>
      </div>
    </div>

    <!-- 端口扫描 -->
    <div v-if="tab === 'portscan'" class="tab-body">
      <label class="field">
        <span class="label">主机地址</span>
        <input v-model.trim="scan.host" placeholder="如 127.0.0.1 或 example.com" spellcheck="false" />
      </label>
      <div class="field-row">
        <label class="field">
          <span class="label">起始端口</span>
          <input type="number" min="1" max="65535" v-model.number="scan.start" />
        </label>
        <label class="field">
          <span class="label">结束端口</span>
          <input type="number" min="1" max="65535" v-model.number="scan.end" />
        </label>
      </div>
      <div class="hint">单次最多扫描 200 个端口，仅做 TCP 连通性探测。</div>
      <div class="actions">
        <button class="btn primary" :disabled="busy" @click="runPortScan">{{ busy ? '扫描中…' : '开始扫描' }}</button>
      </div>
    </div>

    <!-- Whois 查询 -->
    <div v-if="tab === 'whois'" class="tab-body">
      <label class="field">
        <span class="label">域名</span>
        <input v-model.trim="whoisDomain" placeholder="如 example.com" spellcheck="false" />
      </label>
      <div class="hint">依赖服务器上已安装的 whois 命令，未安装时将提示不可用。</div>
      <div class="actions">
        <button class="btn primary" :disabled="busy" @click="runWhois">{{ busy ? '查询中…' : '查询' }}</button>
      </div>
    </div>

    <!-- 结果区（所有页签共用） -->
    <div class="result-area">
      <div class="result-toolbar">
        <span class="result-title">结果</span>
        <button class="btn mini" :disabled="!resultText" @click="copyResult"><Copy :size="12" /> 复制</button>
      </div>
      <textarea
        v-model="resultText"
        readonly
        rows="8"
        placeholder="结果将显示在这里"
        spellcheck="false"
      ></textarea>
      <div v-if="errorText" class="error">{{ errorText }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { Binary, Hash, Clock, Radar, Globe, Copy } from 'lucide-vue-next'
import { toolboxApi } from '../../api'

// 当前页签
const tab = ref('base64')
const tabs = [
  { key: 'base64', label: 'Base64', icon: Binary },
  { key: 'hash', label: '哈希', icon: Hash },
  { key: 'timestamp', label: '时间戳', icon: Clock },
  { key: 'portscan', label: '端口扫描', icon: Radar },
  { key: 'whois', label: 'Whois', icon: Globe },
]

// 状态与结果
const busy = ref(false)
const resultText = ref('')
const errorText = ref('')

// Base64 表单
const b64 = reactive({ mode: 'encode', input: '' })
// 哈希表单
const hashInput = ref('')
const hashAlgo = ref('md5')
// 时间戳表单
const tsMode = ref('to-datetime')
const tsTimestamp = ref('')
const tsDatetime = ref('')
// 端口扫描表单
const scan = reactive({ host: '', start: 1, end: 100 })
// Whois 表单
const whoisDomain = ref('')

// 将后端返回的 result 规范化为可展示文本
function fmtResult(r) {
  if (r == null) return ''
  if (typeof r === 'string') return r
  return JSON.stringify(r, null, 2)
}

function resetResult() {
  errorText.value = ''
  resultText.value = ''
}

// 统一执行入口：调用后端 /toolbox/exec
async function exec(tool, args) {
  busy.value = true
  resetResult()
  try {
    const r = await toolboxApi.exec({ tool, args })
    resultText.value = fmtResult(r && r.result)
  } catch (e) {
    errorText.value = e.response?.data?.detail || e.message || '执行失败'
  } finally {
    busy.value = false
  }
}

function runBase64() {
  if (!b64.input) { errorText.value = '请输入内容'; return }
  exec(b64.mode === 'encode' ? 'base64_encode' : 'base64_decode', { text: b64.input })
}

function runHash() {
  if (!hashInput.value) { errorText.value = '请输入待计算文本'; return }
  exec('hash', { text: hashInput.value, algo: hashAlgo.value })
}

function runTimestamp() {
  if (tsMode.value === 'to-datetime') {
    if (!tsTimestamp.value) { errorText.value = '请输入时间戳'; return }
    exec('timestamp_to_datetime', { timestamp: tsTimestamp.value })
  } else {
    if (!tsDatetime.value) { errorText.value = '请输入日期时间'; return }
    exec('datetime_to_timestamp', { datetime: tsDatetime.value })
  }
}

function runPortScan() {
  if (!scan.host) { errorText.value = '请输入主机地址'; return }
  // 前端校验端口范围，与后端白名单保持一致
  const start = Number(scan.start)
  const end = Number(scan.end)
  if (!(start >= 1 && start <= 65535) || !(end >= 1 && end <= 65535)) {
    errorText.value = '端口必须在 1-65535 之间'
    return
  }
  const lo = Math.min(start, end)
  const hi = Math.max(start, end)
  if (hi - lo + 1 > 200) {
    errorText.value = '单次最多扫描 200 个端口'
    return
  }
  exec('port_scan', { host: scan.host, start_port: lo, end_port: hi })
}

function runWhois() {
  if (!whoisDomain.value) { errorText.value = '请输入域名'; return }
  exec('whois', { domain: whoisDomain.value })
}

async function copyResult() {
  try {
    await navigator.clipboard.writeText(resultText.value)
    alert('已复制到剪贴板')
  } catch (e) {
    alert('复制失败：' + e.message)
  }
}
</script>

<style scoped>
.toolbox-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.tab { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; color: #1d1d1f; }
.tab:hover { background: #f9fafb; }
.tab.active { background: #111827; color: #fff; border-color: #111827; }

.tab-body { display: flex; flex-direction: column; gap: 10px; }
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }
.field { display: block; }
.field .label { display: block; font-size: 11px; color: #1d1d1f; font-weight: 600; margin-bottom: 5px; }
.field input, .field select { width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; font-family: inherit; }
.field input:focus, .field select:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.field textarea { width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; font-family: ui-monospace, Menlo, Consolas, monospace; resize: vertical; }
.field textarea:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.hint { color: #6e6e73; font-size: 12px; }
.actions { display: flex; justify-content: flex-end; }

.result-area { display: flex; flex-direction: column; gap: 6px; flex: 1; min-height: 0; }
.result-toolbar { display: flex; align-items: center; justify-content: space-between; }
.result-title { font-size: 11px; color: #1d1d1f; font-weight: 600; }
.result-area textarea { flex: 1; width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12px; font-family: ui-monospace, Menlo, Consolas, monospace; resize: none; background: #fafafa; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.mini { padding: 3px 8px; font-size: 11.5px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }

.error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; }
</style>
