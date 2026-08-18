<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#f5f5f7;">
    <div style="flex:1; overflow:auto; padding:16px; display:flex; flex-direction:column; gap:14px;">
      <!-- 网站名 -->
      <div class="block">
        <div class="block-title">{{ $t('ui.siteName') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('ui.siteNameHint') }}</div>
        <input v-model.trim="form.site_name" :placeholder="'Graw'" maxlength="60" spellcheck="false" />
      </div>

      <!-- 欢迎语 -->
      <div class="block">
        <div class="block-title">{{ $t('ui.welcome') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('ui.welcomeHint') }}</div>
        <textarea v-model.trim="form.welcome" rows="2" maxlength="200" spellcheck="false" :placeholder="$t('ui.welcomePlaceholder')"></textarea>
      </div>

      <!-- Logo -->
      <div class="block">
        <div class="block-title">{{ $t('ui.logo') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('ui.logoHint') }}</div>
        <div class="logo-row">
          <!-- Logo 预览 -->
          <div v-if="logoPreview" class="logo-preview">
            <img :src="logoPreview" alt="logo" />
          </div>
          <div class="btn-group">
            <label class="btn btn-secondary" for="logo-upload">{{ $t('ui.uploadLogo') }}</label>
            <input id="logo-upload" type="file" accept="image/*" style="display:none;" @change="(e) => onImageChange(e, 'logo')" />
            <button v-if="logoPreview" class="btn btn-mini btn-danger" @click="removeImage('logo')">{{ $t('ui.removeLogo') }}</button>
          </div>
        </div>
        <div v-if="logoError" class="msg err">{{ logoError }}</div>
      </div>

      <!-- 背景 -->
      <div class="block">
        <div class="block-title">{{ $t('ui.background') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('ui.backgroundHint') }}</div>
        <div class="logo-row">
          <!-- 背景预览（等比缩略） -->
          <div v-if="bgPreview" class="bg-preview">
            <img :src="bgPreview" alt="background" />
          </div>
          <div class="btn-group">
            <label class="btn btn-secondary" for="bg-upload">{{ $t('ui.uploadBackground') }}</label>
            <input id="bg-upload" type="file" accept="image/*" style="display:none;" @change="(e) => onImageChange(e, 'background')" />
            <button v-if="bgPreview" class="btn btn-mini btn-danger" @click="removeImage('background')">{{ $t('ui.removeBackground') }}</button>
          </div>
        </div>
        <div v-if="bgError" class="msg err">{{ bgError }}</div>
      </div>

      <div v-if="msg" :class="['msg', msgType]">{{ msg }}</div>

      <div style="display:flex; gap:8px;">
        <button class="btn" :disabled="saving" @click="save">{{ saving ? $t('settings.saveSaving') : $t('settings.save') }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { uiApi } from '../../api'

const { t } = useI18n()

// 表单数据：网站名 / 欢迎语 / Logo / 背景（后两项为 Base64 data URL）
const form = reactive({ site_name: 'Graw', welcome: '', logo: '', background: '' })
const logoPreview = ref('')      // Logo 预览用的 data URL（与 form.logo 在保存前保持一致）
const logoError = ref('')
const bgPreview = ref('')        // 背景预览用的 data URL（与 form.background 保持一致）
const bgError = ref('')
const saving = ref(false)
const msg = ref('')
const msgType = ref('')

// 各图片字段的允许大小（与后端保持一致）
const FIELD_LIMIT = { logo: 2 * 1024 * 1024, background: 8 * 1024 * 1024 }

/** 将本地图片文件转成 Base64 data URL（按字段限制大小），用于预览与上传。 */
async function onImageChange(e, field) {
  const file = e.target.files && e.target.files[0]
  e.target.value = '' // 清空，允许重复选择同一文件
  if (!file) return
  const isLogo = field === 'logo'
  const setError = (v) => (isLogo ? (logoError.value = v) : (bgError.value = v))
  if (file.size > FIELD_LIMIT[field]) {
    setError(isLogo ? t('ui.logoTooLarge') : t('ui.bgTooLarge'))
    return
  }
  if (!/^image\//.test(file.type)) {
    setError(t('ui.logoTypeErr'))
    return
  }
  setError('')
  try {
    // 用 FileReader 读取为 data URL；体积已在前端按字段限制，直接转为 base64
    const dataUrl = await readAsDataURL(file)
    form[field] = dataUrl
    if (isLogo) logoPreview.value = dataUrl
    else bgPreview.value = dataUrl
  } catch (err) {
    setError(isLogo ? t('ui.logoReadErr') : t('ui.bgReadErr'))
    console.error(`[ui] 读取 ${field} 文件失败:`, err)
  }
}

/** 封装 FileReader 读取为 data URL。 */
function readAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

/** 清除指定图片字段（logo / background）。 */
function removeImage(field) {
  form[field] = ''
  if (field === 'logo') {
    logoPreview.value = ''
    logoError.value = ''
  } else {
    bgPreview.value = ''
    bgError.value = ''
  }
}

/** 加载现有配置。 */
async function load() {
  try {
    const config = await uiApi.config()
    form.site_name = config.site_name || 'Graw'
    form.welcome = config.welcome || ''
    form.logo = config.logo || ''
    form.background = config.background || ''
    logoPreview.value = form.logo || ''
    bgPreview.value = form.background || ''
  } catch (e) {
    console.error('[ui] 加载界面配置失败:', e)
  }
}

/** 保存配置。 */
async function save() {
  if (saving.value) return
  saving.value = true
  msg.value = ''
  msgType.value = ''
  try {
    const res = await uiApi.update({
      site_name: form.site_name,
      welcome: form.welcome,
      logo: form.logo,
      background: form.background,
    })
    form.site_name = res.site_name || 'Graw'
    form.welcome = res.welcome || ''
    form.logo = res.logo || ''
    form.background = res.background || ''
    logoPreview.value = form.logo || ''
    bgPreview.value = form.background || ''
    msg.value = t('ui.saved')
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('ui.saveFailed')
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}

onMounted(load)
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
input, textarea {
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
textarea {
  resize: vertical;
}
input:focus, textarea:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10,132,255,0.15);
}
.logo-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.logo-preview {
  width: 64px;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fafafa;
  border: 1px dashed rgba(0,0,0,0.15);
  border-radius: 10px;
  overflow: hidden;
}
.logo-preview img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.bg-preview {
  width: 180px;
  height: 96px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fafafa;
  border: 1px dashed rgba(0,0,0,0.15);
  border-radius: 10px;
  overflow: hidden;
}
.bg-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.btn-group {
  display: flex;
  align-items: center;
  gap: 8px;
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
.btn-secondary {
  background: #0a84ff;
}
.btn-danger {
  background: #e5484d;
}
.btn-danger:hover:not(:disabled) { background: #d63d42; }
.btn-mini {
  padding: 6px 12px;
  font-size: 11px;
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