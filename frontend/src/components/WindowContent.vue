<!--
  WindowContent.vue — 窗口内容渲染统一出口

  作用：桌面模式（WindowFrame 内）与面板模式（PanelLayout 内容区）共用同一个
        「窗口内容」渲染入口，避免两处重复绑定约 50 个 openXxx / close / dirty 事件。
  实现：外层包一个 wc-host 容器（撑满父级高度），把具体窗口组件（window.component）
        渲染出来，props 用 window.props，事件走显式 v-on="$attrs" 转发（不依赖自动
        fallthrough —— SettingsWindow 等多根组件不会自动继承 attrs）。
  数据：window = 窗口描述对象（见 App.vue openWindow，含 key/title/icon/component/props）。
  打开方式：WindowFrame 的 slot 或 PanelLayout 的内容区。
-->
<template>
  <div class="wc-host">
    <component :is="window.component" v-bind="window.props || {}" v-on="$attrs" />
  </div>
</template>

<script setup>
// 窗口描述对象（App.vue openWindows 中的项）：component 为窗口组件、props 为附加属性
defineProps({
  window: { type: Object, required: true },
})
</script>

<style scoped>
/* 高度占满父级（WindowFrame 内容区 / 面板内容区），保证内部窗口组件可正常高度布局 */
.wc-host {
  width: 100%;
  height: 100%;
  overflow: hidden;
}
</style>