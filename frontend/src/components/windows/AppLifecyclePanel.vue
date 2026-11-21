<!--
  AppLifecyclePanel.vue — 应用商店「已安装」页签
  ==========================================================
  业务作用：
    把应用商店安装出来的 compose 应用纳入可运维闭环：列出应用、启停/重启/重建、
    拉取升级、查看与编辑 compose、备份/恢复、导入备份、端口直达。
  后端模块：
    /api/appstore/installed 系列（handlers_app_lifecycle.go）。
  关键状态：
    - apps        已安装应用摘要（状态、服务、容器、端口）
    - detail      当前展开的应用详情
    - backups     备份弹窗数据
    - composeForm compose 编辑弹窗内容
  打开方式：
    由 AppStoreWindow 的「已安装」页签渲染（v-if 挂载，切回即刷新）。
-->
<template>
  <div class="lifecycle">
    <!-- 工具栏 -->
    <div class="toolbar">
      <span class="summary">{{ $t('appstore.lifecycle.installedCount', { count: apps.length }) }}</span>
      <button class="btn" :disabled="loading" @click="load">
        {{ loading ? $t('common.loading') : $t('common.refresh') }}
      </button>
      <button class="btn" @click="showImport = true">
        <Upload :size="13" /> {{ $t('appstore.lifecycle.importBackup') }}
      </button>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <div v-if="loading && !apps.length" class="empty">
      <Loader2 :size="30" class="spin" />
      <div>{{ $t('common.loading') }}</div>
    </div>
    <div v-else-if="!apps.length" class="empty">
      <PackageOpen :size="36" />
      <div>{{ $t('appstore.lifecycle.noInstalled') }}</div>
      <div class="hint">{{ $t('appstore.lifecycle.noInstalledHint') }}</div>
    </div>

    <!-- 已安装应用列表 -->
    <div v-else class="app-list">
      <div v-for="app in apps" :key="app.name" class="app-row">
        <div class="row-main">
          <div class="row-title">
            <button class="name-btn" @click="openDetail(app)">{{ app.name }}</button>
            <span class="state" :class="app.status">{{ statusText(app.status) }}</span>
          </div>
          <div class="row-meta">
            <span class="mono">{{ serviceText(app) }}</span>
            <span v-if="app.updated_at" class="muted">{{ $t('appstore.lifecycle.updatedAt', { time: app.updated_at }) }}</span>
          </div>
          <div class="ports">
            <a v-for="p in app.ports" :key="p.protocol + p.external" class="port-link"
               :href="portUrl(p)" target="_blank" rel="noopener">
              <ExternalLink :size="11" /> {{ p.external }}/{{ p.protocol }}
            </a>
            <span v-if="!app.ports.length" class="muted">{{ $t('appstore.lifecycle.noPorts') }}</span>
          </div>
        </div>
        <div class="row-actions">
          <button class="btn sm" :disabled="busy === app.name" @click="act(app, app.status === 'running' ? 'stop' : 'start')">
            {{ app.status === 'running' ? $t('common.stop') : $t('common.start') }}
          </button>
          <button class="btn sm" :disabled="busy === app.name" @click="act(app, 'restart')">{{ $t('common.restart') }}</button>
          <button class="btn sm primary" :disabled="busy === app.name" @click="upgrade(app)">
            <ArrowUpCircle :size="12" /> {{ $t('appstore.lifecycle.upgrade') }}
          </button>
          <button class="btn sm" :disabled="busy === app.name" @click="checkUpdates(app)">
            <RefreshCw :size="12" /> {{ $t('appstore.lifecycle.checkUpdate') }}
          </button>
          <button class="btn sm" :disabled="busy === app.name" @click="openBackups(app)">
            <Archive :size="12" /> {{ $t('appstore.lifecycle.backup') }}
          </button>
          <button class="btn sm" :disabled="busy === app.name" @click="openCompose(app)">
            <FileCode2 :size="12" /> {{ $t('appstore.lifecycle.compose') }}
          </button>
          <button class="btn sm" :disabled="busy === app.name" @click="act(app, 'rebuild')">{{ $t('appstore.lifecycle.rebuild') }}</button>
        </div>
        <div v-if="updateState[app.name]" class="update-line">
          <span v-for="s in updateState[app.name]" :key="s.service" class="update-item" :class="s.status">
            {{ s.service }}: {{ updateLabel(s.status) }}
          </span>
        </div>
      </div>
    </div>

    <!-- 详情弹窗 -->
    <div v-if="detail" class="modal-overlay" @click.self="detail = null">
      <div class="modal">
        <div class="modal-head">
          <Package :size="16" /> {{ detail.name }}
          <button class="x-btn" @click="detail = null"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="section-title">{{ $t('appstore.lifecycle.services') }}</div>
          <table class="mini-table">
            <thead><tr><th>{{ $t('common.name') }}</th><th>{{ $t('appstore.lifecycle.image') }}</th></tr></thead>
            <tbody>
              <tr v-for="s in detail.services" :key="s.name"><td class="mono">{{ s.name }}</td><td class="mono">{{ s.image || '-' }}</td></tr>
            </tbody>
          </table>
          <div class="section-title">{{ $t('appstore.lifecycle.containers') }}</div>
          <table class="mini-table">
            <thead><tr><th>{{ $t('common.name') }}</th><th>{{ $t('common.status') }}</th><th>{{ $t('appstore.lifecycle.ports') }}</th></tr></thead>
            <tbody>
              <tr v-for="c in detail.containers" :key="c.name">
                <td class="mono">{{ c.name }}</td><td>{{ c.status || c.state }}</td><td class="mono">{{ c.ports || '-' }}</td>
              </tr>
              <tr v-if="!detail.containers.length"><td colspan="3" class="muted">{{ $t('appstore.lifecycle.noContainers') }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 备份弹窗 -->
    <div v-if="backupApp" class="modal-overlay" @click.self="closeBackups">
      <div class="modal">
        <div class="modal-head">
          <Archive :size="16" /> {{ $t('appstore.lifecycle.backupsTitle', { name: backupApp.name }) }}
          <button class="x-btn" @click="closeBackups"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="backup-actions">
            <button class="btn primary sm" :disabled="backingUp" @click="createBackup">
              {{ backingUp ? $t('common.loading') : $t('appstore.lifecycle.createBackup') }}
            </button>
            <span class="muted">{{ $t('appstore.lifecycle.backupHint') }}</span>
          </div>
          <table class="mini-table">
            <thead><tr><th>{{ $t('common.name') }}</th><th>{{ $t('appstore.lifecycle.size') }}</th><th>{{ $t('appstore.lifecycle.createdAt') }}</th><th /></tr></thead>
            <tbody>
              <tr v-for="b in backups" :key="b.name">
                <td class="mono">{{ b.name }}</td>
                <td>{{ fmtSize(b.size) }}</td>
                <td>{{ b.created_at }}</td>
                <td class="right">
                  <button class="btn sm" :disabled="restoring" @click="restore(b)">{{ $t('appstore.lifecycle.restore') }}</button>
                </td>
              </tr>
              <tr v-if="!backups.length"><td colspan="4" class="muted">{{ $t('appstore.lifecycle.noBackups') }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- compose 编辑弹窗 -->
    <div v-if="composeApp" class="modal-overlay" @click.self="composeApp = null">
      <div class="modal wide">
        <div class="modal-head">
          <FileCode2 :size="16" /> {{ $t('appstore.lifecycle.composeTitle', { name: composeApp.name }) }}
          <button class="x-btn" @click="composeApp = null"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="hint" style="margin-bottom:6px;">{{ $t('appstore.lifecycle.composeHint') }}</div>
          <textarea v-model="composeText" class="compose-area" spellcheck="false" />
        </div>
        <div class="modal-foot">
          <button class="btn" @click="composeApp = null">{{ $t('common.cancel') }}</button>
          <button class="btn" :disabled="savingCompose" @click="saveCompose(false)">{{ $t('appstore.lifecycle.saveOnly') }}</button>
          <button class="btn primary" :disabled="savingCompose" @click="saveCompose(true)">{{ $t('appstore.lifecycle.saveApply') }}</button>
        </div>
      </div>
    </div>

    <!-- 导入备份弹窗 -->
    <div v-if="showImport" class="modal-overlay" @click.self="showImport = false">
      <div class="modal">
        <div class="modal-head">
          <Upload :size="16" /> {{ $t('appstore.lifecycle.importBackup') }}
          <button class="x-btn" @click="showImport = false"><X :size="14" /></button>
        </div>
        <div class="modal-body">
          <label class="field">{{ $t('appstore.lifecycle.newAppName') }}
            <input v-model.trim="importName" class="inp" :placeholder="$t('appstore.lifecycle.newAppNameHint')" />
          </label>
          <label class="field">{{ $t('appstore.lifecycle.backupFile') }}
            <input type="file" accept=".tar.gz,.tgz" class="inp" @change="onImportFile" />
          </label>
          <div class="hint">{{ $t('appstore.lifecycle.importHint') }}</div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="showImport = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="importing || !importFile || !importName" @click="doImport">
            {{ importing ? $t('common.loading') : $t('appstore.lifecycle.import') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { appStoreApi } from '../../api'
import {
  Upload, Loader2, PackageOpen, Package, Archive, FileCode2, RefreshCw,
  ArrowUpCircle, ExternalLink, X
} from 'lucide-vue-next'

const { t } = useI18n()
const apps = ref([])
const loading = ref(false)
const error = ref('')
const busy = ref('')
const detail = ref(null)
const updateState = reactive({})
const backupApp = ref(null)
const backups = ref([])
const backingUp = ref(false)
const restoring = ref(false)
const composeApp = ref(null)
const composeText = ref('')
const savingCompose = ref(false)
const showImport = ref(false)
const importName = ref('')
const importFile = ref(null)
const importing = ref(false)

function errText(e) {
  return e?.response?.data?.detail || e?.message || String(e)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const r = await appStoreApi.installed()
    apps.value = r.apps || []
  } catch (e) {
    error.value = errText(e)
    apps.value = []
  } finally {
    loading.value = false
  }
}

function statusText(s) {
  return s === 'running' ? t('common.running') : s === 'partial' ? t('appstore.lifecycle.partial') : t('common.stopped')
}

function serviceText(app) {
  const images = (app.services || []).map(s => s.image).filter(Boolean)
  return images.length ? images.join(' · ') : t('appstore.lifecycle.noServices')
}

function portUrl(p) {
  return `http://${location.hostname}:${p.external}`
}

function fmtSize(n) {
  const v = Number(n || 0)
  if (v > 1024 * 1024) return (v / 1024 / 1024).toFixed(1) + ' MB'
  if (v > 1024) return (v / 1024).toFixed(1) + ' KB'
  return v + ' B'
}

function updateLabel(s) {
  return t('appstore.lifecycle.update_' + s)
}

async function act(app, action) {
  const label = t('appstore.lifecycle.action_' + action)
  if (!window.confirm(t('appstore.lifecycle.confirmAction', { action: label, name: app.name }))) return
  busy.value = app.name
  try {
    await appStoreApi.installedAction(app.name, action)
    await load()
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = ''
  }
}

async function upgrade(app) {
  if (!window.confirm(t('appstore.lifecycle.confirmUpgrade', { name: app.name }))) return
  busy.value = app.name
  error.value = ''
  try {
    await appStoreApi.installedAction(app.name, 'upgrade')
    await checkUpdates(app)
    await load()
  } catch (e) {
    error.value = errText(e)
  } finally {
    busy.value = ''
  }
}

async function checkUpdates(app) {
  try {
    const r = await appStoreApi.checkUpdate(app.name)
    updateState[app.name] = r.services || []
  } catch (e) {
    error.value = errText(e)
  }
}

async function openDetail(app) {
  try {
    const r = await appStoreApi.installedDetail(app.name)
    detail.value = r.app
  } catch (e) {
    error.value = errText(e)
  }
}

async function openBackups(app) {
  backupApp.value = app
  backups.value = []
  try {
    const r = await appStoreApi.installedBackups(app.name)
    backups.value = r.backups || []
  } catch (e) {
    error.value = errText(e)
  }
}

function closeBackups() {
  backupApp.value = null
  backups.value = []
}

async function createBackup() {
  backingUp.value = true
  try {
    await appStoreApi.backupInstalled(backupApp.value.name)
    const r = await appStoreApi.installedBackups(backupApp.value.name)
    backups.value = r.backups || []
  } catch (e) {
    error.value = errText(e)
  } finally {
    backingUp.value = false
  }
}

async function restore(b) {
  if (!window.confirm(t('appstore.lifecycle.confirmRestore', { name: b.name }))) return
  restoring.value = true
  try {
    await appStoreApi.restoreInstalled(backupApp.value.name, b.name)
    closeBackups()
    await load()
  } catch (e) {
    error.value = errText(e)
  } finally {
    restoring.value = false
  }
}

async function openCompose(app) {
  try {
    const r = await appStoreApi.installedCompose(app.name)
    composeText.value = r.compose || ''
    composeApp.value = app
  } catch (e) {
    error.value = errText(e)
  }
}

async function saveCompose(apply) {
  savingCompose.value = true
  try {
    await appStoreApi.saveInstalledCompose(composeApp.value.name, composeText.value, apply)
    composeApp.value = null
    await load()
  } catch (e) {
    error.value = errText(e)
  } finally {
    savingCompose.value = false
  }
}

function onImportFile(e) {
  importFile.value = e.target.files?.[0] || null
}

async function doImport() {
  importing.value = true
  try {
    await appStoreApi.importInstalled(importName.value, importFile.value)
    showImport.value = false
    importName.value = ''
    importFile.value = null
    await load()
  } catch (e) {
    error.value = errText(e)
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.lifecycle { display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--g-border); }
.summary { margin-right: auto; font-size: 12px; color: var(--g-text-muted); }
.error-banner { margin: 8px 12px 0; padding: 6px 10px; background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; border-radius: 6px; font-size: 12px; }
.empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--g-text-muted); font-size: 13px; }
.empty .hint { font-size: 11.5px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.app-list { flex: 1; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.app-row { border: 1px solid var(--g-border); border-radius: 10px; background: var(--g-surface); padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.row-main { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.row-title { display: flex; align-items: center; gap: 8px; }
.name-btn { border: none; background: transparent; color: var(--g-text); font-weight: 700; font-size: 13.5px; cursor: pointer; padding: 0; }
.name-btn:hover { color: #2563eb; }
.state { font-size: 10.5px; padding: 1px 7px; border-radius: 999px; background: var(--g-surface-soft); color: var(--g-text-muted); }
.state.running { background: #ecfdf5; color: #047857; }
.state.partial { background: #fffbeb; color: #b45309; }
.row-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 11.5px; color: var(--g-text-muted); }
.ports { display: flex; flex-wrap: wrap; gap: 6px; }
.port-link { font-size: 11.5px; color: #2563eb; display: inline-flex; align-items: center; gap: 3px; text-decoration: none; }
.port-link:hover { text-decoration: underline; }
.muted { color: var(--g-text-muted); font-size: 11.5px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.row-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.btn.sm { font-size: 11.5px; padding: 3px 8px; }

.update-line { display: flex; flex-wrap: wrap; gap: 8px; font-size: 11px; }
.update-item { padding: 1px 7px; border-radius: 999px; background: var(--g-surface-soft); color: var(--g-text-muted); }
.update-item.update { background: #fffbeb; color: #b45309; }
.update-item.latest { background: #ecfdf5; color: #047857; }
.update-item.not-pulled { background: #eff6ff; color: #1d4ed8; }

.modal-overlay { position: absolute; inset: 0; background: rgba(15, 23, 42, .55); display: flex; align-items: center; justify-content: center; padding: 24px; z-index: 50; }
.modal { width: 100%; max-width: 640px; max-height: 90%; background: var(--g-surface); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 20px 50px rgba(0, 0, 0, .3); }
.modal.wide { max-width: 860px; }
.modal-head { display: flex; align-items: center; gap: 8px; padding: 11px 14px; border-bottom: 1px solid var(--g-border); font-weight: 700; font-size: 13.5px; background: var(--g-surface-muted); }
.x-btn { margin-left: auto; border: none; background: transparent; color: var(--g-text-muted); cursor: pointer; padding: 3px; border-radius: 6px; }
.x-btn:hover { background: var(--g-surface-soft); }
.modal-body { padding: 12px 14px; overflow-y: auto; flex: 1; min-height: 0; }
.modal-foot { padding: 10px 14px; border-top: 1px solid var(--g-border); display: flex; justify-content: flex-end; gap: 8px; background: var(--g-surface-muted); }
.section-title { font-size: 12px; font-weight: 700; margin: 8px 0 6px; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mini-table th, .mini-table td { border-bottom: 1px solid var(--g-border); padding: 5px 6px; text-align: left; }
.mini-table th { color: var(--g-text-muted); font-weight: 600; }
.right { text-align: right; }
.backup-actions { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.compose-area { width: 100%; min-height: 380px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; line-height: 1.5; padding: 8px; border: 1px solid var(--g-border); border-radius: 8px; background: var(--g-surface); color: var(--g-text); }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; margin-bottom: 10px; }
.inp { padding: 6px 8px; border: 1px solid var(--g-border); border-radius: 8px; background: var(--g-surface); color: var(--g-text); font-size: 12px; }
.hint { font-size: 11.5px; color: var(--g-text-muted); }
</style>
