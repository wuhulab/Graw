<!--
  InstallCheckAlert.vue — 安装环境不完整提醒弹窗
  作用：面板启动时后端检测宿主环境，若未按「完整宿主机模式」安装（如缺 Docker /
        权限不足），对在线用户全屏弹窗提示缺失项，引导按 README 重新安装后再使用。
  数据：missing 为后端 /system/install-check 返回的缺失项 key 数组；
        全部文案走 i18n（installCheck.*），逐项翻译缺失能力名与详情。
  打开方式：由 App.vue 在检测到缺失项时渲染，点「知道了」关闭。
-->
<template>
  <!-- 安装环境不完整提醒：未按 README 要求的「完整宿主机模式」安装时弹出 -->
  <div class="install-alert-overlay">
    <div class="install-alert-card">
      <div class="alert-head">
        <span class="alert-logo">Graw</span>
        <span class="alert-sub">{{ $t('installCheck.title') }}</span>
      </div>

      <!-- 核心提示：缺少宿主机权限，请重新安装 -->
      <p class="alert-desc danger">
        <OctagonAlert :size="20" />
        <span>{{ $t('installCheck.desc') }}</span>
      </p>

      <!-- 缺失项清单 -->
      <div v-if="missing.length" class="alert-detail">
        <div
          v-for="k in missing"
          :key="k"
          class="detail-row"
          :title="$t(`installCheck.items.${k}.detail`)"
        >
          <span class="dot">•</span>
          <span class="v">{{ $t(`installCheck.items.${k}.label`) }}</span>
        </div>
      </div>

      <!-- 重新安装指引 -->
      <p class="reinstall-hint">{{ $t('installCheck.reinstallHint') }}</p>

      <button class="btn-dismiss" @click="$emit('close')">{{ $t('installCheck.gotIt') }}</button>
    </div>
  </div>
</template>

<script setup>
import { OctagonAlert } from 'lucide-vue-next'   // 警示图标

// 缺失项 key 列表（来自后端 /system/install-check）
defineProps({
  missing: { type: Array, default: () => [] }   // 缺失能力清单，逐项翻译展示
})
defineEmits(['close'])   // 「知道了」按钮 → 通知父组件隐藏弹窗
</script>

<style scoped>
.install-alert-overlay {
  position: fixed;
  inset: 0;
  z-index: 9997;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(120, 80, 10, 0.32);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}

.install-alert-card {
  width: 500px;
  max-width: 94vw;
  padding: 24px 26px 20px;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
  border-top: 4px solid #d97706;
}

.alert-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.alert-logo { font-size: 24px; font-weight: 800; color: #0a3d7a; }
.alert-sub { font-size: 13px; font-weight: 600; color: #b45309; }

.alert-desc {
  display: flex; align-items: flex-start; gap: 8px;
  font-size: 14px; font-weight: 600; color: #92400e;
  background: #fffbeb; border: 1px solid #fde68a;
  border-radius: 8px; padding: 10px 12px; margin: 0 0 12px;
  line-height: 1.6;
}

.alert-detail {
  background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px;
  padding: 10px 12px; margin-bottom: 12px;
}
.detail-row { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.8; }
.detail-row .dot { color: #d97706; flex-shrink: 0; }
.detail-row .v { color: #1d1d1f; word-break: break-all; }

.reinstall-hint {
  margin: 0 0 14px;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.7;
  color: #78350f;
  background: #fffbeb;
  border: 1px dashed #fcd34d;
  border-radius: 8px;
}

.btn-dismiss {
  width: 100%;
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  background: #d97706;
  border: none;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.15s;
}
.btn-dismiss:hover:not(:disabled) { background: #b45309; }
.btn-dismiss:active:not(:disabled) { background: #92400e; }
</style>
