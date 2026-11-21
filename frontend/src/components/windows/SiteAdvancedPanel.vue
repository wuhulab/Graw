<!--
  站点配置中心（SiteAdvancedPanel）

  在「网站」窗口内以页签形式展示单个站点的高级配置：
    默认文档 / 重定向 / 密码访问 + 限流 / 跨域 / 真实 IP / 负载均衡 / 站点日志 / 原始配置。
  数据由 /api/sites/{id}/advanced 读写；保存后后端会重新生成 nginx 配置并 reload。
  日志与原始配置分别走 /api/sites/{id}/logs 与 /api/sites/{id}/config(/raw)。

  这个组件不单独开窗口：SitesWindow 选中站点后把 advancedSite 传进来，
  保持「窗口内页签」的 1Panel 式体验，也避免再往 App.vue 里加窗口事件。
-->
<template>
  <div class="site-adv">
    <div class="adv-head">
      <button class="ui-btn ghost mini" @click="emit('close')"><ArrowLeft :size="14" /> {{ $t('siteAdv.back') }}</button>
      <div class="adv-title">
        <span class="name">{{ site.name }}</span>
        <span class="ui-badge" :class="site.enabled ? 'ok' : 'off'">{{ site.enabled ? $t('sites.enabled') : $t('sites.disabled') }}</span>
      </div>
      <button class="ui-btn primary mini" :disabled="busy" @click="save"><Save :size="14" /> {{ busy ? $t('common.saving') : $t('common.save') }}</button>
    </div>

    <div class="adv-tabs">
      <button v-for="tab in tabs" :key="tab.key" class="adv-tab" :class="{ active: view === tab.key }" @click="switchTab(tab.key)">
        {{ tab.label }}
      </button>
    </div>

    <div v-if="msg" class="ui-hint adv-msg" :class="{ err: msgErr }">{{ msg }}</div>

    <div class="adv-body">
      <!-- 基础：默认文档 + 站点日志开关 -->
      <section v-if="view === 'basic'" class="adv-section">
        <label class="ui-label">{{ $t('siteAdv.defaultDocs') }}</label>
        <input v-model="form.defaultDocsText" class="ui-input" :placeholder="$t('siteAdv.defaultDocsHint')" />
        <p class="ui-hint">{{ $t('siteAdv.defaultDocsHint') }}</p>
        <label class="switch-row">
          <input v-model="form.loggingEnabled" type="checkbox" />
          <span>{{ $t('siteAdv.siteLogging') }}</span>
        </label>
        <p class="ui-hint">{{ $t('siteAdv.siteLoggingHint') }}</p>
      </section>

      <!-- 重定向 -->
      <section v-else-if="view === 'redirect'" class="adv-section">
        <div class="sec-head">
          <span class="ui-hint">{{ $t('siteAdv.redirectHint') }}</span>
          <button class="ui-btn mini" @click="addRedirect"><Plus :size="13" /> {{ $t('siteAdv.addRedirect') }}</button>
        </div>
        <div class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('siteAdv.from') }}</th><th>{{ $t('siteAdv.to') }}</th><th>{{ $t('siteAdv.code') }}</th><th></th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in form.redirects" :key="i">
                <td><input v-model="r.from" class="ui-input mono" placeholder="/old" /></td>
                <td><input v-model="r.to" class="ui-input mono" placeholder="/new 或 https://..." /></td>
                <td>
                  <select v-model.number="r.code" class="ui-select">
                    <option :value="301">301</option><option :value="302">302</option>
                    <option :value="307">307</option><option :value="308">308</option>
                  </select>
                </td>
                <td><button class="ui-btn danger mini" @click="form.redirects.splice(i, 1)"><Trash2 :size="13" /></button></td>
              </tr>
              <tr v-if="!form.redirects.length"><td colspan="4" class="ui-empty">{{ $t('siteAdv.noRedirect') }}</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 访问限制：密码访问 + 限流 -->
      <section v-else-if="view === 'access'" class="adv-section">
        <h4>{{ $t('siteAdv.basicAuth') }}</h4>
        <label class="switch-row">
          <input v-model="form.basicEnabled" type="checkbox" />
          <span>{{ $t('siteAdv.basicAuthEnable') }}</span>
        </label>
        <div v-if="form.basicEnabled" class="grid-2">
          <div>
            <label class="ui-label">{{ $t('siteAdv.username') }}</label>
            <input v-model="form.basicUser" class="ui-input" autocomplete="off" />
          </div>
          <div>
            <label class="ui-label">{{ $t('siteAdv.password') }}</label>
            <input v-model="form.basicPassword" type="password" class="ui-input" autocomplete="new-password"
              :placeholder="form.passwordSet ? $t('siteAdv.passwordKeep') : ''" />
          </div>
        </div>

        <h4 class="mt">{{ $t('siteAdv.rateLimit') }}</h4>
        <label class="switch-row">
          <input v-model="form.rateEnabled" type="checkbox" />
          <span>{{ $t('siteAdv.rateLimitEnable') }}</span>
        </label>
        <div v-if="form.rateEnabled" class="grid-2">
          <div>
            <label class="ui-label">{{ $t('siteAdv.rps') }}</label>
            <input v-model.number="form.rps" type="number" min="1" max="10000" class="ui-input" />
          </div>
          <div>
            <label class="ui-label">{{ $t('siteAdv.burst') }}</label>
            <input v-model.number="form.burst" type="number" min="0" max="100000" class="ui-input" />
          </div>
        </div>
        <p class="ui-hint">{{ $t('siteAdv.rateLimitHint') }}</p>
      </section>

      <!-- 跨域 -->
      <section v-else-if="view === 'cors'" class="adv-section">
        <label class="switch-row">
          <input v-model="form.corsEnabled" type="checkbox" />
          <span>{{ $t('siteAdv.corsEnable') }}</span>
        </label>
        <div v-if="form.corsEnabled" class="stack">
          <div>
            <label class="ui-label">{{ $t('siteAdv.origins') }}</label>
            <input v-model="form.originsText" class="ui-input mono" placeholder="* 或 https://a.com, https://b.com" />
            <p class="ui-hint">{{ $t('siteAdv.originsHint') }}</p>
          </div>
          <div class="grid-2">
            <div>
              <label class="ui-label">{{ $t('siteAdv.methods') }}</label>
              <input v-model="form.methodsText" class="ui-input mono" placeholder="GET, POST, OPTIONS" />
            </div>
            <div>
              <label class="ui-label">{{ $t('siteAdv.headers') }}</label>
              <input v-model="form.headersText" class="ui-input mono" placeholder="Content-Type, Authorization" />
            </div>
          </div>
          <label class="switch-row">
            <input v-model="form.credentials" type="checkbox" />
            <span>{{ $t('siteAdv.credentials') }}</span>
          </label>
        </div>
      </section>

      <!-- 真实 IP -->
      <section v-else-if="view === 'realip'" class="adv-section">
        <label class="switch-row">
          <input v-model="form.realIpEnabled" type="checkbox" />
          <span>{{ $t('siteAdv.realIpEnable') }}</span>
        </label>
        <div v-if="form.realIpEnabled" class="stack">
          <div>
            <label class="ui-label">{{ $t('siteAdv.realIpHeader') }}</label>
            <select v-model="form.realIpHeader" class="ui-select">
              <option value="X-Forwarded-For">X-Forwarded-For</option>
              <option value="X-Real-IP">X-Real-IP</option>
              <option value="CF-Connecting-IP">CF-Connecting-IP</option>
            </select>
          </div>
          <div>
            <label class="ui-label">{{ $t('siteAdv.trusted') }}</label>
            <textarea v-model="form.trustedText" class="ui-textarea mono" rows="4"
              placeholder="127.0.0.1&#10;10.0.0.0/8"></textarea>
            <p class="ui-hint">{{ $t('siteAdv.trustedHint') }}</p>
          </div>
        </div>
      </section>

      <!-- 负载均衡 -->
      <section v-else-if="view === 'upstream'" class="adv-section">
        <div class="sec-head">
          <span class="ui-hint">{{ $t('siteAdv.upstreamHint') }}</span>
          <button class="ui-btn mini" @click="form.upstreams.push({ server: '', weight: 1 })"><Plus :size="13" /> {{ $t('siteAdv.addUpstream') }}</button>
        </div>
        <div class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('siteAdv.upstreamServer') }}</th><th>{{ $t('siteAdv.weight') }}</th><th></th></tr></thead>
            <tbody>
              <tr v-for="(u, i) in form.upstreams" :key="i">
                <td><input v-model="u.server" class="ui-input mono" placeholder="127.0.0.1:8080" /></td>
                <td><input v-model.number="u.weight" type="number" min="1" max="100" class="ui-input" /></td>
                <td><button class="ui-btn danger mini" @click="form.upstreams.splice(i, 1)"><Trash2 :size="13" /></button></td>
              </tr>
              <tr v-if="!form.upstreams.length"><td colspan="3" class="ui-empty">{{ $t('siteAdv.noUpstream') }}</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 站点日志 -->
      <section v-else-if="view === 'logs'" class="adv-section">
        <div class="sec-head">
          <select v-model="logType" class="ui-select" style="width:150px" @change="loadLogs">
            <option value="access">{{ $t('siteAdv.accessLog') }}</option>
            <option value="error">{{ $t('siteAdv.errorLog') }}</option>
          </select>
          <button class="ui-btn mini" @click="loadLogs"><RefreshCw :size="13" /> {{ $t('common.refresh') }}</button>
        </div>
        <pre class="adv-code">{{ logsText || $t('siteAdv.noLog') }}</pre>
      </section>

      <!-- 原始配置 -->
      <section v-else-if="view === 'raw'" class="adv-section">
        <div class="sec-head">
          <span class="ui-hint">{{ $t('siteAdv.rawHint') }}</span>
          <button class="ui-btn mini" @click="loadRaw"><RefreshCw :size="13" /> {{ $t('common.refresh') }}</button>
        </div>
        <textarea v-model="rawText" class="ui-textarea mono adv-raw" spellcheck="false"></textarea>
        <div class="ui-actions">
          <button class="ui-btn" @click="loadRaw">{{ $t('common.reset') }}</button>
          <button class="ui-btn primary" :disabled="busy" @click="saveRaw">{{ $t('siteAdv.saveRaw') }}</button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { sitesApi } from '../../api'
import { ArrowLeft, Save, Plus, Trash2, RefreshCw } from 'lucide-vue-next'

const props = defineProps({ site: { type: Object, required: true } })
const emit = defineEmits(['close', 'saved'])
const { t } = useI18n()

const view = ref('basic')
const busy = ref(false)
const msg = ref('')
const msgErr = ref(false)

const tabs = [
  { key: 'basic', label: t('siteAdv.tabBasic') },
  { key: 'redirect', label: t('siteAdv.tabRedirect') },
  { key: 'access', label: t('siteAdv.tabAccess') },
  { key: 'cors', label: t('siteAdv.tabCors') },
  { key: 'realip', label: t('siteAdv.tabRealIp') },
  { key: 'upstream', label: t('siteAdv.tabUpstream') },
  { key: 'logs', label: t('siteAdv.tabLogs') },
  { key: 'raw', label: t('siteAdv.tabRaw') },
]

const form = reactive({
  defaultDocsText: 'index.html, index.htm, index.php',
  loggingEnabled: false,
  redirects: [],
  basicEnabled: false,
  basicUser: '',
  basicPassword: '',
  passwordSet: false,
  rateEnabled: false,
  rps: 100,
  burst: 0,
  corsEnabled: false,
  originsText: '*',
  methodsText: 'GET, POST, OPTIONS',
  headersText: 'Content-Type, Authorization',
  credentials: false,
  realIpEnabled: false,
  realIpHeader: 'X-Forwarded-For',
  trustedText: '',
  upstreams: [],
})

const logsText = ref('')
const logType = ref('access')
const rawText = ref('')

function splitList(text) {
  return String(text || '').split(/[\n,]+/).map(s => s.trim()).filter(Boolean)
}

function applyAdvanced(adv) {
  const a = adv || {}
  const docs = Array.isArray(a.default_docs) && a.default_docs.length ? a.default_docs : ['index.html', 'index.htm', 'index.php']
  form.defaultDocsText = docs.join(', ')
  form.loggingEnabled = !!(a.logging && a.logging.enabled)
  form.redirects = Array.isArray(a.redirects)
    ? a.redirects.map(r => ({ from: r.from || '', to: r.to || '', code: Number(r.code) || 301 }))
    : []
  const basic = a.basic_auth || {}
  form.basicEnabled = !!basic.enabled
  form.basicUser = basic.username || ''
  form.basicPassword = ''
  form.passwordSet = !!basic.password_set
  const rate = a.rate_limit || {}
  form.rateEnabled = !!rate.enabled
  form.rps = Number(rate.rps) || 100
  form.burst = Number(rate.burst) || 0
  const cors = a.cors || {}
  form.corsEnabled = !!cors.enabled
  form.originsText = (cors.origins || ['*']).join(', ')
  form.methodsText = (cors.methods || ['GET', 'POST', 'OPTIONS']).join(', ')
  form.headersText = (cors.headers || ['Content-Type', 'Authorization']).join(', ')
  form.credentials = !!cors.credentials
  const real = a.real_ip || {}
  form.realIpEnabled = !!real.enabled
  form.realIpHeader = real.header || 'X-Forwarded-For'
  form.trustedText = (real.trusted || []).join('\n')
  form.upstreams = Array.isArray(a.upstreams)
    ? a.upstreams.map(u => ({ server: u.server || '', weight: Number(u.weight) || 1 }))
    : []
}

function payload() {
  return {
    default_docs: splitList(form.defaultDocsText),
    logging: { enabled: form.loggingEnabled },
    redirects: form.redirects.filter(r => r.from && r.to),
    basic_auth: {
      enabled: form.basicEnabled,
      username: form.basicUser,
      password: form.basicPassword,
    },
    rate_limit: { enabled: form.rateEnabled, rps: Number(form.rps) || 0, burst: Number(form.burst) || 0 },
    cors: {
      enabled: form.corsEnabled,
      origins: splitList(form.originsText),
      methods: splitList(form.methodsText),
      headers: splitList(form.headersText),
      credentials: form.credentials,
    },
    real_ip: {
      enabled: form.realIpEnabled,
      header: form.realIpHeader,
      trusted: splitList(form.trustedText),
    },
    upstreams: form.upstreams.filter(u => u.server).map(u => ({ server: u.server, weight: Number(u.weight) || 1 })),
  }
}

async function load() {
  try {
    const data = await sitesApi.advanced(props.site.id)
    applyAdvanced(data.advanced)
  } catch (e) {
    showMsg(e?.response?.data?.detail || e.message, true)
  }
}

function showMsg(text, err = false) {
  msg.value = text
  msgErr.value = err
}

async function save() {
  busy.value = true
  msg.value = ''
  try {
    await sitesApi.saveAdvanced(props.site.id, payload())
    showMsg(t('siteAdv.saved'))
    emit('saved')
  } catch (e) {
    showMsg(e?.response?.data?.detail || e.message, true)
  } finally {
    busy.value = false
  }
}

function addRedirect() {
  form.redirects.push({ from: '', to: '', code: 301 })
}

async function loadLogs() {
  logsText.value = t('common.loading')
  try {
    const data = await sitesApi.siteLogs(props.site.id, logType.value, 300)
    logsText.value = (data.lines || []).join('\n') || t('siteAdv.noLog')
  } catch (e) {
    logsText.value = e?.response?.data?.detail || e.message
  }
}

async function loadRaw() {
  try {
    const data = await sitesApi.config(props.site.id)
    rawText.value = data.config || ''
  } catch (e) {
    showMsg(e?.response?.data?.detail || e.message, true)
  }
}

async function saveRaw() {
  busy.value = true
  try {
    await sitesApi.saveRawConfig(props.site.id, rawText.value)
    showMsg(t('siteAdv.rawSaved'))
  } catch (e) {
    showMsg(e?.response?.data?.detail || e.message, true)
  } finally {
    busy.value = false
  }
}

function switchTab(key) {
  view.value = key
  msg.value = ''
  if (key === 'logs') loadLogs()
  if (key === 'raw') loadRaw()
}

onMounted(load)
</script>

<style scoped>
.site-adv { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.adv-head { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-bottom: 1px solid var(--g-border-soft); }
.adv-title { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
.adv-title .name { font-weight: 600; color: var(--g-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.adv-tabs { display: flex; gap: 2px; padding: 6px 10px 0; border-bottom: 1px solid var(--g-border-soft); overflow-x: auto; }
.adv-tab { padding: 6px 12px; border: none; background: transparent; color: var(--g-text-muted); font-size: 12px; cursor: pointer; border-radius: 7px 7px 0 0; white-space: nowrap; }
.adv-tab:hover { background: var(--g-hover); color: var(--g-text); }
.adv-tab.active { background: var(--g-primary-soft); color: var(--g-primary); font-weight: 600; }
.adv-msg { padding: 6px 12px; }
.adv-msg.err { color: var(--ui-danger-fg, #e5484d); }
.adv-body { flex: 1; min-height: 0; overflow: auto; padding: 12px; }
.adv-section { display: flex; flex-direction: column; gap: 10px; }
.adv-section h4 { margin: 4px 0; font-size: 13px; color: var(--g-text); }
.adv-section h4.mt { margin-top: 16px; }
.sec-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.stack { display: flex; flex-direction: column; gap: 10px; }
.switch-row { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--g-text); }
.switch-row input { accent-color: var(--g-primary); }
.adv-code { margin: 0; padding: 10px; min-height: 240px; max-height: 52vh; overflow: auto; background: var(--g-code-bg, #1f2329); color: var(--g-text); border: 1px solid var(--g-border); border-radius: 8px; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
.adv-raw { min-height: 320px; height: 52vh; font-size: 12px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
</style>
