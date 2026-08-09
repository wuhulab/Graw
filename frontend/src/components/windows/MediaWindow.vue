<template>
  <div style="display:flex; flex-direction:column; height:100%; align-items:center; justify-content:center; background:#000000;">
    <img v-if="type === 'image'" :src="url" style="max-width:100%; max-height:100%; object-fit:contain;" />
    <video v-else-if="type === 'video'" :src="url" controls style="max-width:100%; max-height:100%;" />
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { auth } from '../../store/auth'

const props = defineProps({ path: String, name: String, type: String })
const url = ref('')

onMounted(async () => {
  try {
    const resp = await fetch('/api/files/download?path=' + encodeURIComponent(props.path), {
      headers: { Authorization: `Bearer ${auth.token}` }
    })
    if (!resp.ok) throw new Error(resp.status)
    const blob = await resp.blob()
    url.value = URL.createObjectURL(blob)
  } catch (e) {
    alert('加载失败：' + e.message)
  }
})

onBeforeUnmount(() => {
  if (url.value) URL.revokeObjectURL(url.value)
})
</script>
