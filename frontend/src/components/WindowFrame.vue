<template>
  <div
    v-show="!window.minimized"
    class="window"
    :style="{ left: window.x + 'px', top: window.y + 'px', width: window.width + 'px', height: window.height + 'px', zIndex: window.z }"
    @mousedown="$emit('focus')"
  >
    <div
      class="titlebar"
      :class="{ dragging: dragging }"
      @mousedown="startDrag"
      @dblclick="$emit('maximize')"
    >
      <span class="title">{{ $t(window.titleKey, window.titleArgs) }}</span>
      <div class="actions">
        <button class="min" @click.stop="$emit('minimize')" title="Minimize"><Minus :size="14" /></button>
        <button class="max" @click.stop="$emit('maximize')" title="Maximize"><Square :size="12" /></button>
        <button class="close" @click.stop="$emit('close')" title="Close"><X :size="14" /></button>
      </div>
    </div>
    <div class="content">
      <slot />
    </div>
    <div
      v-if="!window.maximized"
      class="resize-handle"
      @mousedown="startResize"
    ></div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { X, Minus, Square } from 'lucide-vue-next'

const props = defineProps({
  window: { type: Object, required: true },
  active: Boolean
})
const emit = defineEmits(['focus', 'close', 'minimize', 'maximize', 'move', 'resize'])

const dragging = ref(false)
let dragStart = null

function startDrag(e) {
  if (props.window.maximized) return
  if (e.target.tagName === 'BUTTON') return
  dragging.value = true
  dragStart = { x: e.clientX, y: e.clientY, ox: props.window.x, oy: props.window.y }
  document.addEventListener('mousemove', onDragMove)
  document.addEventListener('mouseup', onDragEnd)
  e.preventDefault()
}
function onDragMove(e) {
  if (!dragging.value) return
  const dx = e.clientX - dragStart.x
  const dy = e.clientY - dragStart.y
  const nx = Math.max(0, dragStart.ox + dx)
  const ny = Math.max(0, Math.min(window.innerHeight - 80, dragStart.oy + dy))
  emit('move', nx, ny)
}
function onDragEnd() {
  dragging.value = false
  document.removeEventListener('mousemove', onDragMove)
  document.removeEventListener('mouseup', onDragEnd)
}

let resizeStart = null
function startResize(e) {
  resizeStart = { x: e.clientX, y: e.clientY, w: props.window.width, h: props.window.height }
  document.addEventListener('mousemove', onResizeMove)
  document.addEventListener('mouseup', onResizeEnd)
  e.preventDefault()
  e.stopPropagation()
}
function onResizeMove(e) {
  const dw = e.clientX - resizeStart.x
  const dh = e.clientY - resizeStart.y
  emit('resize', Math.max(320, resizeStart.w + dw), Math.max(200, resizeStart.h + dh))
}
function onResizeEnd() {
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
}
</script>

<style scoped>
.window {
  position: absolute;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.content {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}
.resize-handle {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  background: transparent;
}
.resize-handle::after {
  content: '';
  position: absolute;
  right: 3px;
  bottom: 3px;
  width: 8px;
  height: 8px;
  border-right: 2px solid rgba(0, 0, 0, 0.25);
  border-bottom: 2px solid rgba(0, 0, 0, 0.25);
  border-bottom-right-radius: 4px;
}
</style>
