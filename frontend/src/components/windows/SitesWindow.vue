<template>
  <div class="sites-window" @click="closeCtx">
    <!-- 视图切换：网站 / SSL证书 -->
    <div class="mode-tabs">
      <button class="tab" :class="{ active: mode === 'sites' }" @click="switchMode('sites')">{{ $t('sites.tabSites') }}</button>
      <button class="tab" :class="{ active: mode === 'ssl' }" @click="switchMode('ssl')">{{ $t('sites.tabSsl') }}</button>
    </div>

    <template v-if="mode === 'sites'">
      <div class="toolbar">
        <button class="btn primary" @click="openTypePicker"><Plus :size="14" /> {{ $t('sites.add') }}</button>
        <span class="hint">{{ $t('sites.webServer', { server: webServer || $t('sites.none') }) }}</span>
        <span class="hint right">{{ $t('sites.rightClickHint') }}</span>
      </div>
      <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ $t('sites.name') }}</th>
            <th>{{ $t('sites.type') }}</th>
            <th>{{ $t('sites.domain') }}</th>
            <th>{{ $t('sites.targetRoot') }}</th>
            <th>{{ $t('sites.port') }}</th>
            <th>{{ $t('sites.status') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sites" :key="s.id" @contextmenu.prevent="openCtx($event, s)" class="site-row">
            <td>{{ s.name }}</td>
            <td>
              <span class="type-badge">{{ typeLabel(s.type) }}</span>
              <span v-if="s.source === '1panel'" class="tag-1p">1Panel兼容</span>
            </td>
            <td>{{ displayServerName(s) }}</td>
            <td class="mono">{{ displayTarget(s) }}</td>
            <td>{{ s.port }}</td>
            <td>
              <span class="badge" :class="s.enabled ? 'ok' : 'off'">{{ s.enabled ? $t('sites.enabled') : $t('sites.disabled') }}</span>
              <span class="badge" :class="s.online ? 'ok' : 'warn'">{{ s.online ? $t('sites.online') : $t('sites.offline') }}</span>
            </td>
          </tr>
          <tr v-if="sites.length === 0">
            <td colspan="6" class="empty">{{ $t('sites.noSites') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    </template>

    <!-- SSL证书视图（合并自独立的「SSL」应用） -->
    <div v-else class="ssl-wrap">
      <SSLWindow />
    </div>

    <!-- 右键菜单 -->
    <Teleport to="body">
      <div v-if="ctxMenu.show" class="context-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }" @click.stop>
        <template v-if="ctxMenu.site?.external">
          <div class="menu-header">{{ ctxMenu.site?.name }}</div>
          <div class="menu-divider"></div>
          <div class="menu-item" @click="menuEdit">{{ $t('sites.config') }}</div>
          <div class="menu-item" @click="menuViewConfig">{{ $t('sites.viewConfigHint') }}</div>
        </template>
        <template v-else>
          <div class="menu-header">{{ ctxMenu.site?.name }}</div>
          <div class="menu-divider"></div>
          <div class="menu-item" @click="menuToggleEnable">{{ ctxMenu.site?.enabled ? $t('sites.disableAction') : $t('sites.enableAction') }}</div>
          <div class="menu-item" @click="menuEdit">{{ $t('sites.config') }}</div>
          <div class="menu-item" @click="menuViewConfig">{{ $t('sites.viewConfigHint') }}</div>
          <div class="menu-divider"></div>
          <div class="menu-item danger" @click="menuRemove">{{ $t('common.delete') }}</div>
        </template>
      </div>
    </Teleport>

    <!-- 类型选择弹窗 -->
    <div v-if="showTypePicker" class="modal-overlay" @click.self="showTypePicker = false">
      <div class="modal type-modal">
        <h3>{{ $t('sites.selectType') }}</h3>
        <div class="type-grid">
          <div
            v-for="t in typeItems"
            :key="t.value"
            class="type-card"
            :class="{ active: t.value === pickedType }"
            @click="pickedType = t.value"
          >
            <component :is="t.icon" :size="22" />
            <div class="t-name">{{ t.label }}</div>
            <div class="t-desc">{{ t.desc }}</div>
          </div>
        </div>
        <div class="actions">
          <button class="btn" @click="showTypePicker = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="!pickedType" @click="confirmType">{{ $t('common.next') }}</button>
        </div>
      </div>
    </div>

    <!-- 创建 / 编辑：由独立窗口承载（SitesWindow 仅负责打开） -->

    <!-- Config viewer -->
    <div v-if="showConfig" class="modal-overlay" @click.self="showConfig = false">
      <div class="modal wide">
        <h3>{{ $t('sites.viewConfig', { name: configSite?.name }) }}</h3>
        <pre class="code">{{ configText }}</pre>
        <div class="actions">
          <button class="btn" @click="showConfig = false">{{ $t('sites.close') }}</button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除站点需输入站点名 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="text"
      :title="t('confirmDanger.deleteSiteTitle')"
      :message="t('confirmDanger.deleteSiteMsg', { name: confirm.site?.name })"
      :required-text="confirm.site?.name || ''"
      :input-label="t('confirmDanger.inputNameLabel')"
      :placeholder="t('confirmDanger.inputNamePlaceholder', { name: confirm.site?.name })"
      :confirm-label="t('common.delete')"
      @confirm="doDeleteSite"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, markRaw, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { sitesApi } from '../../api'
import { siteRevision } from '../../store/siteBus'
import ConfirmDialog from '../ConfirmDialog.vue'
import SSLWindow from './SSLWindow.vue'
import {
  Plus, Globe, Share2, Network, Layers
} from 'lucide-vue-next'

const { t } = useI18n()

// 视图模式：'sites' 网站 / 'ssl' SSL证书
const mode = ref('sites')

function switchMode(m) {
  mode.value = m
}

// 站点类型定义（文案走 i18n）
const typeItems = computed(() => [
  { value: 'static', label: t('sites.static'), desc: t('sites.staticDesc'), icon: markRaw(Globe) },
  { value: 'proxy', label: t('sites.proxy'), desc: t('sites.proxyDesc'), icon: markRaw(Share2) },
  { value: 'tcpudp', label: t('sites.tcpudp'), desc: t('sites.tcpudpDesc'), icon: markRaw(Network) },
  { value: 'subsite', label: t('sites.subsite'), desc: t('sites.subsiteDesc'), icon: markRaw(Layers) }
])

function typeLabel(type) {
  const item = typeItems.value.find(i => i.value === type)
  return item ? item.label : type || t('sites.static')
}

const sites = ref([])
const webServer = ref('')
const showTypePicker = ref(false)
const pickedType = ref('')
const showConfig = ref(false)
const configText = ref('')
const configSite = ref(null)
// 右键菜单状态
const ctxMenu = ref({ show: false, x: 0, y: 0, site: null })
// 高风险操作二次确认状态：记录待删除的站点
const confirm = ref({ show: false, site: null })

// 通知 App 打开独立「站点编辑」窗口（创建 / 编辑共用）
const emit = defineEmits(['openSiteEdit'])

// 站点列表发生变更（独立窗口保存成功）后自动刷新
let revisionInited = false
watch(siteRevision, () => {
  revisionInited = true
  load()
})

function openCtx(e, s) {
  const x = Math.min(e.clientX, window.innerWidth - 180)
  const y = Math.min(e.clientY, window.innerHeight - 200)
  ctxMenu.value = { show: true, x, y, site: s }
}

function closeCtx() {
  ctxMenu.value.show = false
}

function menuToggleEnable() {
  const s = ctxMenu.value.site
  closeCtx()
  if (s) toggleEnable(s)
}

function menuEdit() {
  const s = ctxMenu.value.site
  closeCtx()
  if (s) openEdit(s)
}

function menuViewConfig() {
  const s = ctxMenu.value.site
  closeCtx()
  if (s) viewConfig(s)
}

function menuRemove() {
  const s = ctxMenu.value.site
  closeCtx()
  if (s) remove(s)
}

function remove(s) {
  // 高风险操作二次确认：弹出对话框，要求输入站点名后才能删除
  confirm.value = { show: true, site: s }
}

async function doDeleteSite() {
  const s = confirm.value.site
  confirm.value.show = false
  if (!s) return
  await sitesApi.delete(s.id)
  await load()
}

async function load() {
  const data = await sitesApi.list()
  sites.value = data.sites || []
  webServer.value = data.web_server || ''
}

// 展示 server_name / 域名
function displayServerName(s) {
  if (s.type === 'subsite') {
    const sub = (s.subdomain || '').trim()
    const domain = (s.domain || '').trim()
    if (sub && domain) return `${sub}.${domain}`
    if (domain) return `*.${domain}`
  }
  return (s.domains || []).join(', ')
}

// 展示根目录 / 目标地址
function displayTarget(s) {
  if (s.type === 'proxy') return s.reverse_proxy || '-'
  if (s.type === 'tcpudp') return s.upstream || '-'
  return s.root || '-'
}

function openTypePicker() {
  pickedType.value = ''
  showTypePicker.value = true
}

function confirmType() {
  if (!pickedType.value) return
  const type = pickedType.value
  showTypePicker.value = false
  // 打开独立「站点编辑」窗口承载创建表单
  emit('openSiteEdit', { mode: 'create', type })
}

function openEdit(s) {
  // 打开独立「站点编辑」窗口承载编辑表单
  emit('openSiteEdit', { mode: 'edit', site: s })
}

async function toggleEnable(s) {
  const action = s.enabled ? 'disable' : 'enable'
  await sitesApi.action(s.id, action)
  await load()
}

async function viewConfig(s) {
  const data = await sitesApi.config(s.id)
  configSite.value = data.site
  configText.value = data.config
  showConfig.value = true
}

onMounted(load)
</script>

<style scoped>
.sites-window { padding: 10px; }
/* 视图切换标签（网站 / SSL证书） */
.mode-tabs { display: inline-flex; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; margin-bottom: 10px; }
.mode-tabs .tab { padding: 6px 14px; font-size: 13px; background: #fff; border: none; cursor: pointer; color: #6b7280; }
.mode-tabs .tab.active { background: #111827; color: #fff; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.hint { color: #6e6e73; font-size: 12px; }
.hint.right { margin-left: auto; }
.table-wrap { overflow: auto; max-height: 420px; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
th { background: #f9fafb; position: sticky; top: 0; }
.site-row { cursor: context-menu; }
.site-row:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; margin-right: 4px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.warn { background: #fef3c7; color: #92400e; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.type-badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; background: #eef2ff; color: #4338ca; }
.tag-1p { display: inline-block; margin-left: 4px; padding: 1px 6px; border-radius: 6px; font-size: 10px; background: #fffbeb; color: #b45309; border: 1px solid #fcd34d; white-space: nowrap; }

/* 右键菜单 */
.context-menu {
  position: fixed; background: #fff; border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  z-index: 3000; min-width: 160px; padding: 4px 0;
}
.menu-header { padding: 8px 14px; font-size: 12px; font-weight: 600; color: #111827; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }
.menu-item { padding: 8px 14px; font-size: 12.5px; cursor: pointer; color: #374151; }
.menu-item:hover { background: #f5f5f7; }
.menu-item.readonly { color: #9ca3af; cursor: default; }
.menu-item.danger { color: #b91c1c; }
.menu-item.danger:hover { background: #fef2f2; }
.menu-divider { height: 1px; background: #e5e7eb; margin: 4px 0; }
.empty { text-align: center; color: #9ca3af; padding: 24px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 480px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal.wide { width: 720px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.form .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
.radio-row { display: flex; gap: 16px; }
.radio { display: flex; align-items: center; gap: 4px; font-size: 13px; color: #111827; cursor: pointer; }
.type-modal { width: 560px; }
.type-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 14px; }
.type-card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; cursor: pointer; transition: all 0.15s; }
.type-card:hover { border-color: #94a3b8; background: #f9fafb; }
.type-card.active { border-color: #111827; background: #f3f4f6; }
.t-name { font-size: 14px; font-weight: 600; margin-top: 8px; }
.t-desc { font-size: 12px; color: #6b7280; margin-top: 2px; }
.code { background: #f3f4f6; padding: 12px; border-radius: 8px; font-size: 12px; overflow: auto; max-height: 360px; white-space: pre-wrap; word-break: break-all; }
</style>