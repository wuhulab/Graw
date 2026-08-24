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
import { computed, ref } from 'vue'
import { desktop } from '../store/desktop.js'

const props = defineProps({ model: Object })

const isDragging = ref(false)
const dragOffset = ref({ x: 0, y: 0 })

const styleObj = computed(() => {
  if (props.model.maximized) {
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

function startDrag(e) {
  if (props.model.maximized) return
  activate()
  isDragging.value = true
  dragOffset.value = { x: e.clientX - props.model.x, y: e.clientY - props.model.y }
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
}

function onDrag(e) {
  if (!isDragging.value) return
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
