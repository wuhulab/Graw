<!--
  站点 Git 自动部署窗口（GitDeployWindow）
  业务：为站点绑定 Git 仓库与分支，支持手动触发与 Git 平台 Webhook 自动部署
        （push 后自动 fetch + reset --hard）。记录部署状态，可复制 webhook URL。
  后端模块：gitdeployApi（CRUD + trigger）；webhook 地址由前端拼接
  关键状态：deploys（部署列表）、editing（弹窗编辑对象）、secretOnce（一次性密钥）
  打开方式：桌面「Git 部署」入口（管理员，local）
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <button class="btn" @click="load">{{ $t('common.refresh') }}</button>
      <button class="btn primary" @click="openCreate">{{ $t('gitdeploy.create') }}</button>
      <span v-if="loading" style="margin-left:auto;color:#888;">{{ $t('common.loading') }}</span>
      <span v-else style="margin-left:auto;color:#888;">{{ $t('gitdeploy.count', { count: deploys.length }) }}</span>
    </div>

    <div style="flex:1; overflow:auto; padding: 0 12px 12px;">
      <div v-if="deploys.length === 0 && !loading" style="text-align:center;color:#999;padding:40px;font-size:12px;">
        {{ $t('gitdeploy.empty') }}
      </div>
      <div v-for="d in deploys" :key="d.id" class="row">
        <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
          <b style="font-size:13px;">{{ d.name }}</b>
          <span class="badge">{{ d.site_name || d.site_id }}</span>
          <span class="branch">@{{ d.branch }}</span>
          <span class="status" :class="d.status">{{ statusText(d.status) }}</span>
          <template v-if="d.last_run && d.last_run.at">
            <span style="font-size:11px;color:#888;">
              {{ $t('gitdeploy.lastRun') }} {{ d.last_run.at }}
              <template v-if="d.last_run.ok && d.last_run.rev"> · rev {{ d.last_run.rev }}</template>
            </span>
          </template>
        </div>
        <div style="font-size:11px; color:#888; margin-top:4px;">
          <span style="font-family:Consolas,monospace;">{{ d.repo_url }}</span>
          <span style="margin-left:10px;">→ {{ d.deploy_dir }}（节点 {{ d.node_id }}）</span>
        </div>
        <div class="row-actions">
          <button class="btn" @click="openEdit(d)">{{ $t('common.edit') }}</button>
          <button class="btn" @click="doTrigger(d)">{{ $t('gitdeploy.trigger') }}</button>
          <button class="btn" @click="copyWebhook(d)">{{ $t('gitdeploy.copyWebhook') }}</button>
          <button class="btn danger" @click="doDelete(d)">{{ $t('common.delete') }}</button>
        </div>
      </div>
    </div>

    <!-- 编辑/创建弹窗 -->
    <div v-if="editing" class="modal-mask" @click.self="editing = null">
      <div class="modal">
        <h3 style="margin-top:0;">{{ editing.isNew ? $t('gitdeploy.create') : $t('gitdeploy.edit') }}</h3>
        <label class="fld">
          <span>{{ $t('gitdeploy.name') }}</span>
          <input v-model="editing.name" type="text" />
        </label>
        <label class="fld">
          <span>{{ $t('gitdeploy.site') }}</span>
          <select v-model="editing.site_id" :disabled="!editing.isNew" @change="onSiteChange">
            <option v-for="s in sites" :key="s.id" :value="s.id">{{ s.name }}（{{ s.domains && s.domains[0] || s.id }}）</option>
          </select>
        </label>
        <label class="fld">
          <span>{{ $t('gitdeploy.repoUrl') }}</span>
          <input v-model="editing.repo_url" type="text" placeholder="https://github.com/user/repo.git" />
        </label>
        <label class="fld">
          <span>{{ $t('gitdeploy.branch') }}</span>
          <input v-model="editing.branch" type="text" placeholder="main" />
        </label>
        <label class="fld">
          <span>{{ $t('gitdeploy.auth') }}</span>
          <select v-model="editing.auth">
            <option value="none">{{ $t('gitdeploy.authNone') }}</option>
            <option value="token">{{ $t('gitdeploy.authToken') }}</option>
            <option value="ssh">{{ $t('gitdeploy.authSsh') }}</option>
          </select>
        </label>
        <label v-if="editing.auth === 'token'" class="fld">
          <span>{{ $t('gitdeploy.token') }}</span>
          <input v-model="editing.token" type="password" :placeholder="$t('gitdeploy.tokenPlaceholder')" />
        </label>
        <label v-if="editing.auth === 'ssh'" class="fld">
          <span></span>
          <div style="font-size:11px; color:#888;">{{ $t('gitdeploy.sshHint') }}</div>
        </label>
        <label class="fld">
          <span>{{ $t('gitdeploy.deployDir') }}</span>
          <input v-model="editing.deploy_dir" type="text" :placeholder="$t('gitdeploy.deployDirPlaceholder')" />
        </label>
        <label class="fld">
          <span>{{ $t('gitdeploy.node') }}</span>
          <select v-model="editing.node_id">
            <option v-for="n in nodes.list" :key="n.id" :value="n.id">{{ n.name }}</option>
          </select>
        </label>
        <label class="fld" style="flex-direction:row; align-items:center; gap:8px;">
          <input v-model="editing.notify" type="checkbox" style="width:auto;" />
          <span style="width:auto;">{{ $t('gitdeploy.notify') }}</span>
        </label>

        <!-- 创建/重置后的 secret → webhook URL 展示 -->
        <div v-if="editing.webhookUrl" class="webhook-box">
          <div style="font-size:11px; color:#0a3d7a; font-weight:600;">{{ $t('gitdeploy.webhookUrl') }}</div>
          <code style="word-break:break-all; font-size:11px;">{{ editing.webhookUrl }}</code>
          <button class="btn" style="margin-top:4px;" @click="copyText(editing.webhookUrl)">{{ $t('common.copy') }}</button>
        </div>

        <div class="modal-actions">
          <button class="btn" @click="editing = null">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="saving" @click="save">{{ saving ? $t('common.loading') : $t('common.save') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'                       // 响应式 + 挂载时刷新
import { useI18n } from 'vue-i18n'                          // 国际化
import { gitdeployApi, sitesApi } from '../../api'          // 部署 + 站点接口
import { nodes, refreshNodes } from '../../store/nodes'     // 节点列表（选择部署目标节点）

const { t } = useI18n()
const deploys = ref([])       // 部署列表（脱敏）
const sites = ref([])         // 站点下拉数据
const editing = ref(null)     // 弹窗编辑对象；null = 关闭
const loading = ref(false)
const saving = ref(false)

function statusText(s) {
  return (s === 'running') ? t('gitdeploy.st.running') :
         (s === 'success' ? t('gitdeploy.st.success') :
         (s === 'failed' ? t('gitdeploy.st.failed') : t('gitdeploy.st.idle')))
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
    node_id: nodes.list?.[0]?.id || 'local', notify: true, webhookUrl: '', secretOnce: ''
  }
}

function openEdit(d) {
  // 站点下拉需已有；编辑时可改除站点外的字段（站点变更会切线，保持简单）
  editing.value = {
    isNew: false, id: d.id, name: d.name, site_id: d.site_id, repo_url: d.repo_url,
    branch: d.branch, auth: d.auth, token: '', deploy_dir: d.deploy_dir,
    node_id: d.node_id, notify: d.notify === undefined ? true : d.notify,
    webhookUrl: '', secretOnce: '', reset_secret: false
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
      const body = {
        name: e.name, site_id: e.site_id,
        source: { repo_url: e.repo_url.trim(), branch: e.branch.trim() || 'main', auth: e.auth, token: e.token || '' },
        deploy_dir: e.deploy_dir, node_id: e.node_id, notify: e.notify
      }
      const res = await gitdeployApi.create(body)
      const base = location.origin || ''
      e.webhookUrl = `${base}/api/gitdeploy/webhook/${res.id}?secret=${res.secret_once || ''}`
    } else {
      const body = {
        name: e.name, repo_url: e.repo_url.trim(), branch: e.branch.trim() || 'main',
        auth: e.auth, token: e.token || undefined, deploy_dir: e.deploy_dir,
        node_id: e.node_id, notify: e.notify
      }
      if (e.reset_secret) body.reset_secret = true
      const res = await gitdeployApi.update(e.id, body)
      if (res.secret_once) {
        const base = location.origin || ''
        e.webhookUrl = `${base}/api/gitdeploy/webhook/${res.id}?secret=${res.secret_once}`
      } else {
        editing.value = null
      }
    }
  } catch (err) {
    alert(err?.response?.data?.detail || String(err))
  } finally {
    saving.value = false
    await load()
  }
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
  // 复制的 URL 不带 secret（secret 需创建/重置后可见），提示用户
  const base = location.origin || ''
  const url = `${base}/api/gitdeploy/webhook/${d.id}`
  copyText(url)
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
.row { border: 1px solid #e4e7f0; border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; background: #fafbfe; }
.badge { background: #eaf1fb; color: #0a3d7a; font-size: 11px; padding: 1px 8px; border-radius: 10px; }
.branch { background: #f0f3fa; color: #555; font-size: 11px; padding: 1px 8px; border-radius: 10px; }
.status { font-size: 11px; padding: 1px 8px; border-radius: 10px; color: #fff; }
.status.running { background: #f39c12; }
.status.success { background: #27ae60; }
.status.failed { background: #c0392b; }
.status.idle { background: #7f8c8d; }
.row-actions { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex; align-items: center; justify-content: center; z-index: 9999; }
.modal { background: #fff; border-radius: 10px; padding: 18px; width: 480px; max-height: 86vh; overflow: auto; }
.fld { display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; }
.fld > span { font-size: 12px; color: #0a3d7a; font-weight: 600; }
.fld input, .fld select { font-size: 12px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.webhook-box { background: #f7f8fb; border: 1px dashed #b7c5dd; border-radius: 6px; padding: 8px 10px; margin-top: 6px; }
</style>