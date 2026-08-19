<template>
  <div class="rt-window" @click="closeMenus">
    <!-- 工具栏：创建下拉 + 刷新 -->
    <div class="toolbar">
      <div class="create-wrap" @click.stop>
        <button class="btn primary" @click="toggleCreateMenu">
          <Plus :size="14" /> {{ $t('runtime.create') }}
        </button>
        <Teleport to="body">
          <div v-if="createMenuOpen" class="create-menu" :style="{ left: menuX + 'px', top: menuY + 'px' }" @click.stop>
            <div class="menu-title">{{ $t('runtime.chooseRuntime') }}</div>
            <div v-for="rt in templates" :key="rt.type" class="menu-item" @click="pickType(rt.type)">
              <span class="rt-dot"></span>{{ typeLabel(rt.type) }}
            </div>
          </div>
        </Teleport>
      </div>
      <button class="btn" @click="load"><RefreshCw :size="13" /> {{ $t('runtime.refresh') }}</button>
      <span class="hint">{{ $t('runtime.count', { count }) }}</span>
    </div>

    <!-- 列表 -->
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ $t('runtime.name') }}</th>
            <th>{{ $t('runtime.runtime') }}</th>
            <th>{{ $t('runtime.image') }}</th>
            <th>{{ $t('runtime.container') }}</th>
            <th>{{ $t('runtime.port') }}</th>
            <th>{{ $t('runtime.status') }}</th>
            <th style="width:120px;">{{ $t('runtime.operation') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in runtimes" :key="r.id">
            <td>
              {{ r.name }}
              <div v-if="r.notes" class="sub" :title="r.notes">{{ r.notes }}</div>
            </td>
            <td><span class="tag">{{ typeLabel(r.type) }}</span></td>
            <td class="mono">{{ r.image || typeImage(r.type, r.app_version) }}</td>
            <td class="mono">{{ r.container_name }}</td>
            <td class="mono">{{ portsText(r.ports) }}</td>
            <td><span class="badge" :class="stateClass(r.status?.state)">{{ stateText(r.status?.state) }}</span></td>
            <td class="actions">
              <button class="iconbtn" :title="$t('runtime.start')" @click="act(r, 'start')" :disabled="r.status?.running"><Play :size="14" /></button>
              <button class="iconbtn" :title="$t('runtime.stop')" @click="act(r, 'stop')" :disabled="!r.status?.running"><Square :size="13" /></button>
              <button class="iconbtn" :title="$t('runtime.restart')" @click="act(r, 'restart')"><RotateCw :size="13" /></button>
              <button class="iconbtn danger" :title="$t('runtime.delete')" @click="remove(r)"><Trash2 :size="14" /></button>
            </td>
          </tr>
          <tr v-if="!runtimes.length">
            <td colspan="7"><div class="empty">{{ $t('runtime.noRuntimes') }}</div></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { runtimeApi } from '../../api'
import { Plus, RefreshCw, Play, Square, RotateCw, Trash2 } from 'lucide-vue-next'

const { t } = useI18n()
const emit = defineEmits(['close', 'openRuntimeCreate'])

const runtimes = ref([])
const templates = ref([])
const createMenuOpen = ref(false)
const menuX = ref(0)
const menuY = ref(0)
const count = computed(() => runtimes.value.length)

let timer = null

const TYPE_LABELS = { python: 'Python', java: 'Java', node: 'Node.js', go: 'Go', dotnet: '.NET', php: 'PHP', html: 'HTML', other: '其他' }
function typeLabel(t) { return TYPE_LABELS[t] || t }

function typeImage(type, version) {
  const t = templates.value.find(x => x.type === type)
  if (t) return t.image
  return (version || '') 
}

function portsText(ports) {
  if (!ports || !ports.length) return '-'
  return ports.filter(p => p.external && p.internal)
    .map(p => `${p.external}:${p.internal}/${p.protocol}`).join(', ') || '-'
}

function stateText(s) {
  if (s === 'running') return t('common.running')
  if (s === 'exited') return t('common.stopped')
  if (s === 'unknown') return t('runtime.unknown')
  return t('runtime.notExists')
}
function stateClass(s) {
  if (s === 'running') return 'ok'
  if (s === 'exited') return 'off'
  if (s === 'unknown') return 'warn'
  return 'off'
}

async function load() {
  try {
    const data = await runtimeApi.list()
    runtimes.value = data.runtimes || []
  } catch (e) {
    alert(t('runtime.loadFailed', { error: e.response?.data?.detail || e.message }))
  }
}

async function loadTemplates() {
  try {
    const data = await runtimeApi.templates()
    templates.value = data.runtimes || []
  } catch (e) { /* 模板加载失败不阻塞列表 */ }
}

function toggleCreateMenu(e) {
  createMenuOpen.value = !createMenuOpen.value
  if (createMenuOpen.value) {
    const r = e?.target?.closest('.create-wrap')?.getBoundingClientRect()
    menuX.value = r ? r.left : 120
    menuY.value = r ? r.bottom + 4 : 60
  }
}

function pickType(type) {
  createMenuOpen.value = false
  // 在面板中打开一个独立的新窗口填写运行环境配置
  emit('openRuntimeCreate', type)
}

function closeMenus() {
  createMenuOpen.value = false
}

async function act(r, action) {
  try {
    await runtimeApi.action(r.id, action)
    await load()
  } catch (e) {
    alert(t('runtime.operationFailed', { error: e.response?.data?.detail || e.message }))
  }
}

async function remove(r) {
  if (!confirm(t('runtime.confirmDelete', { name: r.name }))) return
  try {
    await runtimeApi.delete(r.id)
    await load()
  } catch (e) {
    alert(t('runtime.deleteFailed', { error: e.response?.data?.detail || e.message }))
  }
}

onMounted(() => {
  load()
  loadTemplates()
  timer = setInterval(load, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.rt-window { position: relative; display: flex; flex-direction: column; height: 100%; padding: 10px; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.hint { color: #6e6e73; font-size: 12px; }
.table-wrap { flex: 1; overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
th { background: #f9fafb; position: sticky; top: 0; }
.sub { font-size: 11px; color: #9ca3af; max-width: 180px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11.5px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 11px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.badge.warn { background: #fef3c7; color: #92400e; }
.actions { display: flex; gap: 4px; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; display: inline-flex; align-items: center; color: #6b7280; }
.iconbtn:hover:not(:disabled) { background: #f9fafb; }
.iconbtn:disabled { opacity: .35; cursor: not-allowed; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; color: #b91c1c; }
.empty { text-align: center; color: #9ca3af; padding: 40px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 6px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }

.create-wrap { position: relative; }
.create-menu { position: fixed; background: #fff; border: 1px solid rgba(0,0,0,0.1); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.15); z-index: 3000; min-width: 150px; padding: 4px 0; }
.menu-title { padding: 6px 14px; font-size: 11px; color: #9ca3af; font-weight: 600; }
.menu-item { padding: 8px 14px; font-size: 12.5px; cursor: pointer; color: #374151; display: flex; align-items: center; gap: 8px; }
.menu-item:hover { background: #f5f5f7; }
.rt-dot { width: 8px; height: 8px; border-radius: 50%; background: #0a84ff; }
</style>