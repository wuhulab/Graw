<!--
  文件编辑窗口（后端 /api/files 模块）
  作用：以纯文本方式查看并编辑服务器上的单个文件，保存即写回宿主机文件。
  后端模块：/api/files（read 读取内容、write 写回内容）。
  关键状态：localContent（编辑区文本）、savedContent（上次保存内容，用于脏检测）、isDirty（是否有未保存改动）。
  打开方式：文件管理器双击文本文件、或右键「编辑」时由父窗口传入 path/content 打开。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 工具栏：文件路径（带脏标记 *）+ 保存/关闭 -->
    <div class="toolbar">
      <strong style="font-family:monospace; font-size:12px;">{{ path }}
        <span v-if="isDirty" style="color:#ff3b30; font-weight:600;"> *</span>
      </strong>
      <button class="btn" style="margin-left:auto;" @click="save">{{ $t('editor.save') }}</button>
      <button class="btn" @click="emit('close')">{{ $t('editor.close') }}</button>
    </div>
    <textarea v-model="localContent" spellcheck="false" style="flex:1;border:none;outline:none;padding:8px;font-family:Consolas,monospace;font-size:12px;resize:none;"></textarea>
  </div>
</template>

<script setup>
// 响应式、计算属性与侦听
import { ref, computed, watch } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 文件管理 API：write 写回文件内容
import { filesApi } from '../../api'

const { t } = useI18n()

// 父窗口传入：待编辑文件的路径与初始内容
const props = defineProps({ path: String, content: String })
// 通知父窗口：关闭、以及脏状态变化（用于标题提示未保存）
const emit = defineEmits(['close', 'dirty'])
// 编辑区当前文本（可修改）
const localContent = ref(props.content || '')
// 上次保存的内容：与 localContent 比对判断是否有改动
const savedContent = ref(props.content || '')

// 是否有未保存改动（内容偏离已保存版本即视为脏）
const isDirty = computed(() => localContent.value !== savedContent.value)
// 脏状态变化时上报父窗口（用于窗口标题显示 * 或禁止关闭提示）
watch(isDirty, (v) => emit('dirty', v))

// --- 动作：把编辑区内容写回服务器文件 ---
async function save() {
  try {
    await filesApi.write(props.path, localContent.value)   // 调用 /api/files/write
    savedContent.value = localContent.value   // 保存成功后更新基线，清除脏标记
  } catch (e) {
    alert(t('editor.saveFailed', { error: e.response?.data?.detail || e.message }))
  }
}
</script>
