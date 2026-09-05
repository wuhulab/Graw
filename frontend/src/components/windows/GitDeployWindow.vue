<!--
  GitDeployWindow.vue — 站点 Git 自动部署窗口
  ==========================================================
  业务作用：
    为站点绑定 Git 仓库与分支，支持手动触发与 Git 平台 Webhook 自动部署
    （push 后自动 fetch + reset --hard 发布）。列表展示部署状态与最近运行，
    行内提供「手动部署 / 编辑 / 复制 Webhook URL / 删除」。
  后端模块：
    /api/gitdeploy 的 list / create / update / delete / trigger（Webhook
    由 Git 平台直接调用 `/api/gitdeploy/webhook/{id}`，前端仅展示地址）。
  关键状态：
    tab/结构对齐其它应用：toolbar（状态提示）+ 表格 + modal 表单。
  打开方式：桌面「Git 部署」入口（管理员，local）。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 顶部：全局状态提示 + 刷新 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="hint">共 {{ deploys.length }} 个部署 · Git push 后自动拉取发布</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn" :disabled="loading" @click="load">
          <RefreshCw :size="14" /> {{ $t('common.refresh') }}
        </button>
      </div>
    </div>

    <!-- 主表单（表格）：部署列表明细 -->
    <div class="table-toolbar">
      <span class="hint">选择站点、绑定仓库，保存后把 Webhook 地址配到 Git 平台</span>
      <button class="btn primary" @click="openCreate">
        <Plus :size="14" /> {{ $t('gitdeploy.create') }}
      </button>
    </div>

    <div style="flex:1; overflow:auto; padding:0 12px 12px;">
      <div v-if="loading" class="table-wrap">
        <table>
          <tbody><tr><td colspan="7" class="empty">{{ $t('common.loading') }}</td></tr></tbody>
        </table>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{{ $t('gitdeploy.name') }}</th>
              <th>{{ $t('gitdeploy.site') }}</th>
              <th>{{ $t('gitdeploy.repoUrl') }}</th>
              <th>{{ $t('gitdeploy.deployDir') }}</th>
              <th>{{ $t('gitdeploy.node') }}</th>
              <th>{{ $t('common.status') }}</th>
              <th>{{ $t('gitdeploy.lastRun') }}</th>
              <th>{{ $t('common.action') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in deploys" :key="d.id">
              <td>
                {{ d.name }}
                <div class="sub" :title="d.id">{{ d.repo_url }}</div>
              </td>
              <td><span class="site-tag">{{ d.site_name || d.site_id }}</span></td>
              <td class="mono">
                <span class="branch-tag">{{ d.branch }}</span>
                <span v-if="d.auth !== 'none'" class="auth-tag">{{ authText(d.auth) }}</span>
              </td>
              <td class="mono" :title="d.deploy_dir">{{ d.deploy_dir }}</td>
              <td>{{ d.node_id }}</td>
              <td>
                <span class="badge" :class="statusClass(d.status)">{{ statusText(d.status) }}</span>
              </td>
              <td class="mono">
                <template v-if="d.last_run && d.last_run.at">
                  {{ d.last_run.at }}
                  <div v-if="d.last_run.ok && d.last_run.rev" class="sub ok-text">rev {{ d.last_run.rev }}</div>
                  <div v-else-if="d.last_run.error" class="sub err-text" :title="d.last_run.error">{{ $t('gitdeploy.lastFail') }}</div>
                </template>
                <span v-else>—</span>
              </td>
              <td class="actions">
                <button class="iconbtn" :title="$t('gitdeploy.trigger')" :disabled="d.status === 'running'" @click="doTrigger(d)"><Play :size="14" /></button>
                <button class="iconbtn" :title="$t('common.edit')" @click="openEdit(d)"><Pencil :size="14" /></button>
                <button class="iconbtn" :title="$t('gitdeploy.copyWebhook')" @click="copyWebhook(d)"><Link2 :size="14" /></button>
                <button class="iconbtn danger" :title="$t('common.delete')" @click="doDelete(d)"><Trash2 :size="14" /></button>
              </td>
            </tr>
            <tr v-if="deploys.length === 0">
              <td colspan="8" class="empty">{{ $t('gitdeploy.empty') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 新建 / 编辑弹窗 -->
    <div v-if="editing" class="modal-overlay" @click.self="editing = null">
      <div class="modal">
        <h3>{{ editing.isNew ? $t('gitdeploy.create') : $t('gitdeploy.edit') }}</h3>
        <div class="form">
          <label>{{ $t('gitdeploy.name') }}</label>
          <input v-model="editing.name" :placeholder="$t('gitdeploy.namePlaceholder')" />

          <label>{{ $t('gitdeploy.site') }}</label>
          <select v-model="editing.site_id" :disabled="!editing.isNew" @change="onSiteChange">
            <option v-for="s in sites" :key="s.id" :value="s.id">{{ s.name }}（{{ firstDomain(s) }}）</option>
          </select>

          <label>{{ $t('gitdeploy.repoUrl') }}</label>
          <input v-model="editing.repo_url" type="text" placeholder="https://github.com/user/repo.git" />

          <div class="form-row">
            <div>
              <label>{{ $t('gitdeploy.branch') }}</label>
              <input v-model="editing.branch" type="text" placeholder="main" />
            </div>
            <div>
              <label>{{ $t('gitdeploy.auth') }}</label>
              <select v-model="editing.auth">
                <option value="none">{{ $t('gitdeploy.authNone') }}</option>
                <option value="token">{{ $t('gitdeploy.authToken') }}</option>
                <option value="ssh">{{ $t('gitdeploy.authSsh') }}</option>
              </select>
            </div>
          </div>

          <div v-if="editing.auth === 'token'">
            <label>{{ $t('gitdeploy.token') }}</label>
            <input v-model="editing.token" type="password" :placeholder="$t('gitdeploy.tokenPlaceholder')" />
          </div>
          <div v-else-if="editing.auth === 'ssh'" class="sub">{{ $t('gitdeploy.sshHint') }}</div>

          <label>{{ $t('gitdeploy.deployDir') }}</label>
          <input v-model="editing.deploy_dir" type="text" :placeholder="$t('gitdeploy.deployDirPlaceholder')" />

          <div class="form-row">
            <div>
              <label>{{ $t('gitdeploy.node') }}</label>
              <select v-model="editing.node_id">
                <option v-for="n in nodes.list" :key="n.id" :value="n.id">{{ n.name }}</option>
              </select>
            </div>
            <div class="check-line">
              <input id="gd-notify" v-model="editing.notify" type="checkbox" style="width:auto;" />
              <label for="gd-notify">{{ $t('gitdeploy.notify') }}</label>
            </div>
          </div>

          <!-- 创建 / 重置 secret 后展示 Webhook 地址 -->
          <div v-if="editing.webhookUrl" class="webhook-box">
            <div class="sub">{{ $t('gitdeploy.webhookUrl') }}</div>
            <code>{{ editing.webhookUrl }}</code>
            <div class="actions" style="margin-top:6px;">
              <button class="btn mini" @click="copyText(editing.webhookUrl)">{{ $t('common.copy') }}</button>
            </div>
          </div>

          <div class="actions">
            <button class="btn" @click="editing = null">{{ $t('common.cancel') }}</button>
            <button class="btn primary" :disabled="saving" @click="save">{{ saving ? $t('common.loading') : $t('common.save') }}</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'                       // 响应式 + 挂载刷新
import { useI18n } from 'vue-i18n'                         // 国际化
import { RefreshCw, Plus, Play, Pencil, Link2, Trash2 } from 'lucide-vue-next' // 图标（与其它应用一致的行内图标按钮）
import { gitdeployApi, sitesApi } from '../../api'         // 部署 + 站点接口
import { nodes, refreshNodes } from '../../store/nodes'    // 节点列表（选择部署目标节点）

const { t } = useI18n()
const deploys = ref([])       // 部署列表（脱敏）
const sites = ref([])         // 站点下拉数据
const editing = ref(null)     // 弹窗编辑对象；null=关闭
const loading = ref(false)
const saving = ref(false)

function statusText(s) {
  if (s === 'running') return t('gitdeploy.st.running')
  if (s === 'success') return t('gitdeploy.st.success')
  if (s === 'failed') return t('gitdeploy.st.failed')
  return t('gitdeploy.st.idle')
}
function statusClass(s) {
  return s === 'running' ? 'warn' : (s === 'success' ? 'ok' : (s === 'failed' ? 'danger' : 'off'))
}
function authText(a) {
  return a === 'token' ? 'Token' : (a === 'ssh' ? 'SSH' : '')
}
function firstDomain(s) {
  return (s.domains && s.domains[0]) || s.id
}

async function load() {
  loading.value = true
  try {
    const res = await gitdeployApi.list()
    deploys.value = res.deploys || []
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = {
    isNew: true, name: '', site_id: sites.value[0]?.id || '', repo_url: '',
    branch: 'main', auth: 'none', token: '', deploy_dir: '',
    node_id: nodes.list?.[0]?.id || 'local', notify: true, webhookUrl: ''
  }
}

function openEdit(d) {
  editing.value = {
    isNew: false, id: d.id, name: d.name, site_id: d.site_id, repo_url: d.repo_url,
    branch: d.branch, auth: d.auth, token: '', deploy_dir: d.deploy_dir,
    node_id: d.node_id, notify: d.notify === undefined ? true : d.notify,
    webhookUrl: ''
  }
}

function onSiteChange() {
  // 站点选中后自动带出默认部署目录（站点 root），用户可改
  if (!editing.value.site_id) return
  const s = sites.value.find(x => x.id === editing.value.site_id)
  if (s && s.root && editing.value.isNew && !editing.value.deploy_dir.trim()) {
    editing.value.deploy_dir = s.root
  }
}

async function save() {
  const e = editing.value
  if (!e.name && e.isNew) { alert(t('gitdeploy.needName')); return }
  if (!e.repo_url.trim()) { alert(t('gitdeploy.needRepo')); return }
  saving.value = true
  try {
    if (e.isNew) {
      const res = await gitdeployApi.create({
        name: e.name, site_id: e.site_id,
        source: { repo_url: e.repo_url.trim(), branch: e.branch.trim() || 'main', auth: e.auth, token: e.token || '' },
        deploy_dir: e.deploy_dir, node_id: e.node_id, notify: e.notify
      })
      showWebhook(res) // 创建成功：展示一次性 Webhook URL
    } else {
      const res = await gitdeployApi.update(e.id, {
        name: e.name, repo_url: e.repo_url.trim(), branch: e.branch.trim() || 'main',
        auth: e.auth, token: e.token || undefined, deploy_dir: e.deploy_dir,
        node_id: e.node_id, notify: e.notify
      })
      if (res.secret_once) showWebhook(res) // 重置 secret 后重新展示
      else editing.value = null
    }
  } catch (err) {
    alert(err?.response?.data?.detail || String(err))
  } finally {
    saving.value = false
    await load()
  }
}

// 创建/重置成功后：在弹窗内展示一次性 Webhook URL（含 secret）
function showWebhook(res) {
  const base = location.origin || ''
  editing.value.webhookUrl = `${base}/api/gitdeploy/webhook/${res.id}?secret=${res.secret_once || ''}`
}

async function doTrigger(d) {
  if (!confirm(t('gitdeploy.triggerConfirm', { name: d.name }))) return
  try {
    const res = await gitdeployApi.trigger(d.id)
    if (res.ok) alert(t('gitdeploy.triggerOk'))
    else alert(res?.error || t('gitdeploy.triggerFail'))
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
  await load()
}

function copyWebhook(d) {
  // 复制的 URL 不带 secret（secret 仅在创建/重置后可见），提示用户
  const base = location.origin || ''
  copyText(`${base}/api/gitdeploy/webhook/${d.id}`)
  alert(t('gitdeploy.webhookMissingSecret'))
}

function copyText(text) {
  try {
    navigator.clipboard.writeText(text)
  } catch (e) {
    // 剪贴板不可用时降级
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
}

async function doDelete(d) {
  if (!confirm(t('gitdeploy.deleteConfirm', { name: d.name }))) return
  try {
    await gitdeployApi.remove(d.id)
    await load()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

onMounted(async () => {
  await Promise.all([refreshNodes(), load()])
  try {
    const res = await sitesApi.list()
    sites.value = res.sites || []
  } catch (e) { /* 站点加载失败仅影响下拉 */ }
})
</script>

<style scoped>
/* 与 BackupWindow / CronWindow 一致的表格、徽标、弹窗与表单样式 */
.global-status { display: flex; align-items: center; gap: 10px; font-size: 12px; color: #5a6478; }
.toolbar-actions { margin-left: auto; display: flex; gap: 8px; }
.table-toolbar { display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; }
.table-toolbar .hint { font-size: 11px; color: #8a94a6; }
.table-wrap { background: #fff; border: 1px solid #e4e7f0; border-radius: 8px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
thead th { text-align: left; padding: 9px 12px; background: #f7f8fb; color: #4b5563; font-size: 12px; font-weight: 600; border-bottom: 1px solid #eef0f6; white-space: nowrap; }
tbody td { padding: 9px 12px; border-bottom: 1px solid #f3f4f8; vertical-align: top; }
tbody tr:last-child td { border-bottom: none; }
.sub { font-size: 11px; color: #9aa3b2; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 300px; margin-top: 2px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.site-tag { display: inline-block; background: #eef2ff; color: #4338ca; border-radius: 6px; padding: 1px 8px; font-size: 12px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.branch-tag { display: inline-block; background: #f0f3fa; color: #3b5478; border-radius: 6px; padding: 1px 8px; font-size: 11px; margin-right: 4px; }
.auth-tag { display: inline-block; background: #fef3c7; color: #92400e; border-radius: 6px; padding: 1px 6px; font-size: 10px; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.warn { background: #fef3c7; color: #92400e; }
.badge.danger { background: #fee2e2; color: #b91c1c; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.ok-text { color: #065f46; }
.err-text { color: #b91c1c; }
.actions { white-space: nowrap; }
.iconbtn { border: none; background: none; cursor: pointer; color: #6b7280; padding: 4px; border-radius: 6px; }
.iconbtn:hover { background: #f0f2f7; color: #0a3d7a; }
.iconbtn.danger:hover { background: #fee2e2; color: #b91c1c; }
.empty { text-align: center; color: #9aa3b2; padding: 30px; font-size: 12px; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 520px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,.15); }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 4px; }
.form label { font-size: 12px; color: #4b5563; font-weight: 600; margin-top: 8px; }
.form input[type='text'], .form input[type='password'], .form select, .form textarea {
  font-size: 13px; border: 1px solid #d7dbe7; border-radius: 6px; padding: 6px 8px; outline: none; width: 100%; box-sizing: border-box;
}
.form input:focus, .form select:focus { border-color: #3b82f6; }
.form-row { display: flex; gap: 10px; }
.form-row > div { flex: 1; display: flex; flex-direction: column; }
.check-line { flex-direction: row !important; align-items: center; gap: 8px; margin-top: 24px; }
.check-line label { margin-top: 0; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.webhook-box { margin-top: 10px; padding: 8px 10px; background: #f7f8fb; border: 1px dashed #b7c5dd; border-radius: 6px; }
.webhook-box code { font-size: 11px; word-break: break-all; color: #0a3d7a; }
/* 行内小按钮（BackupWindow 同款） */
.btn.mini { padding: 2px 8px; font-size: 11px; }
</style>