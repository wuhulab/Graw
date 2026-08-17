<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#f5f5f7;">
    <div style="flex:1; overflow:auto; padding:16px; display:flex; flex-direction:column; gap:14px;">
      <div class="block">
        <div class="block-title">{{ $t('settings.title') }}</div>
        <button class="btn" @click="emit('openUsers')" :disabled="!isAdmin()">{{ $t('settings.openUsers') }}</button>
        <span v-if="!isAdmin()" style="font-size:11px;color:#6e6e73;margin-left:8px;">{{ $t('common.adminOnly') }}</span>
      </div>

      <!-- ShunX 安全入口管理（仅管理员） -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">{{ $t('settings.shunxTitle') }}</div>
        <div class="row" style="flex-wrap:wrap; gap:6px;">
          <span class="status-dot" :class="currentEntry ? 'on' : 'off'"></span>
          <span style="font-size:12px;color:#1d1d1f;">
            {{ statusText }}
          </span>
        </div>
        <div class="row" style="flex-direction:column; align-items:stretch; gap:8px;">
          <input v-model="entryPath" :placeholder="$t('settings.shunxPlaceholder')" spellcheck="false" @keyup.enter="saveEntry" />
          <div style="display:flex; gap:8px;">
            <button class="btn" :disabled="saving" @click="saveEntry">{{ saving ? $t('settings.saveSaving') : $t('settings.save') }}</button>
            <button class="btn btn-danger" v-if="currentEntry" :disabled="saving" @click="clearEntry">{{ $t('settings.clearEntry') }}</button>
          </div>
          <div v-if="msg" :class="['msg', msgType]">{{ msg }}</div>
        </div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-top:4px;">
          {{ $t('settings.entryHint', { url: origin + '/' + (currentEntry || '...') }) }}
        </div>
      </div>

      <div class="block">
        <div class="block-title">{{ $t('settings.panelTitle') }}</div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.showTaskbarText" />
            <span>{{ $t('settings.showTaskbarText') }}</span>
          </label>
        </div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.taskbarTextOnly" />
            <span>{{ $t('settings.taskbarTextOnly') }}</span>
          </label>
        </div>
      </div>

      <!-- 界面语言 -->
      <div class="block">
        <div class="block-title">{{ $t('settings.language') }}</div>
        <div class="row" style="flex-wrap:wrap; gap:6px;">
          <label class="switch-label" v-for="lang in LANGUAGES" :key="lang.code" :style="{ fontWeight: settings.locale === lang.code ? 700 : 400 }">
            <input type="radio" name="locale" :value="lang.code" :checked="settings.locale === lang.code" @change="changeLocale(lang.code)" />
            <span>{{ lang.name }}</span>
          </label>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { settings } from '../../store/settings'
import { isAdmin } from '../../store/auth'
import { shunxApi } from '../../api'
import { LANGUAGES, setLocale } from '../../locales'

const { t } = useI18n()
const emit = defineEmits(['openUsers'])

// ShunX 安全入口状态
const entryPath = ref('')
const currentEntry = ref('')
const saving = ref(false)
const msg = ref('')
const msgType = ref('')
const origin = computed(() => window.location.origin)

const statusText = computed(() => {
  if (!currentEntry.value) return t('settings.shunxNotSet')
  return t('settings.shunxEnabled', { path: currentEntry.value })
})

onMounted(async () => {
  try {
    const config = await shunxApi.config()
    currentEntry.value = config.entry_path || ''
    entryPath.value = currentEntry.value
  } catch (e) {
    currentEntry.value = ''
  }
})

// 切换界面语言
function changeLocale(code) {
  setLocale(code)
}

async function saveEntry() {
  if (saving.value) return
  saving.value = true
  msg.value = ''
  try {
    const res = await shunxApi.update(entryPath.value)
    const config = res.config || {}
    currentEntry.value = config.entry_path || ''
    entryPath.value = currentEntry.value
    msg.value = currentEntry.value
      ? t('settings.entrySet', { url: `${origin.value}/${currentEntry.value}` })
      : t('settings.entryCleared')
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('settings.saveFailed')
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}

async function clearEntry() {
  if (saving.value) return
  if (!confirm(t('settings.clearConfirm'))) return
  saving.value = true
  msg.value = ''
  try {
    await shunxApi.update('')
    currentEntry.value = ''
    entryPath.value = ''
    msg.value = t('settings.entryCleared')
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('settings.clearFailed')
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.block {
  background: #fff;
  border-radius: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(0,0,0,0.06);
}
.block-title {
  font-size: 12px;
  font-weight: 600;
  color: #1d1d1f;
  margin-bottom: 10px;
}
.row {
  display: flex;
  align-items: center;
  padding: 6px 0;
  font-size: 12px;
}
.switch-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: #1d1d1f;
}
.switch-label input {
  width: 16px;
  height: 16px;
  cursor: pointer;
}
.btn {
  padding: 6px 14px;
  font-size: 12px;
  color: #fff;
  background: #0a84ff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}
.btn:hover:not(:disabled) { background: #006ee6; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-danger {
  background: #e5484d;
}
.btn-danger:hover:not(:disabled) { background: #d63d42; }
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.status-dot.on { background: #0a7d3b; }
.status-dot.off { background: #c0392b; }
input {
  width: 100%;
  padding: 8px 10px;
  font-size: 12px;
  font-family: inherit;
  color: #1d1d1f;
  border: 1px solid rgba(0,0,0,0.12);
  border-radius: 8px;
  outline: none;
  box-sizing: border-box;
}
input:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10,132,255,0.15);
}
.msg {
  font-size: 12px;
  border-radius: 6px;
  padding: 6px 8px;
}
.msg.ok {
  color: #0a7d3b;
  background: rgba(10,132,255,0.08);
  border: 1px solid rgba(10,132,255,0.25);
}
.msg.err {
  color: #c0392b;
  background: rgba(255,59,48,0.08);
  border: 1px solid rgba(255,59,48,0.2);
}
</style>
