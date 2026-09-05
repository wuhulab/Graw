<!--
  FirewallRuleFormWindow.vue — 防火墙规则添加表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 FirewallWindow 的「新增端口规则 / 新增IP规则」两个 modal
    弹窗独立成桌面窗口，避免点击灰色遮罩误关导致已填内容丢失。
    通过 props.mode 区分端口规则（port）与 IP 规则（ip）两种表单。
  后端模块：
    /api/firewall 的 addPort / addIp（防火墙规则新增）。
  关键状态：
    form      当前模式的表单对象（端口规则 / IP 规则）
    error     后端校验错误信息（原内嵌弹窗无错误回显，这里补上）
  打开方式：
    由 App.vue 的 openFirewallRuleForm(payload) 打开，props 传入 { mode }。
    保存成功后 emit('close') 自关窗口，并经 formBus 通知 FirewallWindow 刷新列表。
-->
<template>
  <div class="rule-form-window">
    <!-- 端口规则表单：端口 / 协议 / 动作 / 备注 -->
    <template v-if="mode === 'port'">
      <div class="ui-field">
        <span class="ui-label">{{ $t('firewall.port') }}</span>
        <input class="ui-input" type="number" v-model.number="form.port" min="1" max="65535" />
      </div>
      <div class="ui-field">
        <span class="ui-label">{{ $t('firewall.protocol') }}</span>
        <select class="ui-select" v-model="form.protocol">
          <option value="tcp">TCP</option>
          <option value="udp">UDP</option>
        </select>
      </div>
      <div class="ui-field">
        <span class="ui-label">{{ $t('firewall.action') }}</span>
        <select class="ui-select" v-model="form.action">
          <option value="allow">{{ $t('firewall.allow') }}</option>
          <option value="deny">{{ $t('firewall.deny') }}</option>
        </select>
      </div>
      <div class="ui-field">
        <span class="ui-label">{{ $t('firewall.remark') }}</span>
        <input class="ui-input" v-model.trim="form.comment" />
      </div>
    </template>

    <!-- IP 规则表单：地址 / 动作 / 备注 -->
    <template v-else>
      <div class="ui-field">
        <span class="ui-label">{{ $t('firewall.ipCidr') }}</span>
        <input class="ui-input" v-model.trim="form.ip" placeholder="如: 192.168.1.0/24" />
      </div>
      <div class="ui-field">
        <span class="ui-label">{{ $t('firewall.action') }}</span>
        <select class="ui-select" v-model="form.action">
          <option value="allow">{{ $t('firewall.allow') }}</option>
          <option value="deny">{{ $t('firewall.deny') }}</option>
        </select>
      </div>
      <div class="ui-field">
        <span class="ui-label">{{ $t('firewall.remark') }}</span>
        <input class="ui-input" v-model.trim="form.comment" />
      </div>
    </template>

    <!-- 后端校验错误回显（如端口占用 / 已有重复规则） -->
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? $t('common.loading') : $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与 props
import { ref, reactive } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 防火墙 API：addPort / addIp
import { firewallApi } from '../../api'
// 表单保存信号：通知 FirewallWindow 刷新规则列表
import { bumpForm } from '../../store/formBus'

const { t } = useI18n()

// mode: 'port' 端口规则 / 'ip' IP 规则（由 App.vue 打开窗口时传入）
const props = defineProps({
  mode: { type: String, default: 'port' }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 后端校验错误信息

// 表单对象：端口规则与 IP 规则字段不同，各自独立初始化
const form = reactive(props.mode === 'port'
  ? { port: 80, protocol: 'tcp', action: 'allow', comment: '' }
  : { ip: '', action: 'allow', comment: '' })

// --- 保存：按模式调用对应新增接口，成功后通知父窗口刷新并自关 ---
async function save() {
  saving.value = true
  error.value = ''
  try {
    if (props.mode === 'port') {
      // 端口合法性前端预校验（1~65535），避免后端才报错
      if (!form.port || form.port < 1 || form.port > 65535) {
        error.value = t('firewall.port') + ' 1~65535'
        return
      }
      await firewallApi.addPort(form)
    } else {
      if (!form.ip.trim()) {
        error.value = t('firewall.ipCidr')
        return
      }
      await firewallApi.addIp(form)
    }
    bumpForm('firewall')   // 通知防火墙窗口重新拉取规则列表
    emit('close')          // 成功后关闭本窗口
  } catch (e) {
    // 后端校验失败：把 detail 回显在表单里，保留用户已填内容
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.rule-form-window { padding: 14px; }
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