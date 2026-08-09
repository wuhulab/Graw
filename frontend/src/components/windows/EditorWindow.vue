<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <strong style="font-family:monospace; font-size:12px;">{{ path }}</strong>
      <button class="btn" style="margin-left:auto;" @click="save">保存</button>
      <button class="btn" @click="emit('close')">关闭</button>
    </div>
    <textarea v-model="localContent" spellcheck="false" style="flex:1;border:none;outline:none;padding:8px;font-family:Consolas,monospace;font-size:12px;resize:none;"></textarea>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { filesApi } from '../../api'

const props = defineProps({ path: String, content: String })
const emit = defineEmits(['close'])
const localContent = ref(props.content || '')

async function save() {
  try {
    await filesApi.write(props.path, localContent.value)
    alert('保存成功')
  } catch (e) {
    alert('保存失败：' + (e.response?.data?.detail || e.message))
  }
}
</script>
