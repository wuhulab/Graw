<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 顶部工具栏：与标准文件编辑器一致 -->
    <div class="toolbar">
      <strong style="font-family:monospace; font-size:12px;">
        docker-compose.yml ({{ appId }})
        <span v-if="isDirty" style="color:#ff3b30; font-weight:600;"> *</span>
      </strong>
      <span v-if="saved" class="saved-hint">已应用，安装时将使用此内容</span>
      <button class="btn" style="margin-left:auto;" @click="save">保存</button>
      <button class="btn" @click="emit('close')">关闭</button>
    </div>
    <textarea v-model="localContent" spellcheck="false" class="editor-area"></textarea>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { appStoreComposeState } from '../../store/appStoreCompose'

const props = defineProps({
  appId: String,
  compose: String
})
const emit = defineEmits(['close'])

const localContent = ref(props.compose || '')
const savedContent = ref(props.compose || '')
const saved = ref(false)

const isDirty = computed(() => localContent.value !== savedContent.value)

function save() {
  // 写入跨窗口共享状态，供安装窗口在确认安装时读取
  appStoreComposeState.appId = props.appId
  appStoreComposeState.content = localContent.value
  appStoreComposeState.rev++
  savedContent.value = localContent.value
  saved.value = true
}
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
.saved-hint { color: #047857; font-size: 11.5px; }
.editor-area { flex: 1; border: none; outline: none; padding: 8px; font-family: Consolas, monospace; font-size: 12px; resize: none; }
</style>
