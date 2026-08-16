<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <span style="color:#0a3d7a; font-weight:600;">Docker 配置文件</span>
      <span style="font-size:11px; color:#888; font-family:Consolas,monospace;">{{ configPath || '加载中...' }}</span>
      <button class="btn" style="margin-left:auto;" @click="load">重新加载</button>
      <button class="btn primary" @click="save">保存</button>
      <button class="btn" @click="$emit('close')">关闭</button>
    </div>
    <textarea ref="editor" v-model="content" class="cfg-editor" spellcheck="false"
      :placeholder="error || '加载配置文件...'"></textarea>
    <div v-if="msg" class="msg" :class="{ err: msgErr }">{{ msg }}</div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { dockerApi } from '../../api'

const emit = defineEmits(['close'])

const content = ref('')
const configPath = ref('')
const msg = ref('')
const msgErr = ref(false)
const error = ref('')

async function load() {
  msg.value = ''
  try {
    const cfg = await dockerApi.config()
    configPath.value = cfg.config_path || ''
    content.value = cfg.content || ''
    error.value = ''
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  }
}

async function save() {
  msg.value = ''
  try {
    const r = await dockerApi.saveConfigRaw(content.value)
    msgErr = false
    msg.value = `已保存 → ${r.config_path}`
  } catch (e) {
    msgErr = true
    msg.value = '保存失败：' + (e.response?.data?.detail || e.message)
  }
}

onMounted(load)
</script>

<style scoped>
.cfg-editor {
  flex: 1; margin: 10px; padding: 10px; resize: none;
  border: 1px solid #d1d5db; border-radius: 8px;
  font-family: Consolas, 'Courier New', monospace; font-size: 12.5px;
  background: #fafafa; color: #111827; line-height: 1.5;
  box-sizing: border-box;
}
.btn.primary { background: #0a3d7a; color: #fff; border-color: #0a3d7a; }
.msg { margin: 0 10px 10px; font-size: 12px; color: #2a8f3c; }
.msg.err { color: #b91c1c; }
</style>
