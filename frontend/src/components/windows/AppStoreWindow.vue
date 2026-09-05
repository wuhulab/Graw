<!--
  AppStoreWindow.vue — 应用商店主窗口
  ==========================================================
  业务作用：
    应用商店的浏览与入口页：加载远程/本地应用索引（index），展示应用卡片网格，
    支持按分类筛选与关键词搜索；卡片上可打开应用官网/源码、查看 README、发起
    安装。同时承载索引地址配置弹窗与首次进入的免责声明弹窗。
  后端模块：
    /api/appstore 的 index（拉取应用列表）、config / saveConfig（索引地址
    读写）。安装与 README 分别由 AppStoreInstallWindow / AppStoreReadmeWindow
    继续消费。
  关键状态：
    - apps           索引中的应用列表
    - indexState     索引来源（远程/本地）、更新时间、错误信息
    - filteredApps   按分类 + 搜索过滤后的展示列表
    - showDisclaimer 首次进入的免责声明弹窗（已同意写入 localStorage 不再弹出）
  打开方式：
    由桌面/任务栏打开应用商店入口，无 props 传入。
-->
<template>
  <div class="store-window" @click="closePopovers">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="title-wrap">
        <span class="title"><Store :size="16" /> {{ $t('appstore.title') }}</span>
        <span class="badge" :class="indexState.source === 'remote' ? 'ok' : 'warn'">
          {{ indexState.source === 'remote' ? $t('appstore.remoteIndex') : $t('appstore.localIndex') }}
        </span>
        <span v-if="indexState.updated_at" class="updated">{{ $t('appstore.updatedAt', { time: fmtTime(indexState.updated_at) }) }}</span>
      </div>
      <!-- 分类筛选下拉 -->
      <select class="cat-select" v-model="selectedCategory" :title="$t('appstore.filterByCategory')">
        <option value="">{{ $t('appstore.allCategories') }}</option>
        <option v-for="cat in categories" :key="cat" :value="cat">{{ $t('appstore.categories.' + cat, cat) }}</option>
      </select>
      <!-- 搜索输入框 -->
      <input class="search-inp" type="text" v-model.trim="searchQuery"
             :placeholder="$t('appstore.searchPlaceholder')" />
      <button class="btn" :disabled="loading" @click="loadIndex(false)">
        {{ loading ? $t('common.loading') : $t('common.refresh') }}
      </button>
      <button class="btn" @click="emit('openAppStoreConfig', { indexUrl: configForm.index_url })"><Settings2 :size="14" /> {{ $t('appstore.indexConfig') }}</button>
    </div>

    <!-- 索引错误提示 -->
    <div v-if="indexState.error" class="error-banner">
      <AlertTriangle :size="14" /> {{ indexState.error }}
      <span class="hint">{{ $t('appstore.indexFallbackHint') }}</span>
    </div>

    <!-- 加载 / 空状态 -->
    <div v-if="loading && apps.length === 0" class="empty">
      <Loader2 :size="36" class="spin" />
      <div>{{ $t('appstore.loadingIndex') }}</div>
    </div>
    <div v-else-if="apps.length === 0" class="empty">
      <Store :size="40" style="color:#6b7280;" />
      <div>{{ $t('appstore.noApps') }}</div>
    </div>
    <!-- 筛选无结果 -->
    <div v-else-if="filteredApps.length === 0" class="empty">
      <Search :size="40" style="color:#6b7280;" />
      <div>{{ $t('appstore.noMatch') }}</div>
      <div class="hint">{{ $t('appstore.noMatchHint') }}</div>
    </div>

    <!-- 应用卡片网格（按分类 / 搜索过滤） -->
    <div v-else class="app-grid">
      <div v-for="app in filteredApps" :key="app.id" class="app-card">
        <div class="card-head">
          <img class="app-icon" :src="app.icon" alt="" loading="lazy"
               @error="e => e.target.style.visibility = 'hidden'" />
          <div class="card-titles">
            <div class="app-name">
              <span class="name-text">{{ appName(app) }}</span>
              <span v-for="t in (app.tags || [])" :key="t" class="app-tag" :class="tagClass(t)">{{ $t('appstore.tags.' + t, t) }}</span>
            </div>
            <div class="app-id mono">{{ app.id }}</div>
          </div>
          <div class="card-actions">
            <a v-if="safeUrl(app.homepage)" class="icon-link" :href="safeUrl(app.homepage)" target="_blank" rel="noopener" :title="$t('appstore.officialWebsite')"><Globe :size="14" /></a>
            <a v-if="safeUrl(app.source)" class="icon-link" :href="safeUrl(app.source)" target="_blank" rel="noopener" :title="$t('appstore.openSource')"><Github :size="14" /></a>
            <button v-if="app.source" class="readme-btn" :title="$t('appstore.viewReadme')" @click="emit('openReadme', app)"><BookOpen :size="14" /></button>
          </div>
        </div>

        <p class="app-desc">{{ appDesc(app) }}</p>

        <div class="card-foot">
          <div class="tags">
            <span class="tag" :title="$t('appstore.defaultVersion', { version: app.version })">{{ fmtVersion(app.version) }}</span>
            <span v-for="a in (app.arch || []).slice(0, 3)" :key="a" class="tag arch">{{ a }}</span>
            <span v-if="app.ports && app.ports.length" class="tag port">
              <Container :size="11" /> {{ app.ports.map(p => p.container).join(', ') }}
            </span>
          </div>
          <button class="btn primary install" @click="emit('openAppInstall', app)">{{ $t('appstore.install') }}</button>
        </div>
      </div>
    </div>

    <!-- ============ 首次进入免责声明弹窗（保留内嵌确认） ============ -->
    <div v-if="showDisclaimer" class="disc-modal-overlay">
      <div class="disc-modal">
        <div class="disc-head">
          <ShieldAlert :size="20" />
          <span>{{ $t('appstore.disclaimerTitle') }}</span>
          <span class="disc-version">{{ $t('appstore.disclaimerVersion', { version: '1.1.0' }) }}</span>
        </div>
        <div class="disc-body" ref="discBodyEl" @scroll="onDiscScroll">
          <pre class="disc-text">{{ disclaimerText }}</pre>
        </div>
        <div class="disc-foot">
          <label class="disc-agree-label">
            <input type="checkbox" :disabled="!discScrolled" v-model="discAgreed" />
            <span>
              {{ $t('appstore.disclaimerAgree') }}
              <em v-if="!discScrolled" style="color:#dc2626;">{{ $t('appstore.disclaimerScrollHint') }}</em>
            </span>
          </label>
          <div class="disc-actions">
            <button class="btn" @click="emit('close')">{{ $t('appstore.close') }}</button>
            <button class="btn primary" :disabled="!discAgreed" @click="acceptDisclaimer">{{ $t('appstore.enter') }}</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'   // 状态/表单/派生值/挂载钩子
import { useI18n } from 'vue-i18n'   // 翻译函数与当前语种
import { appStoreApi } from '../../api'   // 应用商店 API（索引/配置）
import { localizedName, localizedDescription } from '../../appStoreL10n'   // 应用名/描述的多语言文案
import { formBus } from '../../store/formBus'   // 表单保存信号：索引配置窗口保存成功后强制刷新
import { Store, Settings2, Globe, Github, Container, AlertTriangle, Loader2, BookOpen, Search, ShieldAlert } from 'lucide-vue-next'   // 工具栏/卡片/弹窗图标

const { t, locale } = useI18n()
const emit = defineEmits(['openAppInstall', 'openReadme', 'openAppStoreConfig', 'close'])   // openAppInstall 打开安装表单；openReadme 打开 README；openAppStoreConfig 打开索引配置窗口；close 关窗

// 应用显示名称：优先索引内嵌翻译（i18n.<locale>.yml），
// 其次前端语言包内 appNames 覆盖，最后回退索引默认名称
function appName(app) {
  return localizedName(app, locale.value) || t('appstore.appNames.' + app.id, app.name)
}

// 应用显示描述：优先索引内嵌翻译，否则回退索引默认描述
function appDesc(app) {
  return localizedDescription(app, locale.value) || app.description || ''
}

// 外链协议白名单：homepage / source 来自索引数据（index_url 可指向任意
// 远程源，属不可信输入），Vue 3 的 :href 不会自动过滤 javascript: 协议，
// 恶意索引注入 javascript: 链接即可在点击时执行任意脚本窃取 token
function safeUrl(u) {
  return /^https?:\/\//i.test(u || '') ? u : ''
}

// 应用标签徽标样式映射：推荐=金色，官方=蓝色（其余标签不显示特殊样式）
function tagClass(t) {
  if (t === '推荐') return 'recommend'
  if (t === '官方') return 'blue'
  return ''
}

const apps = ref([])   // 索引中的应用列表
const loading = ref(false)   // 索引加载中（禁用刷新按钮）
const indexState = reactive({ source: '', updated_at: '', error: '' })   // 索引来源（远程/本地）、更新时间、错误信息

// 分类筛选 + 搜索
const selectedCategory = ref('')       // 空 = 全部分类
const searchQuery = ref('')            // 搜索关键词
// 分类展示顺序（与 data.yml category 字段保持一致）
const CATEGORY_ORDER = ['数据库/存储', '面板/网站', 'AI/开发', '网络/工具', '监控/运维', '开发/DevOps']

// 索引中存在应用的分类列表（按固定顺序，忽略空分类）
const categories = computed(() => {
  const seen = new Set(apps.value.map(a => a.category).filter(Boolean))
  return CATEGORY_ORDER.filter(c => seen.has(c))
})

// 按分类 + 搜索关键词过滤后的应用列表
const filteredApps = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return apps.value.filter(a => {
    // 分类过滤
    if (selectedCategory.value && a.category !== selectedCategory.value) return false
    // 搜索过滤：匹配本地化名称 / ID / 本地化描述
    if (!q) return true
    return (appName(a) || '').toLowerCase().includes(q)
        || (a.id || '').toLowerCase().includes(q)
        || (appDesc(a) || '').toLowerCase().includes(q)
  })
})

// 索引地址配置已拆分为独立窗口（AppStoreConfigWindow）：
// 保存成功后 bumpForm('appstore') 触发此处强制刷新索引
watch(() => formBus.appstore, () => loadIndex(true))
const configForm = reactive({ index_url: '' })

// ===================== 首次进入免责声明 =====================
const DISCLAIMER_KEY = 'graw_appstore_disclaimer_v1'   // 本地存储键（按文档版本）
const showDisclaimer = ref(false)                        // 免责弹窗是否显示
const discScrolled = ref(false)                          // 是否已滚动到底部
const discAgreed = ref(false)                            // 是否已勾选同意
const discBodyEl = ref(null)                             // 免责文本滚动容器

// 免责声明全文：迁入 i18n 语言包（zh-CN 为完整中文版，属「以中文为准」的
// 法律文本；其余语种未提供该 key 时由 vue-i18n 回退到中文，避免误译）。
const disclaimerText = computed(() => t('appstore.disclaimerText'))

// 滚动容器滚动事件：滚动到底部后启用"同意并继续使用"
function onDiscScroll() {
  const el = discBodyEl.value
  if (!el) return
  discScrolled.value = el.scrollHeight - el.scrollTop - el.clientHeight < 8
}

// 同意免责声明：写入本地存储，后续进入不再弹出
function acceptDisclaimer() {
  localStorage.setItem(DISCLAIMER_KEY, '1')
  showDisclaimer.value = false
}

// 时间展示：把 ISO 时间串去掉 T 与毫秒，变成 "YYYY-MM-DD HH:mm:ss"
function fmtTime(t) {
  if (!t) return ''
  return String(t).replace('T', ' ').replace(/\.\d+.*$/, '')
}

// 版本号显示：部分应用 tag 自带 v 前缀（如 AList v3.40.0），避免重复显示 "vv"
function fmtVersion(v) {
  if (!v) return ''
  return String(v).startsWith('v') ? String(v) : 'v' + String(v)
}

// --- 加载应用索引：refresh=true 时强制后端重新拉取 ---
async function loadIndex(refresh) {
  loading.value = true
  try {
    const r = await appStoreApi.index(refresh)
    apps.value = r.apps || []
    indexState.source = r.source || ''
    indexState.updated_at = r.updated_at || ''
    indexState.error = r.error || ''
  } catch (e) {
    // 拉取失败：保留错误信息给界面提示，并清空列表走空状态
    indexState.error = e.response?.data?.detail || e.message
    apps.value = []
  } finally {
    loading.value = false
  }
}

function closePopovers() { /* 预留：点击空白收起下拉 */ }

// --- 保存索引地址并强制刷新（已移至独立配置窗口） ---

onMounted(async () => {
  try {
    const cfg = await appStoreApi.config()
    configForm.index_url = cfg.index_url || ''
  } catch (e) { /* 忽略 */ }
  await loadIndex(false)
  // 首次进入：检查是否已同意免责声明
  showDisclaimer.value = !localStorage.getItem(DISCLAIMER_KEY)
})
</script>

<style scoped>
.store-window { position: relative; display: flex; flex-direction: column; height: 100%; overflow: hidden; }

.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap; }
.title-wrap { display: flex; align-items: center; gap: 8px; margin-right: auto; min-width: 0; }
.title { font-weight: 700; font-size: 13.5px; display: inline-flex; align-items: center; gap: 6px; }
.updated { color: #6b7280; font-size: 11.5px; }
.badge { font-size: 11px; padding: 1px 8px; border-radius: 999px; }
.badge.ok { background: #ecfdf5; color: #047857; }
.badge.warn { background: #fffbeb; color: #b45309; }

/* 分类下拉 */
.cat-select {
  font-size: 12px;
  padding: 4px 8px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #1d1d1f;
  cursor: pointer;
  max-width: 130px;
}
.cat-select:focus { outline: none; border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,.15); }

/* 搜索输入框 */
.search-inp {
  font-size: 12px;
  padding: 5px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #1d1d1f;
  width: 180px;
  min-width: 120px;
}
.search-inp::placeholder { color: #9ca3af; }
.search-inp:focus { outline: none; border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,.15); }

.error-banner { margin: 8px 12px 0; padding: 6px 10px; background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; border-radius: 6px; font-size: 12px; display: flex; align-items: center; gap: 6px; }
.error-banner .hint { color: #6b7280; }

.empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: #6b7280; font-size: 13px; }
.empty .hint { color: #9ca3af; font-size: 11.5px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* 应用卡片网格 */
.app-grid { flex: 1; overflow-y: auto; padding: 12px; display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; align-content: start; }

.app-card { border: 1px solid #e5e7eb; border-radius: 10px; background: #fff; padding: 12px; display: flex; flex-direction: column; gap: 8px; transition: box-shadow .15s, border-color .15s; }
.app-card:hover { border-color: #c7d2fe; box-shadow: 0 4px 14px rgba(0,0,0,.08); }

.card-head { display: flex; align-items: center; gap: 10px; }
.app-icon { width: 42px; height: 42px; border-radius: 8px; object-fit: cover; background: #f3f4f6; }
.card-titles { min-width: 0; flex: 1; }
.app-name { font-weight: 700; font-size: 13.5px; display: flex; align-items: center; gap: 4px; min-width: 0; }
.app-name .name-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.app-tag { flex-shrink: 0; font-size: 10px; font-weight: 600; padding: 0 6px; border-radius: 999px; }
.app-tag.recommend { background: #fff7e6; color: #d46b08; border: 1px solid #ffd591; }
.app-tag.blue { background: #e6f4ff; color: #0958d9; border: 1px solid #91caff; }
.app-id { font-size: 11px; color: #9ca3af; }
.card-actions { display: flex; align-items: center; gap: 2px; }
.icon-link { color: #6b7280; padding: 4px; border-radius: 6px; display: inline-flex; }
.icon-link:hover { background: #f3f4f6; color: #2563eb; }
.readme-btn { display: inline-flex; align-items: center; color: #6b7280; background: transparent; border: none; padding: 4px; border-radius: 6px; cursor: pointer; }
.readme-btn:hover { background: #f3f4f6; color: #2563eb; }

.app-desc { font-size: 12px; color: #374151; line-height: 1.6; margin: 0; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; min-height: 58px; }

.card-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: auto; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.tag { font-size: 10.5px; background: #f3f4f6; color: #374151; padding: 1px 6px; border-radius: 999px; display: inline-flex; align-items: center; gap: 3px; }
.tag.arch { background: #eef2ff; color: #4338ca; }
.tag.port { background: #ecfdf5; color: #047857; }
.btn.install { flex-shrink: 0; }

/* 免责声明弹窗 */
.disc-modal-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.55);
  z-index: 60;
  padding: 24px;
}
.disc-modal {
  width: 100%;
  max-width: 620px;
  max-height: 92%;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.disc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  font-weight: 700;
  font-size: 14px;
  color: #1d1d1f;
  border-bottom: 1px solid #e5e7eb;
  background: #fafafa;
  flex-shrink: 0;
}
.disc-version {
  margin-left: auto;
  font-size: 11px;
  color: #6b7280;
  background: #f3f4f6;
  padding: 1px 8px;
  border-radius: 999px;
}
.disc-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px;
  min-height: 0;
}
.disc-text {
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.75;
  color: #374151;
  white-space: pre-wrap;
  word-break: break-word;
}
.disc-foot {
  padding: 12px 16px;
  border-top: 1px solid #e5e7eb;
  background: #fafafa;
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex-shrink: 0;
}
.disc-agree-label {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12.5px;
  color: #374151;
  cursor: pointer;
  line-height: 1.6;
}
.disc-agree-label input { margin-top: 2px; }
.disc-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
