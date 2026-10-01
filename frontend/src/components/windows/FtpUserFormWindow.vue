<!--
  FtpUserFormWindow.vue — 虚拟 FTP 用户添加 / 编辑表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 FtpUsersWindow 的「添加 / 编辑 FTP 用户」modal 弹窗独立成桌面窗口，
    避免点击灰色遮罩误关导致已填内容丢失。props.user 为编辑对象（null 表示新增），
    以此区分新增 / 编辑两条保存路径。
  后端模块：
    /api/ftpusers 的 create / update（对应 api.js 的 ftpusersApi）。
  关键状态：
    form      表单对象（用户名 / 密码 / 目录 / 启用 / 描述）
    error     后端校验错误信息（保留用户已填内容）
  打开方式：
    由 App.vue 的 openFtpUserForm(payload) 打开，props 传入 { user }。
    保存成功后 bumpForm('ftpusers') 通知父窗口刷新列表，并 emit('close') 自关窗口。
-->
<template>
  <div class="ftpuser-form-window">
    <div class="ui-field">
      <span class="ui-label">用户名</span>
      <input class="ui-input" v-model.trim="form.username" maxlength="64" placeholder="仅字母/数字/._-，如 webuser" spellcheck="false" />
    </div>
    <div class="ui-field">
      <span class="ui-label">{{ isEdit ? '密码（留空保持原密码）' : '密码（至少 6 位）' }}</span>
      <input class="ui-input" v-model="form.password" type="password" maxlength="128" placeholder="FTP 登录密码" autocomplete="new-password" />
    </div>
    <div class="ui-field">
      <span class="ui-label">目录（chroot 路径）</span>
      <input class="ui-input" v-model.trim="form.directory" maxlength="1024" placeholder="如 /srv/ftp/webuser 或 C:\ftp\webuser" spellcheck="false" />
    </div>
    <div class="ui-field check-field">
      <label class="check-row">
        <input type="checkbox" v-model="form.enabled" />
        <span>启用</span>
      </label>
    </div>
    <div class="ui-field">
      <span class="ui-label">描述</span>
      <input class="ui-input" v-model.trim="form.description" maxlength="255" placeholder="可选，记录用途（如：官网文件上传账号）" />
    </div>

    <!-- 后端校验错误回显 -->
    <div v-if="error" class="error-box">{{ error }}</div>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">取消</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存' }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态 / 计算属性与 props
import { ref, reactive, computed } from 'vue'
// FTP 用户 API：list/create/update（保存调用）
import { ftpusersApi } from '../../api'
// 表单保存信号：通知 FtpUsersWindow 刷新列表
import { bumpForm } from '../../store/formBus'

// user: 编辑对象或 null（null 表示新增）
const props = defineProps({
  user: { type: [Object, null], default: null }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 后端校验错误信息

// 编辑模式：props.user 非空即为编辑已有用户
const isEdit = computed(() => !!props.user)

// 表单对象：编辑时灌入原值（密码留空表示保持原密码），否则使用默认值
const form = reactive({
  username: props.user?.username || '',
  password: '',
  directory: props.user?.directory || '',
  enabled: props.user?.enabled !== false,
  description: props.user?.description || '',
})

// --- 保存：新增或编辑，成功后通知父窗口刷新并自关 ---
async function save() {
  if (saving.value) return
  error.value = ''
  if (!form.username.trim()) { error.value = '请填写用户名'; return }
  if (!form.directory.trim()) { error.value = '请填写目录'; return }
  const body = {
    username: form.username.trim(),
    directory: form.directory.trim(),
    enabled: form.enabled,
    description: form.description.trim(),
  }
  if (isEdit.value) {
    // 编辑时密码非空才更新密码
    if (form.password) body.password = form.password
  } else {
    if (!form.password) { error.value = '请填写密码'; return }
    body.password = form.password
  }
  saving.value = true
  try {
    if (isEdit.value) await ftpusersApi.update(props.user.id, body)
    else await ftpusersApi.create(body)
    bumpForm('ftpusers')     // 通知 FTP 用户窗口重新拉取列表
    emit('close')            // 成功后关闭本窗口
  } catch (e) {
    // 后端校验失败：把 detail 回显在表单里，保留用户已填内容
    error.value = e?.response?.data?.detail || e?.message || String(e)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.ftpuser-form-window { padding: 14px; }
/* 勾选框行 */
.check-field { display: flex; align-items: center; }
.check-row { display: flex; align-items: center; gap: 6px; font-size: 13px; }
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