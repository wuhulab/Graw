<!--
  SshKeyImportWindow.vue — SSH 私钥导入表单（独立窗口）
  ==========================================================
  业务作用：原内嵌于 SSHKeysWindow 的「导入私钥」modal 独立为桌面窗口，
  避免误触灰色遮罩丢失已粘贴的私钥长文本（textarea）。
  后端模块：/api/sshkeys 的 importKey。保存成功后 bumpForm('sshkeys') + emit('close')。
-->
<template>
  <div class="import-key-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">名称</span>
      <input class="ui-input" v-model.trim="form.name" placeholder="如：服务器密钥" />
    </label>

    <label class="ui-field">
      <span class="ui-label">私钥内容（-----BEGIN OPENSSH PRIVATE KEY----- …）</span>
      <textarea class="ui-textarea mono-area" v-model="form.private_key" rows="10" spellcheck="false" placeholder="Paste private key here…"></textarea>
    </label>

    <label class="ui-field">
      <span class="ui-label">私钥口令（仅加密私钥需要填写）</span>
      <input class="ui-input" v-model="form.passphrase" type="password" autocomplete="new-password" />
    </label>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">取消</button>
      <button class="ui-btn primary" :disabled="saving" @click="importKey">{{ saving ? '导入中…' : '导入' }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { sshkeysApi } from '../../api'
import { bumpForm } from '../../store/formBus'

const emit = defineEmits(['close'])
const saving = ref(false)
const error = ref('')
const form = reactive({ name: '', private_key: '', passphrase: '' })

async function importKey() {
  if (saving.value) return   // 防重复导入
  saving.value = true
  error.value = ''
  try {
    await sshkeysApi.importKey({
      name: form.name.trim(),
      private_key: form.private_key,
      passphrase: form.passphrase || undefined    // 未加密私钥不传 passphrase，交由后端按无密码处理
    })
    bumpForm('sshkeys')
    emit('close')
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.import-key-window { padding: 14px; overflow-y: auto; }
.mono-area { font-family: ui-monospace, Menlo, Consolas, monospace; }
.error-box { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; word-break: break-all; }
</style>