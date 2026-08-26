<!--
  ConfirmDialog.vue — 高风险操作二次确认对话框
  作用：对不可逆的高风险操作（如删除站点 / 容器）强制二次确认。两种确认模式：
        text     —— 输入必须与 requiredText 完全一致（忽略大小写与首尾空格）才可执行；
        password —— 输入当前登录用户的面板密码，由后端校验通过才放行。
  数据：全部为调用方传入的 props（show / mode / title / message / requiredText…）；
        结果经 confirm（携带输入内容）/ cancel 事件上抛，本组件不直接调用业务接口。
  打开方式：由调用方 v-model:show 控制显隐，执行危险操作前展示。
-->
<template>
  <!-- 高风险操作二次确认对话框
       mode='text'    ：要求输入与 requiredText 完全一致的文本（如站点名/DELETE）才可执行
       mode='password'：要求输入当前登录用户的面板密码（后端校验）才可执行 -->
  <div v-if="show" class="confirm-overlay" @click.self="close">
    <div class="confirm-modal" role="dialog" aria-modal="true">
      <div class="confirm-head">
        <AlertTriangle :size="18" class="warn-icon" />
        <h3>{{ title }}</h3>
      </div>
      <p class="confirm-msg">{{ message }}</p>
      <div class="confirm-form">
        <label>{{ inputLabel }}</label>
        <input
          ref="inputRef"
          v-model="input"
          :type="mode === 'password' ? 'password' : 'text'"
          :placeholder="placeholder"
          :disabled="busy"
          @keyup.enter="submit"
        />
        <p v-if="err" class="confirm-err">{{ err }}</p>
      </div>
      <div class="confirm-actions">
        <button class="btn" :disabled="busy" @click="close">{{ cancelLabel }}</button>
        <button
          class="btn danger"
          :disabled="busy || !canSubmit"
          @click="submit"
        >
          {{ busy ? busyLabel : confirmLabel }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, computed } from 'vue'   // Vue 响应式 / 计算属性 / nextTick 聚焦
import { AlertTriangle } from 'lucide-vue-next'        // 警告三角图标
import { authApi } from '../api'                        // 鉴权 API（password 模式校验当前用户密码）

const props = defineProps({
  show: { type: Boolean, default: false },
  // 对话框标题与风险说明文案（由调用方传入，便于走各自 i18n）
  title: { type: String, default: '高风险操作确认' },
  message: { type: String, default: '' },
  // 确认方式：text=输入指定文本 / password=输入面板密码
  mode: { type: String, default: 'text' },
  // text 模式要求输入的匹配文本（忽略大小写）
  requiredText: { type: String, default: '' },
  inputLabel: { type: String, default: '确认内容' },
  placeholder: { type: String, default: '' },
  confirmLabel: { type: String, default: '确认执行' },
  cancelLabel: { type: String, default: '取消' },
  busyLabel: { type: String, default: '验证中…' }
})

const emit = defineEmits(['confirm', 'cancel'])  // 确认（携带输入内容）/ 取消事件

const input = ref('')         // 用户输入内容（待匹配文本或面板密码）
const err = ref('')           // 错误提示文案（password 模式校验失败时显示）
const busy = ref(false)       // 密码验证请求进行中，期间禁用提交与关闭
const inputRef = ref(null)    // 输入框 DOM 引用，打开时自动聚焦

// text 模式：输入与要求文本一致（忽略大小写与首尾空格）才可提交
const canSubmit = computed(() => {
  if (props.mode === 'text') {
    return input.value.trim().toLowerCase() === (props.requiredText || '').trim().toLowerCase()
  }
  return input.value.length > 0
})

// 每次打开时清空输入与错误，并聚焦输入框
watch(() => props.show, (v) => {
  if (v) {
    input.value = ''
    err.value = ''
    busy.value = false
    nextTick(() => inputRef.value && inputRef.value.focus())
  }
})

// 关闭对话框并通知父组件取消
function close() {
  if (busy.value) return   // 验证进行中禁止关闭，防止绕过二次确认
  emit('cancel')
}

// 提交确认：text 模式直接放行，password 模式先经后端校验当前用户密码
async function submit() {
  if (busy.value || !canSubmit.value) return   // 输入不满足条件或请求进行中则不触发
  if (props.mode === 'text') {
    // 文本匹配已通过校验，直接交由调用方执行
    emit('confirm', input.value)
    return
  }
  // password 模式：先调用后端校验当前用户密码，成功后再由调用方执行
  busy.value = true
  err.value = ''
  try {
    await authApi.verifyPassword(input.value)
    emit('confirm', input.value)
  } catch (e) {
    err.value = e?.response?.data?.detail || '密码错误，无法确认'
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 3000;
}
.confirm-modal {
  background: #fff;
  border-radius: 12px;
  padding: 18px;
  width: 420px;
  max-width: 90vw;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.18);
}
.confirm-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.confirm-head h3 {
  margin: 0;
  font-size: 16px;
}
.warn-icon {
  color: #d97706;
  flex-shrink: 0;
}
.confirm-msg {
  margin: 0 0 12px;
  font-size: 13px;
  color: #374151;
  line-height: 1.6;
  white-space: pre-line;
}
.confirm-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.confirm-form label {
  font-size: 12px;
  color: #374151;
}
.confirm-form input {
  padding: 8px 10px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 13px;
}
.confirm-form input:focus {
  outline: none;
  border-color: #ef4444;
}
.confirm-err {
  margin: 0;
  font-size: 12px;
  color: #dc2626;
}
.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}
.btn {
  padding: 6px 14px;
  border: 1px solid #d1d5db;
  background: #fff;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.btn.danger {
  background: #dc2626;
  color: #fff;
  border-color: #dc2626;
}
.btn.danger:hover:not(:disabled) {
  background: #b91c1c;
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
