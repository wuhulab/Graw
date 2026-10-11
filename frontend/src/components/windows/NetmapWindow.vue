<!--
  内网映射窗口（NetmapWindow）
  业务：把「当前节点」的内网 HTTP 服务（如 http://localhost:3000）反代到面板侧，
        窗口内 iframe 直达访问，或新标签打开；支持 ?c=节点&s=服务地址 URL 直达。
  后端模块：netmapApi（list/create/update/remove/proxyUrl），数据按节点视角存储
        （远端节点经 Agent 隧道自动代理到子节点执行）。
  关键状态：items（映射条目）、view（list 列表 / preview 代理预览）、form（内联新建/编辑表单）
  打开方式：桌面「内网映射」入口（管理员）；App.vue 深链 ?s= 时带 autoService 自动填入。
-->
<template>
  <div class="nm-window">
    <!-- 列表视图 -->
    <template v-if="view === 'list'">
      <div class="ui-toolbar">
        <button class="ui-btn primary" @click="openForm()">{{ $t('nm.create') }}</button>
        <button class="ui-btn" @click="loadAll">{{ $t('common.refresh') }}</button>
        <span class="ui-hint right">{{ $t('nm.hint', { node: currentNodeName() }) }}</span>
      </div>

      <!-- 内联新建/编辑表单：提交前可随时收起，不丢其它窗口上下文 -->
      <div v-if="formOpen" class="ui-card nm-form">
        <div class="nm-form-title">{{ formId ? $t('nm.edit') : $t('nm.create') }}</div>
        <div class="nm-form-row">
          <label>{{ $t('nm.name') }}</label>
          <input v-model.trim="formName" class="ui-input" :placeholder="$t('nm.namePh')" maxlength="64" />
        </div>
        <div class="nm-form-row">
          <label>{{ $t('nm.url') }}</label>
          <input v-model.trim="formUrl" class="ui-input" :placeholder="$t('nm.urlPh')" maxlength="512" />
        </div>
        <div v-if="formError" class="nm-form-err">{{ formError }}</div>
        <div class="nm-form-actions">
          <button class="ui-btn primary" :disabled="saving" @click="submitForm">{{ $t('common.save') }}</button>
          <button class="ui-btn" @click="formOpen = false">{{ $t('common.cancel') }}</button>
          <span v-if="autoService" class="ui-hint">{{ $t('nm.autoFilled') }}</span>
        </div>
      </div>

      <div class="nm-body">
        <div v-if="items.length === 0 && !formOpen" class="ui-empty">{{ $t('nm.empty') }}</div>
        <div v-for="it in items" :key="it.id" class="ui-card nm-row">
          <div class="nm-row-head">
            <b style="font-size:13px;">{{ it.name || it.id }}</b>
            <span class="ui-mono nm-addr">{{ it.url }}</span>
          </div>
          <div class="nm-row-actions">
            <button class="ui-btn mini primary" @click="openPreview(it)">{{ $t('nm.open') }}</button>
            <button class="ui-btn mini" @click="openNewTab(it)">{{ $t('nm.newTab') }}</button>
            <button class="ui-btn mini" @click="openForm(it)">{{ $t('common.edit') }}</button>
            <button class="ui-btn mini danger" @click="doRemove(it)">{{ $t('common.delete') }}</button>
          </div>
        </div>
      </div>
    </template>

    <!-- 代理预览视图：iframe 加载反代地址（同源嵌入，?__graw_token= 鉴权） -->
    <template v-else>
      <div class="ui-toolbar">
        <button class="ui-btn" @click="backToList">{{ $t('nm.back') }}</button>
        <span class="nm-preview-title">{{ previewName }}</span>
        <span class="ui-mono nm-addr">{{ previewUrl }}</span>
        <button class="ui-btn mini right" @click="openNewTab(previewItem)">{{ $t('nm.newTab') }}</button>
      </div>
      <iframe
        v-if="previewItem"
        :key="previewItem.id"
        :src="proxySrc"
        class="nm-frame"
        :title="previewName"
        referrerpolicy="same-origin"
        allow="clipboard-read; clipboard-write"
      ></iframe>
      <div class="nm-ws-hint">{{ $t('nm.wsHint') }}</div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'                  // 组合式 API
import { useI18n } from 'vue-i18n'                              // 国际化
import { netmapApi } from '../../api'                           // 内网映射接口
import { auth } from '../../store/auth'                         // 登录态单例（代理地址要带 token）
import { currentNodeName } from '../../store/nodes'             // 当前节点名（工具栏提示）

const props = defineProps({
  // URL 深链 ?s=http://localhost:3000 直达：挂载时自动填表或直接打开匹配条目
  autoService: { type: String, default: '' },
})

const { t } = useI18n()

const items = ref([])          // 已配置条目（当前节点视角）
const view = ref('list')       // list 列表 / preview 代理预览
const formOpen = ref(false)    // 内联表单显隐
const formId = ref('')         // 空串=新建；否则为编辑条目 id
const formName = ref('')       // 表单：展示名
const formUrl = ref('')        // 表单：服务地址
const formError = ref('')      // 表单校验/接口错误提示
const saving = ref(false)      // 提交中防重复点击
const previewItem = ref(null)  // 预览中的条目

// 代理 iframe 地址：token 经 __graw_token= 传递（后端转发前剥离，不污染目标服务）
const proxySrc = computed(() =>
  previewItem.value ? netmapApi.proxyUrl(previewItem.value.id, auth.token) : ''
)
const previewName = computed(() => (previewItem.value && (previewItem.value.name || previewItem.value.id)) || '')
const previewUrl = computed(() => (previewItem.value && previewItem.value.url) || '')

// 加载条目列表（失败时提示后端 detail，与其它窗口一致）
async function loadAll() {
  try {
    const data = await netmapApi.list()
    items.value = data.items || []
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

// 打开表单：不传参=新建；传条目=编辑并回填
function openForm(it) {
  formId.value = it ? it.id : ''
  formName.value = it ? (it.name || '') : ''
  formUrl.value = it ? (it.url || '') : (props.autoService || '')
  formError.value = ''
  formOpen.value = true
}

// 提交表单：前端先做基本校验，再走后端（后端仍会完整校验，双保险）
async function submitForm() {
  formError.value = ''
  if (!formUrl.value) { formError.value = t('nm.errUrl'); return }
  if (!/^https?:\/\//i.test(formUrl.value)) { formError.value = t('nm.errScheme'); return }
  saving.value = true
  try {
    const body = { name: formName.value, url: formUrl.value }
    if (formId.value) {
      await netmapApi.update(formId.value, body)
    } else {
      const r = await netmapApi.create(body)
      // 来自深链的自动填表：创建成功后直接进入预览，一步到位
      if (props.autoService && r.item) {
        formOpen.value = false
        await loadAll()
        openPreview(r.item)
        return
      }
    }
    formOpen.value = false
    await loadAll()
  } catch (e) {
    formError.value = e?.response?.data?.detail || String(e)
  } finally {
    saving.value = false
  }
}

async function doRemove(it) {
  if (!confirm(t('nm.deleteConfirm', { name: it.name || it.id }))) return
  try {
    await netmapApi.remove(it.id)
    await loadAll()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

// 进入代理预览：iframe 加载反代地址
function openPreview(it) {
  previewItem.value = it
  view.value = 'preview'
}

function backToList() {
  view.value = 'list'
  previewItem.value = null
}

// 新标签打开：同样走代理地址（?__graw_token= 鉴权）
function openNewTab(it) {
  if (!it) return
  window.open(netmapApi.proxyUrl(it.id, auth.token), '_blank', 'noopener')
}

onMounted(async () => {
  await loadAll()
  // URL 深链直达：?s=<服务地址>。目标是「一步看到目标网站内容」：
  //   已有同地址条目 → 直接打开预览；
  //   没有 → 自动创建映射条目并立即预览（免手动点保存）；
  //   创建失败（如权限不足）→ 退回表单展示错误，不让用户面对空窗口。
  if (props.autoService) {
    const norm = (u) => (u || '').replace(/\/+$/, '')   // 忽略末尾斜杠差异
    const hit = items.value.find((x) => norm(x.url) === norm(props.autoService))
    if (hit) {
      openPreview(hit)
      return
    }
    try {
      const r = await netmapApi.create({ name: '', url: props.autoService })
      formOpen.value = false
      await loadAll()
      openPreview(r.item)
    } catch (e) {
      openForm()   // openForm 会把 autoService 填入地址框
      formError.value = e?.response?.data?.detail || String(e)
    }
  }
})
</script>

<style scoped>
.nm-window { display: flex; flex-direction: column; height: 100%; padding: 10px; box-sizing: border-box; }
.nm-body { flex: 1; overflow: auto; padding: 0 4px 12px; }
.nm-form { margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px; }
.nm-form-title { font-weight: 600; font-size: 13px; }
.nm-form-row { display: flex; align-items: center; gap: 8px; }
.nm-form-row label { width: 70px; flex: none; font-size: 12px; color: #455; }
.nm-form-row .ui-input { flex: 1; }
.nm-form-err { color: #c0392b; font-size: 12px; }
.nm-form-actions { display: flex; align-items: center; gap: 8px; }
.nm-row { margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px; }
.nm-row-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.nm-addr { font-size: 12px; color: #455; }
.nm-row-actions { display: flex; gap: 6px; }
.nm-preview-title { font-weight: 600; font-size: 13px; }
.nm-frame { flex: 1; width: 100%; border: 1px solid rgba(0,0,0,.12); border-radius: 6px; background: #fff; box-sizing: border-box; }
.nm-ws-hint { font-size: 11px; color: #98a2b3; padding-top: 4px; }
</style>
