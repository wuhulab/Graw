<!--
  媒体预览窗口（后端 /api/files 模块）
  作用：在纯黑背景下居中预览单个图片或视频文件（来自文件管理器的双击/右键打开）。
  后端模块：/api/files（download：下载文件内容，接口需登录鉴权）。
  关键状态：url（blob 对象地址，作为 <img>/<video> 的预览源）、props.path/type（文件路径与类型）。
  打开方式：文件管理器双击图片/视频时由父窗口传入 path/name/type 打开；
            窗口关闭时释放 blob URL，防止内存泄漏。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%; align-items:center; justify-content:center; background:#000000;">
    <img v-if="type === 'image'" :src="url" style="max-width:100%; max-height:100%; object-fit:contain;" />
    <video v-else-if="type === 'video'" :src="url" controls style="max-width:100%; max-height:100%;" />
  </div>
</template>

<script setup>
// 响应式状态与生命周期钩子
import { ref, onMounted, onBeforeUnmount } from 'vue'
// 国际化：预览加载失败提示
import { useI18n } from 'vue-i18n'
// 登录态 store：下载接口需要 Bearer token（浏览器原生 <img>/<video> 无法携带请求头）
import { auth } from '../../store/auth'

const { t } = useI18n()

// 父窗口传入：待预览文件的路径、名称与类型（image / video）
const props = defineProps({ path: String, name: String, type: String })
// 预览源地址：下载得到的 blob 对象 URL，卸载时释放
const url = ref('')

// --- 动作：带 Bearer 头下载文件并转为 blob 供浏览器直接预览 ---
onMounted(async () => {
  try {
    // 直接 fetch 而非 <img src>：下载接口要求登录态，只能手动带 Authorization 头
    const resp = await fetch('/api/files/download?path=' + encodeURIComponent(props.path), {
      headers: { Authorization: `Bearer ${auth.token}` }
    })
    if (!resp.ok) throw new Error(resp.status)
    const blob = await resp.blob()
    url.value = URL.createObjectURL(blob)
  } catch (e) {
    alert(t('media.loadFailed', { error: e.message }))
  }
})

// 窗口关闭时释放 blob 地址，避免占用内存
onBeforeUnmount(() => {
  if (url.value) URL.revokeObjectURL(url.value)
})
</script>
