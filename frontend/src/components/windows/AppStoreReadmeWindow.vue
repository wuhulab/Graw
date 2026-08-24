<template>
  <div class="readme-window">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <span class="title"><BookOpen :size="15" /> {{ $t('appreadme.title', { name }) }}</span>
      <span class="repo mono">{{ repo || '' }}</span>
      <a v-if="safeSource" class="btn" :href="safeSource" target="_blank" rel="noopener" style="text-decoration:none;">{{ $t('appreadme.github') }}</a>
      <button class="btn" style="margin-left:auto;" @click="emit('close')">{{ $t('appreadme.close') }}</button>
    </div>

    <!-- 加载 / 错误 / 内容 -->
    <div class="body">
      <div v-if="loading" class="center"><Loader2 :size="28" class="spin" /> {{ $t('appreadme.loading') }}</div>
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
import { useI18n } from 'vue-i18n'
import MarkdownIt from 'markdown-it'
import taskLists from 'markdown-it-task-lists'
import DOMPurify from 'dompurify'
import { appStoreApi } from '../../api'
import { localizedName } from '../../appStoreL10n'
import { BookOpen, AlertTriangle, Loader2 } from 'lucide-vue-next'

const { t, locale } = useI18n()

const props = defineProps({ app: Object })
const emit = defineEmits(['close'])

const name = ref('')
const repo = ref('')
const source = ref('')
const raw = ref('')
const loading = ref(true)
const errorMsg = ref('')

// ---------- 完整 Markdown 渲染（GitHub 兼容） ----------
// markdown-it 默认 preset 已支持：标题 / 表格 / 删除线(~~) / 自动链接 /
// 引用 / 代码块 / 有序无序列表 / 嵌套列表 / 图片。
// markdown-it-task-lists 补充 GitHub 任务列表（- [ ] / - [x]）。
// html: true 允许 GitHub README 中常见的原始 HTML 排版（<div align>、<a>、<img>），
// 渲染结果统一经过 DOMPurify 白名单清理，移除 script / on* / javascript: 等危险内容。
const md = new MarkdownIt({
  html: true,      // 允许内联 HTML（如 GitHub README 中的 <div align=center> / <a> / <img src=>）
  linkify: true,   // 裸 URL 自动转链接
  breaks: false,   // 保留 GitHub 换行语义（段落内换行不强制 <br>）
}).use(taskLists, { enabled: false, label: true })

// GitHub 仓库上下文：用于把 README 中的相对链接/图片转成可访问的绝对地址
const repoCtx = { owner: '', name: '', rawBase: '', blobBase: '' }

// 自定义图片渲染：相对路径 → https://github.com/<owner>/<repo>/raw/HEAD/<path>
const _defaultImage = md.renderer.rules.image || function (tokens, idx, options, env, self) {
  return self.renderToken(tokens, idx, options)
}
md.renderer.rules.image = function (tokens, idx, options, env, self) {
  const token = tokens[idx]
  const src = token.attrGet('src') || ''
  if (src && !/^(https?:|data:|mailto:)/i.test(src) && repoCtx.rawBase) {
    token.attrSet('src', repoCtx.rawBase + src.replace(/^\.\//, ''))
  }
  return _defaultImage(tokens, idx, options, env, self)
}

// 自定义链接渲染：相对链接 → https://github.com/<owner>/<repo>/blob/HEAD/<path>
const _defaultLinkOpen = md.renderer.rules.link_open || function (tokens, idx, options, env, self) {
  return self.renderToken(tokens, idx, options)
}
md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  const token = tokens[idx]
  const href = token.attrGet('href') || ''
  // 跳过绝对地址、锚点、邮件协议（javascript: 等危险协议已被 markdown-it 过滤）
  if (href && !/^(https?:|mailto:|#)/i.test(href) && repoCtx.blobBase) {
    token.attrSet('href', repoCtx.blobBase + href.replace(/^\.\//, ''))
  }
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener')
  return _defaultLinkOpen(tokens, idx, options, env, self)
}

// 外链协议白名单：source 来自后端 readme 接口 / 远程索引（不可信），
// Vue 3 的 :href 绑定不会自动过滤 javascript: 等危险协议，必须显式校验
const safeSource = computed(() => (/^https?:\/\//i.test(source.value || '') ? source.value : ''))

const html = computed(() => {
  if (!raw.value) return `<p class="md-empty">${t('appreadme.noContent')}</p>`
  // 每次渲染前刷新仓库上下文，保证相对链接正确拼接
  const [owner, name] = (repo.value || '/').split('/')
  repoCtx.owner = owner || ''
  repoCtx.name = name || ''
  repoCtx.rawBase = repoCtx.owner && repoCtx.name ? `https://github.com/${repoCtx.owner}/${repoCtx.name}/raw/HEAD/` : ''
  repoCtx.blobBase = repoCtx.owner && repoCtx.name ? `https://github.com/${repoCtx.owner}/${repoCtx.name}/blob/HEAD/` : ''
  return DOMPurify.sanitize(md.render(raw.value), {
    ADD_ATTR: ['target', 'rel'],        // 允许链接的 target/rel 属性
    // URI 白名单：仅放行 http(s)/mailto 与相对地址；不放行 data:——
    // data:image/svg+xml 可内嵌脚本，历史上多次出现 DOMPurify 相关绕过
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.-]+(?:[^a-z+.-]|$))/i,
  })
})

onMounted(async () => {
  try {
    const r = await appStoreApi.readme(props.app.id)
    // 标题名称：优先索引内嵌翻译（i18n.<locale>.yml），否则回退接口/默认名称
    name.value = localizedName(props.app, locale.value) || r.name || props.app.name || ''
    repo.value = r.repo || ''
    source.value = r.source || props.app.source || ''
    raw.value = r.readme || ''
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || e.message || t('appreadme.loadFailed')
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
.mono { font-family: Consolas, monospace; }

.body { flex: 1; overflow-y: auto; padding: 14px 18px; }
.center { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: #6b7280; font-size: 13px; }
.center.err { color: #b91c1c; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ============ markdown 内容样式（GitHub 风格） ============ */
.md-body { font-size: 13px; color: #24292f; line-height: 1.7; word-break: break-word; }

.md-body h1, .md-body h2, .md-body h3, .md-body h4, .md-body h5, .md-body h6 {
  margin: 20px 0 10px;
  line-height: 1.4;
  font-weight: 600;
}
.md-body h1 { font-size: 20px; border-bottom: 1px solid #d0d7de; padding-bottom: 7px; }
.md-body h2 { font-size: 17px; border-bottom: 1px solid #d0d7de; padding-bottom: 5px; }
.md-body h3 { font-size: 15px; }
.md-body h4 { font-size: 13.5px; }
.md-body h5, .md-body h6 { font-size: 13px; }

.md-body p { margin: 8px 0; }

.md-body a { color: #0969da; text-decoration: none; }
.md-body a:hover { text-decoration: underline; }

.md-body ul, .md-body ol { margin: 8px 0; padding-left: 26px; }
.md-body li { margin: 4px 0; }
.md-body li > ul, .md-body li > ol { margin: 4px 0; }

/* 任务列表 */
.md-body li.task-list-item { list-style: none; margin-left: -20px; }
.md-body input[type="checkbox"] { margin-right: 6px; vertical-align: -2px; }

/* 行内代码 / 代码块 */
.md-body code {
  background: rgba(175, 184, 193, 0.2);
  padding: 2px 5px;
  border-radius: 5px;
  font-family: Consolas, "SF Mono", monospace;
  font-size: 12px;
  color: #cf222e;
}
.md-body pre {
  background: #f6f8fa;
  border: 1px solid #d0d7de;
  border-radius: 8px;
  padding: 12px 14px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.55;
  margin: 10px 0;
}
.md-body pre code {
  background: transparent;
  color: #24292f;
  padding: 0;
  font-size: 12px;
}

/* 引用 */
.md-body blockquote {
  border-left: 4px solid #d0d7de;
  margin: 10px 0;
  padding: 2px 14px;
  color: #57606a;
  background: rgba(246, 248, 250, 0.6);
}
.md-body blockquote > p { margin: 6px 0; }

/* 表格 */
.md-body table {
  border-collapse: collapse;
  margin: 12px 0;
  display: block;
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
  font-size: 12.5px;
}
.md-body th, .md-body td {
  border: 1px solid #d0d7de;
  padding: 6px 12px;
  text-align: left;
}
.md-body th {
  background: #f6f8fa;
  font-weight: 600;
}
.md-body tr:nth-child(2n) td { background: #f8fafc; }

/* 水平线 */
.md-body hr { border: none; border-top: 1px solid #d0d7de; margin: 16px 0; }

/* 图片 */
.md-body img { max-width: 100%; border-radius: 6px; margin: 6px 0; }

/* 删除线 */
.md-body del { color: #57606a; }

/* GitHub README 常用原始 HTML 元素 */
.md-body kbd {
  display: inline-block;
  padding: 1px 6px;
  font: 11px Consolas, monospace;
  color: #24292f;
  vertical-align: middle;
  background-color: #f6f8fa;
  border: 1px solid #d0d7de;
  border-bottom-color: #b6bcc4;
  border-radius: 5px;
  box-shadow: inset 0 -1px 0 #b6bcc4;
}
.md-body details { margin: 10px 0; }
.md-body summary { cursor: pointer; font-weight: 600; }
.md-body sup { font-size: 10px; }
.md-body mark { background: #fff8c5; color: #24292f; padding: 0 2px; }
</style>
