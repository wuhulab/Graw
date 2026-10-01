<!--
  工具箱窗口（Toolbox）

  这个窗口做什么：
    面板的实用小工具集合，所有计算都在服务器端执行（经 /api/toolbox/exec）：
      - Base64 编解码；哈希计算；时间戳与日期时间互转；
      - 端口扫描（单次最多 200 个端口，仅 TCP 连通性探测）；
      - Whois 域名查询（依赖服务器上安装的 whois 命令）；
      - 网络诊断：ping / 路由追踪 / DNS 查询 / HTTP(S) 探测（在当前管理节点上执行）；
      - 端口排查：监听端口 -> 进程 -> 容器归属映射（GET /api/toolbox/portview）；
      - 脚本库：常用命令片段保存/复用（data/toolbox_scripts.json，含危险命令二次确认）。

  用到的后端模块：
    /api/toolbox/*（管理员）——exec 统一执行入口、portview 端口视图、scripts 脚本库 CRUD。

  关键状态：
    tab          当前页签（base64 / hash / timestamp / portscan / whois / net / ports / scripts）
    busy         请求进行中（禁用按钮）
    resultText   结果区文本（前 5 个页签 + 网络诊断共用）
    errorText    执行失败提示
-->
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

    <!-- 网络诊断（P0：ping / 路由追踪 / DNS / HTTP 探测） -->
    <div v-if="tab === 'net'" class="tab-body">
      <div class="net-grid">
        <div class="net-card">
          <div class="net-title">Ping 连通性</div>
          <label class="field">
            <span class="label">主机地址</span>
            <input v-model.trim="net.ping.host" placeholder="如 8.8.8.8 或 example.com" spellcheck="false" />
          </label>
          <div class="field-row">
            <label class="field">
              <span class="label">次数（1-10）</span>
              <input type="number" min="1" max="10" v-model.number="net.ping.count" />
            </label>
          </div>
          <div class="actions">
            <button class="btn primary" :disabled="busy" @click="runPing">{{ busy ? '测试中…' : '开始 Ping' }}</button>
          </div>
        </div>

        <div class="net-card">
          <div class="net-title">路由追踪</div>
          <label class="field">
            <span class="label">目标主机</span>
            <input v-model.trim="net.trace.host" placeholder="如 example.com" spellcheck="false" />
          </label>
          <div class="field-row">
            <label class="field">
              <span class="label">最大跳数（1-30）</span>
              <input type="number" min="1" max="30" v-model.number="net.trace.maxHops" />
            </label>
          </div>
          <div class="actions">
            <button class="btn primary" :disabled="busy" @click="runTrace">{{ busy ? '追踪中…' : '开始追踪' }}</button>
          </div>
        </div>

        <div class="net-card">
          <div class="net-title">DNS 查询</div>
          <div class="field-row">
            <label class="field">
              <span class="label">域名</span>
              <input v-model.trim="net.dns.domain" placeholder="如 example.com" spellcheck="false" />
            </label>
            <label class="field field-sm">
              <span class="label">类型</span>
              <select v-model="net.dns.type">
                <option v-for="t in dnsTypes" :key="t" :value="t">{{ t }}</option>
              </select>
            </label>
          </div>
          <div class="actions">
            <button class="btn primary" :disabled="busy" @click="runDns">{{ busy ? '查询中…' : '解析' }}</button>
          </div>
        </div>

        <div class="net-card">
          <div class="net-title">HTTP(S) 探测</div>
          <label class="field">
            <span class="label">URL</span>
            <input v-model.trim="net.http.url" placeholder="如 https://example.com" spellcheck="false" />
          </label>
          <div class="field-row">
            <label class="field">
              <span class="label">超时秒（3-60）</span>
              <input type="number" min="3" max="60" v-model.number="net.http.timeout" />
            </label>
          </div>
          <div class="actions">
            <button class="btn primary" :disabled="busy" @click="runHttp">{{ busy ? '探测中…' : '探测' }}</button>
          </div>
        </div>
      </div>
      <div class="hint">网络诊断在当前管理节点上执行（多节点场景会按聚焦节点执行），结果见下方结果区。</div>
    </div>

    <!-- 端口排查（P0：监听端口 -> 进程 -> 容器归属） -->
    <div v-if="tab === 'ports'" class="tab-body">
      <div class="toolbar-row">
        <input
          v-model.trim="portFilter"
          class="filter-input"
          placeholder="过滤：端口 / 进程名 / 容器ID"
          spellcheck="false"
          @keyup.enter="loadPorts"
        />
        <button class="btn" :disabled="portLoading" @click="loadPorts">
          <RefreshCw :size="13" :class="{ spin: portLoading }" /> 刷新
        </button>
      </div>
      <div class="table-wrap">
        <table class="dt">
          <thead>
            <tr><th>端口</th><th>协议</th><th>进程</th><th>PID</th><th>归属容器</th></tr>
          </thead>
          <tbody>
            <tr v-if="portLoading">
              <td colspan="5" class="empty">正在读取监听端口…</td>
            </tr>
            <tr v-else-if="!portItems.length">
              <td colspan="5" class="empty">没有匹配的监听项</td>
            </tr>
            <tr v-for="p in portItems" :key="p.port + '-' + p.proto + '-' + (p.pid || '')">
              <td class="mono">{{ p.port }}</td>
              <td>{{ p.proto }}</td>
              <td>{{ p.process || '—' }}</td>
              <td class="mono">{{ p.pid || '—' }}</td>
              <td class="cid">{{ p.container_id || '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="hint">共 {{ portItems.length }} 条监听项；数据取自当前管理节点的 ss / netstat，容器归属经 Docker inspect 映射。</div>
    </div>

    <!-- 脚本库（P0：常用命令片段保存/复用） -->
    <div v-if="tab === 'scripts'" class="tab-body">
      <div class="toolbar-row">
        <input
          v-model.trim="scriptFilter"
          class="filter-input"
          placeholder="按名称 / 描述 / 标签过滤"
          spellcheck="false"
        />
        <button class="btn primary" @click="openEditor()"><Plus :size="13" /> 新建脚本</button>
      </div>

      <!-- 编辑器 -->
      <div v-if="editorOpen" class="script-editor">
        <div class="field-row">
          <label class="field">
            <span class="label">名称</span>
            <input v-model.trim="form.name" placeholder="如 磁盘占用 TOP10" spellcheck="false" />
          </label>
          <label class="field field-sm">
            <span class="label">适用目标</span>
            <select v-model="form.target">
              <option value="host">宿主机</option>
              <option value="node">节点</option>
              <option value="container">容器内</option>
            </select>
          </label>
        </div>
        <label class="field">
          <span class="label">描述</span>
          <input v-model.trim="form.desc" placeholder="这段脚本是干什么的" spellcheck="false" />
        </label>
        <label class="field">
          <span class="label">标签（逗号分隔，最多 8 个）</span>
          <input v-model.trim="form.tags" placeholder="如 日志,清理,日常" spellcheck="false" />
        </label>
        <label class="field">
          <span class="label">脚本内容</span>
          <textarea v-model="form.content" rows="6" placeholder="#!/bin/bash&#10;# 描述你的脚本…" spellcheck="false"></textarea>
        </label>
        <div v-if="dangerHits.length" class="warn">
          ⚠ 检测到危险命令模式：<code>{{ dangerHits.join('、') }}</code>，保存时需二次确认。
        </div>
        <div class="actions">
          <button class="btn" @click="closeEditor">取消</button>
          <button class="btn primary" :disabled="saving" @click="saveScript">{{ saving ? '保存中…' : '保存' }}</button>
        </div>
      </div>

      <!-- 列表 -->
      <div class="script-list">
        <div v-if="!filteredScripts.length" class="empty-box">还没有脚本片段，点击「新建脚本」添加一条。</div>
        <div v-for="s in filteredScripts" :key="s.id" class="script-item">
          <div class="row">
            <strong>{{ s.name }}</strong>
            <span v-if="s.dangerous" class="tag tag-danger" title="包含危险命令">危险</span>
            <span class="tag" :title="s.target">{{ targetLabel(s.target) }}</span>
            <span v-for="t in (s.tags || [])" :key="t" class="tag">{{ t }}</span>
          </div>
          <div class="desc">{{ s.desc || '（无描述）' }}</div>
          <pre v-if="expandedId === s.id" class="code">{{ s.content }}</pre>
          <div class="ops">
            <button class="btn mini" @click="toggleExpand(s)">
              {{ expandedId === s.id ? '收起' : '查看' }}
            </button>
            <button class="btn mini" @click="copyScript(s)"><Copy :size="11" /> 复制</button>
            <button class="btn mini" @click="openEditor(s)"><Pencil :size="11" /> 编辑</button>
            <button class="btn mini danger" @click="removeScript(s)"><Trash2 :size="11" /> 删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 结果区（前 5 个页签 + 网络诊断共用） -->
    <div v-if="showResult" class="result-area">
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
import { ref, reactive, computed, onMounted } from 'vue'   // 响应式状态、表单对象、生命周期
import { Binary, Hash, Clock, Radar, Globe, Network, Plug, FileCode, RefreshCw, Plus, Pencil, Trash2, Copy } from 'lucide-vue-next'
import { toolboxApi } from '../../api'   // 工具箱后端能力：/api/toolbox/* 的封装

// 当前页签 & 定义：key 与后端 exec 的 tool 参数一一对应（网络诊断沿用 exec）
const tab = ref('base64')
const tabs = [
  { key: 'base64', label: 'Base64', icon: Binary },
  { key: 'hash', label: '哈希', icon: Hash },
  { key: 'timestamp', label: '时间戳', icon: Clock },
  { key: 'portscan', label: '端口扫描', icon: Radar },
  { key: 'whois', label: 'Whois', icon: Globe },
  { key: 'net', label: '网络诊断', icon: Network },
  { key: 'ports', label: '端口排查', icon: Plug },
  { key: 'scripts', label: '脚本库', icon: FileCode },
]

// 状态与结果
const busy = ref(false)         // exec 请求进行中（禁用执行按钮）
const resultText = ref('')      // 结果区文本（所有 exec 页签共用）
const errorText = ref('')       // 执行失败提示
// 结果区只在会产出文本的页签显示（端口排查/脚本库用独立面板）
const showResult = computed(() => ['ports', 'scripts'].indexOf(tab.value) === -1)

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

// ---- 网络诊断表单 ----
const dnsTypes = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS']
const net = reactive({
  ping: { host: '', count: 4 },
  trace: { host: '', maxHops: 15 },
  dns: { domain: '', type: 'A' },
  http: { url: '', timeout: 15 },
})

// ---- 端口排查 ----
const portFilter = ref('')       // 过滤关键字
const portItems = ref([])        // 端口条目
const portLoading = ref(false)

// ---- 脚本库 ----
const scripts = ref([])          // 脚本列表
const scriptFilter = ref('')     // 名称/描述/标签过滤
const editorOpen = ref(false)    // 是否显示编辑器
const editingId = ref(null)      // 编辑中的脚本 id（null = 新建）
const form = reactive({ name: '', desc: '', target: 'host', tags: '', content: '' })
const saving = ref(false)
const expandedId = ref('')       // 展开查看内容的脚本 id

// 将后端返回的 result 规范化为可展示文本
function fmtResult(r) {
  if (r == null) return ''
  if (typeof r === 'string') return r
  return JSON.stringify(r, null, 2)   // 对象结果按 2 空格缩进，方便人工阅读
}

// --- 清空上一次的结果与错误，避免新旧内容混在一起 ---
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

// --- Base64：按当前模式选编码还是解码 ---
function runBase64() {
  if (!b64.input) { errorText.value = '请输入内容'; return }
  exec(b64.mode === 'encode' ? 'base64_encode' : 'base64_decode', { text: b64.input })
}

// --- 哈希：按所选算法计算 ---
function runHash() {
  if (!hashInput.value) { errorText.value = '请输入待计算文本'; return }
  exec('hash', { text: hashInput.value, algo: hashAlgo.value })
}

// --- 时间戳：按方向调互转接口 ---
function runTimestamp() {
  if (tsMode.value === 'to-datetime') {
    if (!tsTimestamp.value) { errorText.value = '请输入时间戳'; return }
    exec('timestamp_to_datetime', { timestamp: tsTimestamp.value })
  } else {
    if (!tsDatetime.value) { errorText.value = '请输入日期时间'; return }
    exec('datetime_to_timestamp', { datetime: tsDatetime.value })
  }
}

// --- 端口扫描：先做前端校验，再按升序区间扫描 ---
function runPortScan() {
  if (!scan.host) { errorText.value = '请输入主机地址'; return }
  // 前端校验端口范围，与后端白名单保持一致
  const start = Number(scan.start)
  const end = Number(scan.end)
  if (!(start >= 1 && start <= 65535) || !(end >= 1 && end <= 65535)) {
    errorText.value = '端口必须在 1-65535 之间'
    return
  }
  const lo = Math.min(start, end)   // 容错：起始大于结束也能正确扫描
  const hi = Math.max(start, end)
  if (hi - lo + 1 > 200) {          // 与后端一致的单次扫描上限
    errorText.value = '单次最多扫描 200 个端口'
    return
  }
  exec('port_scan', { host: scan.host, start_port: lo, end_port: hi })
}

// --- Whois：查询域名注册 / 解析信息 ---
function runWhois() {
  if (!whoisDomain.value) { errorText.value = '请输入域名'; return }
  exec('whois', { domain: whoisDomain.value })
}

// --- 网络诊断四件套（P0） ---
function runPing() {
  if (!net.ping.host) { errorText.value = '请输入主机地址'; return }
  const count = Math.min(Math.max(Number(net.ping.count) || 4, 1), 10)
  exec('ping', { host: net.ping.host, count })
}

function runTrace() {
  if (!net.trace.host) { errorText.value = '请输入目标主机'; return }
  const maxHops = Math.min(Math.max(Number(net.trace.maxHops) || 15, 1), 30)
  exec('traceroute', { host: net.trace.host, max_hops: maxHops })
}

function runDns() {
  if (!net.dns.domain) { errorText.value = '请输入域名'; return }
  exec('dns_lookup', { domain: net.dns.domain, type: net.dns.type })
}

function runHttp() {
  if (!net.http.url) { errorText.value = '请输入 URL'; return }
  const timeout = Math.min(Math.max(Number(net.http.timeout) || 15, 3), 60)
  exec('http_probe', { url: net.http.url, timeout })
}

// --- 端口排查看板（P0） ---
async function loadPorts() {
  portLoading.value = true
  try {
    const r = await toolboxApi.portView(portFilter.value || '')
    portItems.value = (r && r.items) || []
  } catch (e) {
    portItems.value = []
    errorText.value = e.response?.data?.detail || e.message || '读取端口失败'
  } finally {
    portLoading.value = false
  }
}

// --- 脚本库（P0） ---
const filteredScripts = computed(() => {
  const kw = (scriptFilter.value || '').trim().toLowerCase()
  if (!kw) return scripts.value
  return scripts.value.filter(s =>
    ((s.name || '') + ' ' + (s.desc || '') + ' ' + ((s.tags || []).join(' '))).toLowerCase().includes(kw)
  )
})

function targetLabel(t) {
  return { host: '宿主机', node: '节点', container: '容器' }[t] || t
}

// 前端危险命令扫描（与后端 _DANGER_PATTERNS 保持同源，用于保存前二次确认）
function scanDanger(text) {
  const patterns = [
    /\brm\s+-[a-z]*r[a-z]*f[a-z]*(\s|\/|$)/i,
    /\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+[^|;]*\/\*/i,
    /\bmkfs(?:\.[a-z0-9]+)?\b/i,
    /\bdd\b/i,
    /\bchmod\s+-R\s+777\s+\/\s*$/i,
    /\b(?:shutdown|reboot|poweroff|init\s+0|init\s+6)\b/i,
    /\b:\(\)\s*\{/,
    />>\s*\/etc\/(?:passwd|shadow)/i,
  ]
  return patterns.filter(p => p.test(text || ''))
}
const dangerHits = computed(() => scanDanger(form.content))

async function loadScripts() {
  try {
    const r = await toolboxApi.listScripts()
    scripts.value = (r && r.scripts) || []
  } catch (e) {
    errorText.value = e.response?.data?.detail || e.message || '读取脚本库失败'
  }
}

function openEditor(script) {
  editingId.value = script ? script.id : null
  form.name = script ? script.name : ''
  form.desc = script ? script.desc || '' : ''
  form.target = script ? script.target || 'host' : 'host'
  form.tags = script && script.tags ? script.tags.join(',') : ''
  form.content = script ? script.content || '' : ''
  expandedId.value = ''
  editorOpen.value = true
}

function closeEditor() {
  editorOpen.value = false
  editingId.value = null
}

async function saveScript() {
  if (!form.name.trim()) { alert('请填写脚本名称'); return }
  // 危险命令二次确认（仅提示，不强制禁止）
  const hits = scanDanger(form.content)
  if (hits.length) {
    const ok = window.confirm(
      '该脚本包含危险命令（' + hits.join('、') + '），请确认是本人有意的运维操作后保存。\n仍要保存吗？'
    )
    if (!ok) return
  }
  const body = {
    name: form.name,
    desc: form.desc,
    target: form.target,
    tags: form.tags.split(/[,，\s]+/).map(t => t.trim()).filter(Boolean),
    content: form.content,
  }
  saving.value = true
  try {
    if (editingId.value) {
      await toolboxApi.updateScript(editingId.value, body)
    } else {
      await toolboxApi.createScript(body)
    }
    await loadScripts()
    closeEditor()
  } catch (e) {
    alert(e.response?.data?.detail || e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeScript(script) {
  if (!window.confirm(`确认删除脚本「${script.name}」？删除后不可恢复。`)) return
  try {
    await toolboxApi.deleteScript(script.id)
    await loadScripts()
  } catch (e) {
    alert(e.response?.data?.detail || e.message || '删除失败')
  }
}

async function copyScript(script) {
  try {
    await navigator.clipboard.writeText(script.content || '')
    alert('已复制脚本内容')
  } catch (e) {
    alert('复制失败：' + e.message)
  }
}

function toggleExpand(script) {
  expandedId.value = expandedId.value === script.id ? '' : script.id
}

// --- 复制结果到剪贴板 ---
async function copyResult() {
  try {
    await navigator.clipboard.writeText(resultText.value)
    alert('已复制到剪贴板')
  } catch (e) {
    alert('复制失败：' + e.message)
  }
}

// 进入窗口时预加载端口与脚本数据
onMounted(() => {
  loadPorts()
  loadScripts()
})
</script>

<style scoped>
.toolbox-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; }
.tab { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 6px; color: #374151; }
.tab:hover { background: #f9fafb; }
.tab.active { background: #111827; color: #fff; border-color: #111827; }

.tab-body { display: flex; flex-direction: column; gap: 10px; overflow: auto; min-height: 0; }
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }
.field { display: block; }
.field .label { display: block; font-size: 12px; color: #374151; font-weight: 500; margin-bottom: 5px; }
.field input, .field select { width: 100%; box-sizing: border-box; border: 1px solid #d1d5db; border-radius: 6px; padding: 8px 10px; font-size: 13px; font-family: inherit; background: #fff; color: #1d1d1f; }
.field input:focus, .field select:focus { outline: none; border-color: #111827; box-shadow: 0 0 0 3px rgba(17,24,39,0.08); }
.field textarea { width: 100%; box-sizing: border-box; border: 1px solid #d1d5db; border-radius: 6px; padding: 8px 10px; font-size: 13px; font-family: ui-monospace, Menlo, Consolas, monospace; resize: vertical; }
.field textarea:focus { outline: none; border-color: #111827; box-shadow: 0 0 0 3px rgba(17,24,39,0.08); }
.hint { color: #6e6e73; font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }

/* 网络诊断卡片（对齐网站「类型选择卡片」风格） */
.net-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.net-card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; background: #fff; transition: all 0.15s; }
.net-card:hover { border-color: #94a3b8; background: #f9fafb; }
.net-title { font-size: 13px; font-weight: 600; color: #111827; display: flex; align-items: center; gap: 6px; }
.net-title::before { content: ''; width: 3px; height: 12px; border-radius: 2px; background: #111827; }

/* 工具栏 */
.toolbar-row { display: flex; gap: 8px; align-items: center; justify-content: space-between; margin-bottom: 2px; }
.filter-input { flex: 1; border: 1px solid #d1d5db; border-radius: 6px; padding: 8px 10px; font-size: 13px; font-family: ui-monospace, Menlo, Consolas, monospace; background: #fff; color: #1d1d1f; }
.filter-input:focus { outline: none; border-color: #111827; box-shadow: 0 0 0 3px rgba(17,24,39,0.08); }

/* 端口排查表格（对齐网站列表表格风格） */
.table-wrap { border: 1px solid #e5e7eb; border-radius: 8px; overflow: auto; max-height: 260px; background: #fff; }
.table-wrap table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table-wrap th, .table-wrap td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #f0f0f0; white-space: nowrap; }
.table-wrap th { background: #f9fafb; color: #6b7280; font-size: 12px; font-weight: 600; position: sticky; top: 0; }
.table-wrap tbody tr:hover td { background: #f9fafb; }
.table-wrap td.empty { text-align: center; color: #9ca3af; padding: 22px 0; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; }
.cid { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; color: #111827; font-weight: 500; }

/* 脚本库 */
.script-editor { border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; background: #f9fafb; display: flex; flex-direction: column; gap: 10px; }
.script-list { display: flex; flex-direction: column; gap: 8px; overflow: auto; min-height: 0; }
.script-item { border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; background: #fff; display: flex; flex-direction: column; gap: 6px; }
.script-item:hover { border-color: #111827; }
.script-item .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.script-item .desc { color: #6b7280; font-size: 12px; }
.script-item .code { margin: 0; background: #0f1115; color: #d1d5db; border-radius: 8px; padding: 10px; font-size: 12px; font-family: ui-monospace, Menlo, Consolas, monospace; white-space: pre-wrap; word-break: break-all; max-height: 220px; overflow: auto; }
.script-item .ops { display: flex; gap: 6px; }
.tag { font-size: 11px; background: #f3f4f6; border: 1px solid #e5e7eb; color: #6b7280; border-radius: 999px; padding: 1px 8px; }
.tag-danger { background: #fef2f2; color: #b91c1c; border-color: #fecaca; }
.empty-box { color: #9ca3af; text-align: center; padding: 26px 0; font-size: 13px; }
.warn { color: #92400e; font-size: 12px; background: #fef3c7; border: 1px solid #fde68a; border-radius: 6px; padding: 8px 10px; }
.warn code { font-family: ui-monospace, Menlo, Consolas, monospace; }

.result-area { display: flex; flex-direction: column; gap: 6px; flex: 1; min-height: 0; }
.result-toolbar { display: flex; align-items: center; justify-content: space-between; }
.result-title { font-size: 12px; color: #111827; font-weight: 600; }
.result-area textarea { flex: 1; width: 100%; box-sizing: border-box; border: 1px solid #d1d5db; border-radius: 6px; padding: 8px 10px; font-size: 12px; font-family: ui-monospace, Menlo, Consolas, monospace; resize: none; background: #fafafa; color: #1d1d1f; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.mini { padding: 3px 8px; font-size: 12px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.btn.danger { color: #b91c1c; }

.error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; padding: 8px 10px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>