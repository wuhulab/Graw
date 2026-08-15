<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#f5f5f7;">
    <div style="flex:1; overflow:auto; padding:16px; display:flex; flex-direction:column; gap:14px;">
      <div class="block">
        <div class="block-title">账号</div>
        <button class="btn" @click="emit('openUsers')" :disabled="!isAdmin()">打开账号管理</button>
        <span v-if="!isAdmin()" style="font-size:11px;color:#6e6e73;margin-left:8px;">仅管理员可用</span>
      </div>

      <!-- ShunX 安全入口管理（仅管理员） -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">ShunX 安全入口</div>
        <div class="row" style="flex-wrap:wrap; gap:6px;">
          <span class="status-dot" :class="currentEntry ? 'on' : 'off'"></span>
          <span style="font-size:12px;color:#1d1d1f;">
            {{ statusText }}
          </span>
        </div>
        <div class="row" style="flex-direction:column; align-items:stretch; gap:8px;">
          <input v-model="entryPath" placeholder="例如 shunx-8f3k2q7m（留空保存则清除）" spellcheck="false" @keyup.enter="saveEntry" />
          <div style="display:flex; gap:8px;">
            <button class="btn" :disabled="saving" @click="saveEntry">{{ saving ? '保存中…' : '保存' }}</button>
            <button class="btn btn-danger" v-if="currentEntry" :disabled="saving" @click="clearEntry">清除安全入口</button>
          </div>
          <div v-if="msg" :class="['msg', msgType]">{{ msg }}</div>
        </div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-top:4px;">
          设置后陌生设备必须先访问 <code style="background:rgba(10,132,255,0.1);padding:1px 4px;border-radius:4px;">{{ origin }}/{{ currentEntry || '你的入口路径' }}</code> 才能看到登录页。建议使用足够长的随机路径。
        </div>
      </div>

      <div class="block">
        <div class="block-title">面板</div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.showTaskbarText" />
            <span>底栏显示详细文字</span>
          </label>
        </div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.taskbarTextOnly" />
            <span>底栏只显示文字（隐藏图标）</span>
          </label>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { settings } from '../../store/settings'
import { isAdmin } from '../../store/auth'
import { shunxApi } from '../../api'

const emit = defineEmits(['openUsers'])

// ShunX 安全入口状态
const entryPath = ref('')
const currentEntry = ref('')
const saving = ref(false)
const msg = ref('')
const msgType = ref('')
const origin = computed(() => window.location.origin)

const statusText = computed(() => {
  if (!currentEntry.value) return '未设置安全入口（当前允许直接登录）'
  return `已启用：/ ${currentEntry.value}`
})

onMounted(async () => {
  try {
    const config = await shunxApi.config()
    currentEntry.value = config.entry_path || ''
    entryPath.value = currentEntry.value
  } catch (e) {
    currentEntry.value = ''
  }
})

async function saveEntry() {
  if (saving.value) return
  saving.value = true
  msg.value = ''
  try {
    const res = await shunxApi.update(entryPath.value)
    const config = res.config || {}
    currentEntry.value = config.entry_path || ''
    entryPath.value = currentEntry.value
    msg.value = currentEntry.value
      ? `安全入口已设置：${origin.value}/${currentEntry.value}`
      : '已清除安全入口'
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || '保存失败'
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}

async function clearEntry() {
  if (saving.value) return
  if (!confirm('确定清除 ShunX 安全入口吗？清除后任何设备将可直接访问登录页。')) return
  saving.value = true
  msg.value = ''
  try {
    await shunxApi.update('')
    currentEntry.value = ''
    entryPath.value = ''
    msg.value = '已清除安全入口'
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || '清除失败'
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.block {
  background: #fff;
  border-radius: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(0,0,0,0.06);
}
.block-title {
  font-size: 12px;
  font-weight: 600;
  color: #1d1d1f;
  margin-bottom: 10px;
}
.row {
  display: flex;
  align-items: center;
  padding: 6px 0;
  font-size: 12px;
}
.switch-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: #1d1d1f;
}
.switch-label input {
  width: 16px;
  height: 16px;
  cursor: pointer;
}
.btn {
  padding: 6px 14px;
  font-size: 12px;
  color: #fff;
  background: #0a84ff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}
.btn:hover:not(:disabled) { background: #006ee6; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-danger {
  background: #e5484d;
}
.btn-danger:hover:not(:disabled) { background: #d63d42; }
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.status-dot.on { background: #0a7d3b; }
.status-dot.off { background: #c0392b; }
input {
  width: 100%;
  padding: 8px 10px;
  font-size: 12px;
  font-family: inherit;
  color: #1d1d1f;
  border: 1px solid rgba(0,0,0,0.12);
  border-radius: 8px;
  outline: none;
  box-sizing: border-box;
}
input:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10,132,255,0.15);
}
.msg {
  font-size: 12px;
  border-radius: 6px;
  padding: 6px 8px;
}
.msg.ok {
  color: #0a7d3b;
  background: rgba(10,132,255,0.08);
  border: 1px solid rgba(10,132,255,0.25);
}
.msg.err {
  color: #c0392b;
  background: rgba(255,59,48,0.08);
  border: 1px solid rgba(255,59,48,0.2);
}
</style>