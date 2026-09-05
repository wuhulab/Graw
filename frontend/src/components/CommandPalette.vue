<!--
  CommandPalette.vue - 全局快捷搜索（Ctrl+K / Spotlight）
  业务：快捷键唤出浮层，输入关键词按「功能 / 节点 / 站点 / 容器」分组搜索，
        回车直达（打开窗口 / 切换当前节点）。
  数据：功能入口由 App.vue 传入（已做可见性门控）；节点取自 nodesStore；
        站点/容器远程拉取（防抖 + 会话内缓存）。
  键盘：↑↓ 移动、Enter 执行、Esc 关闭、Tab 在分组间跳转。
-->
<template>
  <Teleport to="body">
    <div v-if="visible" class="palette-mask" @click.self="close">
      <div class="palette">
        <input
          ref="inputRef"
          v-model="query"
          class="palette-input"
          :placeholder="t('palette.placeholder')"
          @keydown="onKey"
          @input="onInput"
        />
        <div class="palette-body">
          <template v-for="g in groups" :key="g.key">
            <div v-if="g.items.length" class="g-head">{{ t('palette.group.' + g.key) }}</div>
            <div
              v-for="(it, gi) in g.items"
              :key="g.key + '-' + gi"
              class="g-item"
              :class="{ active: activeKey === g.key && activeIdx === gi }"
              @mousedown.prevent="exec(g.key, it)"
            >
              <span class="g-icon">{{ it.icon || '•' }}</span>
              <span class="g-label">{{ it.label }}</span>
              <span v-if="it.sub" class="g-sub">{{ it.sub }}</span>
            </div>
          </template>
          <div v-if="!hasAny" class="g-empty">{{ t('palette.empty') }}</div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, reactive, computed, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { sitesApi, dockerApi } from '../api'
import { nodes, setCurrentNode } from '../store/nodes'

const props = defineProps({
  appItems: { type: Array, default: () => [] }   // 功能入口（App.vue 已做 adminOnly/remoteCap/vip 门控）
})
const emit = defineEmits(['close', 'open'])

const { t } = useI18n()
const visible = ref(false)
const query = ref('')
const activeKey = ref('apps')
const activeIdx = ref(0)
const inputRef = ref(null)

// 远程数据：会话内缓存 + 防抖
const sitesCache = ref(null)
const containersCache = ref(null)
let debounceTimer = null
let seq = 0

function show() {
  visible.value = true
  query.value = ''
  activeKey.value = 'apps'
  activeIdx.value = 0
  nextTick(() => inputRef.value?.focus())
}
function close() {
  visible.value = false
  emit('close')
}
function toggle() {
  visible.value ? close() : show()
}
defineExpose({ show, close, toggle })

// 输入防抖：本地分组即时，远程（站点/容器）200ms 后才拉
function onInput() {
  activeKey.value = 'apps'
  activeIdx.value = 0
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    const q = query.value.trim()
    const mySeq = ++seq
    if (!q) return
    if (!sitesCache.value) {
      sitesApi.list().then(r => { if (mySeq === seq) sitesCache.value = (r.sites || []) }).catch(() => {})
    }
    if (!containersCache.value) {
      dockerApi.containers().then(r => { if (mySeq === seq) containersCache.value = Array.isArray(r) ? r : (r.containers || []) }).catch(() => {})
    }
  }, 200)
}

// 匹配：小写 includes
function match(q, ...fields) {
  const s = q.toLowerCase()
  return fields.some(f => (f || '').toString().toLowerCase().includes(s))
}

const groups = computed(() => {
  const q = query.value.trim()
  const out = []
  // 功能入口
  const apps = props.appItems.filter(a => !q || match(q, a.label, a.key))
  out.push({ key: 'apps', items: apps.slice(0, 10) })
  // 节点
  const nlist = nodes.list.filter(n => !q || match(q, n.name, n.id, n.host))
  out.push({ key: 'nodes', items: nlist.slice(0, 10).map(n => ({ key: n.id, label: n.name, sub: n.type === 'ssh' ? `${n.host} · SSH` : '本机', icon: '🖥' })) })
  // 站点
  const sites = (sitesCache.value || []).filter(s => !q || match(q, s.name, (s.domains || []).join(' '), s.id))
  out.push({ key: 'sites', items: sites.slice(0, 8).map(s => ({ key: s.id, label: s.name, sub: (s.domains || []).join(', ') || s.root || '', icon: '🌐' })) })
  // 容器
  const ctns = (containersCache.value || []).filter(c => !q || match(q, c.name, c.id))
  out.push({ key: 'containers', items: ctns.slice(0, 8).map(c => ({ key: c.Id || c.id, label: (c.name || c.Names || '').toString().replace(/^\//, ''), sub: c.Status || '', icon: '📦' })) })
  return out
})
const hasAny = computed(() => groups.value.some(g => g.items.length > 0))

const flatIndex = computed(() => {
  const list = []
  groups.value.forEach(g => g.items.forEach((it, i) => list.push({ gkey: g.key, idx: i, it })))
  return list
})

function onKey(e) {
  if (e.key === 'Escape') { close(); return }
  if (e.key === 'ArrowDown') { e.preventDefault(); nav(1); return }
  if (e.key === 'ArrowUp') { e.preventDefault(); nav(-1); return }
  if (e.key === 'Enter') {
    const cur = flatIndex.value.find(x => x.gkey === activeKey.value && x.idx === activeIdx.value)
    if (cur) exec(cur.gkey, cur.it)
    return
  }
}
function nav(d) {
  const flat = flatIndex.value
  if (!flat.length) return
  let pos = flat.findIndex(x => x.gkey === activeKey.value && x.idx === activeIdx.value)
  pos = (pos + d + flat.length) % flat.length
  activeKey.value = flat[pos].gkey
  activeIdx.value = flat[pos].idx
}

function exec(gkey, it) {
  if (gkey === 'apps') {
    emit('open', it.key) // App.vue 负责 openWindow 门控
  } else if (gkey === 'nodes') {
    setCurrentNode(it.key).catch(() => {})
  } else if (gkey === 'sites') {
    emit('open', 'sites')
  } else if (gkey === 'containers') {
    emit('open', 'docker')
  }
  close()
}

// 切换节点后清缓存（站点/容器数据随当前节点变化）
watch(() => nodes.currentId, () => { sitesCache.value = null; containersCache.value = null })
</script>

<style scoped>
.palette-mask { position: fixed; inset: 0; background: rgba(10,16,32,.42); display: flex; align-items: flex-start; justify-content: center; padding-top: 12vh; z-index: 20000; }
.palette { width: 560px; max-width: 92vw; background: #fff; border-radius: 12px; box-shadow: 0 18px 50px rgba(0,0,0,.28); overflow: hidden; }
.palette-input { width: 100%; border: none; outline: none; padding: 16px 18px; font-size: 15px; border-bottom: 1px solid #eef0f6; }
.palette-body { max-height: 50vh; overflow: auto; padding: 8px 0; }
.g-head { padding: 7px 18px 4px; font-size: 11px; color: #8a94a6; font-weight: 600; }
.g-item { display: flex; align-items: center; gap: 10px; padding: 8px 18px; cursor: pointer; font-size: 13px; color: #2c3e50; }
.g-item.active { background: #eaf1fb; }
.g-icon { width: 22px; text-align: center; }
.g-label { font-weight: 500; }
.g-sub { margin-left: auto; font-size: 11px; color: #8a94a6; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.g-empty { padding: 30px; text-align: center; color: #999; font-size: 12px; }
</style>