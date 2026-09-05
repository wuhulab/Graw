<!--
  SiteMaintenanceWindow.vue — 站点维护模式开关（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 SitesWindow 的「维护模式」modal 弹窗独立为桌面窗口，
    避免误触灰色遮罩丢失已填写的自定义维护页 HTML。支持一键开启/关闭
    维护模式，并可选提交自定义维护页 HTML。
  后端模块：
    /api/sites 的 maintenance。
  打开方式：
    由 App.vue 的 openSiteMaintenance(payload) 打开，props 传入 { site }。
    保存成功后 emit('close')，并经 formBus 通知 SitesWindow 刷新列表。
-->
<template>
  <div class="maint-window">
    <label style="display:flex; align-items:center; gap:8px; font-size:13px; margin-bottom:10px;">
      <input type="checkbox" v-model="enabled" style="width:auto;" />
      {{ enabled ? $t('sites.maintEnabled') : $t('sites.maintDisabled') }}
    </label>
    <div style="font-size:11px; color:#888; margin-bottom:6px;">{{ $t('sites.maintHtmlHint') }}</div>
    <textarea
      v-model="html"
      class="ui-textarea mono-area"
      rows="9"
      :placeholder="'<html>…'"
    />
    <div v-if="error" class="error-box">{{ error }}</div>
    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">{{ saving ? $t('common.loading') : $t('common.save') }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { sitesApi } from '../../api'
import { bumpForm } from '../../store/formBus'

// site: 目标站点对象
const props = defineProps({
  site: { type: Object, required: true }
})
const emit = defineEmits(['close'])

const enabled = ref(!!props.site?.maintenance)   // 当前是否已维护中（未维护默认关）
const html = ref('')                             // 自定义 HTML：留空=保持默认/不修改
const saving = ref(false)
const error = ref('')

// --- 保存：下发维护开关 + 可选自定义 HTML，成功后通知网站窗口刷新并自关 ---
async function save() {
  if (saving.value) return
  saving.value = true
  error.value = ''
  try {
    const body = { enabled: enabled.value }
    if (html.value.trim()) body.html = html.value   // 只传用户填写过的自定义 HTML
    await sitesApi.maintenance(props.site.id, body)
    bumpForm('sites')   // 通知网站窗口重新拉取列表（维护状态徽标/配置即时更新）
    emit('close')
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.maint-window { padding: 14px; }
.mono-area { font-family: Consolas, ui-monospace, monospace; }
.error-box { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; word-break: break-all; }
</style>