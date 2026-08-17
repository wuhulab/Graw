<template>
  <div class="login-page">
    <div class="login-card">
      <!-- 安全入口门禁（已配置入口且当前路径不匹配） -->
      <template v-if="shunxChecked && shunx.enabled && !shunx.matched">
        <div class="login-title">ShunX</div>
        <div class="login-subtitle">安全入口保护</div>
        <div class="hint" style="background:rgba(255,59,48,0.08);border-color:rgba(255,59,48,0.2);color:#c0392b;">
          请通过已配置的安全入口路径访问管理面板
        </div>
      </template>

      <!-- 登录表单 -->
      <template v-else-if="!forceChange">
        <div class="login-title">Graw</div>
        <div class="login-subtitle">服务器管理面板</div>

        <form @submit.prevent="handleLogin">
          <label class="field">
            <span class="label">账号</span>
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
            <span class="label">密码</span>
            <input
              v-model="password"
              type="password"
              autocomplete="current-password"
              required
            />
          </label>
          <div v-if="error" class="error">{{ error }}</div>
          <button class="btn-primary" type="submit" :disabled="loading">
            {{ loading ? '登录中…' : '登 录' }}
          </button>
        </form>
      </template>

      <!-- 强制改密 -->
      <form v-else @submit.prevent="handleChangePassword">
        <div v-if="forceChangeReason === 'default'" class="hint danger">
          检测到您正在使用默认密码，出于安全考虑，必须先修改密码才能使用面板。
        </div>
        <div v-else class="hint">首次登录或密码已重置，请设置新密码</div>
        <label class="field">
          <span class="label">原密码</span>
          <input v-model="oldPassword" type="password" required />
        </label>
        <label class="field">
          <span class="label">新密码</span>
          <input v-model="newPassword" type="password" minlength="8" required />
        </label>
        <label class="field">
          <span class="label">确认新密码</span>
          <input v-model="confirmPassword" type="password" minlength="8" required />
        </label>
        <div v-if="error" class="error">{{ error }}</div>
        <button class="btn-primary" type="submit" :disabled="loading">
          {{ loading ? '提交中…' : '更新密码并进入' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { authApi, shunxApi } from '../api'
import { setAuth } from '../store/auth'

const emit = defineEmits(['login'])

const username = ref('admin')
const password = ref('')
const loading = ref(false)
const error = ref('')

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

onMounted(async () => {
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

async function handleLogin() {
  if (loading.value) return
  error.value = ''
  loading.value = true
  try {
    // 获取当前路径用于 ShunX 安全入口校验
    const currentPath = window.location.pathname.replace(/\/+$/, '') || '/'
    const data = await authApi.login(username.value, password.value, currentPath)
    if (data.user?.must_change_password) {
      // 强制改密：先不写入登录态（否则 App 会立即切换到桌面），改密成功后再进入
      pendingToken.value = data.token
      pendingUser.value = data.user
      oldPassword.value = password.value
      password.value = ''
      forceChange.value = true
      // 区分「默认密码」与「重置/首登」，展示不同提示
      forceChangeReason.value = data.user?.default_password ? 'default' : 'reset'
      return
    }
    setAuth(data.token, data.user)
    emit('login', data.user)
  } catch (e) {
    error.value = e?.response?.data?.detail || '登录失败'
  } finally {
    loading.value = false
  }
}

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
      user.must_change_password = false
      user.default_password = false
      setAuth(pendingToken.value, user)
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