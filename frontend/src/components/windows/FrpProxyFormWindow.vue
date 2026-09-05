<!--
  FrpProxyFormWindow.vue — FRP 客户端代理规则表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 FrpWindow 的「新增 / 编辑代理规则」modal 弹窗独立成桌面窗口，
    避免点击灰色遮罩误关导致已填内容丢失。支持 tcp/udp/http/https 四种代理类型。
  后端模块：
    /api/frp 的 addProxy / updateProxy（代理规则增改）。
  关键状态：
    form   代理表单对象（新增为空模板，编辑从 props.proxy 回填）
    error  后端校验错误信息（保存失败回显，保留用户已填内容）
    saving 保存中（禁用按钮防重复提交）
  打开方式：
    由 App.vue 的 openFrpProxyForm(payload) 打开，props 传入 { proxy }。
    保存成功后 emit('close') 自关窗口，并经 formBus 通知 FrpWindow 刷新代理列表。
-->
<template>
  <div class="proxy-form-window">
    <!-- 后端校验错误回显（顶部留出错误框，不清空用户输入） -->
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="ui-field">
      <span class="ui-label">{{ $t('frp.name') }}</span>
      <input class="ui-input" v-model.trim="form.name" />
    </div>

    <div class="ui-field">
      <span class="ui-label">{{ $t('frp.type') }}</span>
      <select class="ui-select" v-model="form.type">
        <option v-for="tp in proxyTypes" :key="tp" :value="tp">{{ tp }}</option>
      </select>
    </div>

    <div class="form-grid">
      <div class="ui-field">
        <span class="ui-label">{{ $t('frp.localIp') }}</span>
        <input class="ui-input" v-model.trim="form.localIp" />
      </div>
      <div class="ui-field">
        <span class="ui-label">{{ $t('frp.localPort') }}</span>
        <input class="ui-input" type="number" v-model.number="form.localPort" />
      </div>
    </div>

    <!-- tcp/udp 走远程端口；http/https 走自定义域名 -->
    <template v-if="isPortType">
      <div class="ui-field">
        <span class="ui-label">{{ $t('frp.remotePort') }}</span>
        <input class="ui-input" type="number" v-model.number="form.remotePort" />
      </div>
    </template>
    <template v-else>
      <div class="ui-field">
        <span class="ui-label">{{ $t('frp.customDomains') }}</span>
        <input class="ui-input" v-model.trim="form.customDomains" :placeholder="$t('frp.customDomainsPlaceholder')" />
      </div>
    </template>

    <div class="ui-field">
      <span class="ui-label">{{ $t('frp.remark') }}</span>
      <input class="ui-input" v-model.trim="form.remark" />
    </div>

    <div class="check-row">
      <label class="ui-inline"><input type="checkbox" v-model="form.useEncryption" /> {{ $t('frp.useEncryption') }}</label>
      <label class="ui-inline"><input type="checkbox" v-model="form.useCompression" /> {{ $t('frp.useCompression') }}</label>
      <label class="ui-inline"><input type="checkbox" v-model="form.enabled" /> {{ $t('frp.enabled') }}</label>
    </div>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? $t('common.loading') : $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与计算属性
import { ref, reactive, computed } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// FRP 代理规则 API：addProxy / updateProxy
import { frpApi } from '../../api'
// 表单保存信号：通知 FrpWindow 刷新代理列表
import { bumpForm } from '../../store/formBus'

const { t } = useI18n()

// proxy: 编辑对象或 null（由 App.vue 打开窗口时传入；null 表示新增）
const props = defineProps({
  proxy: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 后端校验错误信息

// 代理类型与日志级别的可选项（与后端 /api/frp 一致）
const proxyTypes = ['tcp', 'udp', 'http', 'https']

// tcp/udp 代理使用远程端口，http/https 使用自定义域名
const isPortType = computed(() => form.type === 'tcp' || form.type === 'udp')

// 生成一个空白代理表单（编辑时用目标代理覆盖之）
function emptyForm() {
  return { id: '', name: '', type: 'tcp', localIp: '127.0.0.1', localPort: 80, remotePort: 8080, customDomains: '', useEncryption: false, useCompression: false, enabled: true, remark: '' }
}

// 表单对象：新增走空模板；编辑回填目标代理字段
const form = reactive(props.proxy ? { ...emptyForm(), ...props.proxy, id: props.proxy.id || '' } : emptyForm())

// --- 保存：有 id 走更新接口，否则走新增接口，成功后通知父窗口刷新并自关 ---
async function save() {
  saving.value = true
  error.value = ''
  // 端口合法性预校验（1~65535），避免到后端才报错
  if (!form.localPort || form.localPort < 1 || form.localPort > 65535) {
    error.value = t('frp.localPort') + ' 1~65535'
    saving.value = false
    return
  }
  try {
    const payload = {
      name: form.name,
      type: form.type,
      localIp: form.localIp,
      localPort: form.localPort,
      // tcp/udp 传远程端口，http/https 传自定义域名；空值统一给 null/'' 兜底
      remotePort: form.remotePort || null,
      customDomains: form.customDomains || '',
      useEncryption: form.useEncryption,
      useCompression: form.useCompression,
      enabled: form.enabled,
      remark: form.remark
    }
    if (form.id) {
      await frpApi.updateProxy(form.id, payload)
    } else {
      await frpApi.addProxy(payload)
    }
    bumpForm('frp')    // 通知 FrpWindow 重新拉取配置与代理列表
    emit('close')      // 成功后关闭本窗口
  } catch (e) {
    // 后端校验失败：把 detail 回显在表单顶部，保留用户已填内容
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.proxy-form-window { padding: 14px; display: flex; flex-direction: column; gap: 2px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.check-row { display: flex; gap: 14px; flex-wrap: wrap; margin: 10px 0 4px; }
.ui-inline { display: flex; align-items: center; gap: 5px; font-size: 12px; color: #374151; cursor: pointer; }
.error-box {
  color: #b91c1c;
  font-size: 12.5px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
  word-break: break-all;
}
</style>