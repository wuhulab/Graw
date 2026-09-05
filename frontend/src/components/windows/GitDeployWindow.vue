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

    <!-- 新建 / 编辑表单改由独立窗口承载（GitDeployFormWindow），避免点遮罩误关丢失输入 -->
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'                  // 响应式 + 挂载刷新 + 表单信号监听
import { useI18n } from 'vue-i18n'                           // 国际化
import { RefreshCw, Plus, Play, Pencil, Link2, Trash2 } from 'lucide-vue-next' // 图标（与其它应用一致的行内图标按钮）
import { gitdeployApi, sitesApi } from '../../api'            // 部署 + 站点接口
import { nodes, refreshNodes } from '../../store/nodes'       // 节点列表（选择部署目标节点）
import { formBus } from '../../store/formBus'                 // 表单保存信号：独立表单窗口保存成功后刷新列表

const { t } = useI18n()

const emit = defineEmits(['openGitDeployForm'])   // 打开独立「新建/编辑部署绑定」窗口（binding: 表单对象）

const deploys = ref([])       // 部署列表（脱敏）
const sites = ref([])         // 站点下拉数据
const loading = ref(false)

// 表单新增/编辑改由独立窗口承载：保存成功后 bumpForm('gitdeploy') 触发此处重载
watch(() => formBus.gitdeploy, load)

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

// 打开独立「新建」表单窗口
function openCreate() {
  emit('openGitDeployForm', {
    binding: {
      isNew: true, name: '', site_id: sites.value[0]?.id || '', repo_url: '',
      branch: 'main', auth: 'none', token: '', deploy_dir: '',
      node_id: nodes.list?.[0]?.id || 'local', notify: true, webhookUrl: ''
    }
  })
}

// 打开独立「编辑」表单窗口
function openEdit(d) {
  emit('openGitDeployForm', {
    binding: {
      isNew: false, id: d.id, name: d.name, site_id: d.site_id, repo_url: d.repo_url,
      branch: d.branch, auth: d.auth, token: '', deploy_dir: d.deploy_dir,
      node_id: d.node_id, notify: d.notify === undefined ? true : d.notify,
      webhookUrl: ''
    }
  })
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
/* 与 BackupWindow / CronWindow 一致的表格、徽标样式 */
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
</style>