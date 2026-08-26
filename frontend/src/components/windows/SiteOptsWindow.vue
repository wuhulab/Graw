<!--
  站点增强配置窗口
  业务：为各站点配置防盗链、gzip 压缩、静态资源缓存过期等 nginx 增强项，可逐站保存或清除。
  后端模块：/api/sitesopts
  关键状态：sites（站点及其增强配置）、busy（保存忙态）
  打开方式：独立「站点配置」入口挂载
-->
<template>
  <div class="siteopts-window">
    <div class="toolbar">
      <span class="hint"><ShieldCheck :size="14" /> 站点增强配置：防盗链 / gzip / 静态资源缓存</span>
      <button class="btn" :disabled="loading" @click="load"><RefreshCw :size="14" /> 刷新</button>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="sites.length === 0" class="empty">
      <ShieldCheck :size="40" style="color:#9ca3af;" />
      <div>暂无站点</div>
    </div>
    <div v-else class="sites-list">
      <div v-for="s in sites" :key="s.id" class="site-card">
        <div class="card-header">
          <span class="site-name">{{ s.name }}</span>
          <span class="site-type">{{ s.type }}</span>
          <span v-if="!s.enabled" class="badge off">已停用</span>
        </div>
        <div class="card-body">
          <label class="opt-row">
            <input type="checkbox" v-model="s.hotlink_enabled" :disabled="busy" />
            <span>防盗链</span>
          </label>
          <div v-if="s.hotlink_enabled" class="opt-sub">
            <label class="field">
              <span class="label">允许来源域名（每行一个，含通配 *）</span>
              <textarea v-model="s._hotlink_text" rows="3" placeholder="example.com&#10;*.cdn.com" :disabled="busy" class="note-input" spellcheck="false"></textarea>
            </label>
            <label class="opt-row">
              <input type="checkbox" v-model="s.hotlink_allow_empty_referer" :disabled="busy" />
              <span>允许空来源（直接访问图片/文件）</span>
            </label>
          </div>
          <label class="opt-row">
            <input type="checkbox" v-model="s.gzip_enabled" :disabled="busy" />
            <span>启用 gzip 压缩</span>
          </label>
          <label class="field">
            <span class="label">静态资源缓存过期</span>
            <select v-model.number="s._cache_select" :disabled="busy" class="note-input">
              <option :value="0">不设置</option>
              <option :value="3600">1 小时</option>
              <option :value="86400">1 天</option>
              <option :value="604800">7 天</option>
              <option :value="2592000">30 天</option>
            </select>
          </label>
          <div class="card-actions">
            <button class="btn primary" :disabled="busy" @click="apply(s)">{{ busy ? '保存中…' : '保存' }}</button>
            <button class="btn" :disabled="busy" @click="clear(s)">清除配置</button>
          </div>
          <div v-if="s._err" class="err-text">{{ s._err }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'                          // Composition API：响应式、挂载
import { RefreshCw, ShieldCheck } from 'lucide-vue-next'       // 图标集合
import { sitesoptsApi } from '../../api'                       // 站点增强配置后端接口封装

const loading = ref(false)
const busy = ref(false)
const sites = ref([])

async function load() {
  loading.value = true
  try {
    const r = await sitesoptsApi.sites()
    sites.value = (r && r.sites || []).map(s => ({
      ...s,
      _hotlink_text: (s.hotlink_allowed || []).join('\n'),
      _cache_select: s.cache_expire || 0,
      _err: '',
    }))
  } catch (e) {
    alert('加载站点失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// --- 动作：保存站点增强配置 ---
async function apply(s) {
  if (busy.value) return
  busy.value = true
  s._err = ''
  try {
    const allowed = s._hotlink_text.split('\n').map(t => t.trim()).filter(Boolean)
    await sitesoptsApi.apply({
      site_id: s.id,
      hotlink_enabled: s.hotlink_enabled,
      hotlink_allowed: allowed,
      hotlink_allow_empty_referer: s.hotlink_allow_empty_referer,
      gzip_enabled: s.gzip_enabled,
      cache_expire: s._cache_select || 0,
    })
    alert(`站点「${s.name}」配置已保存`)
    await load()
  } catch (e) {
    s._err = e.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

async function clear(s) {
  if (busy.value) return
  if (!confirm(`确认清除站点「${s.name}」的全部增强配置？`)) return
  busy.value = true
  s._err = ''
  try {
    await sitesoptsApi.clear(s.id)
    alert(`站点「${s.name}」配置已清除`)
    await load()
  } catch (e) {
    s._err = e.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.siteopts-window { padding: 10px; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; gap: 10px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.hint { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: #1d1d1f; }
.sites-list { overflow-y: auto; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; align-content: flex-start; }
.site-card { border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden; flex-shrink: 0; }
.card-header { display: flex; align-items: center; gap: 8px; padding: 10px 12px; background: #f9fafb; font-size: 13px; font-weight: 600; border-bottom: 1px solid #e5e7eb; }
.site-name { flex: 1; }
.site-type { font-weight: 400; font-size: 11px; color: #6e6e73; }
.badge.off { background: #f3f4f6; color: #6b7280; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.card-body { padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.opt-row { display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 13px; }
.opt-sub { margin-left: 24px; display: flex; flex-direction: column; gap: 8px; }
.field { display: block; }
.field .label { display: block; font-size: 11px; color: #1d1d1f; font-weight: 600; margin-bottom: 4px; }
.note-input { width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; box-sizing: border-box; font-family: inherit; }
.card-actions { display: flex; gap: 8px; }
.err-text { color: #b91c1c; font-size: 12px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
</style>