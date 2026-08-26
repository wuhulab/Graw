<template>
  <div class="tamper-window" @click="closeMenus">
    <!-- 顶部：全局状态 + 全局开关 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge" :class="globalStatusClass">{{ $t('tamper.globalStatus') }}：{{ globalStatusText }}</span>
        <span v-if="tamperState.temporarilyDisabled" class="remain">{{ $t('tamper.temporarilyRemain', { minutes: disabledRemainMin }) }}</span>
      </div>
      <div class="toolbar-actions">
        <button v-if="!tamperState.enabled" class="btn primary" :disabled="busy" @click="doEnable">
          <Power :size="14" /> {{ $t('tamper.enableBtn') }}
        </button>
        <template v-else>
          <button class="btn" :disabled="busy" @click="doDisable10m">{{ $t('tamper.disable10mBtn') }}</button>
          <button class="btn danger-btn" :disabled="busy" @click="confirmDisableAll">{{ $t('tamper.disableBtn') }}</button>
        </template>
        <button class="btn" :disabled="loading" @click="loadAll">{{ $t('common.refresh') }}</button>
      </div>
    </div>

    <!-- 标签页 -->
    <div class="tabs">
      <button class="tab" :class="{ active: tab === 'sites' }" @click="tab = 'sites'; loadAll()">
        <ShieldAlert :size="14" /> {{ $t('tamper.tabSites') }}
        <span v-if="protections.length" class="count-badge">{{ protections.length }}</span>
      </button>
      <button class="tab" :class="{ active: tab === 'history' }" @click="tab = 'history'; loadHistory()">
        <History :size="14" /> {{ $t('tamper.tabHistory') }}
        <span v-if="history.length" class="count-badge warn">{{ history.length }}</span>
      </button>
    </div>

    <!-- ============ 标签页一：防护站点 ============ -->
    <div v-if="tab === 'sites'" class="tab-body">
      <div class="table-toolbar">
        <span class="hint">{{ $t('tamper.sitesHint') }}</span>
        <button class="btn primary" @click="openAdd">{{ $t('tamper.addBtn') }}</button>
      </div>

      <div v-if="loading" class="empty">{{ $t('common.loading') }}</div>
      <div v-else-if="protections.length === 0" class="empty">
        <ShieldAlert :size="40" style="color:#9ca3af;" />
        <div>{{ $t('tamper.noProtections') }}</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{{ $t('tamper.colSite') }}</th>
              <th>{{ $t('tamper.colRoot') }}</th>
              <th>{{ $t('tamper.colProtected') }}</th>
              <th>{{ $t('tamper.colIgnores') }}</th>
              <th>{{ $t('tamper.colLastBackup') }}</th>
              <th>{{ $t('tamper.colLastScan') }}</th>
              <th>{{ $t('tamper.colLastTamper') }}</th>
              <th>{{ $t('tamper.colStatus') }}</th>
              <th>{{ $t('tamper.colAction') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in protections" :key="p.site_id">
              <td>
                {{ p.site_name }}
                <div class="sub" :class="{ 'warn-text': !p.root_exists }" :title="p.root_exists ? '' : $t('tamper.rootMissing')">
                  {{ p.site_id }}
                </div>
              </td>
              <td class="mono">{{ p.root }}</td>
              <td>{{ $t('tamper.protectedCount', { count: p.protected_count }) }}</td>
              <td>{{ $t('tamper.ignoreCount', { count: p.ignore_count }) }}</td>
              <td class="mono">{{ fmtTime(p.last_backup_at) }}</td>
              <td class="mono">{{ fmtTime(p.last_scan_at) }}</td>
              <td class="mono" :class="{ 'warn-text': p.last_tamper_at }">{{ fmtTime(p.last_tamper_at) }}</td>
              <td>
                <span class="badge" :class="p.enabled ? 'ok' : 'off'">
                  {{ p.enabled ? $t('tamper.siteStatusOn') : $t('tamper.siteStatusOff') }}
                </span>
              </td>
              <td class="actions-cell">
                <button class="btn mini" :disabled="busy" @click="toggleSite(p)">{{ p.enabled ? $t('tamper.siteDisable') : $t('tamper.siteEnable') }}</button>
                <button class="btn mini" :disabled="busy" @click="doBackupNow(p)">{{ $t('tamper.backupNow') }}</button>
                <button class="btn mini" :disabled="busy" @click="doScanNow(p)">{{ $t('tamper.scanNow') }}</button>
                <button class="btn mini" :disabled="busy" @click="openEdit(p)">{{ $t('common.edit') }}</button>
                <button class="btn mini danger-text" :disabled="busy" @click="doDelete(p)">{{ $t('common.delete') }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 标签页二：篡改记录 ============ -->
    <div v-if="tab === 'history'" class="tab-body">
      <div v-if="loading" class="empty">{{ $t('common.loading') }}</div>
      <div v-else-if="history.length === 0" class="empty">
        <ShieldCheck :size="40" style="color:#2a8f3c;" />
        <div>{{ $t('tamper.historyEmpty') }}</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr><th>{{ $t('tamper.colTime') }}</th><th>{{ $t('tamper.colSite') }}</th><th>{{ $t('tamper.colFile') }}</th><th>{{ $t('tamper.colReason') }}</th><th>{{ $t('tamper.colResult') }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(ev, i) in history" :key="ev.id || i">
              <td class="mono">{{ fmtTime(ev.time) }}</td>
              <td>{{ ev.site_name || ev.site_id }}</td>
              <td class="mono">{{ ev.file }}</td>
              <td>{{ ev.reason === 'missing' ? $t('tamper.reasonMissing') : $t('tamper.reasonHashMismatch') }}</td>
              <td>
                <span class="badge" :class="ev.restored ? 'ok' : 'danger'">
                  {{ ev.restored ? $t('tamper.restored') : $t('tamper.restoreFailed') }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 添加 / 编辑表单弹窗 ============ -->
    <div v-if="formOpen" class="modal-overlay" @click.self="formOpen = false">
      <div class="modal">
        <h3>
          <ShieldAlert :size="16" />
          {{ editing ? $t('tamper.formTitleEdit', { name: editing.site_name }) : $t('tamper.formTitle') }}
        </h3>

        <!-- 站点选择（添加时） -->
        <label v-if="!editing" class="field">
          <span class="label">{{ $t('tamper.selectSite') }}</span>
          <select v-model="form.site_id" @change="onSiteChange">
            <option value="">{{ $t('tamper.selectSiteHint') }}</option>
            <option v-for="c in availableCandidates" :key="c.site_id" :value="c.site_id">
              {{ c.name }}（{{ c.root || $t('tamper.noRoot') }}）
            </option>
          </select>
        </label>

        <label class="field">
          <span class="label">{{ $t('tamper.siteNameLabel') }}</span>
          <input v-model.trim="form.site_name" maxlength="128" />
        </label>

        <label class="field">
          <span class="label">{{ $t('tamper.rootLabel') }}</span>
          <input v-model.trim="form.root" placeholder="/var/www/html" spellcheck="false" />
        </label>

        <label class="field">
          <span class="label">{{ $t('tamper.protectedFilesLabel') }}</span>
          <textarea v-model="form.protected_files" rows="4" :placeholder="$t('tamper.protectedFilesPlaceholder')" spellcheck="false"></textarea>
          <span class="hint">{{ $t('tamper.protectedFilesHint') }}</span>
        </label>

        <label class="field">
          <span class="label">{{ $t('tamper.ignoreLabel') }}</span>
          <textarea v-model="form.ignore_patterns" rows="3" :placeholder="$t('tamper.ignorePlaceholder')" spellcheck="false"></textarea>
          <span class="hint">{{ $t('tamper.ignoreHint') }}</span>
        </label>

        <!-- 内置默认忽略规则（始终生效，无需配置） -->
        <div class="field">
          <span class="label">{{ $t('tamper.defaultIgnoreTitle') }}</span>
          <div class="default-ignore">
            <code v-for="p in defaultIgnorePatterns" :key="p" class="pat">{{ p }}</code>
          </div>
          <span class="hint">{{ $t('tamper.defaultIgnoreHint') }}</span>
        </div>

        <div class="field-row">
          <label class="field">
            <span class="label">{{ $t('tamper.backupIntervalLabel') }}</span>
            <input type="number" min="1" max="10080" v-model.number="form.backup_interval_minutes" />
          </label>
          <label class="field">
            <span class="label">{{ $t('tamper.scanIntervalLabel') }}</span>
            <input type="number" min="5" max="3600" v-model.number="form.scan_interval_seconds" />
          </label>
        </div>

        <div v-if="formError" class="error">{{ formError }}</div>

        <div class="actions">
          <button class="btn" :disabled="saving" @click="formOpen = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="saving" @click="saveForm">
            {{ saving ? $t('common.saving') : $t('common.save') }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 完全关闭确认 ============ -->
    <div v-if="showDisableConfirm" class="modal-overlay" @click.self="showDisableConfirm = false">
      <div class="modal">
        <h3 class="danger-title"><OctagonAlert :size="18" /> {{ $t('tamper.confirmDisableAllTitle') }}</h3>
        <div class="danger-box">
          <p><b>{{ $t('tamper.confirmDisableAll') }}</b></p>
          <p class="warn-text">{{ $t('tamper.alertDisableWarning') }}</p>
        </div>
        <div class="actions">
          <button class="btn" :disabled="busy" @click="showDisableConfirm = false">{{ $t('common.cancel') }}</button>
          <button class="btn danger-btn" :disabled="busy" @click="doDisableAll">{{ $t('tamper.confirmDisableBtn') }}</button>
        </div>
      </div>
    </div>

    <!-- 右键菜单 -->
    <Teleport to="body">
      <div v-if="ctxMenu.show" class="context-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }" @click.stop>
        <template v-for="(item, idx) in ctxMenu.items" :key="idx">
          <div v-if="item.divider" class="menu-divider"></div>
          <div v-else class="menu-item" :class="{ danger: item.danger }" @click="item.action">{{ item.label }}</div>
        </template>
      </div>
    </Teleport>

    <!-- 高风险操作二次确认：删除防篡改任务需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="confirm.title"
      :message="confirm.message"
      :input-label="t('confirmDanger.inputPwdLabel')"
      :placeholder="t('confirmDanger.inputPwdPlaceholder')"
      :confirm-label="$t('common.delete')"
      @confirm="doConfirm"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ShieldAlert, ShieldCheck, History, Power, OctagonAlert } from 'lucide-vue-next'
import { tamperApi } from '../../api'
import { tamperState, refreshTamperStatus, enableProtection, disableForMinutes, disableManual } from '../../store/tamper'
import ConfirmDialog from '../ConfirmDialog.vue'

const { t } = useI18n()

const tab = ref('sites')
const loading = ref(false)
const busy = ref(false)
const protections = ref([])
const candidates = ref([])
const history = ref([])
// 内置默认忽略规则（由后端返回；后端不可用时的兜底展示列表）
const DEFAULT_IGNORE_PATTERNS = [
  '**/*.log', '**/*.db', '**/*.sqlite', '**/*.sqlite3', '**/*.sqlitedb',
  '**/*.db3', '**/*.sdb', '**/*.sqlite-wal', '**/*.sqlite-shm', '**/*.wal',
  '**/*.shm', '**/*.tmp', '**/*.swp', '**/*.lock',
]
const defaultIgnorePatterns = ref([...DEFAULT_IGNORE_PATTERNS])

// 全局状态
const globalStatusClass = computed(() => {
  if (!tamperState.enabled) return 'off'
  if (tamperState.temporarilyDisabled) return 'warn'
  return 'ok'
})
const globalStatusText = computed(() => {
  if (!tamperState.enabled) return t('tamper.globalDisabled')
  if (tamperState.temporarilyDisabled) return t('tamper.globalTemporarily')
  return t('tamper.globalRunning')
})
const disabledRemainMin = computed(() => {
  if (!tamperState.disabledUntil) return 0
  const d = new Date(tamperState.disabledUntil)
  if (isNaN(d.getTime())) return 0
  return Math.max(0, Math.ceil((d.getTime() - Date.now()) / 60000))
})

// 表单
const formOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const formError = ref('')
const form = reactive({
  site_id: '',
  site_name: '',
  root: '',
  protected_files: '',
  ignore_patterns: '',
  backup_interval_minutes: 60,
  scan_interval_seconds: 15,
})

const showDisableConfirm = ref(false)
const ctxMenu = ref({ show: false, x: 0, y: 0, items: [] })
// 高风险操作二次确认状态（删除防篡改任务）
const confirm = ref({ show: false, title: '', message: '', action: null })

// 添加时可选的站点：候选且尚未配置防护
const availableCandidates = computed(() => {
  const protectedIds = new Set(protections.value.map((p) => p.site_id))
  return candidates.value.filter((c) => !protectedIds.has(c.site_id) && c.site_id)
})

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function loadAll() {
  loading.value = true
  try {
    const [st, sites] = await Promise.all([refreshTamperStatus(), tamperApi.sites()])
    protections.value = (sites && sites.protections) || []
    candidates.value = (sites && sites.candidates) || []
    // 以后端返回的内置默认忽略规则为准（保持一致，避免前端硬编码过期）
    const defaults = st?.sites?.[0]?.default_ignore_patterns
    if (Array.isArray(defaults) && defaults.length) {
      defaultIgnorePatterns.value = defaults
    }
  } catch (e) {
    // 接口失败时保留已有数据
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  loading.value = true
  try {
    const r = await tamperApi.history()
    history.value = (r && r.history) || []
  } catch (e) {
    history.value = []
  } finally {
    loading.value = false
  }
}

// ---------- 全局开关 ----------
async function doEnable() {
  busy.value = true
  try {
    await enableProtection()
  } catch (e) {
    alert(t('tamper.opFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    busy.value = false
  }
}

async function doDisable10m() {
  if (!window.confirm(t('tamper.confirmDisable10m'))) return
  busy.value = true
  try {
    await disableForMinutes(10)
  } catch (e) {
    alert(t('tamper.opFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    busy.value = false
  }
}

function confirmDisableAll() {
  showDisableConfirm.value = true
}

async function doDisableAll() {
  busy.value = true
  try {
    await disableManual()
    showDisableConfirm.value = false
  } catch (e) {
    alert(t('tamper.opFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    busy.value = false
  }
}

// ---------- 站点操作 ----------
async function toggleSite(p) {
  busy.value = true
  try {
    await tamperApi.update(p.site_id, { enabled: !p.enabled })
    await loadAll()
  } catch (e) {
    alert(t('tamper.opFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    busy.value = false
  }
}

async function doBackupNow(p) {
  busy.value = true
  try {
    await tamperApi.backupNow(p.site_id)
    alert(t('tamper.backupNowDone'))
    await loadAll()
  } catch (e) {
    alert(t('tamper.opFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    busy.value = false
  }
}

async function doScanNow(p) {
  busy.value = true
  try {
    const r = await tamperApi.scanNow(p.site_id)
    alert(t('tamper.scanNowResult', { count: r.tampered_count || 0 }))
    await loadAll()
  } catch (e) {
    alert(t('tamper.opFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    busy.value = false
  }
}

function doDelete(p) {
  // 高风险操作：删除防篡改任务需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.deleteTamperTaskTitle'),
    message: t('confirmDanger.deleteTamperTaskMsg', { name: p.site_name || p.site_id }),
    action: { type: 'tamper', site_id: p.site_id }
  }
}

// ConfirmDialog 密码校验通过后执行真正的删除逻辑
async function doConfirm() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return
  busy.value = true
  try {
    await tamperApi.remove(a.site_id)
    await loadAll()
  } catch (e) {
    alert(t('tamper.opFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    busy.value = false
  }
}

// ---------- 添加 / 编辑表单 ----------
function openAdd() {
  editing.value = null
  formError.value = ''
  Object.assign(form, {
    site_id: '',
    site_name: '',
    root: '',
    protected_files: '',
    ignore_patterns: '',
    backup_interval_minutes: 60,
    scan_interval_seconds: 15,
  })
  formOpen.value = true
}

function openEdit(p) {
  editing.value = p
  formError.value = ''
  Object.assign(form, {
    site_id: p.site_id,
    site_name: p.site_name || p.site_id,
    root: p.root || '',
    protected_files: (p.protected_files || []).join('\n'),
    ignore_patterns: (p.ignore_patterns || []).join('\n'),
    backup_interval_minutes: p.backup_interval_minutes,
    scan_interval_seconds: p.scan_interval_seconds,
  })
  formOpen.value = true
}

function onSiteChange() {
  const c = candidates.value.find((x) => x.site_id === form.site_id)
  if (!c) return
  form.site_name = c.name
  form.root = c.root || ''
}

async function saveForm() {
  if (saving.value) return
  formError.value = ''
  const body = {
    site_id: form.site_id,
    site_name: form.site_name || form.site_id,
    root: form.root,
    protected_files: (form.protected_files || '').split('\n').map((s) => s.trim()).filter(Boolean),
    ignore_patterns: (form.ignore_patterns || '').split('\n').map((s) => s.trim()).filter(Boolean),
    backup_interval_minutes: form.backup_interval_minutes,
    scan_interval_seconds: form.scan_interval_seconds,
  }
  if (!body.root) { formError.value = t('tamper.rootRequired'); return }
  if (body.protected_files.length === 0) { formError.value = t('tamper.protectedRequired'); return }
  if (editing.value) {
    if (!editing.value.site_id) { formError.value = t('tamper.siteRequired'); return }
    body.site_id = editing.value.site_id
  } else if (!body.site_id) {
    formError.value = t('tamper.siteRequired')
    return
  }
  saving.value = true
  try {
    if (editing.value) await tamperApi.update(editing.value.site_id, body)
    else await tamperApi.create(body)
    formOpen.value = false
    await loadAll()
  } catch (e) {
    formError.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

// ---------- 右键菜单 ----------
function closeMenus() { ctxMenu.value.show = false }

onMounted(() => {
  loadAll()
  loadHistory()
})
</script>

<style scoped>
.tamper-window { padding: 0; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; } /* 内嵌聚合窗口：外边距由父容器提供 */
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 8px; }
.status-badge { display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.status-badge.ok { background: #d1fae5; color: #065f46; }
.status-badge.warn { background: #fed7aa; color: #9a3412; }
.status-badge.off { background: #fee2e2; color: #b91c1c; }
.remain { font-size: 12px; color: #9a3412; }
.toolbar-actions { display: flex; gap: 8px; }

.tabs { display: flex; gap: 4px; margin-bottom: 10px; }
.tab {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border: 1px solid #d1d5db; background: #fff;
  border-radius: 8px; cursor: pointer; font-size: 13px; color: #374151;
}
.tab:hover { background: #f9fafb; }
.tab.active { background: #111827; color: #fff; border-color: #111827; }
.count-badge { min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px; background: #2563eb; color: #fff; font-size: 10px; display: inline-flex; align-items: center; justify-content: center; }
.count-badge.warn { background: #dc2626; }

.tab-body { flex: 1; overflow: auto; }
.table-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.hint { color: #6e6e73; font-size: 12px; }
.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; }
.sub { font-size: 10px; color: #888; }
.actions-cell { display: flex; gap: 4px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.mini { padding: 3px 8px; font-size: 11.5px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.btn.danger-btn { background: #dc2626; color: #fff; border-color: #dc2626; }
.btn.danger-btn:hover:not(:disabled) { background: #b91c1c; }
.danger-text { color: #b91c1c; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.badge.danger { background: #fee2e2; color: #b91c1c; font-weight: 600; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.warn-text { color: #b91c1c; }

/* 弹窗 */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 18px; width: 560px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,0.18); max-height: 92vh; overflow: auto; }
.modal h3 { margin: 0 0 12px; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.danger-title { color: #b91c1c; }
.field { display: block; margin-bottom: 10px; }
.field .label { display: block; font-size: 11px; color: #1d1d1f; font-weight: 600; margin-bottom: 5px; }
.field input, .field select, .field textarea { width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; font-family: inherit; }
.field textarea { resize: vertical; font-family: ui-monospace, Menlo, Consolas, monospace; }
.field input:focus, .field select:focus, .field textarea:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.field .hint { display: block; margin-top: 4px; }
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }
.default-ignore {
  display: flex; flex-wrap: wrap; gap: 4px; padding: 8px 10px;
  background: #f6f8fb; border: 1px dashed #c7d2e0; border-radius: 8px;
}
.default-ignore .pat {
  font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px;
  color: #0a3d7a; background: rgba(10, 132, 255, 0.08); border-radius: 4px; padding: 1px 6px;
}
.error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
.danger-box { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 12px 14px; font-size: 13px; color: #7f1d1d; line-height: 1.7; }
.danger-box p { margin: 4px 0; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }

/* 右键菜单 */
.context-menu { position: fixed; background: #fff; border: 1px solid rgba(0,0,0,0.1); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.15); z-index: 3000; min-width: 160px; padding: 4px 0; }
.menu-item { padding: 8px 14px; font-size: 12.5px; cursor: pointer; color: #374151; }
.menu-item:hover { background: #f5f5f7; }
.menu-item.danger { color: #b91c1c; }
.menu-divider { height: 1px; background: #e5e7eb; margin: 4px 0; }
</style>
