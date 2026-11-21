<!--
  ApiKeyWindow.vue — API Key 管理
  ==========================================================
  业务作用：
    为脚本/第三方系统创建长期 API Key：选择 scope（模块或全部/管理）、
    限制来源 IP、设置有效期；明文只在创建成功时显示一次。
  后端模块：
    /api/apikeys（handlers_apikey.go）。
  打开方式：
    桌面/侧边栏「API Key」快捷方式（完整管理员）。
-->
<template>
  <div class="key-window">
    <div class="toolbar">
      <button class="btn" :disabled="loading" @click="load">{{ loading ? $t('common.loading') : $t('common.refresh') }}</button>
      <button class="btn primary" @click="openForm"><Plus :size="13" /> {{ $t('apikey.create') }}</button>
      <span class="hint">{{ $t('apikey.hint') }}</span>
      <span v-if="err" class="err">{{ err }}</span>
    </div>

    <table class="tbl">
      <thead><tr>
        <th>{{ $t('common.name') }}</th><th>{{ $t('apikey.prefix') }}</th><th>Scope</th>
        <th>{{ $t('apikey.owner') }}</th><th>{{ $t('apikey.expires') }}</th><th>{{ $t('apikey.lastUsed') }}</th>
        <th>{{ $t('common.status') }}</th><th style="width:140px;">{{ $t('common.action') }}</th>
      </tr></thead>
      <tbody>
        <tr v-for="k in keys" :key="k.id">
          <td>{{ k.name }}</td>
          <td class="mono">{{ k.prefix }}…</td>
          <td class="mono">{{ (k.scopes || []).join(', ') }}</td>
          <td>{{ k.owner }}</td>
          <td>{{ k.expires_at || $t('apikey.never') }}</td>
          <td>{{ k.last_used_at || '-' }}</td>
          <td>{{ k.enabled ? $t('common.enabled') : $t('common.disabled') }}</td>
          <td>
            <button class="btn sm" @click="toggle(k)">{{ k.enabled ? $t('apikey.disable') : $t('apikey.enable') }}</button>
            <button class="btn sm danger" @click="remove(k)">{{ $t('common.delete') }}</button>
          </td>
        </tr>
        <tr v-if="!keys.length"><td colspan="8" class="muted">{{ $t('apikey.empty') }}</td></tr>
      </tbody>
    </table>

    <!-- 新建 -->
    <div v-if="form.show" class="mask" @click.self="form.show = false">
      <div class="dialog">
        <div class="dialog-title">{{ $t('apikey.create') }}</div>
        <div class="dialog-body">
          <label class="field">{{ $t('common.name') }}<input v-model.trim="form.name" class="inp" /></label>
          <div class="field">
            <span>Scope</span>
            <div class="scopes">
              <label class="check"><input type="checkbox" v-model="form.all" /> {{ $t('apikey.scopeAll') }}</label>
              <label class="check"><input type="checkbox" v-model="form.admin" /> {{ $t('apikey.scopeAdmin') }}</label>
              <label v-for="m in modules" :key="m" class="check">
                <input type="checkbox" v-model="form.scopes" :value="m" :disabled="form.all" /> {{ m }}
              </label>
            </div>
          </div>
          <label class="field">{{ $t('apikey.allowedIps') }}<textarea v-model="form.ips" class="area" rows="2" :placeholder="$t('apikey.ipsHint')" /></label>
          <label class="field">{{ $t('apikey.expiresDays') }}<input v-model.number="form.days" type="number" min="0" max="3650" class="inp" /></label>
          <div v-if="form.err" class="err">{{ form.err }}</div>
        </div>
        <div class="dialog-foot">
          <button class="btn" @click="form.show = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="form.busy" @click="create">{{ form.busy ? $t('common.saving') : $t('common.create') }}</button>
        </div>
      </div>
    </div>

    <!-- 明文只显示一次 -->
    <div v-if="created" class="mask">
      <div class="dialog">
        <div class="dialog-title">{{ $t('apikey.createdTitle') }}</div>
        <div class="dialog-body">
          <div class="warn">{{ $t('apikey.createdWarn') }}</div>
          <div class="plain mono">{{ created }}</div>
          <button class="btn primary" @click="copyKey">{{ $t('apikey.copy') }}</button>
        </div>
        <div class="dialog-foot"><button class="btn" @click="created = ''">{{ $t('common.close') }}</button></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { apiKeyApi } from '../../api'
import { Plus } from 'lucide-vue-next'

const loading = ref(false)
const err = ref('')
const keys = ref([])
const modules = ref([])
const created = ref('')
const form = reactive({ show: false, name: '', scopes: [], all: false, admin: false, ips: '', days: 0, busy: false, err: '' })

function apiErr(e) { return e?.response?.data?.detail || e.message }

async function load() {
  loading.value = true
  err.value = ''
  try {
    const [k, m] = await Promise.all([apiKeyApi.list(), apiKeyApi.modules()])
    keys.value = k.keys || []
    modules.value = m.modules || []
  } catch (e) { err.value = apiErr(e) } finally { loading.value = false }
}

function openForm() {
  Object.assign(form, { show: true, name: '', scopes: [], all: false, admin: false, ips: '', days: 0, busy: false, err: '' })
}

async function create() {
  form.busy = true
  form.err = ''
  try {
    const scopes = []
    if (form.all) scopes.push('*')
    if (form.admin) scopes.push('admin')
    scopes.push(...form.scopes)
    const r = await apiKeyApi.create({
      name: form.name,
      scopes,
      allowed_ips: form.ips.split('\n').map(s => s.trim()).filter(Boolean),
      expires_days: form.days || 0,
    })
    created.value = r.key
    form.show = false
    await load()
  } catch (e) { form.err = apiErr(e) } finally { form.busy = false }
}

async function toggle(k) {
  try { await apiKeyApi.toggle(k.id); await load() } catch (e) { alert(apiErr(e)) }
}

async function remove(k) {
  if (!window.confirm(`删除 API Key「${k.name}」？`)) return
  try { await apiKeyApi.remove(k.id); await load() } catch (e) { alert(apiErr(e)) }
}

async function copyKey() {
  try {
    await navigator.clipboard.writeText(created.value)
    alert('已复制')
  } catch { alert('复制失败，请手动选择文本') }
}

onMounted(load)
</script>

<style scoped>
.key-window { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--g-border); flex-wrap: wrap; }
.hint { font-size: 11.5px; color: var(--g-text-muted); }
.err { color: #b91c1c; font-size: 12px; }
.tbl { flex: 1; width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th, .tbl td { border-bottom: 1px solid var(--g-border); padding: 7px 8px; text-align: left; }
.tbl th { color: var(--g-text-muted); font-weight: 600; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.muted { color: var(--g-text-muted); text-align: center; }
.mask { position: fixed; inset: 0; background: rgba(15,23,42,.55); display: flex; align-items: center; justify-content: center; z-index: 80; padding: 24px; }
.dialog { width: 100%; max-width: 560px; max-height: 90%; background: var(--g-surface); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; }
.dialog-title { padding: 11px 14px; border-bottom: 1px solid var(--g-border); font-weight: 700; font-size: 13.5px; background: var(--g-surface-muted); }
.dialog-body { padding: 12px 14px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
.dialog-foot { padding: 10px 14px; border-top: 1px solid var(--g-border); display: flex; justify-content: flex-end; gap: 8px; background: var(--g-surface-muted); }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.inp, .area { padding: 6px 8px; border: 1px solid var(--g-border); border-radius: 8px; background: var(--g-surface); color: var(--g-text); font-size: 12px; }
.scopes { display: flex; flex-wrap: wrap; gap: 8px; max-height: 160px; overflow: auto; padding: 4px; border: 1px solid var(--g-border); border-radius: 8px; }
.check { display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px; }
.warn { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; border-radius: 8px; padding: 7px 9px; font-size: 12px; }
.plain { background: var(--g-surface-soft); border-radius: 8px; padding: 10px; font-size: 12.5px; word-break: break-all; }
</style>
