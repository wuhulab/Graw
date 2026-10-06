<!--
  节点资源聚合视图窗口（NodeOverviewWindow）
  业务：一次性拉取全部节点的 CPU/内存/磁盘/负载与在线状态（GET /api/nodes/overview），
  支持按「分组」归类展示、按「标签」筛选，并可在勾选多台节点后直接批量执行
  命令 / 容器动作（复用 /api/batch/*）。
  后端模块：nodesApi.overview / nodesApi.setMeta / batchApi
  关键状态：rows（节点指标）、checked（勾选集合）、activeTags（标签筛选）、results（批量结果）
  打开方式：桌面「节点总览」入口（完整管理员；/api/nodes 属面板自身安全边界）
-->
<template>
  <div class="nov-window">
    <!-- 顶部工具栏：刷新 / 自动刷新 / 汇总 -->
    <div class="ui-toolbar">
      <button class="ui-btn mini" :disabled="loading" @click="refresh">{{ loading ? t('nodeoverview.loading') : t('nodeoverview.refresh') }}</button>
      <label class="auto-label">
        <input type="checkbox" v-model="autoRefresh" />
        <span>{{ t('nodeoverview.autoRefresh') }}</span>
      </label>
      <span class="ui-hint">{{ summaryText }}</span>
      <!-- 标签筛选 -->
      <div v-if="allTags.length" class="tag-filter">
        <span class="filter-label">{{ t('nodeoverview.filterTag') }}:</span>
        <button
          v-for="tg in allTags"
          :key="tg"
          class="tag-chip"
          :class="{ active: activeTags.includes(tg) }"
          @click="toggleTag(tg)"
        >{{ tg }}</button>
      </div>
    </div>

    <!-- 批量操作条 -->
    <div class="batch-bar ui-card">
      <label class="sel-all">
        <input type="checkbox" :checked="allChecked" @change="toggleAll" />
        <span>{{ t('nodeoverview.selectAll') }}</span>
      </label>
      <span class="sel-count">{{ t('nodeoverview.selectedCount', { count: checked.length }) }}</span>
      <select v-model="batchMode" class="ui-select mode-select">
        <option value="cmd">{{ t('nodeoverview.batchCommand') }}</option>
        <option value="ctr">{{ t('nodeoverview.batchContainers') }}</option>
      </select>
      <template v-if="batchMode === 'cmd'">
        <textarea
          v-model="command"
          rows="1"
          class="ui-textarea cmd-input"
          :placeholder="t('nodeoverview.cmdPlaceholder')"
        />
      </template>
      <template v-else>
        <input v-model="keyword" type="text" class="ui-input kw-input" :placeholder="t('nodeoverview.keywordPlaceholder')" />
        <select v-model="action" class="ui-select">
          <option value="start">{{ t('nodeoverview.start') }}</option>
          <option value="stop">{{ t('nodeoverview.stop') }}</option>
          <option value="restart">{{ t('nodeoverview.restart') }}</option>
        </select>
      </template>
      <button class="ui-btn primary mini" :disabled="running || loading || checked.length === 0" @click="runBatch">
        {{ running ? t('nodeoverview.executing') : t('nodeoverview.execute') }}
      </button>
    </div>

    <!-- 批量结果 -->
    <div v-if="results.length" class="result-area">
      <div v-for="r in results" :key="r.node_id" class="result-row">
        <span class="dot" :class="r.ok ? 'ok' : 'fail'"></span>
        <b>{{ r.node_name }}</b>
        <span class="rc">{{ r.returncode !== undefined && r.returncode !== null ? 'exit=' + r.returncode : '' }}</span>
        <span class="dur">{{ r.duration }}s</span>
        <template v-if="r.containers">
          <span v-if="!r.containers.length" class="dur">{{ r.note || t('batch.noContainer') }}</span>
          <span v-for="c in r.containers" :key="c.id" class="ctr-item" :class="{ fail: !c.ok }">
            {{ c.ok ? '✓' : '✗' }} {{ c.name }}
          </span>
        </template>
        <pre v-if="r.stdout" class="out">{{ r.stdout }}</pre>
        <pre v-if="r.stderr" class="err">{{ r.stderr }}</pre>
      </div>
    </div>

    <!-- 分组节点卡片 -->
    <div class="cards-area">
      <div v-if="!loading && visibleRows.length === 0" class="ui-empty">{{ t('nodeoverview.noNodes') }}</div>
      <div v-for="g in groupedRows" :key="g.name" class="group-block">
        <div v-if="groupedRows.length > 1 || g.name !== t('nodeoverview.noGroup')" class="group-title">
          <span class="group-name">{{ g.name }}</span>
          <span class="group-count">{{ g.nodes.length }}</span>
        </div>
        <div class="cards">
          <div
            v-for="n in g.nodes"
            :key="n.id"
            class="ui-card node-card"
            :class="{ offline: !n.online, selected: checked.includes(n.id) }"
            @click="toggleCheck(n.id)"
          >
            <div class="card-head">
              <label class="chk" @click.stop>
                <input type="checkbox" :checked="checked.includes(n.id)" @change="toggleCheck(n.id)" />
              </label>
              <span class="dot" :class="n.online ? 'ok' : 'fail'"></span>
              <b class="nm" :title="n.name">{{ n.name }}</b>
              <span class="tag type-tag">{{ n.type === 'ssh' ? 'SSH' : t('nodeoverview.local') }}</span>
              <span v-if="n.agent_enabled" class="tag agent-tag">Agent</span>
              <button class="ui-btn ghost mini edit-btn" @click.stop="openEdit(n)">{{ t('nodeoverview.editMeta') }}</button>
            </div>
            <div v-if="n.type === 'ssh'" class="host-line">{{ n.host }}</div>
            <div v-if="!n.online" class="err-line">{{ n.error || t('nodeoverview.offline') }}</div>
            <template v-else>
              <div class="bar-row">
                <span class="bar-label">{{ t('nodeoverview.cpu') }}</span>
                <div class="bar"><div class="bar-fill" :class="barClass(n.cpu)" :style="{ width: pct(n.cpu) }"></div></div>
                <span class="bar-val">{{ n.cpu }}%</span>
              </div>
              <div class="bar-row">
                <span class="bar-label">{{ t('nodeoverview.memory') }}</span>
                <div class="bar"><div class="bar-fill" :class="barClass(n.memory?.percent)" :style="{ width: pct(n.memory?.percent) }"></div></div>
                <span class="bar-val">{{ n.memory?.percent }}%<small v-if="n.memory?.total"> · {{ formatBytes(n.memory.total) }}</small></span>
              </div>
              <div class="bar-row">
                <span class="bar-label">{{ t('nodeoverview.disk') }}</span>
                <div class="bar"><div class="bar-fill" :class="barClass(n.storage?.percent)" :style="{ width: pct(n.storage?.percent) }"></div></div>
                <span class="bar-val">{{ n.storage?.percent }}%<small v-if="n.storage?.total"> · {{ formatBytes(n.storage.total) }}</small></span>
              </div>
              <div class="meta-line">
                <span>{{ t('nodeoverview.load') }}: {{ n.load?.load1 }}</span>
                <span v-if="n.uptime_seconds">{{ t('nodeoverview.uptime') }}: {{ fmtUptime(n.uptime_seconds) }}</span>
                <span v-if="n.latency_ms">{{ t('nodeoverview.latency') }}: {{ n.latency_ms }}ms</span>
              </div>
            </template>
            <div v-if="n.tags && n.tags.length" class="tags-line">
              <span v-for="tg in n.tags" :key="tg" class="tag node-tag" @click.stop="toggleTag(tg)">{{ tg }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 元数据编辑弹层 -->
    <div v-if="editing" class="modal-mask" @click.self="closeEdit">
      <div class="modal ui-card" @click.stop>
        <div class="modal-title">{{ t('nodeoverview.editMeta') }} · {{ editing.name }}</div>
        <div class="modal-label">{{ t('nodeoverview.group') }}</div>
        <input v-model="editGroup" type="text" class="ui-input" :placeholder="t('nodeoverview.groupPlaceholder')" />
        <div class="modal-label">{{ t('nodeoverview.tags') }}</div>
        <input v-model="editTags" type="text" class="ui-input" :placeholder="t('nodeoverview.tagsPlaceholder')" />
        <div class="modal-hint">{{ t('nodeoverview.tagsHint') }}</div>
        <div v-if="editMsg" class="modal-msg">{{ editMsg }}</div>
        <div class="modal-btns">
          <button class="ui-btn mini" @click="closeEdit">{{ t('nodeoverview.cancel') }}</button>
          <button class="ui-btn primary mini" :disabled="savingMeta" @click="saveMeta">{{ savingMeta ? t('nodeoverview.saving') : t('nodeoverview.save') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'   // 响应式 + 生命周期（离开页面停掉自动刷新）
import { useI18n } from 'vue-i18n'                                    // 国际化
import { nodesApi, batchApi, formatBytes } from '../../api'          // 节点聚合接口 + 批量接口 + 字节格式化

const { t } = useI18n()

const rows = ref([])            // 聚合指标行：[{ id, name, type, host, group, tags, online, cpu, ... }]
const loading = ref(false)      // 加载中标记
const autoRefresh = ref(false)  // 自动刷新开关
let timer = null                // 自动刷新定时器

// 标签筛选：多选，命中任一标签即展示
const activeTags = ref([])

// 批量操作
const checked = ref([])         // 勾选的节点 id
const batchMode = ref('cmd')    // cmd | ctr
const command = ref('')
const keyword = ref('')
const action = ref('restart')
const running = ref(false)
const results = ref([])

// 元数据编辑弹层
const editing = ref(null)
const editGroup = ref('')
const editTags = ref('')
const editMsg = ref('')
const savingMeta = ref(false)

// --- 展示计算 ---

// 全部标签（去重，保持出现顺序）
const allTags = computed(() => {
  const seen = new Set()
  const out = []
  for (const n of rows.value) {
    for (const tg of n.tags || []) {
      if (!seen.has(tg)) { seen.add(tg); out.push(tg) }
    }
  }
  return out
})

// 标签筛选后的可见节点
const visibleRows = computed(() => {
  if (!activeTags.value.length) return rows.value
  return rows.value.filter((n) => (n.tags || []).some((tg) => activeTags.value.includes(tg)))
})

// 按分组归类：有分组名在前（字典序），未分组最后
const groupedRows = computed(() => {
  const map = new Map()
  for (const n of visibleRows.value) {
    const g = n.group || ''
    if (!map.has(g)) map.set(g, [])
    map.get(g).push(n)
  }
  const groups = [...map.entries()]
    .filter(([name]) => name !== '')
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([name, nodes]) => ({ name, nodes }))
  if (map.has('')) groups.push({ name: t('nodeoverview.noGroup'), nodes: map.get('') })
  return groups
})

const allChecked = computed(() => visibleRows.value.length > 0 && visibleRows.value.every((n) => checked.value.includes(n.id)))

const summaryText = computed(() => {
  const total = visibleRows.value.length
  const online = visibleRows.value.filter((n) => n.online).length
  const parts = [t('nodeoverview.totalNodes', { count: total })]
  if (online < total) parts.push(t('nodeoverview.onlineCount', { count: online }))
  return parts.join(' · ')
})

// --- 动作 ---

async function refresh() {
  loading.value = true
  try {
    const data = await nodesApi.overview()
    rows.value = data.nodes || []
    // 节点可能被删除：清掉已不存在节点的勾选
    const ids = new Set(rows.value.map((n) => n.id))
    checked.value = checked.value.filter((id) => ids.has(id))
  } catch (e) {
    alert(e?.response?.data?.detail || t('nodeoverview.errorFetch'))
  } finally {
    loading.value = false
  }
}

function toggleTag(tg) {
  const idx = activeTags.value.indexOf(tg)
  if (idx >= 0) activeTags.value.splice(idx, 1)
  else activeTags.value.push(tg)
}

function toggleCheck(id) {
  const idx = checked.value.indexOf(id)
  if (idx >= 0) checked.value.splice(idx, 1)
  else checked.value.push(id)
}

function toggleAll() {
  if (allChecked.value) checked.value = []
  else checked.value = visibleRows.value.map((n) => n.id)
}

async function runBatch() {
  const node_ids = [...checked.value]
  if (!node_ids.length) { alert(t('nodeoverview.needNodes')); return }
  if (batchMode.value === 'cmd' && !command.value.trim()) { alert(t('nodeoverview.needCommand')); return }
  running.value = true
  results.value = []
  try {
    const res = batchMode.value === 'cmd'
      ? await batchApi.command({ node_ids, command: command.value.trim() })
      : await batchApi.containers({ node_ids, action: action.value, filter: { keyword: keyword.value.trim() } })
    results.value = res.results || []
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  } finally {
    running.value = false
  }
}

function openEdit(n) {
  editing.value = n
  editGroup.value = n.group || ''
  editTags.value = (n.tags || []).join(', ')
  editMsg.value = ''
}

function closeEdit() {
  editing.value = null
}

async function saveMeta() {
  if (!editing.value) return
  savingMeta.value = true
  try {
    const tags = editTags.value.split(',').map((s) => s.trim()).filter(Boolean)
    await nodesApi.setMeta(editing.value.id, { group: editGroup.value.trim(), tags })
    // 本地同步，避免整页刷新
    editing.value.group = editGroup.value.trim()
    editing.value.tags = tags
    closeEdit()
  } catch (e) {
    editMsg.value = e?.response?.data?.detail || String(e)
  } finally {
    savingMeta.value = false
  }
}

// --- 工具 ---

function pct(v) {
  const x = Number(v) || 0
  return `${Math.max(0, Math.min(100, x))}%`
}

function barClass(v) {
  const x = Number(v) || 0
  if (x >= 90) return 'danger'
  if (x >= 75) return 'warn'
  return ''
}

function fmtUptime(sec) {
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return t('nodeoverview.days', { d, h })
  if (h > 0) return t('nodeoverview.hours', { h, m })
  return t('nodeoverview.minutes', { m })
}

// 自动刷新：开关注册/注销定时器
watch(autoRefresh, (on) => {
  if (timer) { clearInterval(timer); timer = null }
  if (on) timer = setInterval(refresh, 10000)
})

onMounted(refresh)

onUnmounted(() => {
  if (timer) { clearInterval(timer); timer = null }
})
</script>

<style scoped>
.nov-window { display: flex; flex-direction: column; height: 100%; padding: 10px; box-sizing: border-box; gap: 8px; }
.auto-label { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; }
.tag-filter { margin-left: auto; display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.filter-label { font-size: 12px; color: var(--ui-text-secondary); }
.tag-chip {
  font-size: 11px; padding: 2px 8px; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--ui-border, #e5e7eb); background: var(--ui-bg-muted, #f0f3fa); color: inherit;
}
.tag-chip.active { background: var(--ui-primary, #2563eb); color: #fff; border-color: transparent; }

.batch-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 8px 10px; }
.sel-all { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; cursor: pointer; }
.sel-count { font-size: 12px; color: var(--ui-text-secondary); }
.mode-select { width: auto; }
.cmd-input { flex: 1; min-width: 240px; font-family: Consolas, monospace; font-size: 12px; }
.kw-input { width: 180px; }

.result-area { max-height: 200px; overflow: auto; display: flex; flex-direction: column; gap: 4px; }
.result-row { font-size: 12px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 2px 4px; }
.result-row .rc, .result-row .dur { font-size: 11px; color: var(--ui-text-secondary); }
.ctr-item { font-size: 11px; color: #27ae60; background: var(--ui-bg-muted, #f0f3fa); border-radius: 9px; padding: 1px 8px; }
.ctr-item.fail { color: #c0392b; }
.result-row pre {
  width: 100%; margin: 2px 0 0; background: #0f1722; color: #d7e3f4; border-radius: 4px;
  padding: 6px 8px; font-size: 12px; max-height: 120px; overflow: auto;
  white-space: pre-wrap; word-break: break-all;
}
.result-row pre.err { background: #33120f; color: #ffb3a8; }

.cards-area { flex: 1; overflow: auto; padding: 0 2px 12px; }
.group-block { margin-bottom: 12px; }
.group-title { display: flex; align-items: center; gap: 6px; margin: 6px 0; }
.group-name { font-size: 13px; font-weight: 600; }
.group-count {
  font-size: 11px; color: var(--ui-text-secondary);
  background: var(--ui-bg-muted, #f0f3fa); border-radius: 8px; padding: 0 7px;
}
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(270px, 1fr)); gap: 10px; }

.node-card { padding: 10px 12px; cursor: pointer; transition: border-color 0.15s; }
.node-card.selected { border-color: var(--ui-primary, #2563eb); }
.node-card.offline { opacity: 0.75; }
.card-head { display: flex; align-items: center; gap: 6px; }
.card-head .nm { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.card-head .chk { display: inline-flex; }
.edit-btn { margin-left: auto; font-size: 11px; }
.host-line { font-size: 11px; color: var(--ui-text-secondary); margin: 4px 0 2px; }
.err-line { font-size: 12px; color: #c0392b; margin-top: 6px; }

.tag { font-size: 10px; padding: 1px 7px; border-radius: 8px; background: var(--ui-bg-muted, #f0f3fa); }
.type-tag { color: var(--ui-text-secondary); }
.agent-tag { color: #0a7d3b; }

.bar-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.bar-label { font-size: 11px; color: var(--ui-text-secondary); width: 34px; flex-shrink: 0; }
.bar { flex: 1; height: 7px; border-radius: 4px; background: var(--ui-bg-muted, #eef1f6); overflow: hidden; }
.bar-fill { height: 100%; border-radius: 4px; background: var(--ui-primary, #2563eb); transition: width 0.3s; }
.bar-fill.warn { background: #d97706; }
.bar-fill.danger { background: #dc2626; }
.bar-val { font-size: 11px; width: 92px; text-align: right; flex-shrink: 0; }
.bar-val small { color: var(--ui-text-secondary); }

.meta-line { display: flex; gap: 12px; margin-top: 8px; font-size: 11px; color: var(--ui-text-secondary); flex-wrap: wrap; }
.tags-line { display: flex; gap: 4px; margin-top: 8px; flex-wrap: wrap; }
.node-tag { cursor: pointer; color: #1d4ed8; }

.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.dot.ok { background: #27ae60; }
.dot.fail { background: #c0392b; }

.modal-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,0.35);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.modal { width: 320px; padding: 14px 16px; display: flex; flex-direction: column; gap: 6px; }
.modal-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.modal-label { font-size: 12px; color: var(--ui-text-secondary); }
.modal-hint { font-size: 11px; color: var(--ui-text-secondary); }
.modal-msg { font-size: 12px; color: #c0392b; }
.modal-btns { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
</style>
