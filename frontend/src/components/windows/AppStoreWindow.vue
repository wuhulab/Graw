<template>
  <div class="store-window" @click="closePopovers">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="title-wrap">
        <span class="title"><Store :size="16" /> Graw 社区应用商店</span>
        <span class="badge" :class="indexState.source === 'remote' ? 'ok' : 'warn'">
          {{ indexState.source === 'remote' ? '远程索引' : '本地索引' }}
        </span>
        <span v-if="indexState.updated_at" class="updated">更新于 {{ fmtTime(indexState.updated_at) }}</span>
      </div>
      <button class="btn" :disabled="loading" @click="loadIndex(false)">
        {{ loading ? '加载中...' : '刷新' }}
      </button>
      <button class="btn" @click="showConfigModal = true"><Settings2 :size="14" /> 索引地址</button>
    </div>

    <!-- 索引错误提示 -->
    <div v-if="indexState.error" class="error-banner">
      <AlertTriangle :size="14" /> {{ indexState.error }}
      <span class="hint">（已回退到本地索引，可点击"索引地址"配置远程地址）</span>
    </div>

    <!-- 加载 / 空状态 -->
    <div v-if="loading && apps.length === 0" class="empty">
      <Loader2 :size="36" class="spin" />
      <div>正在加载应用商店索引...</div>
    </div>
    <div v-else-if="apps.length === 0" class="empty">
      <Store :size="40" style="color:#6b7280;" />
      <div>应用商店暂无可用应用。</div>
    </div>

    <!-- 应用卡片网格 -->
    <div v-else class="app-grid">
      <div v-for="app in apps" :key="app.id" class="app-card">
        <div class="card-head">
          <img class="app-icon" :src="app.icon" alt="" loading="lazy"
               @error="e => e.target.style.visibility = 'hidden'" />
          <div class="card-titles">
            <div class="app-name">{{ app.name }}</div>
            <div class="app-id mono">{{ app.id }}</div>
          </div>
          <div class="card-actions">
            <a v-if="app.homepage" class="icon-link" :href="app.homepage" target="_blank" rel="noopener" title="官方网站"><Globe :size="14" /></a>
            <a v-if="app.source" class="icon-link" :href="app.source" target="_blank" rel="noopener" title="开源社区"><Github :size="14" /></a>
            <button v-if="app.source" class="readme-btn" title="查看 GitHub README" @click="emit('openReadme', app)">README</button>
          </div>
        </div>

        <p class="app-desc">{{ app.description }}</p>

        <div class="card-foot">
          <div class="tags">
            <span class="tag" :title="'默认版本 ' + app.version">v{{ app.version }}</span>
            <span v-for="a in (app.arch || []).slice(0, 3)" :key="a" class="tag arch">{{ a }}</span>
            <span v-if="app.ports && app.ports.length" class="tag port">
              <Container :size="11" /> {{ app.ports.map(p => p.container).join(', ') }}
            </span>
          </div>
          <button class="btn primary install" @click="emit('openAppInstall', app)">安装</button>
        </div>
      </div>
    </div>

    <!-- ============ 索引地址配置弹窗 ============ -->
    <div v-if="showConfigModal" class="modal-overlay" @click.self="showConfigModal = false">
      <div class="modal">
        <h3><Settings2 :size="16" /> 应用商店索引地址</h3>
        <p class="modal-desc">填写托管在 GitHub Pages 的 index.json 地址，留空则使用仓库内置的本地索引。</p>
        <input v-model.trim="configForm.index_url" class="inp mono" style="width:100%;"
               placeholder="https://&lt;owner&gt;.github.io/&lt;repo&gt;/index.json" />
        <div class="actions">
          <button class="btn" @click="showConfigModal = false">取消</button>
          <button class="btn primary" :disabled="savingConfig" @click="saveConfig">
            {{ savingConfig ? '保存中...' : '保存并刷新' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { appStoreApi } from '../../api'
import { Store, Settings2, Globe, Github, Container, AlertTriangle, Loader2 } from 'lucide-vue-next'

const emit = defineEmits(['openAppInstall', 'openReadme', 'close'])

const apps = ref([])
const loading = ref(false)
const indexState = reactive({ source: '', updated_at: '', error: '' })

// 索引地址配置
const showConfigModal = ref(false)
const savingConfig = ref(false)
const configForm = reactive({ index_url: '' })

function fmtTime(t) {
  if (!t) return ''
  return String(t).replace('T', ' ').replace(/\.\d+.*$/, '')
}

async function loadIndex(refresh) {
  loading.value = true
  try {
    const r = await appStoreApi.index(refresh)
    apps.value = r.apps || []
    indexState.source = r.source || ''
    indexState.updated_at = r.updated_at || ''
    indexState.error = r.error || ''
  } catch (e) {
    indexState.error = e.response?.data?.detail || e.message
    apps.value = []
  } finally {
    loading.value = false
  }
}

function closePopovers() { /* 预留：点击空白收起下拉 */ }

async function saveConfig() {
  savingConfig.value = true
  try {
    await appStoreApi.saveConfig(configForm.index_url)
    showConfigModal.value = false
    await loadIndex(true)
  } catch (e) {
    alert('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    savingConfig.value = false
  }
}

onMounted(async () => {
  try {
    const cfg = await appStoreApi.config()
    configForm.index_url = cfg.index_url || ''
  } catch (e) { /* 忽略 */ }
  await loadIndex(false)
})
</script>

<style scoped>
.store-window { display: flex; flex-direction: column; height: 100%; overflow: hidden; }

.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap; }
.title-wrap { display: flex; align-items: center; gap: 8px; margin-right: auto; min-width: 0; }
.title { font-weight: 700; font-size: 13.5px; display: inline-flex; align-items: center; gap: 6px; }
.updated { color: #6b7280; font-size: 11.5px; }
.badge { font-size: 11px; padding: 1px 8px; border-radius: 999px; }
.badge.ok { background: #ecfdf5; color: #047857; }
.badge.warn { background: #fffbeb; color: #b45309; }

.error-banner { margin: 8px 12px 0; padding: 6px 10px; background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; border-radius: 6px; font-size: 12px; display: flex; align-items: center; gap: 6px; }
.error-banner .hint { color: #6b7280; }

.empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: #6b7280; font-size: 13px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* 应用卡片网格 */
.app-grid { flex: 1; overflow-y: auto; padding: 12px; display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; align-content: start; }

.app-card { border: 1px solid #e5e7eb; border-radius: 10px; background: #fff; padding: 12px; display: flex; flex-direction: column; gap: 8px; transition: box-shadow .15s, border-color .15s; }
.app-card:hover { border-color: #c7d2fe; box-shadow: 0 4px 14px rgba(0,0,0,.08); }

.card-head { display: flex; align-items: center; gap: 10px; }
.app-icon { width: 42px; height: 42px; border-radius: 8px; object-fit: cover; background: #f3f4f6; }
.card-titles { min-width: 0; flex: 1; }
.app-name { font-weight: 700; font-size: 13.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.app-id { font-size: 11px; color: #9ca3af; }
.card-actions { display: flex; align-items: center; gap: 2px; }
.icon-link { color: #6b7280; padding: 4px; border-radius: 6px; display: inline-flex; }
.icon-link:hover { background: #f3f4f6; color: #2563eb; }
.readme-btn { font-size: 10.5px; color: #6b7280; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 2px 7px; cursor: pointer; font-family: inherit; }
.readme-btn:hover { background: #eef2ff; color: #4338ca; border-color: #c7d2fe; }

.app-desc { font-size: 12px; color: #374151; line-height: 1.6; margin: 0; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; min-height: 58px; }

.card-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: auto; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.tag { font-size: 10.5px; background: #f3f4f6; color: #374151; padding: 1px 6px; border-radius: 999px; display: inline-flex; align-items: center; gap: 3px; }
.tag.arch { background: #eef2ff; color: #4338ca; }
.tag.port { background: #ecfdf5; color: #047857; }
.btn.install { flex-shrink: 0; }
</style>
