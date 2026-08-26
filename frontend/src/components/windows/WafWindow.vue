<template>
  <div class="waf-window">
    <!-- 顶部工具栏：全局开关 + 站点选择 + 操作按钮 -->
    <div class="toolbar">
      <span class="badge" :class="globalEnabled ? 'ok' : 'off'">
        全局 WAF：{{ globalEnabled ? '已开启' : '已关闭' }}
      </span>
      <button class="btn" @click="toggleGlobal">{{ globalEnabled ? '关闭总开关' : '开启总开关' }}</button>

      <span class="sep" />
      <select v-model="currentSite" class="site-select" @change="loadSite">
        <option disabled value="">{{ sites.length ? '选择站点' : '暂无站点' }}</option>
        <option v-for="s in sites" :key="s.site" :value="s.site">
          {{ s.site }}{{ s.enabled ? ' ✓' : '' }}
        </option>
      </select>

      <button class="btn primary" :disabled="!currentSite" @click="save">保存站点策略</button>
      <button class="btn" :disabled="!currentSite" @click="loadPreview">生成预览</button>
      <button class="btn" @click="applyAll">全局应用</button>
    </div>

    <!-- 页签导航 -->
    <div class="tabs">
      <button v-for="(tab, i) in tabs" :key="tab.key" class="tab"
              :class="{ active: activeTab === i }" @click="activeTab = i">
        {{ tab.label }}
      </button>
    </div>

    <div class="body">
      <!-- ─── 0 总览 ─── -->
      <div v-if="activeTab === 0">
        <div class="notice">
          为选中的站点配置 Web 应用防火墙策略。开启全局总开关后，对所有「已启用」的站点生效；
          未配置站点保持原样。策略保存后生成 nginx include 片段，可用「全局应用」统一写盘。
        </div>
        <div class="form-row"><span>站点启用 WAF</span>
          <input type="checkbox" v-model="cfg.enabled" />
        </div>
        <h4>当前配置摘要</h4>
        <ul class="summary">
          <li>防御规则：<b>{{ enabledCount(cfg.defense) }}</b>/10 项开启</li>
          <li>频率限制：访问 {{ cfg.frequency.access.count }}/{{ cfg.frequency.access.period }}s、
            攻击 {{ cfg.frequency.attack.count }}/{{ cfg.frequency.attack.period }}s、
            404 {{ cfg.frequency.notfound.count }}/{{ cfg.frequency.notfound.period }}s</li>
          <li>黑白名单：IP 白 {{ cfg.blackwhite.ip_whitelist.length }} / 黑 {{ cfg.blackwhite.ip_blacklist.length }}，
            URL 白 {{ cfg.blackwhite.url_whitelist.length }} / 黑 {{ cfg.blackwhite.url_blacklist.length }}，
            UA 白 {{ cfg.blackwhite.ua_whitelist.length }} / 黑 {{ cfg.blackwhite.ua_blacklist.length }}</li>
          <li>ACL {{ cfg.acl.length }} 条；等候厅 {{ cfg.waiting_hall.enabled ? '开启' : '关闭' }}</li>
        </ul>
      </div>

      <!-- ─── 1 频率 ─── -->
      <div v-else-if="activeTab === 1">
        <div v-for="fk in ['access', 'attack', 'notfound']" :key="fk" class="card">
          <h4>{{ { access: '访问频率限制', attack: '攻击频率限制', notfound: '404 频率限制' }[fk] }}</h4>
          <div class="form-row">
            <span>模式</span>
            <select v-model="cfg.frequency[fk].mode">
              <option value="url">按 URL</option>
              <option value="global">全局</option>
            </select>
          </div>
          <div class="form-row">
            <span>周期（秒）</span>
            <input type="number" v-model.number="cfg.frequency[fk].period" min="1" max="86400" />
          </div>
          <div class="form-row">
            <span>频率（次/周期）</span>
            <input type="number" v-model.number="cfg.frequency[fk].count" min="1" />
          </div>
          <div class="form-row">
            <span>封禁时间（秒）</span>
            <input type="number" v-model.number="cfg.frequency[fk].ban" min="1" max="86400" />
          </div>
        </div>
      </div>

      <!-- ─── 2 防御规则 ─── -->
      <div v-else-if="activeTab === 2">
        <div class="form-row" v-for="item in defenseItems" :key="item.key">
          <span>{{ item.label }}</span>
          <input type="checkbox" v-model="cfg.defense[item.key]" />
        </div>
      </div>

      <!-- ─── 3 自定义 ─── -->
      <div v-else-if="activeTab === 3">
        <div class="form-row">
          <span>文件上传大小限制（MB）</span>
          <input type="number" v-model.number="cfg.custom.upload_limit_mb" min="1" max="4096" />
        </div>
        <div class="form-row">
          <span>启用 CDN（关闭后拦截绕过 CDN 的直连）</span>
          <input type="checkbox" v-model="cfg.custom.cdn" />
        </div>
      </div>

      <!-- ─── 4 其他 ─── -->
      <div v-else-if="activeTab === 4">
        <div class="form-row">
          <span>启用蜘蛛 IP 池（关闭后拦截所有蜘蛛访问）</span>
          <input type="checkbox" v-model="cfg.other.spider_pool" />
        </div>
        <p class="hint">内置蜘蛛池：百度、Bing、谷歌、360、神马、搜狗、字节、DuckDuckGo、Yandex。关闭后以上蜘蛛全部拦截。</p>
        <h4>恶意 IP 组（按名称，空格分隔）</h4>
        <input class="wide" v-model="maliciousGroupsText" placeholder="如：cloud_bot 恶意扫描器" />
      </div>

      <!-- ─── 5 黑白名单 ─── -->
      <div v-else-if="activeTab === 5">
        <div v-for="lst in bwGroups" :key="lst.key" class="card">
          <h4>{{ lst.label }}</h4>
          <div class="tag-row">
            <span v-for="v in cfg.blackwhite[lst.key]" :key="v" class="tag">
              {{ v }}
              <button class="iconbtn" @click="removeBw(lst.key, v)">✕</button>
            </span>
            <span v-if="!cfg.blackwhite[lst.key].length" class="empty-tag">暂无</span>
          </div>
          <div class="add-row">
            <input v-model="newBw[lst.key]" :placeholder="lst.placeholder" @keyup.enter="addBw(lst.key)" />
            <button class="btn small" @click="addBw(lst.key)">添加</button>
          </div>
        </div>
      </div>

      <!-- ─── 6 拦截地图 ─── -->
      <div v-else-if="activeTab === 6">
        <div class="form-row">
          <span>统计天数</span>
          <select v-model.number="blockDays" @change="loadBlockmap">
            <option :value="30">近 30 天</option>
            <option :value="7">近 7 天</option>
            <option :value="90">近 90 天</option>
          </select>
          <span>共 {{ blockmap.total }} 次</span>
        </div>
        <div v-if="blockmap.data.length" class="bars">
          <div v-for="b in blockmap.data" :key="b.geo" class="bar-row">
            <span class="bar-label">{{ b.geo }}</span>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: barWidth(b.count) }"></div>
            </div>
            <span class="bar-val">{{ b.count }}</span>
          </div>
        </div>
        <p v-else class="hint">暂无拦截分布数据。</p>
      </div>

      <!-- ─── 7 地区 ─── -->
      <div v-else-if="activeTab === 7">
        <div class="form-row"><span>启用地区访问限制</span>
          <input type="checkbox" v-model="cfg.geo.enabled" />
        </div>
        <div class="form-row">
          <span>模式</span>
          <select v-model="cfg.geo.action">
            <option value="block">仅拒绝以下地区</option>
            <option value="allow">仅允许以下地区</option>
          </select>
        </div>
        <div class="card">
          <h4>国家/地区代码（ISO-3166，如 CN、US、JP）</h4>
          <div class="tag-row">
            <span v-for="c in cfg.geo.countries" :key="c" class="tag">{{ c }}
              <button class="iconbtn" @click="cfg.geo.countries = cfg.geo.countries.filter(x => x !== c)">✕</button>
            </span>
            <span v-if="!cfg.geo.countries.length" class="empty-tag">暂无</span>
          </div>
          <div class="add-row">
            <input v-model="newCountry" placeholder="如：CN" @keyup.enter="addCountry" />
            <button class="btn small" @click="addCountry">添加</button>
          </div>
        </div>
      </div>

      <!-- ─── 8 自定义规则 ACL ─── -->
      <div v-else-if="activeTab === 8">
        <div class="toolbar-inline">
          <button class="btn primary" @click="openAclEditor()">新增 ACL</button>
        </div>
        <table class="table">
          <thead><tr><th>匹配项</th><th>操作符</th><th>值</th><th>动作</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="a in cfg.acl" :key="a.id">
              <td>{{ matchLabel(a.match) }}</td>
              <td>{{ a.op }}</td>
              <td class="mono">{{ a.value }}</td>
              <td><span class="badge" :class="aclActionClass(a.action)">{{ aclActionLabel(a.action) }}</span></td>
              <td>
                <button class="iconbtn" @click="openAclEditor(a)">✎</button>
                <button class="iconbtn danger" @click="delAcl(a.id)">✕</button>
              </td>
            </tr>
            <tr v-if="!cfg.acl.length"><td colspan="5" class="empty">暂无 ACL 规则</td></tr>
          </tbody>
        </table>
      </div>

      <!-- ─── 9 拦截日志 ─── -->
      <div v-else-if="activeTab === 9">
        <div class="form-row">
          <input v-model="logFilter.site" placeholder="站点" class="small" />
          <input v-model="logFilter.ip" placeholder="IP" class="small" />
          <select v-model="logFilter.action" class="small">
            <option value="">全部动作</option>
            <option value="deny">deny</option>
            <option value="challenge">challenge</option>
            <option value="429">429</option>
          </select>
          <button class="btn" @click="loadLogs">查询</button>
          <button class="btn danger" @click="clearLogs">清空</button>
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>时间</th><th>站点</th><th>IP</th><th>规则</th><th>动作</th><th>原因</th></tr></thead>
            <tbody>
              <tr v-for="l in logs" :key="l.time + l.ip + l.reason">
                <td class="mono">{{ l.time }}</td>
                <td>{{ l.site }}</td>
                <td class="mono">{{ l.ip }}</td>
                <td>{{ l.rule }}</td>
                <td><span class="badge" :class="l.action === 'deny' ? 'warn' : 'info'">{{ l.action }}</span></td>
                <td class="clamp">{{ l.reason }}</td>
              </tr>
              <tr v-if="!logs.length"><td colspan="6" class="empty">暂无拦截日志</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ─── 10 等候厅 ─── -->
      <div v-else-if="activeTab === 10">
        <div class="form-row"><span>启用等候厅（挑战页）</span>
          <input type="checkbox" v-model="cfg.waiting_hall.enabled" />
        </div>
        <div class="form-row">
          <span>挑战 URL（以 / 开头）</span>
          <input v-model="cfg.waiting_hall.url" placeholder="/waf_challenge" />
        </div>
        <p class="hint">等候厅会将疑似攻击的超频请求引导到挑战页，人为确认后再放行，可缓解 CC/爬虫压力。</p>
      </div>
    </div>

    <!-- 预览弹窗 -->
    <div v-if="showPreview" class="modal-overlay" @click.self="showPreview = false">
      <div class="modal wide">
        <h3>站点「{{ currentSite }}」nginx 片段预览</h3>
        <pre class="preview">{{ previewText }}</pre>
        <div class="actions">
          <button class="btn" @click="showPreview = false">关闭</button>
        </div>
      </div>
    </div>

    <!-- ACL 编辑弹窗 -->
    <div v-if="aclEditor" class="modal-overlay" @click.self="aclEditor = null">
      <div class="modal">
        <h3>{{ aclEditor.id ? '编辑 ACL' : '新增 ACL' }}</h3>
        <div class="form">
          <label>匹配项</label>
          <select v-model="aclEditor.match">
            <option value="uri">URL</option><option value="ip">IP</option>
            <option value="ua">User-Agent</option><option value="args">参数</option>
            <option value="method">请求方法</option>
          </select>
          <label>操作符</label>
          <select v-model="aclEditor.op">
            <option value="eq">等于</option><option value="regex">正则</option>
            <option value="contains">包含</option><option value="starts">前缀</option>
          </select>
          <label>值</label>
          <input v-model="aclEditor.value" />
          <label>动作</label>
          <select v-model="aclEditor.action">
            <option value="deny">拒绝</option><option value="allow">放行</option>
            <option value="challenge">挑战</option>
          </select>
          <div class="actions">
            <button class="btn" @click="aclEditor = null">取消</button>
            <button class="btn primary" @click="saveAcl">保存</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：清空拦截日志需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      title="清空拦截日志确认"
      message="清空后所有 WAF 拦截日志将不可恢复。请输入面板密码以确认。"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      confirm-label="清空"
      @confirm="doClearLogs"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { wafApi } from '../../api'
import ConfirmDialog from '../ConfirmDialog.vue'

// 高风险操作二次确认状态（清空拦截日志需输入面板密码）
const confirm = ref({ show: false, action: null })

// ---- 页签定义 ----
const tabs = [
  { key: 'overview', label: '总览' },
  { key: 'freq', label: '频率' },
  { key: 'defense', label: '防御规则' },
  { key: 'custom', label: '自定义' },
  { key: 'other', label: '其他' },
  { key: 'bw', label: '黑白名单' },
  { key: 'map', label: '拦截地图' },
  { key: 'geo', label: '地区' },
  { key: 'acl', label: '自定义ACL' },
  { key: 'logs', label: '拦截日志' },
  { key: 'hall', label: '等候厅' },
]
const activeTab = ref(0)

const defenseItems = [
  { key: 'sql', label: 'SQL 注入' },
  { key: 'webshell', label: '一句话木马' },
  { key: 'directory', label: '目录过滤' },
  { key: 'xss', label: 'XSS' },
  { key: 'param', label: '参数规则' },
  { key: 'ua', label: 'User-Agent 规则' },
  { key: 'header', label: 'Header 规则' },
  { key: 'cookie', label: 'Cookie 规则' },
  { key: 'http', label: 'HTTP 规则' },
  { key: 'url', label: 'URL 规则' },
]

const bwGroups = [
  { key: 'ip_whitelist', label: 'IP 白名单', placeholder: 'IP / CIDR，如 1.2.3.4' },
  { key: 'ip_blacklist', label: 'IP 黑名单', placeholder: 'IP / CIDR' },
  { key: 'url_whitelist', label: 'URL 白名单', placeholder: '/path' },
  { key: 'url_blacklist', label: 'URL 黑名单', placeholder: '/path' },
  { key: 'ua_whitelist', label: 'User-Agent 白名单', placeholder: '浏览器 UA 片段' },
  { key: 'ua_blacklist', label: 'User-Agent 黑名单', placeholder: '浏览器 UA 片段' },
  { key: 'ip_groups', label: 'IP 组（IP/CIDR）', placeholder: 'IP / CIDR' },
]

// ---- 数据 ----
const sites = ref([])
const globalEnabled = ref(false)
const currentSite = ref('')
const cfg = ref(emptyCfg())
const showPreview = ref(false)
const previewText = ref('')

// 日志 / 地图
const logs = ref([])
const logFilter = reactive({ site: '', ip: '', action: '' })
const blockmap = reactive({ data: [], total: 0, days: 30 })
const blockDays = ref(30)

// 文本型编辑缓冲
const maliciousGroupsText = ref('')
const newBw = reactive({})
bwGroups.forEach(g => (newBw[g.key] = ''))
const newCountry = ref('')

// ACL 编辑器
const aclEditor = ref(null)

function emptyCfg() {
  return {
    site: '', enabled: false,
    frequency: {
      access: { mode: 'url', period: 60, count: 100, ban: 600 },
      attack: { mode: 'url', period: 60, count: 30, ban: 600 },
      notfound: { mode: 'url', period: 60, count: 10, ban: 600 },
    },
    defense: { sql: true, webshell: true, directory: true, xss: true, param: true,
      ua: true, header: true, cookie: true, http: true, url: true },
    custom: { upload_limit_mb: 20, cdn: false },
    other: { malicious_ip_groups: [], spider_pool: true },
    blackwhite: { ip_whitelist: [], ip_blacklist: [], url_whitelist: [], url_blacklist: [],
      ua_whitelist: [], ua_blacklist: [], ip_groups: [] },
    geo: { enabled: false, action: 'block', countries: [] },
    acl: [],
    waiting_hall: { enabled: false, url: '/waf_challenge' },
  }
}

function applyCfg(data) {
  const base = emptyCfg()
  cfg.value = mergeDeep(base, data)
  maliciousGroupsText.value = (data.other && data.other.malicious_ip_groups || []).join(' ')
}

function mergeDeep(target, src) {
  const out = Array.isArray(src) ? [...src] : { ...target }
  for (const k in src) {
    const s = src[k]
    if (s && typeof s === 'object' && !Array.isArray(s)) {
      out[k] = mergeDeep(out[k] || {}, s)
    } else {
      out[k] = s
    }
  }
  return out
}

// ---- 生命周期 ----
async function loadStatus() {
  try {
    const st = await wafApi.status()
    globalEnabled.value = !!st.enabled
  } catch (e) { /* 忽略 */ }
}

async function loadSites() {
  try {
    const r = await wafApi.sites()
    sites.value = r.sites || []
    if (!r.global_enabled) globalEnabled.value = false
  } catch (e) { /* 忽略 */ }
}

async function loadSite() {
  if (!currentSite.value) return
  try {
    const d = await wafApi.get(currentSite.value)
    applyCfg(d)
  } catch (e) { /* 忽略 */ }
}

function init() {
  loadStatus()
  loadSites()
}

onMounted(init)

// ---- 操作 ----
async function toggleGlobal() {
  try {
    const r = await wafApi.toggle(!globalEnabled.value)
    globalEnabled.value = !!r.enabled
  } catch (e) { /* 忽略 */ }
}

function syncOther() {
  cfg.value.other.malicious_ip_groups = maliciousGroupsText.value
    .split(/[\s,，]+/).map(s => s.trim()).filter(Boolean)
}

function buildBody() {
  syncOther()
  return JSON.parse(JSON.stringify(cfg.value))
}

async function save() {
  if (!currentSite.value) return
  try {
    const body = buildBody()
    // 保存前用预览校验渲染不抛错（捕获异常可反馈））
    const r = await wafApi.save(currentSite.value, body)
    if (r.saved) {
      loadSites()
      previewText.value = (r.write && r.write.path) || ''
    }
  } catch (e) { /* 忽略 */ }
}

async function applyAll() {
  try {
    const r = await wafApi.apply()
    if (r.written !== undefined) previewText.value = `已应用 ${r.written} 个站点，跳过 ${r.skipped}`
    else previewText.value = r.message || '完成'
    if (previewText.value) showPreview.value = true
  } catch (e) { /* 忽略 */ }
}

async function loadPreview() {
  if (!currentSite.value) return
  try {
    const r = await wafApi.preview(currentSite.value)
    previewText.value = r.content || ''
    showPreview.value = true
  } catch (e) { /* 忽略 */ }
}

async function loadLogs() {
  try {
    const r = await wafApi.logs({
      site: logFilter.site || undefined,
      ip: logFilter.ip || undefined,
      action: logFilter.action || undefined,
      limit: 300,
    })
    logs.value = r.logs || []
  } catch (e) { /* 忽略 */ }
}

// 清空拦截日志：高风险操作，先弹密码二次确认框
function clearLogs() {
  confirm.value = { show: true, action: 'clearLogs' }
}

// 面板密码校验通过后真正清空日志
async function doClearLogs() {
  confirm.value.show = false
  try {
    await wafApi.clearLogs()
    logs.value = []
  } catch (e) { /* 忽略 */ }
}

async function loadBlockmap() {
  try {
    const r = await wafApi.blockmap(blockDays.value)
    blockmap.data = r.data || []
    blockmap.total = r.total || 0
    blockmap.days = blockDays.value
  } catch (e) { /* 忽略 */ }
}

// ---- 黑白名单 ----
function addBw(key) {
  const v = newBw[key].trim()
  if (!v) return
  if (!cfg.value.blackwhite[key].includes(v)) cfg.value.blackwhite[key].push(v)
  newBw[key] = ''
}

function removeBw(key, v) {
  cfg.value.blackwhite[key] = cfg.value.blackwhite[key].filter(x => x !== v)
}

function addCountry() {
  const c = newCountry.value.trim().toUpperCase()
  if (!c) return
  if (!cfg.value.geo.countries.includes(c)) cfg.value.geo.countries.push(c)
  newCountry.value = ''
}

// ---- ACL ----
function openAclEditor(acl) {
  aclEditor.value = acl ? { ...acl } : { id: '', match: 'uri', op: 'eq', value: '', action: 'deny' }
}

function saveAcl() {
  if (!aclEditor.value.value) return
  if (aclEditor.value.id) {
    const idx = cfg.value.acl.findIndex(a => a.id === aclEditor.value.id)
    if (idx >= 0) cfg.value.acl[idx] = { ...aclEditor.value }
  } else {
    cfg.value.acl.push({ ...aclEditor.value, id: Date.now().toString(36) })
  }
  aclEditor.value = null
}

function delAcl(id) {
  cfg.value.acl = cfg.value.acl.filter(a => a.id !== id)
}

// ---- 展示辅助 ----
function enabledCount(obj) {
  return Object.values(obj).filter(Boolean).length
}

function barWidth(count) {
  if (!blockmap.total) return 0
  return Math.round((count / Math.max(1, blockmap.data[0].count)) * 100)
}

const matchLabels = { uri: 'URL', ip: 'IP', ua: 'User-Agent', args: '参数', method: '方法' }
function matchLabel(m) { return matchLabels[m] || m }
function aclActionLabel(a) { return { deny: '拒绝', allow: '放行', challenge: '挑战' }[a] || a }
function aclActionClass(a) {
  return a === 'deny' ? 'warn' : (a === 'allow' ? 'ok' : 'info')
}
</script>

<style scoped>
.waf-window { display: flex; flex-direction: column; height: 100%; padding: 0; gap: 6px; } /* 内嵌聚合窗口：外边距由父容器提供，避免上栏与父窗口边缘错位 */
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.toolbar-inline { display: flex; gap: 8px; margin-bottom: 6px; }
.sep { width: 1px; height: 20px; background: #d0d0d4; }

.site-select { width: 220px; padding: 4px 6px; }

.tabs { display: flex; gap: 2px; border-bottom: 1px solid #ddd; flex-wrap: wrap; }
.tab { padding: 6px 12px; background: transparent; border: none; border-bottom: 2px solid transparent;
  cursor: pointer; font-size: 13px; color: #555; }
.tab.active { color: #2563eb; border-bottom-color: #2563eb; font-weight: 600; }

.body { flex: 1; overflow: auto; display: flex; flex-direction: column; gap: 10px;
  padding: 10px 4px; }
.card { border: 1px solid #e4e4e7; border-radius: 8px; padding: 10px; }
h4 { margin: 8px 0 8px; font-size: 13px; }

.form-row { display: flex; align-items: center; gap: 10px; margin: 6px 0; flex-wrap: wrap; }
.form-row > span { width: 170px; font-size: 13px; }
.form-row input[type="number"], .form-row input[type="text"], .form-row input:not([type="checkbox"]),
.form-row select { padding: 4px 6px; width: 180px; }
.form-row input[type="checkbox"] { width: auto; }
.wide { width: 100% !important; padding: 6px; }

.hint { font-size: 12px; color: #888; }
.notice, .summary { font-size: 13px; color: #333; background: #f6f8ff; border: 1px solid #dbe4ff; border-radius: 8px; padding: 10px; }
.summary li { margin: 4px 0; }

.tag-row { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.tag { background: #eef; border-radius: 12px; padding: 2px 8px; font-size: 12px; display: inline-flex; align-items: center; gap: 4px; }
.tag .iconbtn { border: none; background: transparent; cursor: pointer; color: #888; }
.empty-tag { color: #aaa; font-size: 12px; }
.add-row { display: flex; gap: 6px; margin-top: 6px; }
.add-row input { flex: 1; padding: 4px 6px; }

.table-wrap { overflow: auto; }
.table { width: 100%; border-collapse: collapse; font-size: 12px; }
.table th, .table td { border: 1px solid #eee; padding: 5px 8px; text-align: left; }
.table th { background: #f7f7f8; }
.mono { font-family: monospace; font-size: 11px; }
.clamp { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { text-align: center; color: #aaa; padding: 16px; }

.badge { padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.badge.ok { background: #e6f6ec; color: #1a7f37; }
.badge.off { background: #fee; color: #c0392b; }
.badge.warn { background: #fef3e2; color: #b45309; }
.badge.info { background: #e9f0ff; color: #2563eb; }
.btn { padding: 4px 12px; border: 1px solid #ccc; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; }
.btn.primary { background: #2563eb; color: #fff; border-color: #2563eb; }
.btn.danger { background: #fff; color: #c0392b; border-color: #e2b3af; }
.btn.small { padding: 2px 10px; font-size: 12px; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.iconbtn { border: none; background: transparent; cursor: pointer; font-size: 12px; color: #555; }
.iconbtn.danger { color: #c0392b; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.modal { background: #fff; border-radius: 12px; padding: 18px; width: 420px; max-width: 92vw;
  max-height: 86vh; overflow: auto; box-shadow: 0 8px 30px rgba(0,0,0,.2); }
.modal.wide { width: 760px; }
.form { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.form label { font-size: 13px; color: #555; }
.form select, .form input { padding: 6px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }

.preview { background: #0f172a; color: #cbd5e1; padding: 12px; border-radius: 8px;
  font-size: 12px; white-space: pre-wrap; max-height: 60vh; overflow: auto; }

.bars { display: flex; flex-direction: column; gap: 6px; }
.bar-row { display: flex; align-items: center; gap: 10px; }
.bar-label { width: 120px; font-size: 13px; text-align: right; }
.bar-track { flex: 1; background: #eee; height: 16px; border-radius: 8px; overflow: hidden; }
.bar-fill { height: 100%; background: linear-gradient(90deg, #4f8cff, #2563eb); }
.bar-val { width: 60px; font-size: 12px; }
</style>