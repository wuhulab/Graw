<!--
  ShunXSetup.vue — ShunX 安全入口强制设置界面
  作用：登录后若后端判定尚未配置安全入口，则全屏强制展示此页。管理员必须先设置
        「安全入口路径」才能进入面板；非管理员只能看到提示并退出登录。
        安全入口是额外的一道 URL 门禁（shunx 中间件）：陌生设备必须访问
        <站点源>/<入口路径> 才会渲染登录页，路径未知一律拒绝，故建议使用随机长串。
  数据：path 为管理员输入的入口路径；保存走 shunxApi.update，成功后 emit('saved')。
  打开方式：由 App.vue 根据后端安全入口状态决定渲染。
-->
<template>
  <!-- ShunX 安全入口强制设置界面（登录后未配置入口时全屏展示） -->
  <div class="shunx-overlay">
    <div class="shunx-card">
      <div class="shunx-head">
        <span class="shunx-logo">ShunX</span>
        <span class="shunx-sub">安全入口保护</span>
      </div>

      <!-- 管理员：可设置安全入口 -->
      <template v-if="isAdmin()">
        <p class="desc">
          系统尚未配置安全入口。为保护面板安全，请设置一个<strong>安全入口路径</strong>。
          设置后，陌生设备必须先访问
          <code>{{ origin }}/{{ path || '你的入口路径' }}</code> 才能看到登录页。
        </p>
        <p class="warn">
          建议使用足够长且随机的路径（如 <code>shunx-8f3k2q7m</code>），并妥善保管；
          忘记后将无法登录。
        </p>

        <label class="field">
          <span class="label">安全入口路径</span>
          <div class="path-input">
            <span class="prefix">{{ origin }}/</span>
            <input v-model.trim="path" placeholder="例如 shunx-8f3k2q7m" spellcheck="false" @keyup.enter="save" />
          </div>
        </label>

        <div v-if="error" class="error">{{ error }}</div>
        <div v-if="success" class="success">{{ success }}</div>

        <button class="btn-primary" :disabled="saving || !path" @click="save">
          {{ saving ? '保存中…' : '保存并进入面板' }}
        </button>
      </template>

      <!-- 非管理员：提示联系管理员 -->
      <template v-else>
        <p class="desc">
          系统尚未配置安全入口。请联系管理员完成安全入口设置后再使用管理面板。
        </p>
        <button class="btn-primary" @click="logout">退出登录</button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'                       // Vue 响应式与计算属性
import { shunxApi } from '../api'                         // ShunX 安全入口 API
import { clearAuth, isAdmin } from '../store/auth'        // 登出清态 / 管理员判定

const emit = defineEmits(['saved'])   // 保存成功后通知 App 刷新安全入口状态

const origin = computed(() => window.location.origin)   // 当前站点源（用于拼接展示完整入口 URL）
const path = ref('')          // 管理员输入的安全入口路径
const saving = ref(false)     // 保存请求进行中，禁用按钮
const error = ref('')         // 保存失败提示
const success = ref('')       // 保存成功提示

// 保存安全入口路径到后端，成功后通知上层刷新安全入口状态
async function save() {
  if (saving.value || !path.value) return   // 路径为空或请求进行中不重复提交
  error.value = ''
  success.value = ''
  saving.value = true
  try {
    await shunxApi.update(path.value)
    success.value = `安全入口已设置：${origin.value}/${path.value}。下次登录请通过该地址访问。`
    emit('saved', path.value)
  } catch (e) {
    error.value = e?.response?.data?.detail || '保存失败'
  } finally {
    saving.value = false
  }
}

// 非管理员无设置权限：清空登录态并回到首页
function logout() {
  clearAuth()
  window.location.href = '/'
}
</script>

<style scoped>
.shunx-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(20, 30, 48, 0.72);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}

.shunx-card {
  width: 440px;
  padding: 30px 32px 28px;
  background: #ffffff;
  border-radius: 16px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.32);
}

.shunx-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 14px;
}

.shunx-logo {
  font-size: 24px;
  font-weight: 800;
  color: #0a3d7a;
}

.shunx-sub {
  font-size: 12px;
  color: #6e6e73;
}

.desc {
  font-size: 13px;
  line-height: 1.6;
  color: #1d1d1f;
  margin: 0 0 12px;
}

.desc code, .warn code {
  font-family: ui-monospace, Consolas, monospace;
  background: rgba(10, 132, 255, 0.1);
  color: #0a3d7a;
  border-radius: 4px;
  padding: 1px 5px;
}

.warn {
  font-size: 12px;
  line-height: 1.6;
  color: #b45309;
  background: rgba(255, 159, 10, 0.1);
  border: 1px solid rgba(255, 159, 10, 0.3);
  border-radius: 8px;
  padding: 8px 10px;
  margin: 0 0 16px;
}

.field {
  display: block;
  margin-bottom: 12px;
}

.field .label {
  display: block;
  font-size: 11px;
  color: #1d1d1f;
  font-weight: 600;
  margin-bottom: 6px;
}

.path-input {
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 8px;
  padding: 0 12px;
  background: #fff;
}

.path-input:focus-within {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10, 132, 255, 0.2);
}

.path-input .prefix {
  font-size: 12px;
  color: #8e8e93;
  white-space: nowrap;
}

.path-input input {
  flex: 1;
  border: none;
  outline: none;
  padding: 9px 0;
  font-size: 13px;
  font-family: inherit;
  color: #1d1d1f;
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

.success {
  color: #0a7d3b;
  font-size: 12px;
  background: rgba(10, 132, 255, 0.08);
  border: 1px solid rgba(10, 132, 255, 0.25);
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 12px;
}

.btn-primary {
  width: 100%;
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
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