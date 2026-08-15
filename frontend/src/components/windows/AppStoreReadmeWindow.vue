<template>
  <div class="readme-window">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <span class="title"><BookOpen :size="15" /> README：{{ name }}</span>
      <span class="repo mono">{{ repo || '' }}</span>
      <a v-if="source" class="btn" :href="source" target="_blank" rel="noopener" style="text-decoration:none;">GitHub</a>
      <button class="btn" style="margin-left:auto;" @click="emit('close')">关闭</button>
    </div>

    <!-- 加载 / 错误 / 内容 -->
    <div class="body">
      <div v-if="loading" class="center"><Loader2 :size="28" class="spin" /> 正在拉取 README...</div>
      <div v-else-if="errorMsg" class="center err">
        <AlertTriangle :size="28" />
        <div>{{ errorMsg }}</div>
      </div>
      <div v-else class="md-body" v-html="html"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { appStoreApi } from '../../api'
import { BookOpen, AlertTriangle, Loader2 } from 'lucide-vue-next'

const props = defineProps({ app: Object })
const emit = defineEmits(['close'])

const name = ref('')
const repo = ref('')
const source = ref('')
const raw = ref('')
const loading = ref(true)
const errorMsg = ref('')

// ---------- 轻量 markdown 渲染 ----------
function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function renderInline(s) {
  s = escapeHtml(s)
  // 行内代码
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>')
  // 加粗
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  // 链接
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
  return s
}

function renderMarkdown(md) {
  const lines = md.split('\n')
  const out = []
  let inCode = false
  let codeBuf = []
  let listOpen = false
  for (const rawLine of lines) {
    const line = rawLine.trimEnd()
    const codeMatch = line.match(/^```(\w*)\s*$/)
    if (codeMatch) {
      if (inCode) {
        out.push('<pre><code>' + codeBuf.join('\n') + '</code></pre>')
        codeBuf = []
        inCode = false
      } else {
        inCode = true
      }
      continue
    }
    if (inCode) { codeBuf.push(escapeHtml(line)); continue }
    // 关闭未闭合的列表
    if (listOpen && !/^[-*]\s/.test(line)) { out.push('</ul>'); listOpen = false }
    // 标题
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      const lv = h[1].length
      out.push(`<h${lv}>${renderInline(h[2])}</h${lv}>`)
      continue
    }
    // 列表项
    if (/^[-*]\s/.test(line)) {
      if (!listOpen) { out.push('<ul>'); listOpen = true }
      out.push('<li>' + renderInline(line.replace(/^[-*]\s/, '')) + '</li>')
      continue
    }
    // 引用
    if (line.startsWith('> ')) {
      out.push('<blockquote>' + renderInline(line.slice(2)) + '</blockquote>')
      continue
    }
    // 水平线
    if (/^---+$/.test(line.trim())) { out.push('<hr />'); continue }
    // 空行忽略
    if (!line.trim()) continue
    out.push('<p>' + renderInline(line) + '</p>')
  }
  if (inCode) out.push('<pre><code>' + codeBuf.join('\n') + '</code></pre>')
  if (listOpen) out.push('</ul>')
  return out.join('\n')
}

const html = computed(() => renderMarkdown(raw.value))

onMounted(async () => {
  try {
    const r = await appStoreApi.readme(props.app.id)
    name.value = r.name || props.app.name || ''
    repo.value = r.repo || ''
    source.value = r.source || props.app.source || ''
    raw.value = r.readme || ''
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || e.message || '拉取 README 失败'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.readme-window { display: flex; flex-direction: column; height: 100%; overflow: hidden; }

.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
.title { font-weight: 700; font-size: 13.5px; display: inline-flex; align-items: center; gap: 6px; }
.repo { font-size: 11.5px; color: #6b7280; }

.body { flex: 1; overflow-y: auto; padding: 14px 18px; }
.center { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: #6b7280; font-size: 13px; }
.center.err { color: #b91c1c; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* markdown 内容样式 */
.md-body { font-size: 13px; color: #1f2937; line-height: 1.7; word-break: break-word; }
.md-body h1, .md-body h2, .md-body h3, .md-body h4 { margin: 16px 0 8px; line-height: 1.4; }
.md-body h1 { font-size: 19px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }
.md-body h2 { font-size: 16px; border-bottom: 1px solid #e5e7eb; padding-bottom: 5px; }
.md-body h3 { font-size: 14.5px; }
.md-body h4 { font-size: 13.5px; }
.md-body p { margin: 6px 0; }
.md-body a { color: #2563eb; }
.md-body ul { margin: 6px 0; padding-left: 22px; }
.md-body li { margin: 3px 0; }
.md-body code { background: #f3f4f6; padding: 1px 5px; border-radius: 4px; font-family: Consolas, monospace; font-size: 12px; color: #b91c1c; }
.md-body pre { background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 10px 12px; overflow-x: auto; font-size: 12px; line-height: 1.55; }
.md-body pre code { background: transparent; color: inherit; padding: 0; }
.md-body blockquote { border-left: 3px solid #d1d5db; margin: 8px 0; padding: 2px 12px; color: #4b5563; }
.md-body hr { border: none; border-top: 1px solid #e5e7eb; margin: 14px 0; }
.md-body img { max-width: 100%; border-radius: 6px; }
</style>
