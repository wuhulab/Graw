<!--
  SslLeFormWindow.vue — Let's Encrypt 证书签发表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 SSLWindow 的「Let's Encrypt 签发」modal 弹窗独立为
    桌面窗口，避免误触灰色遮罩丢失已填写的域名与注册邮箱。
  后端模块：
    /api/ssl 的 letsencrypt（certbot 自动签发）。
  关键状态：
    leForm   逗号分隔的域名 + 注册邮箱
    error    必填校验 / 后端错误信息回显
  打开方式：
    由 App.vue 的 openSslLeForm(payload) 打开，无 props。
    保存成功后 emit('close')，并经 formBus 通知 SSLWindow 刷新列表。
-->
<template>
  <div class="ssl-le-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">{{ $t('ssl.leDomains') }}</span>
      <input class="ui-input" v-model.trim="leForm.domains" placeholder="example.com,www.example.com" />
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('ssl.leEmail') }}</span>
      <input class="ui-input" v-model.trim="leForm.email" placeholder="admin@example.com" />
    </label>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="apply">{{ $t('ssl.apply') }}</button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态
import { ref, reactive } from 'vue'
// SSL 证书 API
import { sslApi } from '../../api'
// 表单保存信号：通知 SSLWindow 刷新证书列表
import { bumpForm } from '../../store/formBus'

const emit = defineEmits(['close'])

const saving = ref(false)   // 签发中（禁用按钮防重复提交）
const error = ref('')       // 必填校验 / 后端错误信息
// 签发表单：逗号分隔的域名 + 注册邮箱
const leForm = reactive({ domains: '', email: '' })

// --- 签发：把逗号分隔的域名拆成数组再提交，成功后通知父窗口刷新并自关 ---
async function apply() {
  if (saving.value) return
  error.value = ''
  const domains = leForm.domains.split(',').map(d => d.trim()).filter(Boolean)   // 去空白、去空项，避免把空串当域名
  if (domains.length === 0) { error.value = '请填写至少一个域名'; return }
  saving.value = true
  try {
    await sslApi.letsencrypt({ domains, email: leForm.email.trim() })
    bumpForm('ssl')   // 通知 SSL 窗口重新拉取证书列表
    emit('close')     // 成功后关闭本窗口
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.ssl-le-window { padding: 14px; }
.error-box {
  color: #b91c1c;
  font-size: 12.5px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
  word-break: break-all;
}
</style>