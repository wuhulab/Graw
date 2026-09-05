<!--
  配置回滚窗口（RollbackWindow）
  业务：展示站点 nginx 配置 / 防火墙规则 JSON 的「写前快照」历史，支持内容预览、
        一键恢复（回滚到任意历史版本，恢复后自动 reload）与删除快照。
  后端模块：rollbackApi（GET /api/rollback 列表、详情；POST restore；DELETE）
  关键状态：items（快照列表）、detail（当前选中快照详情）、kind（类型过滤）
  打开方式：桌面「配置回滚」入口（管理员）
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 工具栏：类型过滤 + 刷新 -->
    <div class="toolbar">
      <button class="btn" @click="loadList">{{ $t('common.refresh') }}</button>
      <label style="font-size:11px;color:#0a3d7a;">{{ $t('rollback.kindLabel') }}</label>
      <select v-model="kind" @change="loadList" style="font-size:11px;">
        <option value="">{{ $t('rollback.kindAll') }}</option>
        <option value="site">{{ $t('rollback.kind.site') }}</option>
        <option value="firewall">{{ $t('rollback.kind.firewall') }}</option>
      </select>
      <span v-if="loading" style="margin-left:auto;color:#888;">{{ $t('common.loading') }}</span>
      <span v-else style="margin-left:auto;color:#888;">{{ $t('rollback.count', { count: items.length }) }}</span>
    </div>

    <!-- 主体：左列表 + 右详情 -->
    <div style="display:flex; flex:1; overflow:hidden;">
      <!-- 左：快照列表 -->
      <div style="width:320px; overflow:auto; border-right:1px solid #dfe3ec;">
        <div
          v-for="it in items"
          :key="it.id"
          class="snap-item"
          :class="{ selected: selectedId === it.id }"
          @click="selectSnap(it.id)"
        >
          <div style="display:flex; align-items:center; gap:6px;">
            <span class="kind-badge" :class="it.kind">{{ it.kind === 'site' ? $t('rollback.kind.site') : $t('rollback.kind.firewall') }}</span>
            <span style="font-weight:600; font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{{ it.target_id }}</span>
          </div>
          <div style="font-size:11px; color:#666; margin-top:4px;">
            {{ it.when }}
            <template v-if="it.user"> · {{ it.user }}</template>
          </div>
          <div style="font-size:10px; color:#999; margin-top:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{{ it.file_path }}</div>
        </div>
        <div v-if="!loading && items.length === 0" style="padding:24px; text-align:center; color:#999; font-size:12px;">
          {{ $t('rollback.noSnapshots') }}
        </div>
      </div>

      <!-- 右：详情 / 内容预览 -->
      <div style="flex:1; display:flex; flex-direction:column; overflow:hidden;">
        <template v-if="detail">
          <div class="toolbar" style="border-bottom:1px solid #dfe3ec;">
            <button class="btn danger" :disabled="restoring" @click="doRestore">{{ $t('rollback.restore') }}</button>
            <button class="btn" @click="doDelete">{{ $t('rollback.delete') }}</button>
            <span v-if="restoring" style="margin-left:auto;color:#888;">{{ $t('common.loading') }}</span>
            <span v-else style="margin-left:auto;color:#888;font-size:11px;">{{ detail.file_path }}</span>
          </div>
          <div style="flex:1; overflow:auto; padding:10px;">
            <!-- 元信息 -->
            <table class="meta">
              <tbody>
                <tr><td>{{ $t('rollback.meta.when') }}</td><td>{{ detail.when }}</td></tr>
                <tr><td>{{ $t('rollback.meta.target') }}</td><td>{{ detail.target_id }}</td></tr>
                <tr><td>{{ $t('rollback.meta.user') }}</td><td>{{ detail.user || '-' }}</td></tr>
                <tr><td>{{ $t('rollback.meta.route') }}</td><td>{{ detail.route || '-' }}</td></tr>
                <tr><td>{{ $t('rollback.meta.bytes') }}</td><td>{{ formatBytes(detail.bytes) }}</td></tr>
              </tbody>
            </table>
            <!-- 内容预览（base64 解码） -->
            <div style="margin-top:10px; font-size:11px; color:#0a3d7a; font-weight:600;">{{ $t('rollback.preview') }}</div>
            <pre class="preview">{{ previewText }}</pre>
          </div>
        </template>
        <div v-else style="flex:1; display:flex; align-items:center; justify-content:center; color:#999; font-size:12px;">
          {{ $t('rollback.selectHint') }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'      // 响应式 + 挂载时加载列表
import { useI18n } from 'vue-i18n'         // 国际化：取 t() 生成动态文案
import { rollbackApi, formatBytes } from '../../api'   // 快照接口 + 字节格式化

const { t } = useI18n()
const items = ref([])          // 快照列表（元信息）
const kind = ref('')           // 类型过滤：'' / site / firewall
const selectedId = ref('')     // 当前选中快照 id
const detail = ref(null)       // 当前选中快照详情（含 content_b64）
const previewText = ref('')    // 内容预览（解码后文本）
const loading = ref(false)
const restoring = ref(false)

// 解码 base64（兼容 UTF-8 中文，不用 atob 直接把中文搞乱）
function decodeBase64(b64) {
  try {
    const bin = atob(b64)
    const bytes = Uint8Array.from(bin, c => c.charCodeAt(0))
    return new TextDecoder('utf-8').decode(bytes)
  } catch (e) {
    return ''
  }
}

// 加载快照列表（可选按类型过滤）
async function loadList() {
  loading.value = true
  try {
    const res = await rollbackApi.list(kind.value)
    items.value = res.items || []
    if (selectedId.value && !items.value.some(i => i.id === selectedId.value)) {
      selectedId.value = ''
      detail.value = null
      previewText.value = ''
    }
    if (!selectedId.value && items.value.length > 0) {
      selectSnap(items.value[0].id)
    }
  } catch (e) {
    // 列表加载失败静默提示
    alert(e?.response?.data?.detail || String(e))
  } finally {
    loading.value = false
  }
}

// 选中快照并拉取详情
async function selectSnap(id) {
  selectedId.value = id
  detail.value = null
  previewText.value = ''
  try {
    const res = await rollbackApi.detail(id)
    detail.value = res
    previewText.value = decodeBase64(res.content_b64 || '')
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

// 一键恢复（二次确认）
async function doRestore() {
  if (!detail.value) return
  if (!confirm(t('rollback.restoreConfirm'))) return
  restoring.value = true
  try {
    await rollbackApi.restore(detail.value.id)
    alert(t('rollback.restoreOk'))
    await loadList() // 恢复后刷新（旧快照仍在，但详情可能变化）
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  } finally {
    restoring.value = false
  }
}

// 删除快照
async function doDelete() {
  if (!detail.value) return
  if (!confirm(t('rollback.deleteConfirm'))) return
  try {
    await rollbackApi.remove(detail.value.id)
    selectedId.value = ''
    detail.value = null
    previewText.value = ''
    await loadList()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

onMounted(loadList)
</script>

<style scoped>
.snap-item {
  padding: 8px 10px;
  border-bottom: 1px solid #eef0f6;
  cursor: pointer;
}
.snap-item:hover { background: #f5f7fb; }
.snap-item.selected { background: #eaf1fb; }
.kind-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  color: #fff;
  flex-shrink: 0;
}
.kind-badge.site { background: #3b82f6; }
.kind-badge.firewall { background: #ef4444; }
.meta td {
  font-size: 12px;
  padding: 3px 10px 3px 0;
  color: #333;
}
.meta td:first-child { color: #0a3d7a; font-weight: 600; width: 90px; }
.preview {
  background: #f7f8fb;
  border: 1px solid #e4e7f0;
  border-radius: 4px;
  padding: 10px;
  font-family: Consolas, monospace;
  font-size: 12px;
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>