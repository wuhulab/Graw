<!--
  BackupRemoteFormWindow.vue — 远程备份目标（WebDAV）新建/编辑 表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 BackupWindow 的「添加/编辑远程备份目标」modal 弹窗独立为
    桌面窗口，避免点击灰色遮罩误关丢已填内容。维护 WebDAV 连接信息
    （名称 / 地址 / 账号 / 密码），编辑时密码留空表示不修改。
  后端模块：
    /api/backup 的 createRemote / updateRemote。
  关键状态：
    form   远程目标表单对象
    error  必填校验 / 后端错误信息回显
  打开方式：
    由 App.vue 的 openBackupRemoteForm(payload) 打开，props 传入
    { remote: 编辑对象或 null }。保存成功后 emit('close') 自关，
    并经 formBus 通知 BackupWindow 刷新远程目标列表。
-->
<template>
  <div class="remote-form-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <label class="ui-field">
      <span class="ui-label">名称</span>
      <input class="ui-input" v-model.trim="form.name" maxlength="64" placeholder="如：坚果云 / Nextcloud" />
    </label>

    <label class="ui-field">
      <span class="ui-label">WebDAV 地址（http/https 根 URL）</span>
      <input class="ui-input" v-model.trim="form.base" placeholder="https://dav.example.com/dav/" spellcheck="false" />
    </label>

    <div class="ui-field-row">
      <label class="ui-field">
        <span class="ui-label">用户名</span>
        <input class="ui-input" v-model.trim="form.username" autocomplete="off" spellcheck="false" />
      </label>
      <label class="ui-field">
        <span class="ui-label">密码（编辑时留空表示不修改）</span>
        <input class="ui-input" v-model="form.password" type="password" autocomplete="new-password" />
      </label>
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
// 响应式状态与 props
import { ref, reactive } from 'vue'
// 备份 API：createRemote / updateRemote
import { backupApi } from '../../api'
// 表单保存信号：通知 BackupWindow 刷新远程目标列表
import { bumpForm } from '../../store/formBus'

// remote: 编辑对象（null = 新建）
const props = defineProps({
  remote: { type: Object, default: null }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 必填校验 / 后端错误信息

// 表单初值：编辑时回填，新建时全空
const form = reactive(props.remote
  ? { name: props.remote.name || '', base: props.remote.base || '', username: props.remote.username || '', password: '' }
  : { name: '', base: '', username: '', password: '' })

// --- 保存：编辑走 updateRemote，新建走 createRemote，成功后通知父窗口刷新并自关 ---
async function save() {
  if (saving.value) return   // 防重复提交
  error.value = ''
  // 前端必填校验：名称与地址缺一不可
  if (!form.name.trim()) { error.value = '请填写名称'; return }
  if (!/^https?:\/\/.+/i.test(form.base.trim())) { error.value = '请输入完整的 http/https WebDAV 地址'; return }
  const body = {
    name: form.name.trim(),
    base: form.base.trim(),
    username: form.username.trim(),
    password: form.password   // 编辑时留空 = 不修改（后端处理）
  }
  saving.value = true
  try {
    if (props.remote) await backupApi.updateRemote(props.remote.id, body)
    else await backupApi.createRemote(body)
    bumpForm('backup')   // 通知备份中心窗口重新拉取列表
    emit('close')        // 成功后关闭本窗口
  } catch (e) {
    // 后端校验失败：回显错误并保留用户已填内容
    error.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.remote-form-window { padding: 14px; }
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