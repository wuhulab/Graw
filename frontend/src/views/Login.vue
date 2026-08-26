<!-- Login.vue — 登录 / 强制改密 / 安全入口门禁 页面。

  业务背景：这是面板的「大门」。除登录表单外，还承担三类前置状态：
    1. ShunX 安全入口：已配置入口且当前路径不匹配时，先显示门禁提示而不是登录表单；
    2. 强制改密：默认密码 / 重置 / 首次登录的账号必须先在本地改密，成功后才写入登录态；
    3. 两步验证（2FA）：密码通过后需输入 6 位动态验证码。
  登录成功通过 emit('login') 通知 App.vue 切换进入桌面。
-->
<template>
  <!-- 登录页背景：优先动态壁纸（视频/轮播），否则自定义单图，再回退内置 hero.png -->
  <div class="login-page" :style="bgStyle">
    <div v-if="loginWallpaperVideo" class="login-wallpaper-video">
      <video :src="loginWallpaperVideo" autoplay muted loop playsinline></video>
      <div class="login-wallpaper-mask"></div>
    </div>
    <div v-else-if="loginCarouselImages.length > 1" class="login-wallpaper-carousel">
      <div
        v-for="(_, i) in loginCarouselImages"
        :key="i"
        class="login-wallpaper-slide"
        :class="{ active: i === loginCarouselIndex }"
        :style="{ backgroundImage: `url('${loginCarouselImages[i]}')` }"
      ></div>
      <div class="login-wallpaper-mask"></div>
    </div>
    <div class="login-card" style="position:relative; z-index:2;">
      <!-- 安全入口门禁（已配置入口且当前路径不匹配） -->
      <template v-if="shunxChecked && shunx.enabled && !shunx.matched">
        <div class="login-title">ShunX</div>
        <div class="login-subtitle">{{ $t('login.shunxGate') }}</div>
        <div class="hint" style="background:rgba(255,59,48,0.08);border-color:rgba(255,59,48,0.2);color:#c0392b;">
          {{ $t('login.shunxHint') }}
        </div>
      </template>

      <!-- 登录表单 -->
      <template v-else-if="!forceChange">
        <!-- 自定义 Logo（若有） -->
        <img v-if="ui.logo" class="login-logo" :src="ui.logo" alt="logo" />
        <div class="login-title">{{ ui.site_name }}</div>
        <div class="login-subtitle">{{ ui.welcome || $t('login.subtitle') }}</div>

        <form @submit.prevent="handleLogin">
          <label class="field">
            <span class="label">{{ $t('login.username') }}</span>
            <input
              v-model.trim="username"
              type="text"
              autocomplete="username"
              autofocus
              spellcheck="false"
              required
            />
          </label>
          <label class="field">
            <span class="label">{{ $t('login.password') }}</span>
            <input
              v-model="password"
              type="password"
              autocomplete="current-password"
              required
            />
          </label>
          <!-- 两步验证（2FA）：密码通过后需输入 6 位动态验证码 -->
          <div v-if="otpRequired" class="hint">已开启两步验证，请输入手机验证器中的 6 位动态验证码</div>
          <label v-if="otpRequired" class="field">
            <span class="label">两步验证码</span>
            <input
              v-model="otpCode"
              type="text"
              inputmode="numeric"
              maxlength="6"
              autocomplete="one-time-code"
              placeholder="6 位验证码"
              required
            />
          </label>
          <div v-if="error" class="error">{{ error }}</div>
          <button class="btn-primary" type="submit" :disabled="loading">
            {{ loading ? $t('login.loggingIn') : $t('login.login') }}
          </button>
        </form>
      </template>

      <!-- 强制改密 -->
      <form v-else @submit.prevent="handleChangePassword">
        <div v-if="forceChangeReason === 'default'" class="hint danger">
          {{ $t('login.defaultPwdHint') }}
        </div>
        <div v-else class="hint">{{ $t('login.resetPwdHint') }}</div>
        <label class="field">
          <span class="label">{{ $t('login.oldPassword') }}</span>
          <input v-model="oldPassword" type="password" required />
        </label>
        <label class="field">
          <span class="label">{{ $t('login.newPassword') }}</span>
          <input v-model="newPassword" type="password" minlength="8" required />
        </label>
        <label class="field">
          <span class="label">{{ $t('login.confirmPassword') }}</span>
          <input v-model="confirmPassword" type="password" minlength="8" required />
        </label>
        <div v-if="error" class="error">{{ error }}</div>
        <button class="btn-primary" type="submit" :disabled="loading">
          {{ loading ? $t('login.submitting') : $t('login.updateAndEnter') }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'      // 表单状态 / 背景样式 / 挂载后初始化
import { useI18n } from 'vue-i18n'                  // $t：错误与提示文案走多语言
import { authApi, shunxApi } from '../api'          // 登录/改密接口 + ShunX 安全入口状态接口
import { setAuth } from '../store/auth'             // 登录成功/改密成功后把身份写入全局并落盘
import { uiState, loadUi } from '../store/ui'       // 登录页品牌配置（网站名/欢迎语/Logo/背景）

const { t } = useI18n()
const emit = defineEmits(['login'])   // 登录/改密成功后通知 App.vue 收起登录页、进入桌面

// 界面品牌配置（共享 store）：自定义网站名 / 欢迎语 / Logo / 背景
const ui = uiState

// 登录页背景样式：配置了背景则使用自定义图（覆盖后裁切），否则交给 CSS 回退默认背景
const bgStyle = computed(() => {
  // 动态壁纸（视频/轮播）由独立层渲染，这里仅兜底单图背景
  if (loginWallpaperVideo.value || loginCarouselImages.value.length > 1) return {}
  if (uiState.background) {
    return {
      backgroundImage: `url('${uiState.background}')`,
      backgroundSize: 'cover',
      backgroundPosition: 'center',
    }
  }
  return {}
})

// 登录页动态壁纸：视频 / 多背景轮播（与桌面共用 uiState 配置）
const loginWallpaperVideo = computed(() =>
  uiState.background_mode === 'video' && uiState.wallpaper_video ? uiState.wallpaper_video : ''
)
const loginCarouselImages = computed(() =>
  uiState.background_mode === 'image' && Array.isArray(uiState.backgrounds) ? uiState.backgrounds : []
)
const loginCarouselIndex = ref(0)
let loginCarouselTimer = null
function startLoginCarousel() {
  stopLoginCarousel()   // 先停掉可能残留的旧定时器，保证只跑一个
  if (loginCarouselImages.value.length <= 1) return   // 少于两张图没有轮播意义，直接不启动
  const interval = Math.max(3, Number(uiState.background_interval) || 8) * 1000   // 间隔至少 3 秒，避免配置过小导致疯狂切换
  loginCarouselTimer = setInterval(() => {
    if (loginCarouselImages.value.length <= 1) return   // 运行中配置被清空时安全退出
    loginCarouselIndex.value = (loginCarouselIndex.value + 1) % loginCarouselImages.value.length   // 取模循环切到下一张
  }, interval)
}
function stopLoginCarousel() {
  if (loginCarouselTimer) {
    clearInterval(loginCarouselTimer)
    loginCarouselTimer = null
  }
}

const username = ref('admin')   // 默认填 admin（常见管理员账号），减少输入
const password = ref('')
const loading = ref(false)      // 提交中标记：防重复提交 + 按钮转「登录中…」
const error = ref('')           // 表单顶部错误提示文案

// 两步验证（2FA）：密码校验通过后等待验证码
const otpRequired = ref(false)
const otpCode = ref('')

const forceChange = ref(false)
const forceChangeReason = ref('') // '' | 'default'（默认密码）| 'reset'（重置/首登）
// 强制改密时暂存登录凭据：改密成功后才写入登录态，避免立即进入桌面
const pendingToken = ref(null)
const pendingUser = ref(null)
const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')

// ShunX 安全入口状态
const shunxChecked = ref(false)
const shunx = ref({ enabled: false, matched: false })

// 挂载后并行初始化：品牌配置 + ShunX 安全入口状态
onMounted(async () => {
  // 加载界面品牌配置（网站名 / 欢迎语 / Logo / 背景）并更新浏览器标签标题
  try {
    await loadUi()
    if (document.title !== uiState.site_name && uiState.site_name) {
      document.title = uiState.site_name
    }
    startLoginCarousel()
  } catch (e) {
    // 接口失败时使用默认品牌（兼容旧版后端）
    console.warn('[login] 加载界面配置失败:', e)
  }
  try {
    // 取当前 URL 路径（去掉可能的多余斜杠）
    const currentPath = window.location.pathname.replace(/\/+$/, '') || '/'
    const res = await shunxApi.status(currentPath)
    shunx.value = res
  } catch (e) {
    // 接口调用失败时允许正常登录（兼容旧版后端）
    shunx.value = { enabled: false, matched: false }
  } finally {
    shunxChecked.value = true
  }
})

// --- 动作说明：提交登录表单（含 2FA 二段与强制改密分支） ---
async function handleLogin() {
  if (loading.value) return   // 防连点：请求进行中直接忽略
  error.value = ''
  loading.value = true
  try {
    // 获取当前路径用于 ShunX 安全入口校验
    const currentPath = window.location.pathname.replace(/\/+$/, '') || '/'
    const data = await authApi.login(username.value, password.value, currentPath, otpCode.value || undefined)
    if (data.otp_required) {
      // 密码已通过，但该账号开启了 2FA——显示验证码输入框，等待用户输入后再次提交
      otpRequired.value = true
      otpCode.value = ''     // 清空旧验证码，让用户重新输入
      error.value = ''
      return                 // 不写登录态，等下一轮带验证码的提交
    }
    if (data.user?.must_change_password) {
      // 强制改密：先不写入登录态（否则 App 会立即切换到桌面），改密成功后再进入
      pendingToken.value = data.token
      pendingUser.value = data.user
      oldPassword.value = password.value   // 原密码预填，用户只需填新密码
      password.value = ''                  // 清空主表单密码，防止泄漏到屏幕上
      forceChange.value = true
      // 区分「默认密码」与「重置/首登」，展示不同提示
      forceChangeReason.value = data.user?.default_password ? 'default' : 'reset'
      return
    }
    setAuth(data.token, data.user)   // 正常登录：写入身份（含 localStorage），全站请求开始带 token
    emit('login', data.user)
  } catch (e) {
    error.value = e?.response?.data?.detail || '登录失败'   // 优先展示后端 detail，拿不到时用兜底文案
  } finally {
    loading.value = false
  }
}

// --- 动作说明：强制改密表单提交（成功后才把暂存的身份写入登录态） ---
async function handleChangePassword() {
  if (loading.value) return
  if (newPassword.value.length < 8) {
    error.value = '新密码至少 8 位'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次输入的新密码不一致'
    return
  }
  error.value = ''
  loading.value = true
  try {
    await authApi.changePassword(oldPassword.value, newPassword.value, pendingToken.value || undefined)
    // 改密成功：写入登录态并进入面板
    const user = pendingUser.value
    if (user) {
      user.must_change_password = false   // 本地同步清除「待改密」标记，避免下次又强制改密
      user.default_password = false
      setAuth(pendingToken.value, user)   // 此时才真正登录：刷新页面也会保持在线
    }
    forceChange.value = false
    forceChangeReason.value = ''
    oldPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
    emit('login', user)
  } catch (e) {
    error.value = e?.response?.data?.detail || '修改失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #1f2937;
  background-image: url('../assets/hero.png');
  background-size: cover;
  background-position: center;
  overflow: hidden;
}
.login-wallpaper-video,
.login-wallpaper-carousel {
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  background: #1f2937;
}
.login-wallpaper-video video {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.login-wallpaper-carousel > .login-wallpaper-slide {
  position: absolute;
  inset: 0;
  background-size: cover;
  background-position: center;
  opacity: 0;
  transition: opacity 1.2s ease-in-out;
}
.login-wallpaper-carousel > .login-wallpaper-slide.active {
  opacity: 1;
}
.login-wallpaper-mask {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.18);
}

.login-card {
  width: 360px;
  padding: 32px 32px 28px;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(255, 255, 255, 0.5);
  border-radius: 18px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.32), 0 2px 6px rgba(0, 0, 0, 0.12);
  backdrop-filter: saturate(180%) blur(28px);
  -webkit-backdrop-filter: saturate(180%) blur(28px);
  user-select: none;
}

.login-title {
  font-size: 28px;
  font-weight: 700;
  color: #0a3d7a;
  letter-spacing: 0.5px;
}

.login-logo {
  display: block;
  width: 64px;
  height: 64px;
  object-fit: contain;
  margin: 0 auto 12px;
}

.login-subtitle {
  font-size: 12px;
  color: #6e6e73;
  margin-bottom: 22px;
}

.field {
  display: block;
  margin-bottom: 14px;
}

.field .label {
  display: block;
  font-size: 11px;
  color: #1d1d1f;
  font-weight: 600;
  margin-bottom: 6px;
}

.field input {
  width: 100%;
  padding: 9px 12px;
  font-size: 13px;
  font-family: inherit;
  color: #1d1d1f;
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 8px;
  outline: none;
}

.field input:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10, 132, 255, 0.2);
}

.hint {
  background: rgba(10, 132, 255, 0.12);
  color: #0a3d7a;
  border: 1px solid rgba(10, 132, 255, 0.3);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  margin-bottom: 14px;
}

.hint.danger {
  background: rgba(255, 59, 48, 0.08);
  color: #c0392b;
  border-color: rgba(255, 59, 48, 0.2);
}

.error {
  color: #c0392b;
  font-size: 12px;
  background: rgba(255, 59, 48, 0.08);
  border: 1px solid rgba(255, 59, 48, 0.2);
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 12px;
}

.btn-primary {
  width: 100%;
  margin-top: 4px;
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
  background: #0a84ff;
  border: none;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.15s;
}

.btn-primary:hover:not(:disabled) { background: #006ee6; }
.btn-primary:active:not(:disabled) { background: #0058b8; }
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
</style>