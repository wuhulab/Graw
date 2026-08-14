<template>
  <div class="prot-window" @click="closeMenus">
    <!-- 顶部工具栏：标签页 + 状态摘要 -->
    <div class="toolbar">
      <div class="tabs">
        <button class="tab" :class="{ active: tab === 'docker' }" @click="tab = 'docker'; loadAll()">
          <HardDrive :size="14" /> Docker 数据库
          <span v-if="dockerWarnings.length" class="count-badge">{{ dockerWarnings.length }}</span>
        </button>
        <button class="tab" :class="{ active: tab === 'db' }" @click="tab = 'db'; loadAll()">
          <DatabaseBackup :size="14" /> 数据库文件
          <span v-if="uncoveredDb.length" class="count-badge warn">{{ uncoveredDb.length }}</span>
        </button>
        <button class="tab" :class="{ active: tab === 'ignored' }" @click="tab = 'ignored'; loadIgnored()">
          <BellRing :size="14" /> 忽略列表
          <span v-if="ignoredList.length" class="count-badge">{{ ignoredList.length }}</span>
        </button>
      </div>
      <button class="btn" :disabled="loading" @click="loadAll">
        {{ loading ? '扫描中...' : '重新扫描' }}
      </button>
      <span class="hint">右键点击条目打开操作菜单 · 临时忽略 {{ ignoreDays }} 天后自动恢复</span>
    </div>

    <!-- ============ 标签页一：Docker 数据库 ============ -->
    <div v-if="tab === 'docker'" class="tab-body">
      <div v-if="dockerUnavailable" class="empty warn-text">
        Docker/Podman 不可用：{{ dockerReason }}。启动容器引擎后重试。
      </div>
      <div v-else-if="dockerWarnings.length === 0" class="empty">
        <ShieldCheck :size="40" style="color:#2a8f3c;" />
        <div>未发现缺少永久数据卷映射的数据库容器，一切正常。</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr><th>容器</th><th>镜像</th><th>状态</th><th>风险</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="w in dockerWarnings" :key="w.name"
                @contextmenu.prevent="onDockerContextMenu($event, w)">
              <td>{{ w.name }}<div class="sub">{{ w.id }}</div></td>
              <td class="mono">{{ w.image }}</td>
              <td><span class="badge" :class="w.status.startsWith('Up') ? 'ok' : 'off'">{{ w.status }}</span></td>
              <td>
                <span class="badge" :class="w.level === 'danger' ? 'danger' : 'warn'">
                  {{ w.level === 'danger' ? '高危' : '警告' }}
                </span>
              </td>
              <td class="reason">
                {{ w.message }}
                <div class="sub" v-if="w.data_dir">建议挂载目录：<span class="mono">{{ w.data_dir }}</span></div>
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
          <span>全选</span>
        </label>
        <span class="selected-count">已选 {{ selectedDbPaths.length }} 项</span>
        <button class="btn primary" :disabled="selectedDbPaths.length === 0" @click="batchAddBackup">
          批量加入备份
        </button>
      </div>

      <h4>未纳入自动备份的数据库文件</h4>
      <div v-if="uncoveredDb.length === 0" class="empty">
        <ShieldCheck :size="40" style="color:#2a8f3c;" />
        <div>未发现缺少自动备份的数据库文件，一切正常。</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead><tr><th style="width:30px;"></th><th>路径</th><th>类型</th><th>大小</th></tr></thead>
          <tbody>
            <tr v-for="f in uncoveredDb" :key="f.path"
                @contextmenu.prevent="onDbContextMenu($event, f)">
              <td @contextmenu.prevent.stop><input type="checkbox" :value="f.path" v-model="selectedDbPaths" /></td>
              <td class="mono">{{ f.path }}</td>
              <td>{{ f.type === 'dir' ? '数据目录' : 'SQLite 文件' }}</td>
              <td>{{ fmtSize(f.size) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h4>已纳入自动备份（{{ backups.length }}）</h4>
      <div v-if="backups.length === 0" class="empty small">暂无已纳入自动备份的数据库。</div>
      <div v-else class="table-wrap">
        <table>
          <thead><tr><th>路径</th><th>计划</th><th>创建时间</th></tr></thead>
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
        <div>当前没有处于忽略状态的保护警告。</div>
      </div>
      <div v-else class="table-wrap">
        <table>
          <thead><tr><th>类型</th><th>名称 / 路径</th><th>过期时间</th><th>忽略模式</th></tr></thead>
          <tbody>
            <tr v-for="(it, i) in ignoredList" :key="i"
                @contextmenu.prevent="onIgnoredContextMenu($event, it)">
              <td>{{ it.kind === 'docker' ? 'Docker 容器' : '数据库文件' }}</td>
              <td class="mono">{{ it.name || it.key }}</td>
              <td>{{ it.permanent ? '永久' : (it.expire_at || '').replace('T', ' ').slice(0, 19) }}</td>
              <td><span class="badge" :class="it.permanent ? 'danger' : 'warn'">{{ it.permanent ? '永久忽略' : '临时忽略' }}</span></td>
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
        <h3><HardDrive :size="16" /> 确认一键映射「{{ mapTarget?.name }}」</h3>
        <p class="modal-desc">系统将执行以下操作来为该数据库容器建立永久数据卷映射：</p>
        <ul class="modal-list">
          <li>1. 停止容器，避免拷贝过程中数据写入冲突；</li>
          <li>2. 创建命名数据卷：<span class="mono">{{ mapTarget?.name ? 'graw-data-' + mapTarget.name + '-<时间戳>' : '' }}</span>；</li>
          <li>3. 将容器内现有数据（{{ mapTarget?.data_dir }}）拷贝到数据卷中（<b>数据不会丢失</b>）；</li>
          <li>4. 以相同配置（端口/环境变量/重启策略/网络）重建容器并挂载数据卷；</li>
          <li>5. 启动新容器。</li>
        </ul>
        <p class="modal-desc warn-text">注意：操作期间容器会有短暂停机；若容器属于 docker compose 项目，重建后 compose 管理可能失效。</p>
        <div class="actions">
          <button class="btn" @click="showMapModal = false">取消</button>
          <button class="btn primary" :disabled="mapping" @click="doMap">
            {{ mapping ? '正在执行...' : '确认映射' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 忽略确认弹窗（临时 + 永久） ============ -->
    <div v-if="showIgnoreConfirm" class="modal-overlay" @click.self="closeIgnoreConfirm">
      <div class="modal">
        <h3 class="danger-title"><OctagonAlert :size="18" /> 危险操作确认</h3>
        <div class="danger-box">
          <p><b>您正在忽略以下保护警告：</b></p>
          <p class="mono">{{ ignoreTarget?.label }}</p>
          <p>
            忽略后，系统将<b>不再提醒</b>您为这项数据配置持久化 / 自动备份。
            若相关容器被删除、重建或磁盘发生故障，其中的数据将
            <b style="color:#b91c1c;">永久丢失且无法恢复</b>。
          </p>
          <p><b style="color:#b91c1c;">生产环境强烈建议不要执行此操作！</b></p>
        </div>

        <!-- 忽略模式选择 -->
        <div class="ignore-options">
          <label class="ignore-option" :class="{ active: !ignorePermanent }">
            <input type="radio" :value="false" v-model="ignorePermanent" />
            <div>
              <div class="opt-title">暂时忽略</div>
              <div class="opt-desc">{{ ignoreDays }} 天后系统自动恢复提醒</div>
            </div>
          </label>
          <label class="ignore-option" :class="{ active: ignorePermanent, danger: true }">
            <input type="radio" :value="true" v-model="ignorePermanent" />
            <div>
              <div class="opt-title">永久忽略</div>
              <div class="opt-desc">永不提醒。仅用于必要的非生产数据库或日志储存</div>
            </div>
          </label>
        </div>

        <label class="confirm-row">
          <input type="checkbox" v-model="ignoreAck" />
          <span>我已充分了解风险，仍要{{ ignorePermanent ? '永久' : '暂时' }}忽略此项警告</span>
        </label>
        <div class="actions">
          <button class="btn" @click="closeIgnoreConfirm">返回</button>
          <button class="btn danger-btn" :disabled="!ignoreAck || ignoring" @click="doIgnore">
            {{ ignoring ? '处理中...' : (ignorePermanent ? '确认永久忽略' : '确认暂时忽略') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { protectionApi } from '../../api'
import {
  HardDrive, DatabaseBackup, BellRing, ShieldCheck, OctagonAlert
} from 'lucide-vue-next'

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
      dockerReason.value = r.reason || '未知原因'
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

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadDocker(), loadDb(), loadIgnored()])
  } finally {
    loading.value = false
  }
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
    { label: '一键映射', action: () => { closeMenus(); confirmMap(w) } },
    { divider: true },
    { label: '暂时忽略', action: () => { closeMenus(); openIgnoreConfirm('docker', w.name, w.name, false) } },
    { label: '永久忽略', danger: true, action: () => { closeMenus(); openIgnoreConfirm('docker', w.name, w.name, true) } },
  ])
}

function onDbContextMenu(e, f) {
  showCtxMenu(e, [
    { label: '加入备份', action: () => { closeMenus(); addBackup(f) } },
    { label: '批量加入备份（含选中项）', action: () => { closeMenus(); batchAddBackupWith(f.path) } },
    { divider: true },
    { label: '暂时忽略', action: () => { closeMenus(); openIgnoreConfirm('db_file', f.path, f.path, false) } },
    { label: '永久忽略', danger: true, action: () => { closeMenus(); openIgnoreConfirm('db_file', f.path, f.path, true) } },
  ])
}

function onBackupContextMenu(e, b) {
  showCtxMenu(e, [
    { label: '移出备份', action: () => { closeMenus(); removeBackup(b.path) } },
  ])
}

function onIgnoredContextMenu(e, it) {
  showCtxMenu(e, [
    { label: '恢复提醒', action: () => { closeMenus(); restoreIgnore(it.kind, it.key) } },
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
    alert(`一键映射完成！\n数据卷：${r.volume}\n挂载目录：${r.data_dir}\n旧容器已重建为挂载命名数据卷的新容器。`)
    await loadAll()
  } catch (e) {
    alert('一键映射失败：' + (e.response?.data?.detail || e.message))
  } finally {
    mapping.value = false
  }
}

// ---------- 加入备份 ----------
async function addBackup(f) {
  if (!confirm(`确定将「${f.path}」加入自动备份？\n将创建每日 02:30 的定时备份任务。`)) return
  try {
    const r = await protectionApi.addBackup(f.path)
    alert(r.already ? '该路径已在备份清单中。' : '已加入自动备份，并创建了每日 02:30 的定时任务。')
    await loadDb()
  } catch (e) {
    alert('加入备份失败：' + (e.response?.data?.detail || e.message))
  }
}

async function batchAddBackup() {
  const paths = selectedDbPaths.value
  if (paths.length === 0) return
  if (!confirm(`确定将选中的 ${paths.length} 个路径批量加入自动备份？`)) return
  try {
    const r = await protectionApi.batchBackup(paths)
    const ok = r.results.filter(x => x.ok).length
    const fail = r.results.filter(x => !x.ok).length
    alert(`批量备份完成：成功 ${ok} 项${fail ? '，失败 ' + fail + ' 项' : ''}。`)
    selectedDbPaths.value = []
    await loadDb()
  } catch (e) {
    alert('批量备份失败：' + (e.response?.data?.detail || e.message))
  }
}

async function batchAddBackupWith(extraPath) {
  // 右键"批量加入备份"：把右键项 + 已选项合并去重
  const paths = [...new Set([extraPath, ...selectedDbPaths.value])]
  if (!confirm(`确定将 ${paths.length} 个路径批量加入自动备份？`)) return
  try {
    const r = await protectionApi.batchBackup(paths)
    const ok = r.results.filter(x => x.ok).length
    const fail = r.results.filter(x => !x.ok).length
    alert(`批量备份完成：成功 ${ok} 项${fail ? '，失败 ' + fail + ' 项' : ''}。`)
    selectedDbPaths.value = []
    await loadDb()
  } catch (e) {
    alert('批量备份失败：' + (e.response?.data?.detail || e.message))
  }
}

async function removeBackup(path) {
  if (!confirm(`确定将「${path}」移出自动备份清单？（不会删除已创建的计划任务）`)) return
  try {
    await protectionApi.removeBackup(path)
    await loadDb()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
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
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    ignoring.value = false
  }
}

async function restoreIgnore(kind, key) {
  if (!confirm('确定恢复该警告的提醒？')) return
  try {
    await protectionApi.unignore(kind, key)
    await loadAll()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

function fmtSize(size) {
  if (size == null) return '较大/未知'
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
