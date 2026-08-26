<!--
  ChangePasswordWindow.vue — 修改密码窗口
  ==========================================================
  业务作用：
    修改当前登录用户的密码：输入旧密码、新密码与确认密码，前端校验长度与
    一致性后提交。修改成功后同步清除本地登录态里的 must_change_password
    标记（首次登录强制改密后解除限制）。
  后端模块：
    /api/auth 的 changePassword（修改当前用户密码）。
  关键状态：
    - oldPassword / newPassword / confirmPassword 三个密码输入框
    - ok    修改成功提示
  打开方式：
    首次登录强制改密时或用户主动打开，无 props。
-->
<template>
  <div class="change-pwd">
    <form @submit.prevent="submit">
      <label class="field">
        <span class="label">{{ $t('changepassword.oldPassword') }}</span>
        <input v-model="oldPassword" type="password" required />
      </label>
      <label class="field">
        <span class="label">{{ $t('changepassword.newPassword') }}</span>
        <input v-model="newPassword" type="password" minlength="6" required />
      </label>
      <label class="field">
        <span class="label">{{ $t('changepassword.confirmPassword') }}</span>
        <input v-model="confirmPassword" type="password" minlength="6" required />
      </label>
      <div v-if="error" class="error">{{ error }}</div>
      <div v-if="ok" class="ok">{{ $t('changepassword.changeSuccess') }}</div>
      <button class="btn-primary" type="submit" :disabled="saving">
        {{ saving ? $t('changepassword.submitting') : $t('changepassword.save') }}
      </button>
    </form>
  </div>
</template>

<script setup>
import { ref } from 'vue'   // 表单输入状态
import { useI18n } from 'vue-i18n'   // 翻译函数
import { authApi } from '../../api'   // /api/auth：修改密码接口
import { auth, setAuth } from '../../store/auth'   // 登录态读写（改密成功后清除强制改密标记）

const { t } = useI18n()

const oldPassword = ref('')   // 当前密码
const newPassword = ref('')   // 新密码
const confirmPassword = ref('')   // 新密码确认
const saving = ref(false)   // 提交中
const error = ref('')   // 错误提示
const ok = ref(false)   // 成功提示

// --- 提交改密：先做前端校验，成功后同步本地登录态 ---
async function submit() {
  if (saving.value) return   // 防重复提交
  error.value = ''
  ok.value = false
  if (newPassword.value.length < 6) { error.value = t('changepassword.pwdTooShort'); return }   // 最短 6 位，与后端策略一致
  if (newPassword.value !== confirmPassword.value) { error.value = t('changepassword.pwdMismatch'); return }   // 两次输入不一致直接中止
  saving.value = true
  try {
    await authApi.changePassword(oldPassword.value, newPassword.value)
    // 改密成功：清除首次登录强制改密标记并回写登录态
    if (auth.user) {
      auth.user.must_change_password = false
      setAuth(auth.token, auth.user)
    }
    ok.value = true
    oldPassword.value = ''
    newPassword.value = ''
    confirmPassword.value = ''
  } catch (e) {
    error.value = e?.response?.data?.detail || t('changepassword.changeFailed')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.change-pwd {
  height: 100%;
  background: #f5f5f7;
  padding: 18px;
}

.field {
  display: block;
  margin-bottom: 12px;
}

.field .label {
  display: block;
  font-size: 11px;
  color: #6e6e73;
  font-weight: 600;
  margin-bottom: 4px;
}

.field input {
  width: 100%;
  padding: 8px 10px;
  font-size: 13px;
  font-family: inherit;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 8px;
  outline: none;
  background: #ffffff;
  color: #1d1d1f;
}

.field input:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10, 132, 255, 0.18);
}

.error {
  color: #c0392b;
  font-size: 12px;
  background: rgba(255, 59, 48, 0.08);
  border: 1px solid rgba(255, 59, 48, 0.2);
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 10px;
}

.ok {
  color: #2d6a4f;
  font-size: 12px;
  background: rgba(103, 194, 58, 0.12);
  border: 1px solid rgba(103, 194, 58, 0.32);
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 10px;
}

.btn-primary {
  width: 100%;
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #ffffff;
  background: #0a84ff;
  border: none;
  border-radius: 10px;
  cursor: pointer;
}

.btn-primary:hover:not(:disabled) { background: #006ee6; }
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
