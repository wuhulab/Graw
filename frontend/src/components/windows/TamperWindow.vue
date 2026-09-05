<!--
  网页防篡改窗口（Tamper / 防篡改）

  这个窗口做什么：
    面板的「网页防篡改」功能。它持续监听站点根目录下受保护文件的哈希，
    文件被非法修改或删除时自动从备份还原，并把事件推送到告警中心。
    管理员可以：
      - 全局开关：开启防护、临时停用 10 分钟、完全关闭；
      - 为网站添加 / 编辑防篡改任务：配置受保护文件、忽略规则、备份与扫描间隔；
      - 对单个站点立即备份 / 扫描、启用 / 停用；
      - 切到「篡改记录」页签查看历史事件与还原结果。

  用到的后端模块：
    /api/tamper/*（端点内自行鉴权）——sites 站点与候选列表、history 篡改记录、
    {site_id} 增删改、{site_id}/backup 立即备份、{site_id}/scan 立即扫描。
    全局开关状态放在全局 store/tamper（含 WS 实时告警推送），
    界面的全局状态徽标与「剩余临时停用分钟」都读它。

  关键状态：
    tab          当前页签（sites 防护站点 / history 篡改记录）
    protections  已配置防篡改的站点列表
    candidates   尚未配置防护、可添加的候选站点
    history      篡改记录列表
    tamperState  全局防篡改状态（来自 store/tamper，含 WS 实时更新）
    form / editing   添加 / 编辑表单
    confirm      删除任务的二次确认（需输入面板密码）

  怎么被打开：
    桌面「防篡改」应用图标打开。
-->
<template>
  <div class="tamper-window" @click="closeMenus">
    <!-- 顶部：全局状态 + 全局开关 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge" :class="globalStatusClass">{{ $t('tamper.globalStatus') }}：{{ globalStatusText }}</span>
        <span v-if="tamperState.temporarilyDisabled" class="remain">{{ $t('tamper.temporarilyRemain', { minutes: disabledRemainMin }) }}</span>
      </div>
      <div class="toolbar-actions">
        <button v-if="!tamperState.enabled" class="ui-btn primary" :disabled="busy" @click="doEnable">
          <Power :size="14" /> {{ $t('tamper.enableBtn') }}
        </button>
        <template v-else>
          <button class="ui-btn" :disabled="busy" @click="doDisable10m">{{ $t('tamper.disable10mBtn') }}</button>
          <button class="ui-btn danger" :disabled="busy" @click="confirmDisableAll">{{ $t('tamper.disableBtn') }}</button>
        </template>
        <button class="ui-btn" :disabled="loading" @click="loadAll">{{ $t('common.refresh') }}</button>
      </div>
    </div>

    <!-- 标签页 -->
    <div class="ui-tabs">
      <button class="ui-tab" :class="{ active: tab === 'sites' }" @click="tab = 'sites'; loadAll()">
        <ShieldAlert :size="14" /> {{ $t('tamper.tabSites') }}
        <span v-if="protections.length" class="count-badge">{{ protections.length }}</span>
      </button>
      <button class="ui-tab" :class="{ active: tab === 'history' }" @click="tab = 'history'; loadHistory()">
        <History :size="14" /> {{ $t('tamper.tabHistory') }}
        <span v-if="history.length" class="count-badge warn">{{ history.length }}</span>
      </button>
    </div>

    <!-- ============ 标签页一：防护站点 ============ -->
    <div v-if="tab === 'sites'" class="tab-body">
      <div class="table-toolbar">
        <span class="hint">{{ $t('tamper.sitesHint') }}</span>
        <button class="ui-btn primary" @click="emit('openTamperForm', { task: null, candidates: availableCandidates })">{{ $t('tamper.addBtn') }}</button>
      </div>

      <div v-if="loading" class="ui-empty">{{ $t('common.loading') }}</div>
      <div v-else-if="protections.length === 0" class="ui-empty">
        <ShieldAlert :size="40" style="color:#9ca3af;" />
        <div>{{ $t('tamper.noProtections') }}</div>
      </div>
      <div v-else class="ui-table-wrap">
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
                <span class="ui-badge" :class="p.enabled ? 'ok' : 'off'">
                  {{ p.enabled ? $t('tamper.siteStatusOn') : $t('tamper.siteStatusOff') }}
                </span>
              </td>
              <td class="actions-cell">
                <button class="ui-btn mini" :disabled="busy" @click="toggleSite(p)">{{ p.enabled ? $t('tamper.siteDisable') : $t('tamper.siteEnable') }}</button>
                <button class="ui-btn mini" :disabled="busy" @click="doBackupNow(p)">{{ $t('tamper.backupNow') }}</button>
                <button class="ui-btn mini" :disabled="busy" @click="doScanNow(p)">{{ $t('tamper.scanNow') }}</button>
                <button class="ui-btn mini" :disabled="busy" @click="emit('openTamperForm', { task: p, candidates: availableCandidates })">{{ $t('common.edit') }}</button>
                <button class="ui-btn mini danger-text" :disabled="busy" @click="doDelete(p)">{{ $t('common.delete') }}</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 标签页二：篡改记录 ============ -->
    <div v-if="tab === 'history'" class="tab-body">
      <div v-if="loading" class="ui-empty">{{ $t('common.loading') }}</div>
      <div v-else-if="history.length === 0" class="ui-empty">
        <ShieldCheck :size="40" style="color:#2a8f3c;" />
        <div>{{ $t('tamper.historyEmpty') }}</div>
      </div>
      <div v-else class="ui-table-wrap">
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
                <span class="ui-badge" :class="ev.restored ? 'ok' : 'danger'">
                  {{ ev.restored ? $t('tamper.restored') : $t('tamper.restoreFailed') }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ============ 完全关闭确认（危险操作，保留内嵌确认） ============ -->
    <div v-if="showDisableConfirm" class="ui-modal-overlay" @click.self="showDisableConfirm = false">
      <div class="ui-modal">
        <h3 class="danger-title"><OctagonAlert :size="18" /> {{ $t('tamper.confirmDisableAllTitle') }}</h3>
        <div class="danger-box">
          <p><b>{{ $t('tamper.confirmDisableAll') }}</b></p>
          <p class="warn-text">{{ $t('tamper.alertDisableWarning') }}</p>
        </div>
        <div class="ui-actions">
          <button class="ui-btn" :disabled="busy" @click="showDisableConfirm = false">{{ $t('common.cancel') }}</button>
          <button class="ui-btn danger" :disabled="busy" @click="doDisableAll">{{ $t('tamper.confirmDisableBtn') }}</button>
        </div>
      </div>
    </div>

    <!-- 右键菜单 -->
    <Teleport to="body">
      <div v-if="ctxMenu.show" class="ui-context-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }" @click.stop>
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
import { ref, computed, onMounted, watch } from 'vue'   // 响应式状态、派生文案、挂载钩子
import { useI18n } from 'vue-i18n'   // 取 t()，界面文案跟随面板语言
import { ShieldAlert, ShieldCheck, History, Power, OctagonAlert } from 'lucide-vue-next'   // 页签 / 全局开关 / 危险确认的图标
import { tamperApi } from '../../api'   // 防篡改后端能力：/api/tamper/* 的封装
import { tamperState, refreshTamperStatus, enableProtection, disableForMinutes, disableManual } from '../../store/tamper'   // 全局防篡改状态与操作（含 WS 告警）
import ConfirmDialog from '../ConfirmDialog.vue'   // 高风险操作确认框（删除任务要求输入面板密码）
import { formBus } from '../../store/formBus'   // 表单保存信号：独立「添加/编辑防护」窗口保存成功后刷新

const { t } = useI18n()

// openTamperForm 打开独立「添加/编辑防护」表单窗口
const emit = defineEmits(['openTamperForm'])

const tab = ref('sites')      // 当前页签：'sites' 防护站点 / 'history' 篡改记录
const loading = ref(false)    // 列表加载中（首屏与空状态判断）
const busy = ref(false)       // 行内操作进行中（启停 / 备份 / 扫描等），用于禁用按钮
const protections = ref([])   // 已配置防篡改的站点列表
const candidates = ref([])    // 可添加防护的候选站点（未防护）
const history = ref([])       // 篡改记录列表

// 添加/编辑表单已拆分为独立窗口（TamperFormWindow）：保存成功后 bumpForm('tamper') 触发此处重载
watch(() => formBus.tamper, loadAll)

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
  return Math.max(0, Math.ceil((d.getTime() - Date.now()) / 60000))   // 60000 = 每分钟毫秒数，不足 1 分钟向上取整
})

const showDisableConfirm = ref(false)
const ctxMenu = ref({ show: false, x: 0, y: 0, items: [] })
// 高风险操作二次确认状态（删除防篡改任务）
const confirm = ref({ show: false, title: '', message: '', action: null })

// 添加时可选的站点：候选且尚未配置防护（传给独立表单窗口做站点下拉）
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

// --- 拉取全局状态 + 防护站点 + 候选站点，并同步后端默认忽略规则 ---
async function loadAll() {
  loading.value = true
  try {
    const [st, sites] = await Promise.all([refreshTamperStatus(), tamperApi.sites()])   // 状态与站点并发拉取，减少等待
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

// --- 拉取篡改记录（页签「篡改记录」的数据源） ---
async function loadHistory() {
  loading.value = true
  try {
    const r = await tamperApi.history()
    history.value = (r && r.history) || []    // 后端无 history 字段时兜空数组
  } catch (e) {
    history.value = []    // 拉取失败按空列表展示，避免残留旧数据误导
  } finally {
    loading.value = false
  }
}

// ---------- 全局开关 ----------
async function doEnable() {
  busy.value = true
  try {
    await enableProtection()   // 写入后端全局开关并立即开始监控
  } catch (e) {
    alert(t('tamper.opFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    busy.value = false
  }
}

async function doDisable10m() {
  if (!window.confirm(t('tamper.confirmDisable10m'))) return   // 临时停用会短暂暴露风险，先跟管理员确认
  busy.value = true
  try {
    await disableForMinutes(10)   // 后端按分钟临时停用，到期自动恢复防护
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
    await disableManual()   // 后端彻底关闭全局防护（非定时，需手动再开启）
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
    await tamperApi.update(p.site_id, { enabled: !p.enabled })   // 单站点启停：按当前状态取反
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
    await tamperApi.backupNow(p.site_id)   // 后端立即对受保护目录做一次全量备份
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
    const r = await tamperApi.scanNow(p.site_id)   // 后端立即对比文件哈希，返回被篡改数量
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
  if (!a) return   // 无待执行动作（异常触发）时直接退出
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

// ---------- 右键菜单 ----------
// 点击窗口空白处收起右键菜单（模板根节点绑定了 closeMenus）
function closeMenus() { ctxMenu.value.show = false }

onMounted(() => {
  loadAll()        // 打开即拉取全局状态与防护站点
  loadHistory()    // 同时预拉篡改记录，切页签时无需再等
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

.count-badge { min-width: 16px; height: 16px; padding: 0 4px; border-radius: 999px; background: #2563eb; color: #fff; font-size: 10px; display: inline-flex; align-items: center; justify-content: center; }
.count-badge.warn { background: #dc2626; }

.tab-body { flex: 1; overflow: auto; }
.table-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.hint { color: #6e6e73; font-size: 12px; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; }
.sub { font-size: 10px; color: #888; }
.actions-cell { display: flex; gap: 4px; }
.danger-text { color: #b91c1c; }
.warn-text { color: #b91c1c; }
.danger-title { color: #b91c1c; }
.error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
.danger-box { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 12px 14px; font-size: 13px; color: #7f1d1d; line-height: 1.7; }
.danger-box p { margin: 4px 0; }
</style>
