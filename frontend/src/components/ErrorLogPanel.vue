<!--
  ErrorLogPanel.vue — 全局错误日志浮窗。

  业务背景：面板把接口错误、Vue 运行时异常、未处理的 Promise、window 报错
  统一收进 store/errorLog.js；本组件负责以浮窗呈现，支持：
    - 关闭：只收起浮窗，日志仍保留；
    - 复制：把完整日志（含页面 / 浏览器 / 版本）写入剪贴板；
    - 提交 Issue：带着日志正文跳到项目 GitHub 新建 Issue 页面。

  实现约束：Teleport 到 body 顶层，避免被桌面窗口 / 面板内容区裁剪；
  浮窗自身不阻塞页面交互（无全屏遮罩），z-index 高于窗口与任务栏。
-->
<template>
  <Teleport to="body">
    <div v-if="errorLog.visible" class="err-panel" role="dialog" aria-modal="false">
      <div class="err-head">
        <span class="err-dot"></span>
        <span class="err-title">{{ $t('errorLog.title') }}</span>
        <span class="err-count">{{ errorLog.items.length }}</span>
        <button class="err-close" :title="$t('errorLog.close')" @click="dismissErrorLog">×</button>
      </div>

      <div class="err-list">
        <div v-for="it in errorLog.items" :key="it.id" class="err-item">
          <div class="err-meta">
            <span class="err-time">{{ it.time }}</span>
            <span class="err-source">{{ it.source }}</span>
            <span v-if="it.status" class="err-status">{{ it.status }}</span>
            <span v-if="it.count > 1" class="err-repeat">×{{ it.count }}</span>
          </div>
          <div class="err-msg">{{ it.message }}</div>
          <div v-if="it.method || it.url" class="err-req">{{ it.method }} {{ it.url }}</div>
          <pre v-if="it.detail" class="err-detail">{{ it.detail }}</pre>
        </div>
        <div v-if="!errorLog.items.length" class="err-empty">{{ $t('errorLog.empty') }}</div>
      </div>

      <div class="err-foot">
        <span class="err-hint">{{ $t('errorLog.hint') }}</span>
        <button class="err-btn" @click="copyAll">{{ copied ? $t('errorLog.copied') : $t('errorLog.copy') }}</button>
        <button class="err-btn primary" @click="reportIssue">{{ $t('errorLog.report') }}</button>
        <button class="err-btn" @click="dismissErrorLog">{{ $t('errorLog.close') }}</button>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { errorLog, dismissErrorLog, errorLogText, issueURL } from '../store/errorLog'

const copied = ref(false)
let copiedTimer = null

// 剪贴板写入：优先安全上下文 API，失败时降级为临时 textarea + execCommand
async function writeClipboard(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch (e) {
    // 权限被拒 / 非安全上下文：继续走降级路径
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed; left:-9999px; top:0; opacity:0;'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch (e) {
    return false
  }
}

// 复制全部日志：按钮短暂显示「已复制」，给用户明确反馈
async function copyAll() {
  const ok = await writeClipboard(errorLogText())
  if (!ok) return
  copied.value = true
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { copied.value = false }, 1800)
}

// 提交 Issue：先复制一份完整日志（Issue 正文字数有限），再新窗口打开预填页面
async function reportIssue() {
  await writeClipboard(errorLogText())
  try {
    window.open(issueURL(), '_blank', 'noopener')
  } catch (e) {
    // 弹窗被拦截时退化为当前页跳转
    window.location.href = issueURL()
  }
}

onBeforeUnmount(() => {
  if (copiedTimer) clearTimeout(copiedTimer)
})
</script>

<style scoped>
.err-panel {
  position: fixed;
  right: 18px;
  bottom: 80px;
  width: min(560px, calc(100vw - 24px));
  max-height: min(62vh, 560px);
  display: flex;
  flex-direction: column;
  background: rgba(28, 28, 32, 0.96);
  color: #e6e6e6;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 12px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  z-index: 99999;
  overflow: hidden;
  font-size: 12px;
}
.err-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(255, 59, 48, 0.12);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.err-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ff453a;
  box-shadow: 0 0 8px rgba(255, 69, 58, 0.8);
  flex: none;
}
.err-title { font-weight: 700; }
.err-count {
  font-size: 11px;
  color: #ffb4ae;
  background: rgba(255, 69, 58, 0.18);
  border-radius: 999px;
  padding: 1px 8px;
}
.err-close {
  margin-left: auto;
  border: none;
  background: transparent;
  color: #d4d4d4;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
}
.err-close:hover { color: #fff; }
.err-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 8px 12px;
}
.err-item {
  padding: 8px 0;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.1);
}
.err-item:last-child { border-bottom: none; }
.err-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  color: #9a9a9f;
  font-size: 11px;
  flex-wrap: wrap;
}
.err-time { font-family: Consolas, monospace; }
.err-source {
  color: #8ab4f8;
  background: rgba(138, 180, 248, 0.12);
  border-radius: 4px;
  padding: 0 5px;
}
.err-status {
  color: #ffb4ae;
  background: rgba(255, 69, 58, 0.12);
  border-radius: 4px;
  padding: 0 5px;
}
.err-repeat { color: #ffd60a; }
.err-msg {
  color: #f2f2f7;
  line-height: 1.5;
  word-break: break-word;
}
.err-req {
  margin-top: 3px;
  color: #8e8e93;
  font-family: Consolas, monospace;
  font-size: 11px;
  word-break: break-all;
}
.err-detail {
  margin: 5px 0 0;
  padding: 6px 8px;
  max-height: 120px;
  overflow: auto;
  background: rgba(0, 0, 0, 0.28);
  border-radius: 6px;
  color: #b8b8bd;
  font-family: Consolas, monospace;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-word;
}
.err-empty { color: #8e8e93; padding: 18px 4px; text-align: center; }
.err-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.03);
}
.err-hint {
  flex: 1;
  min-width: 0;
  color: #8e8e93;
  font-size: 11px;
  line-height: 1.4;
}
.err-btn {
  border: 1px solid rgba(255, 255, 255, 0.18);
  background: rgba(255, 255, 255, 0.08);
  color: #f2f2f7;
  border-radius: 7px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.err-btn:hover { background: rgba(255, 255, 255, 0.16); }
.err-btn.primary {
  background: #0a84ff;
  border-color: #0a84ff;
}
.err-btn.primary:hover { background: #006ee6; }
@media (max-width: 767px) {
  .err-panel { right: 8px; left: 8px; width: auto; bottom: 72px; max-height: 60vh; }
  .err-hint { display: none; }
}
</style>
