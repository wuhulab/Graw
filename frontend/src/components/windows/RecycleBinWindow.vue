<!--
  回收站窗口（后端 /api/recycle 模块）
  作用：展示当前管理主机回收站中的条目，支持恢复 / 彻底删除 / 清空回收站；
        回收站配置（启用开关、自动删除、保留天数）在「设置」窗口中维护。
  后端模块：/api/recycle（config/list/restore/delete/empty）。
  关键状态：items（回收站条目）、loading、busyFlag（操作中禁点）。
  打开方式：桌面「回收站」卡片；数据按「当前管理主机」展示（与文件管理一致）。
  安全：恢复/删除 path 后端强校验必须落在回收站目录内。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <button class="btn" @click="load" :disabled="loading"><RefreshCw :size="14" /> {{ $t('recycle.refresh') }}</button>
      <button class="btn btn-danger" @click="emptyAll" :disabled="busy || !items.length">
        <Trash2 :size="14" /> {{ $t('recycle.emptyTrash') }}
      </button>
      <span style="font-size:11px;color:#8e8e93;margin-left:8px;">{{ $t('recycle.count', { total: items.length }) }}</span>
    </div>
    <div style="flex:1; overflow:auto;">
      <table class="dt">
        <thead>
          <tr>
            <th>{{ $t('recycle.name') }}</th>
            <th>{{ $t('recycle.original') }}</th>
            <th style="width:170px;">{{ $t('recycle.deletedAt') }}</th>
            <th style="width:100px;">{{ $t('recycle.deletedBy') }}</th>
            <th style="width:160px;">{{ $t('recycle.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in items" :key="it.id || it.trash">
            <td>
              <span style="margin-right:4px;"><Folder :size="14" /></span>{{ nameOf(it) }}
            </td>
            <td class="mono" :title="it.original">{{ it.original }}</td>
            <td>{{ formatTime(it.deleted_at) }}</td>
            <td>{{ it.deleted_by }}</td>
            <td>
              <button class="btn btn-mini" @click="restoreItem(it)" :disabled="busy">{{ $t('recycle.restore') }}</button>
              <button class="btn btn-mini btn-danger" @click="deleteItem(it)" :disabled="busy">{{ $t('recycle.delete') }}</button>
            </td>
          </tr>
          <tr v-if="!items.length">
            <td colspan="5"><div class="empty">{{ $t('recycle.empty') }}</div></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="msg" :class="['msg', msgType]" style="margin:8px 12px;">{{ msg }}</div>
  </div>
</template>

<script setup>
// 响应式状态与生命周期钩子
import { ref, onMounted } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 回收站 API 与统一错误处理
import { recycleApi } from '../../api'
import { getApiErrorMessage } from '../../utils/apiErrors'
// 图标（刷新 / 清空 / 目录）
import { RefreshCw, Trash2, Folder } from 'lucide-vue-next'

const { t } = useI18n()
const items = ref([])          // 回收站条目
const loading = ref(false)     // 加载中
const busy = ref(false)        // 操作中（防止并发点击）
const msg = ref('')            // 状态提示
const msgType = ref('')        // ok / err

// 条目显示名：取原路径最后一段（可能为空时回退为回收站路径末段）
function nameOf(it) {
  const p = it.original || it.trash || ''
  const parts = String(p).replace(/\\/g, '/').split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

function formatTime(ts) {
  if (!ts) return '-'
  return new Date(Number(ts) * 1000).toLocaleString()
}

// --- 动作：加载回收站条目（后端已顺带清理过期条目） ---
async function load() {
  if (loading.value) return
  loading.value = true
  msg.value = ''
  try {
    const r = await recycleApi.list()
    items.value = r.items || []
  } catch (e) {
    msg.value = t('recycle.loadFailed', { error: getApiErrorMessage(e, t) })
    msgType.value = 'err'
  } finally {
    loading.value = false
  }
}

// --- 动作：恢复单个条目到原位置 ---
async function restoreItem(it) {
  if (busy.value) return
  const name = nameOf(it)
  if (!confirm(t('recycle.confirmRestore', { name }))) return
  busy.value = true
  msg.value = ''
  try {
    await recycleApi.restore(it.trash)
    await load()
    msg.value = t('recycle.restoreOk', { name })
    msgType.value = 'ok'
  } catch (e) {
    msg.value = t('recycle.restoreFailed', { error: getApiErrorMessage(e, t) })
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

// --- 动作：彻底删除单个条目（不可恢复） ---
async function deleteItem(it) {
  if (busy.value) return
  if (!confirm(t('recycle.confirmDelete', { name: nameOf(it) }))) return
  busy.value = true
  msg.value = ''
  try {
    await recycleApi.remove(it.trash)
    await load()
  } catch (e) {
    msg.value = t('recycle.deleteFailed', { error: getApiErrorMessage(e, t) })
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

// --- 动作：清空回收站（高危二次确认） ---
async function emptyAll() {
  if (busy.value || !items.value.length) return
  if (!confirm(t('recycle.confirmEmpty'))) return
  busy.value = true
  msg.value = ''
  try {
    await recycleApi.empty()
    await load()
  } catch (e) {
    msg.value = t('recycle.emptyFailed', { error: getApiErrorMessage(e, t) })
    msgType.value = 'err'
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid rgba(0,0,0,0.06);
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 12px;
  font-size: 12px;
  color: #1d1d1f;
  background: #f2f2f4;
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 8px;
  cursor: pointer;
}
.btn:hover:not(:disabled) { background: #e8e8ea; }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.btn-danger { background: #fdecec; color: #c0392b; border-color: rgba(192,57,43,0.2); }
.btn-danger:hover:not(:disabled) { background: #fbdcdc; }
.btn-mini { padding: 2px 9px; font-size: 11px; margin-right: 4px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; color: #6e6e73; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty { text-align: center; color: #8e8e93; padding: 40px 0; }
.msg { font-size: 12px; border-radius: 6px; padding: 6px 10px; }
.msg.ok { color: #0a7d3b; background: rgba(10,125,59,0.08); }
.msg.err { color: #c0392b; background: rgba(192,57,43,0.08); }
</style>