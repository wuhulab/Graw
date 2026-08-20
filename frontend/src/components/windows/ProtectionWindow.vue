<template>
  <div class="prot-window" @click="closeMenus">
    <!-- 顶部工具栏：标签页 + 状态摘要 -->
    <div class="toolbar">
      <div class="tabs">
        <button class="tab" :class="{ active: tab === 'docker' }" @click="tab = 'docker'; loadAll()">
          <HardDrive :size="14" /> {{ $t('protection.tabDocker') }}
          <span v-if="dockerWarnings.length" class="count-badge">{{ dockerWarnings.length }}</span>
        </button>
        <button class="tab" :class="{ active: tab === 'db' }" @click="tab = 'db'; loadAll()">
          <DatabaseBackup :size="14" /> {{ $t('protection.tabFiles') }}
          <span v-if="uncoveredDb.length" class="count-badge warn">{{ uncoveredDb.length }}</span>
        </button>
        <button class="tab" :class="{ active: tab === 'ignored' }" @click="tab = 'ignored'; loadIgnored()">
          <BellRing :size="14" /> {{ $t('protection.tabIgnores') }}
          <span v-if="ignoredList.length" class="count-badge">{{ ignoredList.length }}</span>
        </button>
      </div>
      <button class="btn" :disabled="loading" @click="loadAll">
        {{ loading ? $t('protection.scanning') : $t('protection.rescan') }}
      </button>
      <span class="hint">{{ $t('protection.hint', { days: ignoreDays }) }}</span>
    </div>

    <!-- ============ 标签页一：Docker 数据库 ============ -->
    <div v-if="tab === 'docker'" class="tab-body">
      <div v-if="dockerUnavailable" class="empty warn-text">
        {{ $t('protection.dockerUnavailableDetail', { reason: dockerReason }) }}
      </div>
      <div v-else-if="dockerWarnings.length === 0" class="empty">
        <ShieldCheck :size="40" style="color:#2a8f3c;" />
        <div>{{ $t('protection.dockerNoRisks') }}</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr><th>{{ $t('protection.container') }}</th><th>{{ $t('protection.image') }}</th><th>{{ $t('protection.status') }}</th><th>{{ $t('protection.risk') }}</th><th>{{ $t('protection.description') }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="w in dockerWarnings" :key="w.name"
                @contextmenu.prevent="onDockerContextMenu($event, w)">
              <td>{{ w.name }}<div class="sub">{{ w.id }}</div></td>
              <td class="mono">{{ w.image }}</td>
              <td><span class="badge" :class="w.status.startsWith('Up') ? 'ok' : 'off'">{{ w.status }}</span></td>
              <td>
                <span class="badge" :class="w.level === 'danger' ? 'danger' : 'warn'">
                  {{ w.level === 'danger' ? $t('protection.levelDanger') : $t('protection.levelWarn') }}
                </span>
              </td>
              <td class="reason">
                {{ w.message }}
                <div class="sub" v-if="w.data_dir">{{ $t('protection.suggestMountDir') }}<span class="mono">{{ w.data_dir }}</span></div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 标签页二：数据库文件 ============ -->
    <div v-if="tab === 'db'" class="tab-body">
      <!-- 多选工具栏 -->
      <div class="select-bar" v-if="uncoveredDb.length > 0">
        <label class="check-label">
          <input type="checkbox" :checked="allDbSelected" @change="toggleAllDb($event.target.checked)" />
          <span>{{ $t('protection.selectAll') }}</span>
        </label>
        <span class="selected-count">{{ $t('protection.selectedCount', { count: selectedDbPaths.length }) }}</span>
        <button class="btn primary" :disabled="selectedDbPaths.length === 0" @click="batchAddBackup">
          {{ $t('protection.addToBackup') }}
        </button>
      </div>

      <h4>{{ $t('protection.uncoveredTitle') }}</h4>
      <div v-if="uncoveredDb.length === 0" class="empty">
        <ShieldCheck :size="40" style="color:#2a8f3c;" />
        <div>{{ $t('protection.noUnbackedFiles') }}</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead><tr><th style="width:30px;"></th><th>{{ $t('protection.path') }}</th><th>{{ $t('protection.type') }}</th><th>{{ $t('protection.size') }}</th></tr></thead>
          <tbody>
            <tr v-for="f in uncoveredDb" :key="f.path"
                @contextmenu.prevent="onDbContextMenu($event, f)">
              <td @contextmenu.prevent.stop><input type="checkbox" :value="f.path" v-model="selectedDbPaths" /></td>
              <td class="mono">{{ f.path }}</td>
              <td>{{ f.type === 'dir' ? $t('protection.dirType') : $t('protection.sqliteType') }}</td>
              <td>{{ fmtSize(f.size) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="backups-head">
        <h4>{{ $t('protection.backupsTitle', { count: backups.length }) }}</h4>
        <button class="btn" :disabled="!backupDir" :title="backupDir" @click="openBackupDir">
          <FolderOpen :size="14" /> {{ $t('protection.openBackupDir') }}
        </button>
      </div>
      <div v-if="backups.length === 0" class="empty small">{{ $t('protection.noBackups') }}</div>
      <div v-else class="table-wrap">
        <table>
          <thead><tr><th>{{ $t('protection.path') }}</th><th>{{ $t('protection.schedule') }}</th><th>{{ $t('protection.createdAt') }}</th></tr></thead>
          <tbody>
            <tr v-for="b in backups" :key="b.path"
                @contextmenu.prevent="onBackupContextMenu($event, b)">
              <td class="mono">{{ b.path }}</td>
              <td class="mono">{{ b.schedule || '—' }}</td>
              <td>{{ (b.created_at || '').replace('T', ' ').slice(0, 19) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 标签页三：忽略列表 ============ -->
    <div v-if="tab === 'ignored'" class="tab-body">
      <div v-if="ignoredList.length === 0" class="empty">
        <ShieldCheck :size="40" style="color:#2a8f3c;" />
        <div>{{ $t('protection.noIgnores') }}</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead><tr><th>{{ $t('protection.typeCol') }}</th><th>{{ $t('protection.namePath') }}</th><th>{{ $t('protection.expires') }}</th><th>{{ $t('protection.ignoreMode') }}</th></tr></thead>
          <tbody>
            <tr v-for="(it, i) in ignoredList" :key="i"
                @contextmenu.prevent="onIgnoredContextMenu($event, it)">
              <td>{{ it.kind === 'docker' ? $t('protection.dockerContainer') : $t('protection.dbFile') }}</td>
              <td class="mono">{{ it.name || it.key }}</td>
              <td>{{ it.permanent ? $t('protection.permanent') : (it.expire_at || '').replace('T', ' ').slice(0, 19) }}</td>
              <td><span class="badge" :class="it.permanent ? 'danger' : 'warn'">{{ it.permanent ? $t('protection.ignorePermanent') : $t('protection.ignoreTemporary') }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 右键菜单 ============ -->
    <Teleport to="body">
      <div v-if="ctxMenu.show" class="context-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }" @click.stop>
        <template v-for="(item, idx) in ctxMenuItems" :key="idx">
          <div v-if="item.divider" class="menu-divider"></div>
          <div v-else class="menu-item" :class="{ danger: item.danger }" @click="item.action">
            {{ item.label }}
          </div>
        </template>
      </div>
    </Teleport>

    <!-- ============ 一键映射确认弹窗 ============ -->
    <div v-if="showMapModal" class="modal-overlay" @click.self="showMapModal = false">
      <div class="modal wide">
        <h3><HardDrive :size="16" /> {{ $t('protection.confirmOneClickMapTitle', { container: mapTarget?.name }) }}</h3>
        <p class="modal-desc">{{ $t('protection.oneClickMapDesc') }}</p>
        <ul class="modal-list">
          <li>{{ $t('protection.mapStep1') }}</li>
          <li>{{ $t('protection.mapStep2') }}<span class="mono">{{ mapTarget?.name ? 'graw-data-' + mapTarget.name + '-' + $t('protection.timestampPlaceholder') : '' }}</span>；</li>
          <li>{{ $t('protection.mapStep3', { dir: mapTarget?.data_dir }) }}</li>
          <li>{{ $t('protection.mapStep4') }}</li>
          <li>{{ $t('protection.mapStep5') }}</li>
        </ul>
        <p class="modal-desc warn-text">{{ $t('protection.oneClickMapWarning') }}</p>
        <div class="actions">
          <button class="btn" @click="showMapModal = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="mapping" @click="doMap">
            {{ mapping ? $t('protection.mapping') : $t('protection.confirmMapping') }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 忽略确认弹窗（临时 + 永久） ============ -->
    <div v-if="showIgnoreConfirm" class="modal-overlay" @click.self="closeIgnoreConfirm">
      <div class="modal">
        <h3 class="danger-title"><OctagonAlert :size="18" /> {{ $t('protection.dangerConfirm') }}</h3>
        <div class="danger-box">
          <p><b>{{ $t('protection.ignoreDescription') }}</b></p>
          <p class="mono">{{ ignoreTarget?.label }}</p>
          <p>{{ $t('protection.ignoreWarning1') }}<b>{{ $t('protection.ignoreWarningBold1') }}</b>{{ $t('protection.ignoreWarning2') }}<b style="color:#b91c1c;">{{ $t('protection.ignoreWarningBold2') }}</b>{{ $t('protection.ignoreWarning3') }}</p>
          <p><b style="color:#b91c1c;">{{ $t('protection.prodWarning') }}</b></p>
        </div>

        <!-- 忽略模式选择 -->
        <div class="ignore-options">
          <label class="ignore-option" :class="{ active: !ignorePermanent }">
            <input type="radio" :value="false" v-model="ignorePermanent" />
            <div>
              <div class="opt-title">{{ $t('protection.ignoreTemporary') }}</div>
              <div class="opt-desc">{{ $t('protection.ignoreTmpDesc', { days: ignoreDays }) }}</div>
            </div>
          </label>
          <label class="ignore-option" :class="{ active: ignorePermanent, danger: true }">
            <input type="radio" :value="true" v-model="ignorePermanent" />
            <div>
              <div class="opt-title">{{ $t('protection.ignorePermanent') }}</div>
              <div class="opt-desc">{{ $t('protection.ignorePermDesc') }}</div>
            </div>
          </label>
        </div>

        <label class="confirm-row">
          <input type="checkbox" v-model="ignoreAck" />
          <span>{{ $t('protection.ignoreConfirmCheckbox', { ignoreMode: ignorePermanent ? $t('protection.permanent') : $t('protection.ignoreTemporary') }) }}</span>
        </label>
        <div class="actions">
          <button class="btn" @click="closeIgnoreConfirm">{{ $t('protection.back') }}</button>
          <button class="btn danger-btn" :disabled="!ignoreAck || ignoring" @click="doIgnore">
            {{ ignoring ? $t('protection.processing') : (ignorePermanent ? $t('protection.confirmPermanent') : $t('protection.confirmTemporary')) }}
          </button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除防护备份需输入面板密码 -->
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
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { protectionApi } from '../../api'
import {
  HardDrive, DatabaseBackup, BellRing, ShieldCheck, OctagonAlert, FolderOpen
} from 'lucide-vue-next'
import ConfirmDialog from '../ConfirmDialog.vue'

const { t } = useI18n()
const emit = defineEmits(['openFiles'])
const ignoreDays = 7

const tab = ref('docker')
const loading = ref(false)

// Docker 扫描结果
const dockerWarnings = ref([])
const dockerUnavailable = ref(false)
const dockerReason = ref('')

// 数据库文件扫描结果
const allDbFiles = ref([])
const backups = ref([])
// 自动备份目录（来自 /protection/status，用于「打开备份目录」跳转）
const backupDir = ref('')
// 多选
const selectedDbPaths = ref([])

// 忽略列表
const ignoredList = ref([])

// 一键映射弹窗
const showMapModal = ref(false)
const mapTarget = ref(null)
const mapping = ref(false)

// 忽略确认弹窗
const showIgnoreConfirm = ref(false)
const ignoreTarget = ref(null)
const ignoreAck = ref(false)
const ignoring = ref(false)
const ignorePermanent = ref(false)
// 高风险操作二次确认状态（删除防护备份）
const confirm = ref({ show: false, title: '', message: '', action: null })

// 右键菜单
const ctxMenu = ref({ show: false, x: 0, y: 0, items: [] })
const ctxMenuItems = computed(() => ctxMenu.value.items)

const uncoveredDb = computed(() => allDbFiles.value.filter(f => !f.covered))
const allDbSelected = computed(() => uncoveredDb.value.length > 0 && selectedDbPaths.value.length === uncoveredDb.value.length)

function toggleAllDb(checked) {
  selectedDbPaths.value = checked ? uncoveredDb.value.map(f => f.path) : []
}

async function loadDocker() {
  dockerUnavailable.value = false
  try {
    const r = await protectionApi.scanDocker()
    if (!r.available) {
      dockerUnavailable.value = true
      dockerReason.value = r.reason || t('protection.unknownReason')
      dockerWarnings.value = []
    } else {
      dockerWarnings.value = r.warnings || []
    }
  } catch (e) {
    dockerUnavailable.value = true
    dockerReason.value = e.response?.data?.detail || e.message
    dockerWarnings.value = []
  }
}

async function loadDb() {
  try {
    allDbFiles.value = await protectionApi.scanDbFiles()
    backups.value = (await protectionApi.listBackups()).backups || []
    // 清理已不在扫描结果中的选中项
    selectedDbPaths.value = selectedDbPaths.value.filter(p => uncoveredDb.value.some(f => f.path === p))
  } catch (e) {
    allDbFiles.value = []
    backups.value = []
  }
}

async function loadIgnored() {
  try {
    ignoredList.value = (await protectionApi.listIgnored()).ignored || []
  } catch (e) {
    ignoredList.value = []
  }
}

// 拉取保护机制状态摘要（含自动备份目录），供「打开备份目录」按钮使用
async function loadStatus() {
  try {
    const s = await protectionApi.status()
    backupDir.value = s.backup_dir || ''
  } catch (e) {
    backupDir.value = ''
  }
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadDocker(), loadDb(), loadIgnored(), loadStatus()])
  } finally {
    loading.value = false
  }
}

// 打开文件管理器并跳转到自动备份目录
function openBackupDir() {
  if (!backupDir.value) return
  emit('openFiles', { path: backupDir.value })
}

// ---------- 右键菜单 ----------
function closeMenus() {
  ctxMenu.value.show = false
}

function showCtxMenu(e, items) {
  // 防止菜单溢出视口右下边界
  const x = Math.min(e.clientX, window.innerWidth - 180)
  const y = Math.min(e.clientY, window.innerHeight - items.length * 32 - 20)
  ctxMenu.value = { show: true, x, y, items }
}

function onDockerContextMenu(e, w) {
  showCtxMenu(e, [
    { label: t('protection.oneClickMap'), action: () => { closeMenus(); confirmMap(w) } },
    { divider: true },
    { label: t('protection.ignoreTemporary'), action: () => { closeMenus(); openIgnoreConfirm('docker', w.name, w.name, false) } },
    { label: t('protection.ignorePermanent'), danger: true, action: () => { closeMenus(); openIgnoreConfirm('docker', w.name, w.name, true) } },
  ])
}

function onDbContextMenu(e, f) {
  showCtxMenu(e, [
    { label: t('protection.addBackupSingle'), action: () => { closeMenus(); addBackup(f) } },
    { label: t('protection.batchAddBackupWithSelected'), action: () => { closeMenus(); batchAddBackupWith(f.path) } },
    { divider: true },
    { label: t('protection.ignoreTemporary'), action: () => { closeMenus(); openIgnoreConfirm('db_file', f.path, f.path, false) } },
    { label: t('protection.ignorePermanent'), danger: true, action: () => { closeMenus(); openIgnoreConfirm('db_file', f.path, f.path, true) } },
  ])
}

function onBackupContextMenu(e, b) {
  showCtxMenu(e, [
    { label: t('protection.removeFromBackup'), action: () => { closeMenus(); removeBackup(b.path) } },
  ])
}

function onIgnoredContextMenu(e, it) {
  showCtxMenu(e, [
    { label: t('protection.restore'), action: () => { closeMenus(); restoreIgnore(it.kind, it.key) } },
  ])
}

// ---------- 一键映射 ----------
function confirmMap(w) {
  mapTarget.value = w
  showMapModal.value = true
}

async function doMap() {
  mapping.value = true
  try {
    const r = await protectionApi.mapDocker(mapTarget.value.name)
    showMapModal.value = false
    alert(t('protection.mapSuccess', { volume: r.volume, dir: r.data_dir }))
    await loadAll()
  } catch (e) {
    alert(t('protection.oneClickMapFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    mapping.value = false
  }
}

// ---------- 加入备份 ----------
async function addBackup(f) {
  if (!window.confirm(t('protection.confirmAddBackup', { path: f.path }))) return
  try {
    const r = await protectionApi.addBackup(f.path)
    alert(r.already ? t('protection.alreadyInBackup') : t('protection.addedToBackup'))
    await loadDb()
  } catch (e) {
    alert(t('protection.addBackupFailed', { error: e.response?.data?.detail || e.message }))
  }
}

async function batchAddBackup() {
  const paths = selectedDbPaths.value
  if (paths.length === 0) return
  if (!window.confirm(t('protection.confirmBatchAdd', { count: paths.length }))) return
  try {
    const r = await protectionApi.batchBackup(paths)
    const ok = r.results.filter(x => x.ok).length
    const fail = r.results.filter(x => !x.ok).length
    alert(t('protection.batchAddResult', { success: ok, failedText: fail ? t('protection.batchFailText', { count: fail }) : '' }))
    selectedDbPaths.value = []
    await loadDb()
  } catch (e) {
    alert(t('protection.batchAddFailed', { error: e.response?.data?.detail || e.message }))
  }
}

async function batchAddBackupWith(extraPath) {
  // 右键"批量加入备份"：把右键项 + 已选项合并去重
  const paths = [...new Set([extraPath, ...selectedDbPaths.value])]
  if (!window.confirm(t('protection.confirmBatchAdd', { count: paths.length }))) return
  try {
    const r = await protectionApi.batchBackup(paths)
    const ok = r.results.filter(x => x.ok).length
    const fail = r.results.filter(x => !x.ok).length
    alert(t('protection.batchAddResult', { success: ok, failedText: fail ? t('protection.batchFailText', { count: fail }) : '' }))
    selectedDbPaths.value = []
    await loadDb()
  } catch (e) {
    alert(t('protection.batchAddFailed', { error: e.response?.data?.detail || e.message }))
  }
}

function removeBackup(path) {
  // 高风险操作：删除防护备份需输入面板密码确认
  confirm.value = {
    show: true,
    title: t('confirmDanger.deleteProtectionBackupTitle'),
    message: t('confirmDanger.deleteProtectionBackupMsg', { path }),
    action: { type: 'backup', path }
  }
}

// ConfirmDialog 密码校验通过后执行真正的删除逻辑
async function doConfirm() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return
  try {
    await protectionApi.removeBackup(a.path)
    await loadDb()
  } catch (e) {
    alert(t('protection.removeBackupFailed', { error: e.response?.data?.detail || e.message }))
  }
}

// ---------- 忽略（临时 + 永久） ----------
function openIgnoreConfirm(kind, key, label, permanent) {
  ignoreTarget.value = { kind, key, label }
  ignoreAck.value = false
  ignorePermanent.value = !!permanent
  showIgnoreConfirm.value = true
}

function closeIgnoreConfirm() {
  showIgnoreConfirm.value = false
  ignoreTarget.value = null
}

async function doIgnore() {
  ignoring.value = true
  try {
    await protectionApi.ignore(
      ignoreTarget.value.kind,
      ignoreTarget.value.key,
      ignoreTarget.value.label,
      ignorePermanent.value
    )
    showIgnoreConfirm.value = false
    await loadAll()
  } catch (e) {
    alert(t('protection.removeBackupFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    ignoring.value = false
  }
}

async function restoreIgnore(kind, key) {
  if (!window.confirm(t('protection.confirmRestore'))) return
  try {
    await protectionApi.unignore(kind, key)
    await loadAll()
  } catch (e) {
    alert(t('protection.restoreFailed', { error: e.response?.data?.detail || e.message }))
  }
}

function fmtSize(size) {
  if (size == null) return t('protection.sizeUnknown')
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = size
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v < 10 && i > 0 ? 2 : v < 100 ? 1 : 0)} ${units[i]}`
}

onMounted(loadAll)
</script>

<style scoped>
.prot-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.tabs { display: flex; gap: 4px; }
.tab {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border: 1px solid #d1d5db; background: #fff;
  border-radius: 8px; cursor: pointer; font-size: 13px; color: #374151;
}
.tab:hover { background: #f9fafb; }
.tab.active { background: #111827; color: #fff; border-color: #111827; }
.count-badge {
  min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px;
  background: #2563eb; color: #fff; font-size: 10px;
  display: inline-flex; align-items: center; justify-content: center;
}
.count-badge.warn { background: #dc2626; }
.hint { color: #6e6e73; font-size: 12px; margin-left: auto; }

.tab-body { flex: 1; overflow: auto; }
h4 { margin: 14px 0 6px; font-size: 13px; color: #374151; }
h4:first-child { margin-top: 0; }
.backups-head { display: flex; align-items: center; gap: 10px; }
.backups-head h4 { margin: 14px 0 6px; }
.backups-head .btn { display: inline-flex; align-items: center; gap: 5px; }

.select-bar {
  display: flex; align-items: center; gap: 12px;
  padding: 6px 10px; background: #f9fafb; border: 1px solid #e5e7eb;
  border-radius: 8px; margin-bottom: 8px;
}
.check-label { display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; }
.selected-count { font-size: 12px; color: #6e6e73; }

.table-wrap { overflow: auto; max-height: 300px; border: 1px solid #e5e7eb; border-radius: 8px; margin-bottom: 10px; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: top; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Monaco, Consolas, monospace; font-size: 12px; word-break: break-all; }
.sub { font-size: 10px; color: #888; font-family: ui-monospace, Menlo, Consolas, monospace; }
.reason { min-width: 260px; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.badge.warn { background: #fed7aa; color: #9a3412; }
.badge.danger { background: #fee2e2; color: #b91c1c; font-weight: 600; }

.empty {
  text-align: center; color: #9ca3af; padding: 40px 20px;
  display: flex; flex-direction: column; align-items: center; gap: 10px;
}
.empty.small { padding: 20px; }
.warn-text { color: #b91c1c; }

/* 右键菜单 */
.context-menu {
  position: fixed; background: #fff; border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  z-index: 3000; min-width: 160px; padding: 4px 0;
}
.menu-item { padding: 8px 14px; font-size: 12.5px; cursor: pointer; color: #374151; }
.menu-item:hover { background: #f5f5f7; }
.menu-item.danger { color: #b91c1c; }
.menu-item.danger:hover { background: #fef2f2; }
.menu-divider { height: 1px; background: #e5e7eb; margin: 4px 0; }

/* 按钮 */
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.btn.danger-btn { background: #dc2626; color: #fff; border-color: #dc2626; }
.btn.danger-btn:disabled { background: #fca5a5; border-color: #fca5a5; }

/* 弹窗 */
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 18px; width: 520px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,0.18); }
.modal.wide { width: 600px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.danger-title { color: #b91c1c; }
.modal-desc { margin: 6px 0; color: #374151; font-size: 13px; }
.modal-list { margin: 8px 0 12px; padding-left: 18px; color: #374151; font-size: 13px; line-height: 1.7; }
.danger-box {
  background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px;
  padding: 12px 14px; font-size: 13px; color: #7f1d1d; line-height: 1.7;
}
.danger-box p { margin: 4px 0; }

/* 忽略选项卡片 */
.ignore-options { display: flex; gap: 10px; margin: 14px 0; }
.ignore-option {
  flex: 1; display: flex; align-items: flex-start; gap: 8px;
  padding: 10px 12px; border: 2px solid #e5e7eb; border-radius: 8px;
  cursor: pointer; font-size: 12px; transition: border-color 0.15s;
}
.ignore-option:hover { border-color: #d1d5db; }
.ignore-option.active { border-color: #111827; }
.ignore-option.active.danger { border-color: #dc2626; }
.ignore-option input[type=radio] { margin-top: 2px; }
.opt-title { font-weight: 600; color: #111827; font-size: 13px; }
.ignore-option.active.danger .opt-title { color: #b91c1c; }
.opt-desc { color: #6e6e73; margin-top: 2px; }

.confirm-row { display: flex; align-items: flex-start; gap: 8px; margin: 14px 0 4px; font-size: 13px; color: #374151; cursor: pointer; }
.confirm-row input { margin-top: 2px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
