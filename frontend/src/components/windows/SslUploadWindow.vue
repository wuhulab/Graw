<!--
  SslUploadWindow.vue — SSL 证书上传表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 SSLWindow 的「上传证书」modal 弹窗独立为桌面窗口，
    避免误触灰色遮罩丢失已选证书文件与名称。cert/key 两个文件
    走 multipart 提交。
  后端模块：
    /api/ssl 的 upload。
  关键状态：
    upForm   名称 / 域名 / cert 文件 / key 文件
    error    必填校验 / 后端错误信息回显
  打开方式：
    由 App.vue 的 openSslUpload(payload) 打开，无 props。
    保存成功后 emit('close')，并经 formBus 通知 SSLWindow 刷新列表。
-->
<template>
  <div class="ssl-upload-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">{{ $t('ssl.uploadName') }}</span>
      <input class="ui-input" v-model.trim="upForm.name" />
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('ssl.uploadDomains') }}</span>
      <input class="ui-input" v-model.trim="upForm.domains" />
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('ssl.certFile') }}</span>
      <input class="ui-input" type="file" @change="e=>upForm.cert=e.target.files[0]" />
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('ssl.keyFile') }}</span>
      <input class="ui-input" type="file" @change="e=>upForm.key=e.target.files[0]" />
    </label>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="upload">{{ $t('ssl.upload') }}</button>
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

const saving = ref(false)   // 上传中（禁用按钮防重复提交）
const error = ref('')       // 必填校验 / 后端错误信息
// 上传表单；cert/key 是所选文件对象
const upForm = reactive({ name: '', domains: '', cert: null, key: null })

// --- 上传证书（cert/key 两个文件走 multipart），成功后通知父窗口刷新并自关 ---
async function upload() {
  if (saving.value) return
  error.value = ''
  // 前端必填校验：名称、证书文件、私钥文件缺一不可
  if (!upForm.name.trim()) { error.value = '请填写证书名称'; return }
  if (!upForm.cert) { error.value = '请选择证书文件（cert）'; return }
  if (!upForm.key) { error.value = '请选择私钥文件（key）'; return }
  const fd = new FormData()
  fd.append('name', upForm.name.trim())
  fd.append('domains', upForm.domains.trim())
  fd.append('cert', upForm.cert)
  fd.append('key', upForm.key)
  saving.value = true
  try {
    await sslApi.upload(fd)
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
.ssl-upload-window { padding: 14px; }
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