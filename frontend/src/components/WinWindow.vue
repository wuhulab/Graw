<!--
  WinWindow.vue — 简化版窗口外壳（Win7 风格）
  作用：另一种窗口外框实现，相比 WindowFrame 更轻量：标题栏拖拽 + 最小化 / 最大化 /
        关闭按钮，内容经 <slot> 注入。窗口几何直接改写 desktop 中的 model 对象。
  数据：窗口状态来自 desktop 单例（model 即 store 中的窗口对象，直接响应式修改）。
  打开方式：由桌面 / 任务栏按窗口列表渲染。
-->
<template>
  <div
    v-show="!model.minimized"
    class="win7-window"
    :class="{ maximized: model.maximized }"
    :style="styleObj"
    @mousedown="activate"
  >
    <div
      class="win7-titlebar"
      :class="{ inactive: !model.active }"
      @mousedown.prevent="startDrag"
      @dblclick="desktop.toggleMaximize(model.id)"
    >
      <span class="title-text">{{ model.title }}</span>
      <span class="title-btns">
        <span class="win7-btn" @click.stop="desktop.minimize(model.id)">&#8212;</span>
        <span class="win7-btn" @click.stop="desktop.toggleMaximize(model.id)"><component :is="model.maximized ? Copy : Square" :size="12" /></span>
        <span class="win7-btn close" @click.stop="desktop.close(model.id)"><X :size="12" /></span>
      </span>
    </div>
    <div class="win7-content">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'                  // Vue 响应式与计算属性
import { desktop } from '../store/desktop.js'        // 桌面窗口状态单例

const props = defineProps({ model: Object })

const isDragging = ref(false)
const dragOffset = ref({ x: 0, y: 0 })

// --- 窗口几何样式（普通 / 最大化两种布局） ---
const styleObj = computed(() => {
  if (props.model.maximized) {
    // 最大化时铺满视口，底部留 40px 给任务栏
    return {
      left: '0px',
      top: '0px',
      width: '100%',
      height: 'calc(100% - 40px)',
      zIndex: props.model.zIndex,
    }
  }
  return {
    left: props.model.x + 'px',
    top: props.model.y + 'px',
    width: props.model.width + 'px',
    height: props.model.height + 'px',
    zIndex: props.model.zIndex,
  }
})

function activate() {
  desktop.activate(props.model.id)
}

// --- 拖拽移动（直接改写 model 坐标） ---
function startDrag(e) {
  if (props.model.maximized) return             // 最大化时不响应拖拽
  activate()                                     // 拖拽即先把本窗口置于前台
  isDragging.value = true
  dragOffset.value = { x: e.clientX - props.model.x, y: e.clientY - props.model.y }
  // 监听挂在 window 上，鼠标移出窗口也能继续拖拽
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
}

function onDrag(e) {
  if (!isDragging.value) return
  // model 是 store 中的响应式对象，直接改 x/y 即实时生效
  props.model.x = e.clientX - dragOffset.value.x
  props.model.y = e.clientY - dragOffset.value.y
}

function stopDrag() {
  isDragging.value = false
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', stopDrag)
}
</script>

<style scoped>
.maximized {
  border-radius: 0 !important;
}
.maximized .win7-titlebar {
  border-radius: 0 !important;
}
.title-text {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  max-width: calc(100% - 90px);
}
.title-btns {
  display: flex;
  align-items: center;
}
</style>
