<!--
  GitDeployFormWindow.vue — 站点 Git 自动部署绑定表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 GitDeployWindow 的「新建 / 编辑部署绑定」 modal 弹窗独立成桌面窗口，
    避免点击灰色遮罩误关导致已填内容丢失。
    支持选择站点、绑定仓库/分支/鉴权方式、部署目录与目标节点，
    新建或重置 secret 后在本窗口内展示一次性 Webhook URL 供复制。
  后端模块：
    /api/gitdeploy 的 create / update（继承站点列表 /api/sites、节点 store）。
  关键状态：
    form    绑定额定表单对象（新建为空模板，编辑从 props.binding 回填）
    sites   站点下拉数据（选择站点后自动带出默认部署目录）
    error   后端校验错误信息（保存失败回显，保留用户已填内容）
    saving  保存中（禁用按钮防重复提交）
  打开方式：
    由 App.vue 的 openGitDeployForm(payload) 打开，props 传入 { binding }。
    保存成功后 bumpForm('gitdeploy') 通知父窗口刷新；新建 / 重置 secret 后
    需展示 Webhook URL，故窗口保持打开（用户复制后手动关闭），
    普通编辑则直接 emit('close') 自关。
-->
<template>
  <div class="gitdeploy-form-window">
    <!-- 后端校验错误回显（顶部留出错误框，不清空用户输入） -->
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="ui-field">
      <span class="ui-label">{{ $t('gitdeploy.name') }}</span>
      <input class="ui-input" v-model.trim="form.name" :placeholder="$t('gitdeploy.namePlaceholder')" />
    </div>

    <div class="ui-field">
      <span class="ui-label">{{ $t('gitdeploy.site') }}</span>
      <select class="ui-select" v-model="form.site_id" :disabled="!form.isNew" @change="onSiteChange">
        <option v-for="s in sites" :key="s.id" :value="s.id">{{ s.name }}（{{ firstDomain(s) }}）</option>
      </select>
    </div>

    <div class="ui-field">
      <span class="ui-label">{{ $t('gitdeploy.repoUrl') }}</span>
      <input class="ui-input" v-model.trim="form.repo_url" type="text" placeholder="https://github.com/user/repo.git" />
    </div>

    <div class="form-grid">
      <div class="ui-field">
        <span class="ui-label">{{ $t('gitdeploy.branch') }}</span>
        <input class="ui-input" v-model.trim="form.branch" type="text" placeholder="main" />
      </div>
      <div class="ui-field">
        <span class="ui-label">{{ $t('gitdeploy.auth') }}</span>
        <select class="ui-select" v-model="form.auth">
          <option value="none">{{ $t('gitdeploy.authNone') }}</option>
          <option value="token">{{ $t('gitdeploy.authToken') }}</option>
          <option value="ssh">{{ $t('gitdeploy.authSsh') }}</option>
        </select>
      </div>
    </div>

    <div v-if="form.auth === 'token'" class="ui-field">
      <span class="ui-label">{{ $t('gitdeploy.token') }}</span>
      <input class="ui-input" v-model="form.token" type="password" :placeholder="$t('gitdeploy.tokenPlaceholder')" />
    </div>
    <div v-else-if="form.auth === 'ssh'" class="ui-hint">{{ $t('gitdeploy.sshHint') }}</div>

    <div class="ui-field">
      <span class="ui-label">{{ $t('gitdeploy.deployDir') }}</span>
      <input class="ui-input" v-model.trim="form.deploy_dir" type="text" :placeholder="$t('gitdeploy.deployDirPlaceholder')" />
    </div>

    <div class="form-grid">
      <div class="ui-field">
        <span class="ui-label">{{ $t('gitdeploy.node') }}</span>
        <select class="ui-select" v-model="form.node_id">
          <option v-for="n in nodes.list" :key="n.id" :value="n.id">{{ n.name }}</option>
        </select>
      </div>
      <div class="check-line">
        <input id="gd-notify" v-model="form.notify" type="checkbox" />
        <label for="gd-notify">{{ $t('gitdeploy.notify') }}</label>
      </div>
    </div>

    <!-- 创建 / 重置 secret 后展示一次性 Webhook URL -->
    <div v-if="form.webhookUrl" class="webhook-box">
      <div class="ui-hint">{{ $t('gitdeploy.webhookUrl') }}</div>
      <code>{{ form.webhookUrl }}</code>
      <div class="ui-actions">
        <button class="ui-btn mini" @click="copyText(form.webhookUrl)">{{ $t('common.copy') }}</button>
      </div>
    </div>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? $t('common.loading') : $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与生命周期钩子
import { ref, reactive, onMounted } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 部署 + 站点接口、节点全局状态
import { gitdeployApi, sitesApi } from '../../api'
import { nodes, refreshNodes } from '../../store/nodes'
// 表单保存信号：通知 GitDeployWindow 刷新部署列表
import { bumpForm } from '../../store/formBus'

const { t } = useI18n()

// binding: 编辑对象或 null（由 App.vue 打开窗口时传入；null 表示新建）
const props = defineProps({
  binding: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const sites = ref([])        // 站点下拉数据
const saving = ref(false)    // 保存中（禁用按钮防重复提交）
const error = ref('')        // 后端校验错误信息

// 表单对象：从 props.binding 回填（新建时 binding 为 null，取空模板）
const defaults = () => ({
  isNew: true, id: '', name: '', site_id: nodes.list?.[0]?.id || '', repo_url: '',
  branch: 'main', auth: 'none', token: '', deploy_dir: '',
  node_id: nodes.list?.[0]?.id || 'local', notify: true, webhookUrl: ''
})
const form = reactive(props.binding ? { ...defaults(), ...props.binding } : defaults())

// 站点下拉展示用的首个域名（无则显示站点 id）
function firstDomain(s) {
  return (s.domains && s.domains[0]) || s.id
}

// 站点选中后自动带出默认部署目录（站点 root），用户可改
function onSiteChange() {
  if (!form.site_id) return
  const s = sites.value.find(x => x.id === form.site_id)
  if (s && s.root && form.isNew && !form.deploy_dir.trim()) {
    form.deploy_dir = s.root
  }
}

// 创建/重置成功后：在本窗口内展示一次性 Webhook URL（含 secret）
function showWebhook(res) {
  const base = location.origin || ''
  form.webhookUrl = `${base}/api/gitdeploy/webhook/${res.id}?secret=${res.secret_once || ''}`
}

// --- 保存：按 isNew 调创建/更新接口，成功后通知父窗口刷新 ---
async function save() {
  error.value = ''
  if (form.isNew && !form.name) { error.value = t('gitdeploy.needName'); return }
  if (!form.repo_url.trim()) { error.value = t('gitdeploy.needRepo'); return }
  saving.value = true
  try {
    if (form.isNew) {
      const res = await gitdeployApi.create({
        name: form.name, site_id: form.site_id,
        source: { repo_url: form.repo_url.trim(), branch: form.branch.trim() || 'main', auth: form.auth, token: form.token || '' },
        deploy_dir: form.deploy_dir, node_id: form.node_id, notify: form.notify
      })
      // 创建成功：展示一次性 Webhook URL（窗口保持打开供复制，用户复制后手动关闭）
      showWebhook(res)
      bumpForm('gitdeploy')
    } else {
      const res = await gitdeployApi.update(form.id, {
        name: form.name, repo_url: form.repo_url.trim(), branch: form.branch.trim() || 'main',
        auth: form.auth, token: form.token || undefined, deploy_dir: form.deploy_dir,
        node_id: form.node_id, notify: form.notify
      })
      bumpForm('gitdeploy')
      if (res.secret_once) showWebhook(res)   // 重置 secret 后重新展示，保持窗口打开
      else emit('close')                       // 普通编辑则自关
    }
  } catch (e) {
    // 后端校验失败：把 detail 回显在表单顶部，保留用户已填内容
    error.value = e?.response?.data?.detail || String(e)
  } finally {
    saving.value = false
  }
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

onMounted(async () => {
  await refreshNodes()                 // 确保节点列表就绪（供目标节点下拉）
  try {
    const res = await sitesApi.list()
    sites.value = res.sites || []
  } catch (e) {
    /* 站点加载失败仅影响下拉 */
  }
})
</script>

<style scoped>
.gitdeploy-form-window { padding: 14px; display: flex; flex-direction: column; gap: 2px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.check-line { display: flex; align-items: center; gap: 8px; margin-top: 22px; }
.check-line label { font-size: 13px; color: #374151; cursor: pointer; }
.webhook-box { margin-top: 10px; padding: 8px 10px; background: #f7f8fb; border: 1px dashed #b7c5dd; border-radius: 6px; }
.webhook-box code { font-size: 11px; word-break: break-all; color: #0a3d7a; }
.error-box {
  color: #b91c1c;
  font-size: 12.5px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
  word-break: break-all;
}
</style>