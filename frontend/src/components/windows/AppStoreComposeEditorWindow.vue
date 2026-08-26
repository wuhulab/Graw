<!--
  AppStoreComposeEditorWindow.vue — 应用商店 compose 编辑器
  ==========================================================
  业务作用：
    编辑某个应用（appId）的 docker-compose 配置原文。本窗口不直接调用后端，
    而是把用户修改后的 compose 文本写入跨窗口共享状态（appStoreComposeState），
    真正发起安装的是 AppStoreInstallWindow，在用户「确认安装」时读取这份内容。
  后端模块：
    本窗口无独立后端调用；compose 文本最终由 /api/appstore 安装流程消费。
  关键状态：
    - localContent  编辑器当前文本（初始值来自 props.compose）
    - savedContent  上次保存时的快照，用于脏检查 isDirty
    - saved         是否已写入共享状态（顶部显示「已应用」）
    - appStoreComposeState（store）跨窗口存放 {appId, content, rev}
  打开方式：
    由 AppStoreInstallWindow 的「编辑 compose」按钮打开，props 传入 appId 与
    当前 compose 文本；改动仅在本窗口保存后才对安装流程可见。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 顶部工具栏：与标准文件编辑器一致 -->
    <div class="toolbar">
      <strong style="font-family:monospace; font-size:12px;">
        {{ $t('appcompose.title', { appId }) }}
        <span v-if="isDirty" style="color:#ff3b30; font-weight:600;"> *</span>
      </strong>
      <span v-if="saved" class="saved-hint">{{ $t('appcompose.applied') }}</span>
      <button class="btn" style="margin-left:auto;" @click="save">{{ $t('appcompose.save') }}</button>
      <button class="btn" @click="emit('close')">{{ $t('appcompose.close') }}</button>
    </div>
    <textarea v-model="localContent" spellcheck="false" class="editor-area"></textarea>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'   // ref/computed：编辑器文本与脏检查所需的响应式能力
import { appStoreComposeState } from '../../store/appStoreCompose'   // 跨窗口共享状态：编辑器写入、安装窗口读取 compose 的中转

const props = defineProps({
  appId: String,   // 当前编辑的应用标识（标题展示 + 保存时关联共享状态）
  compose: String  // 父窗口传入的 compose 初始文本
})
const emit = defineEmits(['close'])   // 对外仅暴露 close：通知桌面关闭本窗口

const localContent = ref(props.compose || '')   // 编辑器当前文本（空串兜底，避免 null 传入 textarea）
const savedContent = ref(props.compose || '')   // 上次保存时的快照，用于脏检查
const saved = ref(false)                        // 是否已写入共享状态（顶部显示「已应用」）

// 本地文本与保存快照不一致即视为有未保存修改（标题栏出现红色 *）
const isDirty = computed(() => localContent.value !== savedContent.value)

// --- 保存 compose 到共享状态（安装窗口确认安装时读取） ---
function save() {
  // 写入跨窗口共享状态，供安装窗口在确认安装时读取
  appStoreComposeState.appId = props.appId
  appStoreComposeState.content = localContent.value
  appStoreComposeState.rev++   // 递增版本号，让安装窗口感知到内容已更新
  savedContent.value = localContent.value
  saved.value = true
}
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
.saved-hint { color: #047857; font-size: 11.5px; }
.editor-area { flex: 1; border: none; outline: none; padding: 8px; font-family: Consolas, monospace; font-size: 12px; resize: none; }
</style>
