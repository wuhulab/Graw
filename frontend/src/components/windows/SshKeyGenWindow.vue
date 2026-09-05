<!--
  SshKeyGenWindow.vue — SSH 密钥对生成表单（独立窗口）
  ==========================================================
  业务作用：原内嵌于 SSHKeysWindow 的「生成密钥对」modal 独立为桌面窗口，
  避免误触灰色遮罩丢失已填的名称/算法/注释。默认 ed25519 算法。
  后端模块：/api/sshkeys 的 create。保存成功后 bumpForm('sshkeys') + emit('close')。
-->
<template>
  <div class="gen-key-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">密钥名称</span>
      <input class="ui-input" v-model.trim="form.name" maxlength="128" placeholder="如：个人笔记本" />
    </label>

    <label class="ui-field">
      <span class="ui-label">加密算法</span>
      <select class="ui-select" v-model="form.key_type">
        <option value="ed25519">Ed25519（推荐）</option>
        <option value="rsa">RSA（4096）</option>
        <option value="ecdsa">ECDSA</option>
      </select>
    </label>

    <label class="ui-field">
      <span class="ui-label">注释（可选）</span>
      <input class="ui-input" v-model.trim="form.comment" placeholder="user@host" />
    </label>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">取消</button>
      <button class="ui-btn primary" :disabled="saving" @click="create">{{ saving ? '生成中…' : '生成密钥对' }}</button>
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
const form = reactive({ name: '', key_type: 'ed25519', comment: '' })   // ed25519 为默认算法（更快更安全）

async function create() {
  if (saving.value) return   // 防重复生成
  saving.value = true
  error.value = ''
  try {
    await sshkeysApi.create({
      name: form.name.trim(),
      key_type: form.key_type,
      comment: form.comment.trim()
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
.gen-key-window { padding: 14px; }
.error-box { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; word-break: break-all; }
</style>