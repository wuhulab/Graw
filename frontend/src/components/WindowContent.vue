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
    <!-- 事件以 props 形式显式下发给窗口组件：只下发该组件**已声明 emits** 的监听器。
         未声明的监听器会落进 attrs，多根（fragment）组件无法继承，Vue 每次渲染都会打
         「Extraneous non-emits event listeners」警告；按 emits 过滤即可消除该刷屏。 -->
    <component :is="window.component" v-bind="{ ...(window.props || {}), ...boundEvents }" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'   // 计算属性 + 响应式引用（异步组件解析后刷新可用事件）

defineOptions({ inheritAttrs: false })   // 禁止 attrs 自动 fallthrough，全部走显式 props

// 窗口描述对象（App.vue openWindows 中的项）：component 为窗口组件、props 为附加属性
const props = defineProps({
  window: { type: Object, required: true },
  // 窗口内容事件处理器字典（onOpenXxx / onClose / onDirty 等），由调用方传入并按需下发
  contentEvents: { type: Object, default: () => ({}) },
})

// 目标窗口组件声明的 emits（数组或对象）；异步组件解析完成前为 null
const declaredEmits = ref(null)

/**
 * 解析窗口组件声明的 emits。
 *
 * App.vue 里的窗口组件是 defineAsyncComponent 包装层：首次渲染时真实组件可能还没解析完，
 * 包装层上的 __asyncResolved 只在解析后才可用，且它不是响应式数据——若在计算属性里直接读，
 * 解析完成后也不会重新求值（事件会一直下发不出去）。因此这里用 watch + 异步等待把结果写进
 * 响应式 ref，解析完成即触发重新渲染、把事件补上。
 */
watch(
  () => props.window?.component,
  async (comp) => {
    declaredEmits.value = null
    if (!comp) return
    const resolved = comp.__asyncResolved   // 异步组件已解析时直接取真实组件
    if (resolved) {
      declaredEmits.value = resolved.emits || null
      return
    }
    if (typeof comp.__asyncLoader === 'function') {
      // 尚未解析：复用包装层内部的加载 Promise（同一份缓存，不会重复请求模块）
      try {
        const real = await comp.__asyncLoader()
        declaredEmits.value = (real && real.emits) || null
      } catch {
        declaredEmits.value = null
      }
      return
    }
    declaredEmits.value = comp.emits || null   // 同步组件：直接读自身 emits
  },
  { immediate: true },
)

/**
 * 只把「目标窗口组件已声明 emits」的事件处理器下发下去。
 *
 * 背景：contentEvents 汇总了 App.vue 里全部窗口的 openXxx / close 处理器（约 50 个）。
 * 若原样展开到动态组件，组件没声明的事件会落进 attrs；单根组件尚可静默 fallthrough，
 * 多根（fragment）组件无法继承 attrs，Vue 会在每次渲染时警告
 * 「Extraneous non-emits event listeners (...) because component renders fragment ...」，
 * 打开这类窗口就会持续刷屏。
 *
 * 事件名按 Vue 的规则做 camelize + onXxx 映射（例如 emits: ['open-site-edit'] 对应 onOpenSiteEdit）。
 * 未声明 emits 的组件不下发任何事件（项目内窗口均显式声明，不依赖 attrs 透传）。
 */
const boundEvents = computed(() => {
  const events = props.contentEvents || {}
  const declared = declaredEmits.value
  if (!declared) return {}
  const names = Array.isArray(declared) ? declared : Object.keys(declared)
  const allowed = new Set(
    names.map((name) => {
      const camel = String(name).replace(/-(\w)/g, (_, c) => c.toUpperCase())
      return `on${camel.charAt(0).toUpperCase()}${camel.slice(1)}`
    }),
  )
  const out = {}
  for (const [key, handler] of Object.entries(events)) {
    if (allowed.has(key)) out[key] = handler
  }
  return out
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