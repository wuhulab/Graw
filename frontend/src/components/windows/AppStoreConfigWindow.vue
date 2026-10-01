<!--
  AppStoreConfigWindow.vue — 应用商店索引地址配置（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 AppStoreWindow 的「索引地址配置」modal 弹窗独立为桌面窗口，
    避免误触灰色遮罩丢失已修改的索引地址。
  后端模块：
    /api/appstore 的 saveConfig。
  打开方式：
    由 App.vue 的 openAppStoreConfig(payload) 打开，props 传入 { indexUrl }。
    保存成功后 emit('close')，并经 formBus 通知 AppStoreWindow 强制刷新索引。
-->
<template>
  <div class="appstore-config-window">
    <div v-if="error" class="error-box">{{ error }}</div>
    <p class="desc">{{ $t('appstore.indexConfigDesc') }}</p>
    <label class="ui-field">
      <span class="ui-label">索引地址</span>
      <input class="ui-input mono" v-model.trim="indexUrl" style="width:100%;"
             :placeholder="$t('appstore.indexConfigPlaceholder')" />
    </label>
    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? $t('common.saving') : $t('appstore.saveAndRefresh') }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { appStoreApi } from '../../api'
import { bumpForm } from '../../store/formBus'

// indexUrl: 当前索引地址（父窗口加载后传入预填）
const props = defineProps({
  indexUrl: { type: String, default: '' }
})
const emit = defineEmits(['close'])

const saving = ref(false)
const error = ref('')
const indexUrl = ref(props.indexUrl)

// --- 保存索引地址并通知 AppStoreWindow 强制刷新索引 ---
async function save() {
  if (saving.value) return
  saving.value = true
  error.value = ''
  try {
    await appStoreApi.saveConfig(indexUrl.value.trim())
    bumpForm('appstore')   // 通知应用商店窗口强制刷新索引
    emit('close')
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.appstore-config-window { padding: 14px; }
.desc { font-size: 12.5px; color: #6b7280; margin: 0 0 12px; line-height: 1.6; }
.error-box { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; word-break: break-all; }
</style>