<template>
  <div class="sites-window">
    <div class="toolbar">
      <button class="btn primary" @click="openTypePicker"><Plus :size="14" /> 添加</button>
      <span class="hint">Web服务器: {{ webServer || '无' }}</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>域名</th>
            <th>根目录 / 目标</th>
            <th>端口</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sites" :key="s.id">
            <td>{{ s.name }}</td>
            <td><span class="type-badge">{{ typeLabel(s.type) }}</span></td>
            <td>{{ displayServerName(s) }}</td>
            <td class="mono">{{ displayTarget(s) }}</td>
            <td>{{ s.port }}</td>
            <td>
              <span class="badge" :class="s.enabled ? 'ok' : 'off'">{{ s.enabled ? '已启用' : '已停用' }}</span>
              <span class="badge" :class="s.online ? 'ok' : 'warn'">{{ s.online ? '运行中' : '离线' }}</span>
            </td>
            <td class="actions">
              <button class="iconbtn" title="启用/停用" @click="toggleEnable(s)">
                <Power :size="14" />
              </button>
              <button class="iconbtn" title="配置" @click="openEdit(s)">
                <Settings :size="14" />
              </button>
              <button class="iconbtn" title="查看配置" @click="viewConfig(s)">
                <FileText :size="14" />
              </button>
              <button class="iconbtn danger" title="删除" @click="remove(s)">
                <Trash2 :size="14" />
              </button>
            </td>
          </tr>
          <tr v-if="sites.length === 0">
            <td colspan="7" class="empty">暂无站点</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 类型选择弹窗 -->
    <div v-if="showTypePicker" class="modal-overlay" @click.self="showTypePicker = false">
      <div class="modal type-modal">
        <h3>选择站点类型</h3>
        <div class="type-grid">
          <div
            v-for="t in typeItems"
            :key="t.value"
            class="type-card"
            :class="{ active: t.value === pickedType }"
            @click="pickedType = t.value"
          >
            <component :is="t.icon" :size="22" />
            <div class="t-name">{{ t.label }}</div>
            <div class="t-desc">{{ t.desc }}</div>
          </div>
        </div>
        <div class="actions">
          <button class="btn" @click="showTypePicker = false">取消</button>
          <button class="btn primary" :disabled="!pickedType" @click="confirmType">下一步</button>
        </div>
      </div>
    </div>

    <!-- 创建 / 编辑 Modal -->
    <div v-if="showModal" class="modal-overlay" @click.self="closeModal">
      <div class="modal">
        <h3>{{ editing ? '编辑：' + typeLabel(form.type) : '创建：' + typeLabel(form.type) }}</h3>
        <div class="form">
          <label>站点名称</label>
          <input v-model="form.name" :disabled="editing" placeholder="如: my-site" />

          <!-- 静态网址 -->
          <template v-if="form.type === 'static'">
            <label>域名列表（逗号分隔）</label>
            <input v-model="domainsText" placeholder="如: example.com, www.example.com" />
            <label>根目录</label>
            <input v-model="form.root" placeholder="如: /var/www/html" />
            <label>端口</label>
            <input v-model.number="form.port" type="number" />
          </template>

          <!-- 反向代理 -->
          <template v-else-if="form.type === 'proxy'">
            <label>域名列表（逗号分隔）</label>
            <input v-model="domainsText" placeholder="如: api.example.com" />
            <label>监听端口</label>
            <input v-model.number="form.port" type="number" />
            <label>目标后端地址</label>
            <input v-model="form.reverse_proxy" placeholder="如: http://localhost:3000" />
          </template>

          <!-- TCP/UDP 代理 -->
          <template v-else-if="form.type === 'tcpudp'">
            <label>协议</label>
            <div class="radio-row">
              <label class="radio"><input type="radio" value="tcp" v-model="form.protocol" /> TCP</label>
              <label class="radio"><input type="radio" value="udp" v-model="form.protocol" /> UDP</label>
            </div>
            <label>监听端口</label>
            <input v-model.number="form.port" type="number" />
            <label>上游地址（IP:端口）</label>
            <input v-model="form.upstream" placeholder="如: 127.0.0.1:3306" />
          </template>

          <!-- 子网站 -->
          <template v-else-if="form.type === 'subsite'">
            <label>子域名</label>
            <input v-model="form.subdomain" placeholder="如: blog" />
            <label>根域名</label>
            <input v-model="form.domain" placeholder="如: example.com" />
            <label>根目录</label>
            <input v-model="form.root" placeholder="如: /var/www/html/blog" />
            <label>端口</label>
            <input v-model.number="form.port" type="number" />
          </template>

          <div class="actions">
            <button class="btn" @click="closeModal">取消</button>
            <button class="btn primary" @click="saveSite">保存</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Config viewer -->
    <div v-if="showConfig" class="modal-overlay" @click.self="showConfig = false">
      <div class="modal wide">
        <h3>站点配置: {{ configSite?.name }}</h3>
        <pre class="code">{{ configText }}</pre>
        <div class="actions">
          <button class="btn" @click="showConfig = false">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, markRaw } from 'vue'
import { sitesApi } from '../../api'
import {
  Plus, Power, Settings, FileText, Trash2,
  Globe, Share2, Network, Layers
} from 'lucide-vue-next'

// 站点类型定义
const typeItems = [
  { value: 'static', label: '静态网址', desc: '托管静态文件', icon: markRaw(Globe) },
  { value: 'proxy', label: '反向代理', desc: '转发到后端Web服务', icon: markRaw(Share2) },
  { value: 'tcpudp', label: 'TCP/UDP代理', desc: '转发TCP/UDP流量', icon: markRaw(Network) },
  { value: 'subsite', label: '子网站', desc: '子域名绑定到根域名', icon: markRaw(Layers) }
]

function typeLabel(type) {
  const t = typeItems.find(i => i.value === type)
  return t ? t.label : type || '静态网址'
}

const sites = ref([])
const webServer = ref('')
const showTypePicker = ref(false)
const pickedType = ref('')
const showModal = ref(false)
const showConfig = ref(false)
const editing = ref(false)
const configText = ref('')
const configSite = ref(null)

const emptyForm = (type) => ({
  name: '',
  type,
  domains: [],
  root: '',
  port: type === 'tcpudp' ? 443 : 80,
  reverse_proxy: '',
  protocol: 'tcp',
  upstream: '',
  subdomain: '',
  domain: ''
})
const form = ref(emptyForm('static'))
const domainsText = ref('')

async function load() {
  const data = await sitesApi.list()
  sites.value = data.sites || []
  webServer.value = data.web_server || ''
}

// 展示 server_name / 域名
function displayServerName(s) {
  if (s.type === 'subsite') {
    const sub = (s.subdomain || '').trim()
    const domain = (s.domain || '').trim()
    if (sub && domain) return `${sub}.${domain}`
    if (domain) return `*.${domain}`
  }
  return (s.domains || []).join(', ')
}

// 展示根目录 / 目标地址
function displayTarget(s) {
  if (s.type === 'proxy') return s.reverse_proxy || '-'
  if (s.type === 'tcpudp') return s.upstream || '-'
  return s.root || '-'
}

function openTypePicker() {
  pickedType.value = ''
  showTypePicker.value = true
}

function confirmType() {
  if (!pickedType.value) return
  editing.value = false
  form.value = emptyForm(pickedType.value)
  domainsText.value = ''
  showTypePicker.value = false
  showModal.value = true
}

function openEdit(s) {
  editing.value = true
  form.value = {
    name: s.name,
    type: s.type || 'static',
    domains: [...(s.domains || [])],
    root: s.root || '',
    port: s.port ?? (s.type === 'tcpudp' ? 443 : 80),
    reverse_proxy: s.reverse_proxy || '',
    protocol: s.protocol || 'tcp',
    upstream: s.upstream || '',
    subdomain: s.subdomain || '',
    domain: s.domain || ''
  }
  domainsText.value = (s.domains || []).join(', ')
  showModal.value = true
}

async function saveSite() {
  const payload = {
    name: form.value.name,
    type: form.value.type,
    domains: domainsText.value.split(',').map(d => d.trim()).filter(Boolean),
    root: form.value.root,
    port: Number(form.value.port) || (form.value.type === 'tcpudp' ? 443 : 80),
    reverse_proxy: form.value.reverse_proxy || '',
    protocol: form.value.protocol || 'tcp',
    upstream: form.value.upstream || '',
    subdomain: form.value.subdomain || '',
    domain: form.value.domain || ''
  }
  if (editing.value) {
    const site = sites.value.find(s => s.name === form.value.name)
    if (site) await sitesApi.update(site.id, payload)
  } else {
    await sitesApi.create(payload)
  }
  showModal.value = false
  await load()
}

async function toggleEnable(s) {
  const action = s.enabled ? 'disable' : 'enable'
  await sitesApi.action(s.id, action)
  await load()
}

async function viewConfig(s) {
  const data = await sitesApi.config(s.id)
  configSite.value = data.site
  configText.value = data.config
  showConfig.value = true
}

async function remove(s) {
  if (!confirm(`确定删除站点 "${s.name}" 吗？`)) return
  await sitesApi.delete(s.id)
  await load()
}

function closeModal() {
  showModal.value = false
}

onMounted(load)
</script>

<style scoped>
.sites-window { padding: 10px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.hint { color: #6e6e73; font-size: 12px; }
.table-wrap { overflow: auto; max-height: 420px; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
th { background: #f9fafb; position: sticky; top: 0; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; margin-right: 4px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.warn { background: #fef3c7; color: #92400e; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.type-badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; background: #eef2ff; color: #4338ca; }
.actions { display: flex; gap: 4px; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; }
.iconbtn:hover { background: #f9fafb; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
.empty { text-align: center; color: #9ca3af; padding: 24px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 480px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal.wide { width: 720px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.form .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
.radio-row { display: flex; gap: 16px; }
.radio { display: flex; align-items: center; gap: 4px; font-size: 13px; color: #111827; cursor: pointer; }
.type-modal { width: 560px; }
.type-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 14px; }
.type-card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; cursor: pointer; transition: all 0.15s; }
.type-card:hover { border-color: #94a3b8; background: #f9fafb; }
.type-card.active { border-color: #111827; background: #f3f4f6; }
.t-name { font-size: 14px; font-weight: 600; margin-top: 8px; }
.t-desc { font-size: 12px; color: #6b7280; margin-top: 2px; }
.code { background: #f3f4f6; padding: 12px; border-radius: 8px; font-size: 12px; overflow: auto; max-height: 360px; white-space: pre-wrap; word-break: break-all; }
</style>