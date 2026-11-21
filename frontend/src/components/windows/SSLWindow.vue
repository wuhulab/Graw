<!--
  SSL 证书窗口（SSL Certificates）

  证书中心：上传 / Let's Encrypt(HTTP) / 自签证书 / DNS-01 签发 / Acme 账户 / DNS 账户，
  以及证书详情、续签与删除。被「网站」窗口的 SSL 页签内嵌复用。
  后端：/api/ssl/*（list/upload/letsencrypt/selfsigned/detail/renew/acme-accounts/
  dns-accounts/letsencrypt/dns/delete）。
-->
<template>
  <div class="ssl-window">
    <!-- 工具栏：上传 / HTTP 签发 / 自签 / DNS-01 / 账户管理 -->
    <div class="ui-toolbar">
      <button class="ui-btn primary" @click="emit('openSslUpload')">{{ $t('ssl.upload') }}</button>
      <button class="ui-btn primary" @click="emit('openSslLeForm')">{{ $t('ssl.letsEncrypt') }}</button>
      <button class="ui-btn" @click="openSelfSigned">{{ $t('ssl.selfSigned') }}</button>
      <button class="ui-btn" @click="openDnsIssue">{{ $t('ssl.dnsIssue') }}</button>
      <button class="ui-btn" @click="openDnsAccounts">{{ $t('ssl.dnsAccounts') }}</button>
      <button class="ui-btn" @click="openAcme">{{ $t('ssl.acmeAccounts') }}</button>
      <span class="ui-hint right">{{ $t('ssl.certbot', { status: $t(certbot ? 'database.installed' : 'database.notInstalled') }) }}</span>
    </div>
    <!-- 证书列表 -->
    <div class="ui-table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ $t('ssl.name') }}</th><th>{{ $t('ssl.domains') }}</th><th>{{ $t('ssl.type') }}</th>
            <th>{{ $t('ssl.issuer') }}</th><th>{{ $t('ssl.expiresAt') }}</th><th>{{ $t('ssl.daysLeft') }}</th>
            <th>{{ $t('common.action') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in certs" :key="c.id">
            <td>{{ c.name }}</td>
            <td>{{ (c.domains || []).join(', ') }}</td>
            <td><span class="ui-badge" :class="c.type === 'letsencrypt' ? 'ok' : 'off'">{{ c.type }}</span></td>
            <td>{{ c.issuer || '-' }}</td>
            <td>{{ c.expires_at || '-' }}</td>
            <td>
              <span v-if="daysLeft(c) !== null" class="ui-badge" :class="daysLeft(c) < 15 ? 'danger' : daysLeft(c) < 30 ? 'warn' : 'ok'">
                {{ daysLeft(c) }}
              </span>
              <span v-else>-</span>
            </td>
            <td class="actions">
              <button class="ui-btn mini" @click="showDetail(c)">{{ $t('ssl.detail') }}</button>
              <button class="ui-btn mini" @click="renew(c)">{{ $t('ssl.renew') }}</button>
              <button class="iconbtn danger" :title="$t('common.delete')" @click="remove(c)"><Trash2 :size="14"/></button>
            </td>
          </tr>
          <tr v-if="certs.length===0"><td colspan="7" class="ui-empty">{{ $t('ssl.noCerts') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 详情 -->
    <div v-if="detail.show" class="ui-modal-overlay" @click.self="detail.show = false">
      <div class="ui-modal">
        <h3>{{ $t('ssl.detailTitle', { name: detail.cert?.name || '' }) }}</h3>
        <table class="kv">
          <tr><th>{{ $t('ssl.issuer') }}</th><td>{{ detail.data?.issuer || '-' }}</td></tr>
          <tr><th>{{ $t('ssl.subject') }}</th><td>{{ detail.data?.subject || '-' }}</td></tr>
          <tr><th>Serial</th><td class="mono">{{ detail.data?.serial || '-' }}</td></tr>
          <tr><th>{{ $t('ssl.notBefore') }}</th><td>{{ detail.data?.not_before || '-' }}</td></tr>
          <tr><th>{{ $t('ssl.notAfter') }}</th><td>{{ detail.data?.not_after || '-' }}</td></tr>
          <tr><th>SAN</th><td class="mono">{{ (detail.data?.dns_names || []).join(', ') || '-' }}</td></tr>
        </table>
        <div class="ui-actions"><button class="ui-btn" @click="detail.show = false">{{ $t('common.close') }}</button></div>
      </div>
    </div>

    <!-- 自签证书 -->
    <div v-if="selfSigned.show" class="ui-modal-overlay" @click.self="selfSigned.show = false">
      <div class="ui-modal">
        <h3>{{ $t('ssl.selfSigned') }}</h3>
        <label class="ui-label">{{ $t('ssl.certName') }}</label>
        <input v-model.trim="selfSigned.name" class="ui-input" :placeholder="$t('ssl.certNameHint')" />
        <label class="ui-label">{{ $t('ssl.domainsHint') }}</label>
        <textarea v-model="selfSigned.domains" class="ui-textarea mono" rows="3" placeholder="example.com&#10;*.example.com&#10;192.168.1.10"></textarea>
        <label class="ui-label">{{ $t('ssl.validDays') }}</label>
        <input v-model.number="selfSigned.days" type="number" min="1" max="3650" class="ui-input" />
        <div class="ui-actions">
          <button class="ui-btn" @click="selfSigned.show = false">{{ $t('common.cancel') }}</button>
          <button class="ui-btn primary" :disabled="busy" @click="createSelfSigned">{{ $t('common.create') }}</button>
        </div>
      </div>
    </div>

    <!-- DNS-01 签发 -->
    <div v-if="dnsIssue.show" class="ui-modal-overlay" @click.self="dnsIssue.show = false">
      <div class="ui-modal">
        <h3>{{ $t('ssl.dnsIssue') }}</h3>
        <label class="ui-label">{{ $t('ssl.domainsHint') }}</label>
        <textarea v-model="dnsIssue.domains" class="ui-textarea mono" rows="3" placeholder="example.com&#10;*.example.com"></textarea>
        <label class="ui-label">{{ $t('ssl.email') }}</label>
        <input v-model.trim="dnsIssue.email" class="ui-input" placeholder="admin@example.com" />
        <label class="ui-label">{{ $t('ssl.dnsAccount') }}</label>
        <select v-model="dnsIssue.accountId" class="ui-select">
          <option value="">{{ $t('ssl.selectDnsAccount') }}</option>
          <option v-for="a in dnsAccounts" :key="a.id" :value="a.id">{{ a.name }}（{{ a.provider }}）</option>
        </select>
        <p class="ui-hint">{{ $t('ssl.dnsIssueHint') }}</p>
        <div class="ui-actions">
          <button class="ui-btn" @click="dnsIssue.show = false">{{ $t('common.cancel') }}</button>
          <button class="ui-btn primary" :disabled="busy" @click="issueDns">{{ $t('ssl.issue') }}</button>
        </div>
      </div>
    </div>

    <!-- DNS 账户 -->
    <div v-if="dns.show" class="ui-modal-overlay" @click.self="dns.show = false">
      <div class="ui-modal wide">
        <h3>{{ $t('ssl.dnsAccounts') }}</h3>
        <div class="acct-form">
          <input v-model.trim="dns.name" class="ui-input" :placeholder="$t('ssl.accountName')" />
          <select v-model="dns.provider" class="ui-select">
            <option v-for="p in providers" :key="p" :value="p">{{ p }}</option>
          </select>
          <textarea v-model="dns.credentials" class="ui-textarea mono" rows="3"
            placeholder='{"CLOUDFLARE_API_TOKEN":"..."}'></textarea>
          <button class="ui-btn primary" @click="addDnsAccount">{{ $t('common.create') }}</button>
        </div>
        <div class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('common.name') }}</th><th>Provider</th><th>Keys</th><th></th></tr></thead>
            <tbody>
              <tr v-for="a in dnsAccounts" :key="a.id">
                <td>{{ a.name }}</td><td>{{ a.provider }}</td>
                <td class="mono">{{ (a.credential_keys || []).join(', ') }}</td>
                <td><button class="iconbtn danger" @click="removeDnsAccount(a)"><Trash2 :size="13" /></button></td>
              </tr>
              <tr v-if="!dnsAccounts.length"><td colspan="4" class="ui-empty">{{ $t('ssl.noAccounts') }}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="ui-actions"><button class="ui-btn" @click="dns.show = false">{{ $t('common.close') }}</button></div>
      </div>
    </div>

    <!-- ACME 账户 -->
    <div v-if="acme.show" class="ui-modal-overlay" @click.self="acme.show = false">
      <div class="ui-modal">
        <h3>{{ $t('ssl.acmeAccounts') }}</h3>
        <div class="acct-form">
          <input v-model.trim="acme.email" class="ui-input" placeholder="admin@example.com" />
          <button class="ui-btn primary" @click="addAcme">{{ $t('common.create') }}</button>
        </div>
        <div class="ui-table-wrap">
          <table>
            <thead><tr><th>{{ $t('ssl.email') }}</th><th></th></tr></thead>
            <tbody>
              <tr v-for="a in acmeAccounts" :key="a.id">
                <td>{{ a.email }}</td>
                <td><button class="iconbtn danger" @click="removeAcme(a)"><Trash2 :size="13" /></button></td>
              </tr>
              <tr v-if="!acmeAccounts.length"><td colspan="2" class="ui-empty">{{ $t('ssl.noAccounts') }}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="ui-actions"><button class="ui-btn" @click="acme.show = false">{{ $t('common.close') }}</button></div>
      </div>
    </div>

    <!-- 删除证书：高风险操作，需面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="$t('ssl.deleteTitle')"
      :message="$t('ssl.deleteMsg', { name: confirm.cert?.name || '' })"
      :input-label="$t('confirmDanger.inputPwdLabel')"
      :placeholder="$t('confirmDanger.inputPwdPlaceholder')"
      :confirm-label="$t('common.delete')"
      @confirm="doRemove"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { sslApi } from '../../api'
import { Trash2 } from 'lucide-vue-next'
import { formBus } from '../../store/formBus'
import ConfirmDialog from '../ConfirmDialog.vue'

const { t } = useI18n()
const emit = defineEmits(['openSslUpload', 'openSslLeForm'])

const certs = ref([])
const certbot = ref(false)
const busy = ref(false)

const detail = reactive({ show: false, cert: null, data: null })
const selfSigned = reactive({ show: false, name: '', domains: '', days: 365 })
const dnsIssue = reactive({ show: false, domains: '', email: '', accountId: '' })
const dns = reactive({ show: false, name: '', provider: 'cloudflare', credentials: '' })
const acme = reactive({ show: false, email: '' })
const dnsAccounts = ref([])
const acmeAccounts = ref([])
const providers = ref([])
const confirm = reactive({ show: false, cert: null })

watch(() => formBus.ssl, load)

async function load() {
  const data = await sslApi.list()
  certs.value = data.certs || []
  certbot.value = data.certbot
}

function splitDomains(text) {
  return String(text || '').split(/[\s,]+/).map(s => s.trim()).filter(Boolean)
}

function daysLeft(c) {
  if (!c.expires_at) return null
  const ts = Date.parse(String(c.expires_at).replace(' ', 'T'))
  if (!Number.isFinite(ts)) return null
  return Math.ceil((ts - Date.now()) / 86400000)
}

async function showDetail(c) {
  try {
    const data = await sslApi.detail(c.id)
    detail.cert = data.cert
    detail.data = data.detail
    detail.show = true
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function renew(c) {
  if (!window.confirm(t('ssl.renewConfirm', { name: c.name }))) return
  busy.value = true
  try {
    await sslApi.renew(c.id)
    await load()
    alert(t('ssl.renewDone'))
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  } finally {
    busy.value = false
  }
}

function openSelfSigned() {
  selfSigned.name = ''
  selfSigned.domains = ''
  selfSigned.days = 365
  selfSigned.show = true
}

async function createSelfSigned() {
  const domains = splitDomains(selfSigned.domains)
  if (!domains.length) { alert(t('ssl.domainsRequired')); return }
  busy.value = true
  try {
    await sslApi.selfSigned({ name: selfSigned.name, domains, days: Number(selfSigned.days) || 365 })
    selfSigned.show = false
    await load()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  } finally {
    busy.value = false
  }
}

async function openDnsAccounts() {
  dns.show = true
  await loadDnsAccounts()
}

async function loadDnsAccounts() {
  try {
    const data = await sslApi.dnsAccounts()
    dnsAccounts.value = data.accounts || []
    providers.value = data.providers || []
    if (providers.value.length && !providers.value.includes(dns.provider)) dns.provider = providers.value[0]
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function addDnsAccount() {
  if (!dns.name.trim() || !dns.credentials.trim()) { alert(t('ssl.accountRequired')); return }
  try {
    await sslApi.addDnsAccount({ name: dns.name.trim(), provider: dns.provider, credentials: dns.credentials })
    dns.name = ''
    dns.credentials = ''
    await loadDnsAccounts()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function removeDnsAccount(a) {
  if (!window.confirm(t('ssl.deleteAccountConfirm', { name: a.name }))) return
  try {
    await sslApi.deleteDnsAccount(a.id)
    await loadDnsAccounts()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function openDnsIssue() {
  dnsIssue.show = true
  await loadDnsAccounts()
}

async function issueDns() {
  const domains = splitDomains(dnsIssue.domains)
  if (!domains.length || !dnsIssue.email.trim() || !dnsIssue.accountId) {
    alert(t('ssl.dnsIssueRequired'))
    return
  }
  busy.value = true
  try {
    await sslApi.letsencryptDns({ domains, email: dnsIssue.email.trim(), account_id: dnsIssue.accountId })
    dnsIssue.show = false
    await load()
    alert(t('ssl.issueDone'))
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  } finally {
    busy.value = false
  }
}

async function openAcme() {
  acme.show = true
  await loadAcme()
}

async function loadAcme() {
  try {
    const data = await sslApi.acmeAccounts()
    acmeAccounts.value = data.accounts || []
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function addAcme() {
  if (!acme.email.trim()) return
  try {
    await sslApi.addAcmeAccount(acme.email.trim())
    acme.email = ''
    await loadAcme()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

async function removeAcme(a) {
  try {
    await sslApi.deleteAcmeAccount(a.id)
    await loadAcme()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message)
  }
}

function remove(c) {
  confirm.show = true
  confirm.cert = c
}

async function doRemove() {
  const c = confirm.cert
  confirm.show = false
  if (!c) return
  try {
    await sslApi.delete(c.id)
    await load()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message || t('common.error'))
  }
}

onMounted(load)
</script>

<style scoped>
.ssl-window { padding: 0; }
.actions { display: flex; gap: 4px; align-items: center; }
.iconbtn { padding: 4px; border: 1px solid var(--g-border); background: var(--g-surface); border-radius: 6px; cursor: pointer; }
.iconbtn.danger:hover { background: rgba(226, 50, 79, 0.16); border-color: var(--ui-danger-fg, #fca5a5); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.acct-form { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--g-border-soft); }
.kv { width: 100%; border-collapse: collapse; margin-bottom: 10px; }
.kv th, .kv td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--g-border-soft); font-size: 12px; }
.kv th { width: 110px; color: var(--g-text-muted); font-weight: 500; }
.ui-label { display: block; margin: 8px 0 4px; font-size: 12px; color: var(--g-text-secondary); }
</style>
