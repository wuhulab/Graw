<!--
  伪静态规则窗口
  业务：为静态/子网站一键应用常见框架（WordPress/ThinkPHP/Laravel 等）的 nginx 伪静态规则，规则由面板白名单模板生成。
  后端模块：/api/rewrite
  关键状态：sites（站点列表）、templates（规则模板）、previewText（nginx 片段预览）、selSite / selTemplate（选择）
  打开方式：独立「伪静态」入口挂载
-->
<template>
  <div class="rewrite-window">
    <!-- 顶部说明 -->
    <div class="intro">
      <FileCode2 :size="14" />
      为静态网站一键应用常用框架（WordPress / ThinkPHP / Laravel 等）的伪静态规则，
      规则由面板内置白名单模板生成，应用后自动写入 nginx 配置并重载。
    </div>

    <!-- 选择区 -->
    <div class="panel">
      <div class="panel-title"><Layers :size="14" /> 应用伪静态规则</div>
      <div class="select-row">
        <div class="sel-group">
          <label>选择站点</label>
          <select v-model="selSite" class="sel">
            <option value="">— 请选择站点 —</option>
            <option v-for="s in sites" :key="s.id" :value="s.id" :disabled="s.type !== 'static' && s.type !== 'subsite'">
              {{ s.name }}（{{ s.type }}）{{ s.rewrite ? ' · 已应用' : '' }}
            </option>
          </select>
        </div>
        <div class="sel-group">
          <label>选择框架模板</label>
          <select v-model="selTemplate" class="sel" @change="previewText = previewFor(selTemplate)">
            <option value="">— 请选择模板 —</option>
            <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}（{{ t.desc }}）</option>
          </select>
        </div>
        <button class="btn primary" :disabled="busy || !selSite || !selTemplate" @click="doApply">
          <CheckCircle :size="13" /> 应用
        </button>
        <button class="btn" :disabled="busy || !selSite" @click="doClear">
          <XCircle :size="13" /> 清除该站规则
        </button>
      </div>

      <!-- 规则预览 -->
      <div v-if="previewText" class="preview">
        <div class="preview-title">nginx 片段预览（{{ templateName }}）</div>
        <pre class="code">{{ previewText }}</pre>
      </div>

      <div v-if="msg" :class="['msg', msgType]">{{ msg }}</div>
    </div>

    <!-- 站点当前规则列表 -->
    <div class="panel">
      <div class="panel-title"><List :size="14" /> 各站点当前伪静态</div>
      <div v-if="sites.length === 0" class="empty">暂无站点</div>
      <table v-else class="dt">
        <thead>
          <tr>
            <th>站点</th>
            <th>类型</th>
            <th>状态</th>
            <th>当前规则</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in sites" :key="s.id">
            <td>{{ s.name }}</td>
            <td>{{ s.type }}</td>
            <td><span :class="['badge', s.enabled ? 'ok' : 'off']">{{ s.enabled ? '已启用' : '已停用' }}</span></td>
            <td>
              <span v-if="s.rewrite" class="badge info">{{ ruleName(s.rewrite) }}</span>
              <span v-else class="muted">未应用</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'                                       // Composition API：响应式、挂载
import { FileCode2, Layers, CheckCircle, XCircle, List } from 'lucide-vue-next'   // 图标集合
import { rewriteApi } from '../../api'                                       // 伪静态规则后端接口封装

const sites = ref([])
const templates = ref([])
const selSite = ref('')
const selTemplate = ref('')
const previewText = ref('')
const busy = ref(false)
const msg = ref('')
const msgType = ref('')

const templateName = ref('')

function ruleName(id) {
  const t = templates.value.find(t => t.id === id)
  return t ? t.name : id
}

function previewFor(id) {
  const t = templates.value.find(t => t.id === id)
  templateName.value = t ? t.name : ''
  return t ? t.nginx : ''
}

async function loadAll() {
  try {
    const [st, tp] = await Promise.all([rewriteApi.sites(), rewriteApi.templates()])
    sites.value = (st && st.sites) || []
    templates.value = (tp && tp.templates) || []
  } catch (e) {
    msg.value = '加载失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  }
}

// --- 动作：应用伪静态规则到站点 ---
async function doApply() {
  if (!selSite.value || !selTemplate.value) return
  busy.value = true
  msg.value = ''
  try {
    const r = await rewriteApi.apply(selSite.value, selTemplate.value)
    msg.value = `已应用 ${ruleName(selTemplate.value)} 到站点（引擎：${r.engine}）`
    msgType.value = 'ok'
    await loadAll()
  } catch (e) {
    msg.value = '应用失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

async function doClear() {
  if (!selSite.value) return
  busy.value = true
  msg.value = ''
  try {
    await rewriteApi.clear(selSite.value)
    msg.value = '已清除该站伪静态规则'
    msgType.value = 'ok'
    selTemplate.value = ''
    previewText.value = ''
    await loadAll()
  } catch (e) {
    msg.value = '清除失败：' + (e.response?.data?.detail || e.message)
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.rewrite-window { padding: 10px; display: flex; flex-direction: column; gap: 12px; height: 100%; box-sizing: border-box; overflow: auto; }
.intro { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #6b7280; background: #f3f4f6; border-radius: 8px; padding: 8px 12px; }
.panel { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px 14px; background: #fff; }
.panel-title { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; color: #1d1d1f; margin-bottom: 10px; }
.select-row { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
.sel-group { display: flex; flex-direction: column; gap: 4px; }
.sel-group label { font-size: 11px; color: #6b7280; }
.sel { padding: 6px 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12px; background: #fff; min-width: 180px; }
.preview { margin-top: 12px; }
.preview-title { font-size: 11px; color: #6b7280; margin-bottom: 6px; }
.code { background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 10px 12px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11.5px; line-height: 1.6; overflow: auto; max-height: 200px; margin: 0; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 5px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.msg { margin-top: 10px; font-size: 12px; padding: 6px 10px; border-radius: 6px; }
.msg.ok { background: #d1fae5; color: #065f46; }
.msg.err { background: #fee2e2; color: #b91c1c; }
.empty { color: #9ca3af; font-size: 12px; padding: 16px 0; text-align: center; }
.dt { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.dt th, .dt td { padding: 7px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
.dt th { background: #f9fafb; font-size: 11px; color: #6b7280; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.badge.info { background: #dbeafe; color: #1d4ed8; }
.muted { color: #9ca3af; font-size: 12px; }
</style>
