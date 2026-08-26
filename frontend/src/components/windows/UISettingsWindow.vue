<!--
  界面设置窗口（UI Settings）

  这个窗口做什么：
    面板「设置 → 界面」页。管理员在这里自定义桌面观感：
      - 网站名、欢迎语、Logo（上传后以 data URL 存入后端配置）；
      - 动态壁纸：图片轮播（背景列表 + 切换间隔）或视频壁纸，
        每项都支持「仅用于这个账号」（否则走全局配置）；
      - 系统概览环形统计图的配色与阈值报警开关。
    保存后重新加载，保证表单始终对齐「账号级 > 全局 > 默认」的读取优先级。

  用到的后端模块：
    /api/ui/*（端点内自行鉴权）——config 读取、update 保存。
    媒体文件以 data URL 直接存进 JSON 配置，没有单独上传接口；
    大小限制（Logo 2MB / 背景 8MB / 视频 50MB、最多 12 张背景）与后端保持一致。

  关键状态：
    form       表单（站点名 / 欢迎语 / Logo / 背景列表 / 视频 / 模式 / 间隔 / 环形配色）
    wallpaperPersonal / ringPersonal   「仅用于这个账号」开关
    logoPreview / videoPreview         上传后的即时预览
    msg / msgType                      保存结果提示

  怎么被打开：
    「设置」窗口（SettingsWindow）的「界面」页签内嵌。
-->
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
            <input id="logo-upload" type="file" accept="image/*" style="display:none;" @change="(e) => onLogoChange(e)" />
            <button v-if="logoPreview" class="btn btn-mini btn-danger" @click="removeLogo">{{ $t('ui.removeLogo') }}</button>
          </div>
        </div>
        <div v-if="logoError" class="msg err">{{ logoError }}</div>
      </div>

      <!-- 动态壁纸：多背景轮播 / 视频 -->
      <div class="block">
        <div class="block-title">{{ $t('ui.wallpaper') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('ui.wallpaperHint') }}</div>

        <!-- 仅用于这个账号：勾选后动态壁纸只对该账号生效，否则走全局 -->
        <label class="switch-label">
          <input type="checkbox" v-model="wallpaperPersonal" />
          <span>{{ $t('ui.personalOnly') }}</span>
        </label>
        <div style="font-size:11px;color:#8e8e93;margin:-4px 0 6px 24px;">{{ $t('ui.personalOnlyHint') }}</div>

        <!-- 模式切换：图片（可轮播） / 视频 -->
        <div class="row" style="flex-wrap:wrap; gap:16px;">
          <label class="switch-label">
            <input type="radio" name="wallpaperMode" value="image" v-model="form.background_mode" />
            <span>{{ $t('ui.modeImage') }}</span>
          </label>
          <label class="switch-label">
            <input type="radio" name="wallpaperMode" value="video" v-model="form.background_mode" />
            <span>{{ $t('ui.modeVideo') }}</span>
          </label>
        </div>

        <!-- 图片模式：多背景列表 + 轮播间隔 -->
        <template v-if="form.background_mode === 'image'">
          <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin:8px 0 6px;">
            {{ $t('ui.bgListHint', { n: form.backgrounds.length }) }}
          </div>
          <div style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom:8px;">
            <div v-for="(bg, i) in form.backgrounds" :key="i" class="bg-item">
              <img :src="bg" alt="bg" class="bg-thumb" />
              <div class="bg-item-ops">
                <button class="btn btn-mini btn-danger" @click="removeBackgroundAt(i)">{{ $t('ui.removeBackground') }}</button>
              </div>
            </div>
          </div>
          <div class="btn-group">
            <label class="btn btn-secondary" for="bg-upload">{{ $t('ui.uploadBackground') }}</label>
            <input id="bg-upload" type="file" accept="image/*" style="display:none;" @change="(e) => onBackgroundImage(e)" />
          </div>
          <div v-if="bgError" class="msg err">{{ bgError }}</div>
          <div class="row" style="gap:8px; margin-top:6px;">
            <span style="font-size:12px;color:#1d1d1f;">{{ $t('ui.bgInterval') }}</span>
            <input v-model.number="form.background_interval" type="number" min="3" max="120" style="width:80px;" />
          </div>
          <div style="font-size:11px;color:#8e8e93;">{{ $t('ui.intervalHint') }}</div>
        </template>

        <!-- 视频模式：上传视频壁纸 -->
        <template v-else>
          <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin:8px 0 6px;">{{ $t('ui.videoHint') }}</div>
          <div class="btn-group">
            <label class="btn btn-secondary" for="video-upload">{{ $t('ui.uploadVideo') }}</label>
            <input id="video-upload" type="file" accept="video/mp4,video/webm,video/ogg" style="display:none;" @change="(e) => onVideoChange(e)" />
            <button v-if="form.wallpaper_video" class="btn btn-mini btn-danger" @click="removeVideo">{{ $t('ui.removeVideo') }}</button>
          </div>
          <div v-if="videoPreview" class="video-preview" style="margin-top:8px;">
            <video :src="videoPreview" muted loop playsinline controls style="width:100%;max-height:180px;border-radius:8px;"></video>
          </div>
          <div v-if="videoError" class="msg err">{{ videoError }}</div>
        </template>
      </div>

      <!-- 系统概览环形统计图配色 -->
      <div class="block">
        <div class="block-title">{{ $t('ui.ringColor') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('ui.ringColorHint') }}</div>
        <div class="row" style="gap:8px;">
          <input type="color" :value="validColorValue" @input="onColorPick" style="width:42px;height:34px;padding:2px;border:1px solid rgba(0,0,0,0.12);border-radius:6px;background:#fff;" />
          <input v-model.trim="ringColorText" maxlength="7" spellcheck="false" style="flex:1;" :placeholder="'#409eff'" />
        </div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="form.ring_alarm" />
            <span>{{ $t('ui.ringAlarm') }}</span>
          </label>
        </div>
        <!-- 仅用于这个账号：勾选后环形图配色只对该账号生效，否则走全局 -->
        <label class="switch-label">
          <input type="checkbox" v-model="ringPersonal" />
          <span>{{ $t('ui.personalOnly') }}</span>
        </label>
        <div v-if="ringColorError" class="msg err">{{ ringColorError }}</div>
      </div>

      <div v-if="msg" :class="['msg', msgType]">{{ msg }}</div>

      <div style="display:flex; gap:8px;">
        <button class="btn" :disabled="saving" @click="save">{{ saving ? $t('settings.saveSaving') : $t('settings.save') }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'   // 响应式状态、取色器联动、挂载钩子
import { useI18n } from 'vue-i18n'   // 取 t()，界面文案跟随面板语言
import { uiApi } from '../../api'   // 界面设置后端能力：/api/ui/* 的封装

const { t } = useI18n()

// 表单数据：网站名 / 欢迎语 / Logo / 动态壁纸（背景列表 / 视频 / 模式 / 间隔）+ 环形图配色
const form = reactive({
  site_name: 'Graw',
  welcome: '',
  logo: '',
  backgrounds: [],
  wallpaper_video: '',
  background_mode: 'image',
  background_interval: 8,
  ring_color: '#409eff',
  ring_alarm: true,
})
const logoPreview = ref('')      // Logo 预览用的 data URL（与 form.logo 在保存前保持一致）
const logoError = ref('')
const bgError = ref('')
const videoPreview = ref('')     // 视频壁纸预览 data URL（与 form.wallpaper_video 一致）
const videoError = ref('')
const saving = ref(false)
const msg = ref('')
const msgType = ref('')
// 「仅用于这个账号」：动态壁纸 / 环形图 各自是否走账号级配置
const wallpaperPersonal = ref(false)
const ringPersonal = ref(false)

// ---- 环形图颜色：文本输入 + 取色器联动 ----
const ringColorText = ref('#409eff') // 文本输入框（支持手动输入 #RRGGBB）
const ringColorError = ref('')
const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/
// 取色器需要合法 hex，非法时回退默认蓝色
const validColorValue = computed(() => (HEX_COLOR_RE.test(form.ring_color) ? form.ring_color : '#409eff'))

// 取色器选择后同步到文本输入框与表单
function onColorPick(e) {
  const v = e.target.value
  form.ring_color = v
  ringColorText.value = v
  ringColorError.value = ''
}

// 文本输入变化时实时校验并同步到表单（仅 hex 合法时生效）
watch(ringColorText, (v) => {
  const value = (v || '').trim()
  if (HEX_COLOR_RE.test(value)) {
    form.ring_color = value
    ringColorError.value = ''
  } else if (value) {
    ringColorError.value = t('ui.ringColorInvalid')
  } else {
    ringColorError.value = ''
  }
})

// 各媒体字段的允许大小（与后端保持一致）
const LOGO_LIMIT = 2 * 1024 * 1024      // Logo 上限 2MB
const BG_LIMIT = 8 * 1024 * 1024        // 背景图上限 8MB
const VIDEO_LIMIT = 50 * 1024 * 1024    // 视频壁纸上限 50MB
const MAX_BACKGROUNDS = 12              // 轮播背景最多 12 张

/** 封装 FileReader 读取为 data URL。 */
function readAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

/** 上传 Logo：读为 data URL。 */
async function onLogoChange(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = ''   // 清空 input，保证连续选同一文件也能重新触发 change
  if (!file) return   // 取消选择时 file 为空，直接退出
  if (file.size > LOGO_LIMIT) { logoError.value = t('ui.logoTooLarge'); return }   // 超限直接拒绝，避免大图写进配置
  if (!/^image\//.test(file.type)) { logoError.value = t('ui.logoTypeErr'); return }   // 只收图片 MIME 类型
  logoError.value = ''
  try {
    const dataUrl = await readAsDataURL(file)
    form.logo = dataUrl      // data URL 直接进表单，随保存提交
    logoPreview.value = dataUrl   // 同步预览
  } catch (err) {
    logoError.value = t('ui.logoReadErr')
    console.error('[ui] 读取 Logo 失败:', err)
  }
}

/** 移除 Logo。 */
function removeLogo() {
  form.logo = ''
  logoPreview.value = ''
  logoError.value = ''
}

/** 上传背景图并追加到轮播列表（限量、限大小）。 */
async function onBackgroundImage(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!file) return
  if (file.size > BG_LIMIT) { bgError.value = t('ui.bgTooLarge'); return }
  if (!/^image\//.test(file.type)) { bgError.value = t('ui.logoTypeErr'); return }
  bgError.value = ''
  try {
    const dataUrl = await readAsDataURL(file)
    if (form.backgrounds.length >= MAX_BACKGROUNDS) {
      bgError.value = t('ui.bgListHint', { n: MAX_BACKGROUNDS })
      return   // 已达上限不再追加，避免配置无限膨胀
    }
    form.backgrounds.push(dataUrl)
  } catch (err) {
    bgError.value = t('ui.bgReadErr')
    console.error('[ui] 读取背景失败:', err)
  }
}

/** 移除指定下标的背景图。 */
function removeBackgroundAt(i) {
  form.backgrounds.splice(i, 1)
}

/** 上传视频壁纸：读为 data URL（视频原生文件可能较大，data URL 体积约 +37%）。 */
async function onVideoChange(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = ''
  if (!file) return
  if (file.size > VIDEO_LIMIT) { videoError.value = t('ui.videoTooLarge'); return }
  if (!/^video\/(mp4|webm|ogg)$/.test(file.type)) {
    videoError.value = t('ui.videoTypeErr')
    return   // 只收浏览器原生能播放的三种视频容器格式
  }
  videoError.value = ''
  try {
    const dataUrl = await readAsDataURL(file)
    form.wallpaper_video = dataUrl
    videoPreview.value = dataUrl
  } catch (err) {
    videoError.value = t('ui.videoReadErr')
    console.error('[ui] 读取视频失败:', err)
  }
}

/** 移除视频壁纸。 */
function removeVideo() {
  form.wallpaper_video = ''
  videoPreview.value = ''
  videoError.value = ''
}

/** 加载现有配置：优先当前账号的个人覆盖（「仅用于这个账号」），否则全局。 */
async function load() {
  try {
    const config = await uiApi.config()
    const p = config && config.personal || {}
    // 动态壁纸：账号级优先，否则全局，否则默认
    const wp = p.wallpaper || config
    wallpaperPersonal.value = !!p.wallpaper
    const bgList = Array.isArray(wp.backgrounds) && wp.backgrounds.length
      ? wp.backgrounds.slice()
      : (wp.background ? [wp.background] : (Array.isArray(config.backgrounds) ? config.backgrounds.slice() : []))
    form.backgrounds = bgList
    form.wallpaper_video = wp.wallpaper_video || config.wallpaper_video || ''
    form.background_mode = wp.background_mode === 'video' ? 'video' : 'image'
    form.background_interval = wp.background_interval || 8
    // 环形图：账号级优先，否则全局，否则默认
    const rp = p.ring || config
    ringPersonal.value = !!p.ring
    form.ring_color = rp.ring_color || '#409eff'
    form.ring_alarm = rp.ring_alarm !== false
    form.site_name = config.site_name || 'Graw'
    form.welcome = config.welcome || ''
    form.logo = config.logo || ''
    logoPreview.value = form.logo || ''
    videoPreview.value = form.wallpaper_video || ''
    bgError.value = ''
    videoError.value = ''
    ringColorText.value = form.ring_color
    ringColorError.value = ''
  } catch (e) {
    console.error('[ui] 加载界面配置失败:', e)
  }
}

/** 保存配置。 */
async function save() {
  if (saving.value) return
  const color = (ringColorText.value || '').trim()
  if (!HEX_COLOR_RE.test(color)) {
    ringColorError.value = t('ui.ringColorInvalid')
    return
  }
  saving.value = true
  msg.value = ''
  msgType.value = ''
  try {
    await uiApi.update({
      site_name: form.site_name,
      welcome: form.welcome,
      logo: form.logo,
      background: (form.backgrounds && form.backgrounds[0]) || '',
      backgrounds: form.backgrounds || [],
      wallpaper_video: form.wallpaper_video || '',
      background_mode: form.background_mode === 'video' ? 'video' : 'image',
      background_interval: Math.max(3, Math.min(120, Number(form.background_interval) || 8)),   // 间隔限在 3-120 秒，越界值收敛到边界
      ring_color: color,
      ring_alarm: form.ring_alarm,
      // 「仅用于这个账号」：勾选则写入当前账号，否则写入全局
      wallpaper_personal: wallpaperPersonal.value,
      ring_personal: ringPersonal.value,
    })
    // 后端已落盘，重新按「账号级>全局>默认」刷新表单与勾选状态，避免读到错误的层
    await load()
    msg.value = t('ui.saved')
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('ui.saveFailed')
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}

onMounted(load)   // 打开即读取当前界面配置
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
.bg-item {
  position: relative;
  width: 120px;
  height: 72px;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid rgba(0,0,0,0.12);
  background: #fafafa;
}
.bg-item img.bg-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.bg-item-ops {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0,0,0,0.35);
  opacity: 0;
  transition: opacity 0.15s;
}
.bg-item:hover .bg-item-ops {
  opacity: 1;
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
  /* 与界面设置其它说明文字保持相同字号（如「仅用于这个账号」） */
  font-size: 12px;
}
.switch-label input {
  width: 16px;
  height: 16px;
  cursor: pointer;
}
</style>