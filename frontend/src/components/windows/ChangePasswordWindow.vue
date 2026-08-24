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
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { authApi } from '../../api'
import { auth, setAuth } from '../../store/auth'

const { t } = useI18n()

const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const saving = ref(false)
const error = ref('')
const ok = ref(false)

async function submit() {
  if (saving.value) return
  error.value = ''
  ok.value = false
  if (newPassword.value.length < 6) { error.value = t('changepassword.pwdTooShort'); return }
  if (newPassword.value !== confirmPassword.value) { error.value = t('changepassword.pwdMismatch'); return }
  saving.value = true
  try {
    await authApi.changePassword(oldPassword.value, newPassword.value)
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
