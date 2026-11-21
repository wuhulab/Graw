<!--
  WindowTabNav — 窗口内左侧竖排页签导航（统一外观）

  业务背景：
    面板内不少应用窗口自带多个子视图（网站/SSL、系统/登录/审计日志、容器/镜像/卷…）。
    过去各窗口各写一套顶部横向 tab，样式与交互不统一；这里提供与「设置」窗口左侧
    分类导航完全一致的一套竖排小标签，供各窗口直接复用。

  用法：
    <WindowTabNav v-model="mode" :items="tabs">
      <div class="your-window">…原有窗口内容…</div>
    </WindowTabNav>

  items 结构：
    { key: string|number, label: string, icon?: Component, badge?: string|number }
    - key   当前页签值（与 v-model 对应）
    - label 已翻译的显示文案
    - icon  可选 lucide 图标组件（建议 size 由组件统一为 15）
    - badge 可选计数（如备份任务数、告警记录数）

  布局约定：
    组件本身是 flex 横向容器：左侧固定宽度导航 + 右侧自适应内容区。
    右侧内容区是 flex 纵向容器，原有窗口根元素保持 height:100% 即可填满。
-->
<template>
  <div class="wt-shell">
    <aside class="wt-nav">
      <button
        v-for="it in items"
        :key="it.key"
        class="wt-item"
        :class="{ active: it.key === modelValue }"
        :title="it.label"
        @click="select(it.key)"
      >
        <component :is="it.icon" v-if="it.icon" :size="15" class="wt-icon" />
        <span class="wt-label">{{ it.label }}</span>
        <span v-if="it.badge" class="wt-badge">{{ it.badge }}</span>
      </button>
    </aside>
    <div class="wt-main"><slot /></div>
  </div>
</template>

<script setup>
// 页签导航是纯受控组件：当前值由父窗口持有，点击只向外抛出变更事件
defineProps({
  modelValue: { type: [String, Number], default: '' },
  items: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue'])

function select(key) {
  emit('update:modelValue', key)
}
</script>

<style scoped>
/* 外壳：左侧导航 + 右侧内容，铺满窗口内容区 */
.wt-shell {
  display: flex;
  height: 100%;
  min-height: 0;
}
/* 与设置窗口 .settings-nav 保持一致：浅色侧栏、窄宽度、竖排按钮 */
.wt-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 148px;
  flex-shrink: 0;
  padding: 12px 8px;
  background: var(--g-sidebar);
  border-right: 1px solid var(--g-border-soft);
  overflow-y: auto;
  box-sizing: border-box;
}
.wt-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--g-text-secondary);
  font-size: 12.5px;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.wt-item:hover { background: var(--g-hover); color: var(--g-text); }
.wt-item.active { background: var(--g-tab-active-bg); color: var(--g-tab-active-fg); font-weight: 600; }
.wt-icon { flex-shrink: 0; }
.wt-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 计数角标：未选中时低调，选中时半透明白底避免抢色 */
.wt-badge {
  flex-shrink: 0;
  padding: 2px 6px;
  border-radius: 999px;
  background: var(--g-surface-muted);
  color: var(--g-text-muted);
  font-size: 10px;
  line-height: 1.2;
}
.wt-item.active .wt-badge { background: var(--g-tab-active-badge-bg); color: var(--g-tab-active-badge-fg); }
/* 右侧内容区：纵向 flex，原有窗口根元素的 height:100% 才能撑满 */
.wt-main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}
</style>
