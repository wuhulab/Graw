<!--
  FirstRunSetup.vue — 首次安装引导（账户设置完成后强制选择主题与界面形态）

  业务背景：面板首次安装后会强制修改初始管理员密码；改密成功进入面板时，
  再要求用户完成两项本机偏好选择：
    1. 界面主题：浅色 / 暗色；
    2. 界面形态：新一代理论桌面 / 传统服务器面板。
  选择写入 settings store（localStorage 持久化），完成后清除待引导标记，
  后续启动不再弹出。用户之后可随时在「设置 → 通用与外观」中修改。

  打开方式：App.vue 在登录后检测 localStorage 的 graw_onboarding_pending 标记，
  为 1 时渲染本组件并覆盖整个界面（不可跳过，必须两项都选择后才能进入面板）。
-->
<template>
  <div class="firstrun-mask">
    <div class="firstrun-card" role="dialog" aria-modal="true">
      <div class="firstrun-head">
        <div class="firstrun-logo">Graw</div>
        <div class="firstrun-title">{{ $t('onboarding.title') }}</div>
        <div class="firstrun-desc">{{ $t('onboarding.desc') }}</div>
      </div>

      <!-- 主题选择 -->
      <div class="firstrun-section">
        <div class="firstrun-label">{{ $t('onboarding.themeTitle') }}</div>
        <div class="firstrun-options">
          <button
            class="firstrun-option"
            :class="{ active: themeChoice === 'light' }"
            @click="themeChoice = 'light'"
          >
            <Sun :size="22" />
            <span class="opt-name">{{ $t('onboarding.themeLight') }}</span>
            <span class="opt-desc">{{ $t('onboarding.themeLightHint') }}</span>
          </button>
          <button
            class="firstrun-option"
            :class="{ active: themeChoice === 'dark' }"
            @click="themeChoice = 'dark'"
          >
            <Moon :size="22" />
            <span class="opt-name">{{ $t('onboarding.themeDark') }}</span>
            <span class="opt-desc">{{ $t('onboarding.themeDarkHint') }}</span>
          </button>
        </div>
      </div>

      <!-- 界面形态选择 -->
      <div class="firstrun-section">
        <div class="firstrun-label">{{ $t('onboarding.modeTitle') }}</div>
        <div class="firstrun-options">
          <button
            class="firstrun-option"
            :class="{ active: modeChoice === 'desktop' }"
            @click="modeChoice = 'desktop'"
          >
            <Monitor :size="22" />
            <span class="opt-name">{{ $t('onboarding.modeDesktop') }}</span>
            <span class="opt-desc">{{ $t('onboarding.modeDesktopHint') }}</span>
          </button>
          <button
            class="firstrun-option"
            :class="{ active: modeChoice === 'panel' }"
            @click="modeChoice = 'panel'"
          >
            <PanelLeft :size="22" />
            <span class="opt-name">{{ $t('onboarding.modePanel') }}</span>
            <span class="opt-desc">{{ $t('onboarding.modePanelHint') }}</span>
          </button>
        </div>
      </div>

      <div class="firstrun-actions">
        <span class="firstrun-hint">{{ ready ? '' : $t('onboarding.required') }}</span>
        <button class="firstrun-confirm" :disabled="!ready" @click="confirm">{{ $t('onboarding.confirm') }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'                 // 选择状态与按钮可用性
import { Sun, Moon, Monitor, PanelLeft } from 'lucide-vue-next'
import { settings } from '../store/settings'        // 主题 / 界面形态偏好（自动落盘）

const emit = defineEmits(['done'])

// 两项都未预选：明确要求用户自己做出选择（而不是默默沿用默认值）
const themeChoice = ref('')
const modeChoice = ref('')
const ready = computed(() => !!themeChoice.value && !!modeChoice.value)

// 完成引导：写入偏好、清除待引导标记，并通知 App 关闭覆盖层
function confirm() {
  if (!ready.value) return
  settings.theme = themeChoice.value
  settings.panelMode = modeChoice.value === 'panel'
  try {
    localStorage.removeItem('graw_onboarding_pending')
    localStorage.setItem('graw_onboarding_done', '1')
  } catch (e) {
    // 隐私模式 / 存储被禁用：本次仍按用户选择进入面板，只是下次可能再次提示
  }
  emit('done')
}
</script>

<style scoped>
.firstrun-mask {
  position: fixed;
  inset: 0;
  z-index: 12000;               /* 高于 ShunX 强制设置（9999）与普通窗口 */
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: var(--g-overlay);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}
.firstrun-card {
  width: 640px;
  max-width: 100%;
  max-height: 92vh;
  overflow-y: auto;
  padding: 26px 28px 22px;
  background: var(--g-surface);
  border: 1px solid var(--g-border);
  border-radius: 18px;
  box-shadow: 0 28px 80px rgba(0, 0, 0, 0.35);
  color: var(--g-text);
}
.firstrun-logo {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 1px;
  color: #0a84ff;
  margin-bottom: 4px;
}
.firstrun-title { font-size: 22px; font-weight: 700; }
.firstrun-desc { margin-top: 6px; font-size: 12.5px; line-height: 1.6; color: var(--g-text-muted); }
.firstrun-section { margin-top: 20px; }
.firstrun-label { font-size: 12px; font-weight: 600; color: var(--g-text-secondary); margin-bottom: 8px; }
.firstrun-options { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.firstrun-option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 14px;
  border: 1px solid var(--g-border);
  border-radius: 12px;
  background: var(--g-surface-soft);
  color: var(--g-text-secondary);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, color 0.15s;
}
.firstrun-option:hover { border-color: rgba(10, 132, 255, 0.45); }
.firstrun-option.active {
  border-color: #0a84ff;
  background: rgba(10, 132, 255, 0.1);
  color: #0a84ff;
}
.firstrun-option .opt-name { font-size: 13px; font-weight: 600; color: var(--g-text); }
.firstrun-option.active .opt-name { color: #0a84ff; }
.firstrun-option .opt-desc { font-size: 11px; line-height: 1.5; color: var(--g-text-muted); }
.firstrun-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 24px;
}
.firstrun-hint { font-size: 11.5px; color: var(--g-text-muted); }
.firstrun-confirm {
  padding: 9px 22px;
  border: none;
  border-radius: 10px;
  background: #0a84ff;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}
.firstrun-confirm:hover:not(:disabled) { background: #006ee6; }
.firstrun-confirm:disabled { opacity: 0.45; cursor: not-allowed; }
@media (max-width: 600px) {
  .firstrun-card { padding: 20px; }
  .firstrun-options { grid-template-columns: 1fr; }
}
</style>
