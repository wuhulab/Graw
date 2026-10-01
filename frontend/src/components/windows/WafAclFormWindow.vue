<!--
  WafAclFormWindow.vue — WAF 自定义 ACL 新增/编辑 表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 WafWindow 的「新增/编辑 ACL」modal 弹窗独立为桌面窗口，
    避免误触灰色遮罩丢失已填写的匹配项与值。编辑匹配项 / 操作符 / 值 /
    动作四元组。
  数据流：
    ACL 数据保存在父窗口 WafWindow 的 cfg.acl 内存对象中（随「保存站点
    策略」统一提交后端），因此本窗口不直接调后端，而是把编辑结果通过
    props.onSaved(aclData) 回调交还父窗口写入，随后自关。
  打开方式：
    由 App.vue 的 openWafAclForm(payload) 打开，props 传入
    { acl: 编辑对象或 null, onSaved: 父窗口回调 }。
-->
<template>
  <div class="acl-form-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">匹配项</span>
      <select class="ui-select" v-model="form.match">
        <option value="uri">URL</option><option value="ip">IP</option>
        <option value="ua">User-Agent</option><option value="args">参数</option>
        <option value="method">请求方法</option>
      </select>
    </label>

    <label class="ui-field">
      <span class="ui-label">操作符</span>
      <select class="ui-select" v-model="form.op">
        <option value="eq">等于</option><option value="regex">正则</option>
        <option value="contains">包含</option><option value="starts">前缀</option>
      </select>
    </label>

    <label class="ui-field">
      <span class="ui-label">值</span>
      <input class="ui-input" v-model.trim="form.value" spellcheck="false" />
    </label>

    <label class="ui-field">
      <span class="ui-label">动作</span>
      <select class="ui-select" v-model="form.action">
        <option value="deny">拒绝</option><option value="allow">放行</option>
        <option value="challenge">挑战</option>
      </select>
    </label>

    <div class="ui-actions">
      <button class="ui-btn" @click="emit('close')">取消</button>
      <button class="ui-btn primary" @click="save">保存</button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与 props
import { ref, reactive } from 'vue'

// acl: 编辑对象（null = 新增，带默认值）；onSaved: 父窗口写回回调
const props = defineProps({
  acl: { type: Object, default: null },
  onSaved: { type: Function, default: null }
})
const emit = defineEmits(['close'])

const error = ref('')   // 必填校验信息

// 表单初值：编辑时深拷贝 acl，新增用默认四元组
const form = reactive(props.acl
  ? { id: props.acl.id || '', match: props.acl.match || 'uri', op: props.acl.op || 'eq', value: props.acl.value || '', action: props.acl.action || 'deny' }
  : { id: '', match: 'uri', op: 'eq', value: '', action: 'deny' })

// --- 保存：值必填校验通过后回调父窗口写入 cfg.acl，随后自关 ---
function save() {
  if (!form.value) { error.value = '请填写匹配值'; return }
  const data = { ...form }
  if (props.onSaved) props.onSaved(data)   // 交还父窗口（写入本地 cfg，随「保存站点策略」统一提交）
  emit('close')
}
</script>

<style scoped>
.acl-form-window { padding: 14px; }
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