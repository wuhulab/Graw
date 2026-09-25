<!--
  WindowContent.vue — 窗口内容渲染统一出口

  作用：桌面模式（WindowFrame 内）与面板模式（PanelLayout 内容区）共用同一个
        「窗口内容」渲染入口，避免两处重复绑定约 50 个 openXxx / close / dirty 事件。
  实现：外层包一个 wc-host 容器（撑满父级高度），把具体窗口组件（window.component）
        渲染出来；窗口内容事件（openXxx / close / dirty 等）经 contentEvents**显式
        vnode props** 绑定到动态组件（不依赖 $attrs 自动继承——多根/fragment 组件
        无法自动继承 attrs，会导致「新建」一类子窗口打开事件被丢弃）。
  数据：window = 窗口描述对象（见 App.vue openWindow，含 key/title/icon/component/props）。
  打开方式：WindowFrame 的 slot（桌面）或 PanelLayout 的内容区（面板）。
-->
<template>
  <div class="wc-host">
    <!-- 事件以 props 形式显式下发给窗口组件：已声明 emits 的监听器经 vnode props 到达，
         即使窗口组件渲染多根也能被 emit 正常触发 -->
    <component :is="window.component" v-bind="{ ...(window.props || {}), ...contentEvents }" />
  </div>
</template>

<script setup>
defineOptions({ inheritAttrs: false })   // 禁止 attrs 自动 fallthrough，全部走显式 props

// 窗口描述对象（App.vue openWindows 中的项）：component 为窗口组件、props 为附加属性
defineProps({
  window: { type: Object, required: true },
  // 窗口内容事件处理器字典（onOpenXxx / onClose / onDirty 等），由调用方传入并逐一下发
  contentEvents: { type: Object, default: () => ({}) },
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