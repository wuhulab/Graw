<!--
  Docker 配置文件编辑器窗口（后端 /api/docker 模块）
  作用：以纯文本方式直接查看并编辑 Docker 引擎守护配置（daemon.json 原始内容），
        保存即写回宿主机配置文件。
  后端模块：/api/docker（config 读取原始配置、saveConfigRaw 直接保存原始文本）。
  关键状态：content（编辑区文本）、configPath（配置文件路径）、msg/error（保存/加载提示）。
  打开方式：Docker 窗口「配置」视图里的「打开 Docker 配置文件」按钮；仅管理员可写。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <span style="color:#0a3d7a; font-weight:600;">{{ $t('dockerconfig.title') }}</span>
      <span style="font-size:11px; color:#888; font-family:Consolas,monospace;">{{ configPath || $t('common.loading') }}</span>
      <button class="btn" style="margin-left:auto;" @click="load">{{ $t('dockerconfig.reload') }}</button>
      <button class="btn primary" @click="save">{{ $t('dockerconfig.save') }}</button>
      <button class="btn" @click="$emit('close')">{{ $t('dockerconfig.close') }}</button>
    </div>
    <textarea ref="editor" v-model="content" class="cfg-editor" spellcheck="false"
      :placeholder="error || $t('dockerconfig.loadPlaceholder')"></textarea>
    <div v-if="msg" class="msg" :class="{ err: msgErr }">{{ msg }}</div>
  </div>
</template>

<script setup>
// 引入生命周期钩子
import { ref, onMounted } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// Docker API：config 读取、saveConfigRaw 直接保存原始配置文本
import { dockerApi } from '../../api'

const { t } = useI18n()

// 通知父窗口关闭本编辑器窗口
const emit = defineEmits(['close'])

const content = ref('')        // 编辑区文本（daemon.json 原始内容）
const configPath = ref('')    // 配置文件在宿主机的绝对路径（仅展示）
const msg = ref('')            // 保存结果提示
const msgErr = ref(false)      // 保存是否失败（注意：此处为 ref 但被当作值直接赋值，见 save 内）
const error = ref('')          // 加载失败提示

// --- 动作：读取 Docker 引擎配置文件原始内容 ---
async function load() {
  msg.value = ''
  try {
    const cfg = await dockerApi.config()       // 调用 /api/docker/config 取原始配置
    configPath.value = cfg.config_path || ''
    content.value = cfg.content || ''
    error.value = ''
  } catch (e) {
    error.value = t('dockerconfig.loadFailed', { error: e.response?.data?.detail || e.message })
  }
}

// --- 动作：把编辑区原始文本保存回宿主机配置文件 ---
async function save() {
  msg.value = ''
  try {
    const r = await dockerApi.saveConfigRaw(content.value)   // 调用 /api/docker/config/raw
    msgErr = false
    msg.value = t('dockerconfig.saved', { path: r.config_path })
  } catch (e) {
    msgErr = true
    msg.value = t('dockerconfig.saveFailed', { error: e.response?.data?.detail || e.message })
  }
}

// 窗口挂载即加载配置文件内容
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
