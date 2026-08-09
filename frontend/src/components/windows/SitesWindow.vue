<template>
  <div class="sites-window">
    <div class="toolbar">
      <button class="btn primary" @click="openCreate"><Plus :size="14" /> 创建站点</button>
      <span class="hint">Web服务器: {{ webServer || '无' }}</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>域名</th>
            <th>根目录</th>
            <th>端口</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sites" :key="s.id">
            <td>{{ s.name }}</td>
            <td>{{ (s.domains || []).join(', ') }}</td>
            <td class="mono">{{ s.root }}</td>
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
            <td colspan="6" class="empty">暂无站点</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Create / Edit Modal -->
    <div v-if="showModal" class="modal-overlay" @click.self="closeModal">
      <div class="modal">
        <h3>{{ editing ? '编辑站点' : '创建站点' }}</h3>
        <div class="form">
          <label>站点名称</label>
          <input v-model="form.name" :disabled="editing" placeholder="如: my-site" />
          <label>域名列表（逗号分隔）</label>
          <input v-model="domainsText" placeholder="如: example.com, www.example.com" />
          <label>根目录</label>
          <input v-model="form.root" placeholder="如: /var/www/html" />
          <label>端口</label>
          <input v-model.number="form.port" type="number" />
          <label>反向代理地址（可选）</label>
          <input v-model="form.reverse_proxy" placeholder="如: http://localhost:3000" />
          <div class=" actions">
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
import { ref, onMounted } from 'vue'
import { sitesApi } from '../../api'
import { Plus, Power, Settings, FileText, Trash2 } from 'lucide-vue-next'

const sites = ref([])
const webServer = ref('')
const showModal = ref(false)
const showConfig = ref(false)
const editing = ref(false)
const configText = ref('')
const configSite = ref(null)

const form = ref({ name: '', domains: [], root: '', port: 80, reverse_proxy: '' })
const domainsText = ref('')

async function load() {
  const data = await sitesApi.list()
  sites.value = data.sites || []
  webServer.value = data.web_server || ''
}

function openCreate() {
  editing.value = false
  form.value = { name: '', domains: [], root: '', port: 80, reverse_proxy: '' }
  domainsText.value = ''
  showModal.value = true
}

function openEdit(s) {
  editing.value = true
  form.value = {
    name: s.name,
    domains: [...(s.domains || [])],
    root: s.root,
    port: s.port,
    reverse_proxy: s.reverse_proxy || ''
  }
  domainsText.value = (s.domains || []).join(', ')
  showModal.value = true
}

async function saveSite() {
  const payload = {
    name: form.value.name,
    domains: domainsText.value.split(',').map(d => d.trim()).filter(Boolean),
    root: form.value.root,
    port: Number(form.value.port) || 80,
    reverse_proxy: form.value.reverse_proxy || ''
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
.actions { display: flex; gap: 4px; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; }
.iconbtn:hover { background: #f9fafb; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
.empty { text-align: center; color: #9ca3af; padding: 24px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 480px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal.wide { width: 720px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.form .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
.code { background: #f3f4f6; padding: 12px; border-radius: 8px; font-size: 12px; overflow: auto; max-height: 360px; white-space: pre-wrap; word-break: break-all; }
</style>
