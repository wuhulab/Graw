<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#f5f5f7;">
    <div style="flex:1; overflow:auto; padding:16px; display:flex; flex-direction:column; gap:14px;">
      <div class="block">
        <div class="block-title">账号</div>
        <button class="btn" @click="emit('openUsers')" :disabled="!isAdmin()">打开账号管理</button>
        <span v-if="!isAdmin()" style="font-size:11px;color:#6e6e73;margin-left:8px;">仅管理员可用</span>
      </div>
      <div class="block">
        <div class="block-title">面板</div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.showTaskbarText" />
            <span>底栏显示详细文字</span>
          </label>
        </div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.taskbarTextOnly" />
            <span>底栏只显示文字（隐藏图标）</span>
          </label>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { settings } from '../../store/settings'
import { isAdmin } from '../../store/auth'

const emit = defineEmits(['openUsers'])
</script>

<style scoped>
.block {
  background: #fff;
  border-radius: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(0,0,0,0.06);
}
.block-title {
  font-size: 12px;
  font-weight: 600;
  color: #1d1d1f;
  margin-bottom: 10px;
}
.row {
  display: flex;
  align-items: center;
  padding: 6px 0;
  font-size: 12px;
}
.switch-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: #1d1d1f;
}
.switch-label input {
  width: 16px;
  height: 16px;
  cursor: pointer;
}
</style>
