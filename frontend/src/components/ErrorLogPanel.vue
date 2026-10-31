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
        <span class="err-title">{{ $t('errorLog.title') }}</span>
        <span class="ui-badge danger">{{ errorLog.items.length }}</span>
        <button
          class="err-mode"
          :class="{ on: errorLog.programOnly }"
          :title="$t('errorLog.programOnlyHint')"
          @click="toggleProgramOnly"
        >{{ errorLog.programOnly ? $t('errorLog.programOnlyOn') : $t('errorLog.programOnlyOff') }}</button>
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
        <button class="ui-btn mini" @click="copyAll">{{ copied ? $t('errorLog.copied') : $t('errorLog.copy') }}</button>
        <button class="ui-btn mini primary" @click="reportIssue">{{ $t('errorLog.report') }}</button>
        <button class="ui-btn mini ghost" @click="dismissErrorLog">{{ $t('errorLog.close') }}</button>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { errorLog, dismissErrorLog, errorLogText, issueURL, setProgramOnly } from '../store/errorLog'

const copied = ref(false)
let copiedTimer = null

// 切换「仅程序性错误弹窗」：接口/网络错误仍记录，只是不再自动弹出浮窗。
function toggleProgramOnly() {
  setProgramOnly(!errorLog.programOnly)
}

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
/* 沿用面板通用设计 tokens（白色卡片 / 细边框 / 深色主按钮），
   不再使用高对比暗色玻璃拟态，避免与面板其余界面风格割裂。 */
.err-panel {
  position: fixed;
  right: 18px;
  bottom: 80px;
  width: min(540px, calc(100vw - 24px));
  max-height: min(62vh, 540px);
  display: flex;
  flex-direction: column;
  background: var(--ui-bg);
  color: var(--ui-text);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-md);
  box-shadow: var(--ui-shadow-md);
  z-index: 99999;
  overflow: hidden;
  font-size: 12.5px;
}
.err-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--ui-border);
}
.err-title { font-weight: 600; }
.err-mode {
  margin-left: auto;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  background: transparent;
  color: var(--ui-text-secondary);
  font-size: 11.5px;
  line-height: 22px;
  cursor: pointer;
  white-space: nowrap;
}
.err-mode:hover { background: var(--ui-bg-muted); color: var(--ui-text); }
.err-mode.on {
  color: var(--ui-primary, #2563eb);
  border-color: var(--ui-primary, #2563eb);
}
.err-close {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  border-radius: 6px;
  color: var(--ui-text-tertiary);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}
.err-close:hover { background: var(--ui-bg-muted); color: var(--ui-text); }
.err-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 2px 14px;
}
.err-item {
  padding: 10px 0;
  border-bottom: 1px solid var(--ui-border);
}
.err-item:last-child { border-bottom: none; }
.err-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  color: var(--ui-text-tertiary);
  font-size: 11.5px;
  flex-wrap: wrap;
}
.err-time { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
.err-source { color: var(--ui-text-secondary); }
.err-status {
  color: var(--ui-danger-fg);
  background: var(--ui-danger-bg);
  border-radius: 4px;
  padding: 0 5px;
}
.err-repeat { color: var(--ui-warn-fg); }
.err-msg {
  color: var(--ui-text);
  line-height: 1.55;
  word-break: break-word;
}
.err-req {
  margin-top: 3px;
  color: var(--ui-text-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  word-break: break-all;
}
.err-detail {
  margin: 6px 0 0;
  padding: 8px 10px;
  max-height: 120px;
  overflow: auto;
  background: var(--ui-bg-muted);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-sm);
  color: var(--ui-text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  white-space: pre-wrap;
  word-break: break-word;
}
.err-empty { color: var(--ui-text-tertiary); padding: 24px 4px; text-align: center; }
.err-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid var(--ui-border);
  background: var(--ui-bg-muted);
}
.err-hint {
  flex: 1;
  min-width: 0;
  color: var(--ui-text-tertiary);
  font-size: 11.5px;
  line-height: 1.4;
}
@media (max-width: 767px) {
  .err-panel { right: 8px; left: 8px; width: auto; bottom: 72px; max-height: 60vh; }
  .err-hint { display: none; }
}
</style>
