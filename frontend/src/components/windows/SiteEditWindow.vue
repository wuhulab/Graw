<template>
  <div class="site-edit-window">
    <form class="form" @submit.prevent="save">
      <label>{{ $t('sites.siteName') }}</label>
      <input v-model="form.name" :placeholder="$t('sites.namePlaceholder')" />

      <!-- 静态网址 -->
      <template v-if="form.type === 'static'">
        <label>{{ $t('sites.domains') }}</label>
        <input v-model="domainsText" :placeholder="$t('sites.domainsPlaceholder')" />
        <label>{{ $t('sites.root') }}</label>
        <input v-model="form.root" :placeholder="$t('sites.rootPlaceholder')" />
        <label>{{ $t('sites.port') }}</label>
        <input v-model.number="form.port" type="number" />
      </template>

      <!-- 反向代理 -->
      <template v-else-if="form.type === 'proxy'">
        <label>{{ $t('sites.domains') }}</label>
        <input v-model="domainsText" :placeholder="$t('sites.domainsPlaceholder')" />
        <label>{{ $t('sites.listenPort') }}</label>
        <input v-model.number="form.port" type="number" />
        <label>{{ $t('sites.reverseProxy') }}</label>
        <input v-model="form.reverse_proxy" :placeholder="$t('sites.reverseProxyPlaceholder')" />
      </template>

      <!-- TCP/UDP 代理 -->
      <template v-else-if="form.type === 'tcpudp'">
        <label>{{ $t('sites.protocol') }}</label>
        <div class="radio-row">
          <label class="radio"><input type="radio" value="tcp" v-model="form.protocol" /> {{ $t('sites.tcp') }}</label>
          <label class="radio"><input type="radio" value="udp" v-model="form.protocol" /> {{ $t('sites.udp') }}</label>
        </div>
        <label>{{ $t('sites.listenPort') }}</label>
        <input v-model.number="form.port" type="number" />
        <label>{{ $t('sites.upstream') }}</label>
        <input v-model="form.upstream" :placeholder="$t('sites.upstreamPlaceholder')" />
      </template>

      <!-- 子网站 -->
      <template v-else-if="form.type === 'subsite'">
        <label>{{ $t('sites.subdomain') }}</label>
        <input v-model="form.subdomain" :placeholder="$t('sites.subdomainPlaceholder')" />
        <label>{{ $t('sites.domainRoot') }}</label>
        <input v-model="form.domain" :placeholder="$t('sites.domainRootPlaceholder')" />
        <label>{{ $t('sites.root') }}</label>
        <input v-model="form.root" :placeholder="$t('sites.rootPlaceholder')" />
        <label>{{ $t('sites.port') }}</label>
        <input v-model.number="form.port" type="number" />
      </template>

      <p v-if="err" class="err">{{ err }}</p>

      <div class="actions">
        <button type="button" class="btn" @click="cancel">{{ $t('common.cancel') }}</button>
        <button type="submit" class="btn primary" :disabled="busy">{{ busy ? $t('common.saving') : $t('common.save') }}</button>
      </div>
    </form>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { sitesApi } from '../../api'
import { bumpSites } from '../../store/siteBus'

const props = defineProps({
  mode: { type: String, default: 'create' }, // create | edit
  type: { type: String, default: 'static' }, // 仅新建时需要
  site: { type: Object, default: null }      // 仅编辑时需要
})
const emit = defineEmits(['close'])
const { t } = useI18n()

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
const err = ref('')
const busy = ref(false)

onMounted(() => {
  if (props.mode === 'edit' && props.site) {
    const s = props.site
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
  } else {
    form.value = emptyForm(props.type || 'static')
    domainsText.value = ''
  }
  // 预填占位：主窗口进入时焦点给名称输入框
  const input = document.querySelector('.site-edit-window input')
  if (input) {
    setTimeout(() => input.focus(), 50)
  }
})

async function save() {
  err.value = ''
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
  busy.value = true
  try {
    if (props.mode === 'edit' && props.site?.id) {
      await sitesApi.update(props.site.id, payload)
    } else {
      await sitesApi.create(payload)
    }
    bumpSites()
    emit('close')
  } catch (e) {
    err.value = e?.response?.data?.detail || String(e?.message || e)
  } finally {
    busy.value = false
  }
}

function cancel() {
  emit('close')
}
</script>

<style scoped>
.site-edit-window { padding: 14px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.form .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
.radio-row { display: flex; gap: 16px; }
.radio { display: flex; align-items: center; gap: 4px; font-size: 13px; color: #111827; cursor: pointer; }
.err { margin: 0; font-size: 12px; color: #dc2626; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>