<!--
  用户管理窗口（Users）

  这个窗口做什么：
    面板「用户」管理页。管理员在这里管理登录账号：
      - 创建用户（用户名 / 密码 / 角色）；
      - 重置指定用户的密码，可勾选「下次登录强制改密」；
      - 通过右键菜单提升 / 降级角色、删除用户；
      - 每 10 秒自动刷新一次列表。
    删除用户、降级唯一管理员都是高风险操作：删除需输入面板密码，
    降级会先校验至少保留一名管理员；不能删除当前登录的自己。

  用到的后端模块：
    /api/auth/*（管理员）——listUsers 列表、createUser 创建、
    updateUser 更新（密码 / 角色 / 强制改密）、deleteUser 删除。
    当前登录用户名取自已登录态 store/auth。

  关键状态：
    users         用户列表
    showCreate / form   创建用户弹窗
    showReset / resetTarget / resetPwd / resetMustChange   重置密码弹窗
    contextMenu   右键菜单（重置密码 / 改角色 / 删除）
    confirm       删除用户的二次确认（需输入面板密码）

  怎么被打开：
    「设置」窗口（SettingsWindow）的「用户」页签内嵌。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#f5f5f7;" @click="closeMenus">
    <div class="toolbar">
      <span style="color:#0a3d7a; font-weight:600;">{{ $t('users.title') }}</span>
      <span style="color:#6e6e73; font-size:11px;">{{ $t('users.count', { count: users.length }) }}</span>
      <button class="btn" style="margin-left:auto;" @click="openCreate">+ {{ $t('users.create') }}</button>
      <button class="btn" @click="refresh">{{ $t('users.refresh') }}</button>
    </div>

    <div style="flex:1; overflow:auto;">
      <table class="dt">
        <thead>
          <tr>
            <th>{{ $t('users.username') }}</th>
            <th>{{ $t('users.role') }}</th>
            <th>{{ $t('users.permCol') }}</th>
            <th>{{ $t('users.status') }}</th>
            <th>{{ $t('users.createdAt') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="users.length === 0">
            <td colspan="5" class="empty">{{ $t('users.noUsers') }}</td>
          </tr>
          <tr v-for="u in users" :key="u.username" @contextmenu.prevent="onContextMenu($event, u)">
            <td>
              <span style="font-family: ui-monospace, monospace; font-weight:600;">{{ u.username }}</span>
              <span v-if="u.username === currentUser" style="color:#0a84ff; font-size:11px; margin-left:6px;">{{ $t('users.me') }}</span>
            </td>
            <td>
              <span :class="['role-pill', u.role]">{{ u.role === 'admin' ? $t('users.admin') : $t('users.user') }}</span>
            </td>
            <td style="font-size:11px;">
              <!-- 模块权限：null/缺失 = 全量（完整管理员）；数组 = 受限管理员白名单 -->
              <span v-if="u.role !== 'admin'" style="color:#6e6e73;">-</span>
              <span v-else-if="u.perms == null" style="color:#0a3d7a;">{{ $t('users.permAll') }}</span>
              <span v-else :title="(u.perms || []).map(permLabel).join('、')" style="color:#c0392b;">
                {{ $t('users.permCount', { count: (u.perms || []).length }) }}
              </span>
            </td>
            <td>
              <span v-if="u.must_change_password" style="color:#c0392b; font-size:11px;">{{ $t('users.mustChange') }}</span>
              <span v-else style="color:#67c23a; font-size:11px;">{{ $t('users.normal') }}</span>
            </td>
            <td style="font-size:11px; color:#6e6e73;">{{ formatTime(u.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <Teleport to="body">
      <!-- 右键菜单：必须由 contextMenu.show 控制显隐，否则挂载时会在 (0,0) 位置显示一个空菜单 -->
      <div v-if="contextMenu.show" class="context-menu" :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }" @click.stop>
        <div class="menu-item" @click="menuResetPwd">{{ $t('users.resetPassword') }}</div>
        <div class="menu-item" @click="menuPermEdit">{{ $t('users.permEdit') }}</div>
        <div class="menu-item" @click="menuToggleRole">{{ contextMenu.item?.role === 'admin' ? $t('users.demote') : $t('users.promote') }}</div>
        <div class="menu-item danger" @click="menuDelete">{{ $t('users.delete') }}</div>
      </div>
    </Teleport>

    <!-- 创建对话框 -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal">
        <div class="modal-title">{{ $t('users.createTitle') }}</div>
        <label class="field">
          <span class="label">{{ $t('users.usernameLabel') }}</span>
          <input v-model.trim="form.username" maxlength="32" />
        </label>
        <label class="field">
          <span class="label">{{ $t('users.passwordLabel') }}</span>
          <input v-model="form.password" type="password" />
        </label>
        <label class="field">
          <span class="label">{{ $t('users.roleLabel') }}</span>
          <select v-model="form.role">
            <option value="user">{{ $t('users.user') }}</option>
            <option value="admin">{{ $t('users.admin') }}</option>
          </select>
        </label>
        <!-- 模块授权（仅管理员角色）：默认「完整管理员」；取消勾选后可指定白名单 -->
        <div v-if="form.role === 'admin'" class="perm-block">
          <label class="field checkbox">
            <input type="checkbox" v-model="form.permsFull" />
            <span>{{ $t('users.permFullLabel') }}</span>
          </label>
          <div v-if="!form.permsFull" class="perm-list">
            <div class="perm-actions">
              <button type="button" class="btn-link" @click="form.perms = [...moduleList]">{{ $t('users.permSelectAll') }}</button>
              <button type="button" class="btn-link" @click="form.perms = []">{{ $t('users.permClear') }}</button>
            </div>
            <label v-for="m in moduleList" :key="m" class="perm-item">
              <input type="checkbox" :value="m" v-model="form.perms" />
              <span>{{ permLabel(m) }}</span>
            </label>
          </div>
        </div>
        <div v-if="modalError" class="error">{{ modalError }}</div>
        <div class="modal-actions">
          <button class="btn" @click="showCreate = false">{{ $t('common.cancel') }}</button>
          <button class="btn-primary" :disabled="saving" @click="submitCreate">
            {{ saving ? $t('common.saving') : $t('common.create') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 重置密码对话框 -->
    <div v-if="showReset" class="modal-mask" @click.self="showReset = false">
      <div class="modal">
        <div class="modal-title">{{ $t('users.resetTitle', { username: resetTarget?.username }) }}</div>
        <label class="field">
          <span class="label">{{ $t('users.newPasswordLabel') }}</span>
          <input v-model="resetPwd" type="password" />
        </label>
        <label class="field checkbox">
          <input type="checkbox" v-model="resetMustChange" />
          <span>{{ $t('users.requireChange') }}</span>
        </label>
        <div v-if="modalError" class="error">{{ modalError }}</div>
        <div class="modal-actions">
          <button class="btn" @click="showReset = false">{{ $t('common.cancel') }}</button>
          <button class="btn-primary" :disabled="saving" @click="submitReset">
            {{ saving ? $t('common.saving') : $t('common.save') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 模块授权对话框（受限管理员）：全量开关 + 模块白名单多选 -->
    <div v-if="permEdit.show" class="modal-mask" @click.self="permEdit.show = false">
      <div class="modal">
        <div class="modal-title">{{ $t('users.permTitle', { username: permEdit.username }) }}</div>
        <label class="field checkbox">
          <input type="checkbox" v-model="permEdit.full" />
          <span>{{ $t('users.permFullLabel') }}</span>
        </label>
        <div v-if="!permEdit.full" class="perm-list">
          <div class="perm-actions">
            <button type="button" class="btn-link" @click="permEdit.selected = [...moduleList]">{{ $t('users.permSelectAll') }}</button>
            <button type="button" class="btn-link" @click="permEdit.selected = []">{{ $t('users.permClear') }}</button>
          </div>
          <label v-for="m in moduleList" :key="m" class="perm-item">
            <input type="checkbox" :value="m" v-model="permEdit.selected" />
            <span>{{ permLabel(m) }}</span>
          </label>
        </div>
        <div v-if="modalError" class="error">{{ modalError }}</div>
        <div class="modal-actions">
          <button class="btn" @click="permEdit.show = false">{{ $t('common.cancel') }}</button>
          <button class="btn-primary" :disabled="saving" @click="submitPermEdit">
            {{ saving ? $t('common.saving') : $t('common.save') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除用户需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="t('confirmDanger.deleteUserTitle')"
      :message="t('confirmDanger.deleteUserMsg', { username: confirm.username })"
      :input-label="t('confirmDanger.inputPwdLabel')"
      :placeholder="t('confirmDanger.inputPwdPlaceholder')"
      :confirm-label="$t('common.delete')"
      @confirm="doDelete"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'   // 响应式状态、列表自动刷新的定时器启停钩子
import { useI18n } from 'vue-i18n'   // 取 t()，界面文案跟随面板语言
import { authApi } from '../../api'   // 用户后端能力：/api/auth/* 的封装
import { auth } from '../../store/auth'   // 登录态：标出「我」并禁止删除自己
import ConfirmDialog from '../ConfirmDialog.vue'   // 高风险操作确认框（删除用户要求输入面板密码）

const { t } = useI18n()
const users = ref([])            // 用户列表，表格数据源
const showCreate = ref(false)    // 创建用户弹窗是否展开
const showReset = ref(false)     // 重置密码弹窗是否展开
const resetTarget = ref(null)    // 要重置密码的目标用户
const resetPwd = ref('')         // 重置密码弹窗里的新密码
const resetMustChange = ref(true)   // 重置后是否强制下次登录改密（默认开）
const form = ref({ username: '', password: '', role: 'user', permsFull: true, perms: [] })   // 创建用户表单（含模块授权）
const saving = ref(false)        // 创建 / 重置请求提交中
const modalError = ref('')       // 弹窗内错误提示
const contextMenu = ref({ show: false, x: 0, y: 0, item: null })   // 右键菜单位置与命中的用户
// 模块授权（受限管理员）：key 清单来自后端 /auth/modules（单一来源，避免前端清单漂移）
const moduleList = ref([])
const permEdit = ref({ show: false, username: '', full: true, selected: [] })   // 模块授权编辑弹窗
// 高风险操作二次确认：记录待删除用户
const confirm = ref({ show: false, username: '' })
let timer = null   // 10 秒自动刷新定时器句柄

const currentUser = auth.user?.username   // 当前登录用户名，用于标「我」与自删保护

// --- 加载可授权模块清单（失败不阻塞页面，仅记录告警） ---
async function loadModules() {
  try {
    const r = await authApi.listModules()
    moduleList.value = Array.isArray(r?.modules) ? r.modules : []
  } catch (e) {
    console.warn('list modules failed', e)
  }
}

// --- 模块显示名：优先取 i18n，缺翻译时回退显示原始 key（避免界面出现裸 key） ---
function permLabel(m) {
  const key = 'users.perm.' + m
  const v = t(key)
  return v === key ? m : v
}

// --- 拉取用户列表，按创建时间升序排列 ---
async function refresh() {
  try {
    users.value = await authApi.listUsers()
    users.value.sort((a, b) => (a.created_at || 0) - (b.created_at || 0))   // 最早创建的排前面，管理员好找新账号
  } catch (e) {
    if (e?.response?.status !== 401) {
      console.warn('list users failed', e)   // 401 是会话过期，属正常路径，不刷屏告警
    }
  }
}

// --- 打开创建用户弹窗：重置表单（模块授权默认「完整管理员」） ---
function openCreate() {
  form.value = { username: '', password: '', role: 'user', permsFull: true, perms: [...moduleList.value] }
  modalError.value = ''
  showCreate.value = true
}

async function submitCreate() {
  if (saving.value) return   // 提交进行中直接退出，防止重复创建
  if (form.value.username.length < 2) { modalError.value = t('users.usernameTooShort'); return }
  if (form.value.password.length < 6) { modalError.value = t('users.passwordTooShort'); return }
  // 模块白名单：勾选「完整管理员」→ null（全量）；否则提交所选模块数组
  const perms = form.value.role === 'admin'
    ? (form.value.permsFull ? null : form.value.perms)
    : null
  saving.value = true
  modalError.value = ''
  try {
    await authApi.createUser(form.value.username, form.value.password, form.value.role, perms)
    showCreate.value = false
    await refresh()
  } catch (e) {
    modalError.value = e?.response?.data?.detail || t('users.createFailed')
  } finally {
    saving.value = false
  }
}

// --- 打开模块授权弹窗（仅管理员账号可配置） ---
function openPermEdit(u) {
  if (u.role !== 'admin') { alert(t('users.permOnlyAdmin')); return }
  permEdit.value = {
    show: true,
    username: u.username,
    full: u.perms == null,                                  // null/缺失 = 全量（完整管理员）
    selected: Array.isArray(u.perms) ? [...u.perms] : [...moduleList.value],
  }
  modalError.value = ''
}

// --- 提交模块授权：full=true 传 null（恢复全量），否则传白名单数组 ---
async function submitPermEdit() {
  if (saving.value) return
  saving.value = true
  modalError.value = ''
  try {
    const { username, full, selected } = permEdit.value
    await authApi.updateUser(username, { perms: full ? null : selected })
    permEdit.value.show = false
    await refresh()
  } catch (e) {
    modalError.value = e?.response?.data?.detail || t('users.permUpdateFailed')
  } finally {
    saving.value = false
  }
}

// --- 打开重置密码弹窗 ---
function openResetPwd(u) {
  resetTarget.value = u
  resetPwd.value = ''
  resetMustChange.value = true
  modalError.value = ''
  showReset.value = true
}

async function submitReset() {
  if (saving.value) return
  if (resetPwd.value.length < 6) { modalError.value = t('users.passwordTooShort'); return }
  saving.value = true
  modalError.value = ''
  try {
    await authApi.updateUser(resetTarget.value.username, {
      password: resetPwd.value,
      must_change_password: resetMustChange.value
    })
    showReset.value = false
  } catch (e) {
    modalError.value = e?.response?.data?.detail || t('users.resetFailed')
  } finally {
    saving.value = false
  }
}

// --- 提升 / 降级角色：降级前保证至少还剩一名管理员 ---
async function toggleRole(u) {
  const next = u.role === 'admin' ? 'user' : 'admin'
  if (u.role === 'admin' && next === 'user') {
    const admins = users.value.filter(u2 => u2.role === 'admin')
    if (admins.length <= 1) {
      alert(t('users.atLeastOneAdmin'))   // 面板必须保留一个管理员入口，拒绝降级唯一管理员
      return
    }
  }
  try {
    await authApi.updateUser(u.username, { role: next })
    await refresh()
  } catch (e) {
    alert(e?.response?.data?.detail || t('users.toggleRoleFailed'))
  }
}

// --- 点击删除：先做自删与二次确认两道防线 ---
function del(u) {
  if (u.username === currentUser) { alert(t('users.cannotDeleteSelf')); return }   // 不能删自己，否则面板将无人可管理
  // 高风险操作：删除用户需输入面板密码确认
  confirm.value = { show: true, username: u.username }
}

// --- ConfirmDialog 密码校验通过后真正执行删除 ---
async function doDelete() {
  const username = confirm.value.username
  confirm.value.show = false
  if (!username) return   // 无待删用户名（异常触发）时直接退出
  try {
    await authApi.deleteUser(username)
    await refresh()
  } catch (e) {
    alert(e?.response?.data?.detail || t('users.deleteFailed'))
  }
}

// --- 时间格式化：后端给的是 Unix 秒，转成可读时间串 ---
function formatTime(t) {
  if (!t) return '-'
  try { return new Date(t * 1000).toLocaleString() } catch { return '-' }
}

// --- 收起右键菜单 ---
function closeMenus() {
  contextMenu.value.show = false
}

// --- 点击菜单外的任意位置（其它窗口 / 桌面 / 任务栏 / 表格空白）自动收起 ---
// 菜单经 Teleport 挂到 body，不属于本组件根节点，只靠根节点的 @click 覆盖不到窗口外部，
// 因此必须挂文档级监听；命中菜单内部时跳过，交给菜单项自身处理（它们会先 closeMenus 再执行动作）。
function onDocMouseDown(e) {
  if (!contextMenu.value.show) return
  if (e.target instanceof Element && e.target.closest('.context-menu')) return
  closeMenus()
}

// --- 在非表格行处再次右键同样收起（行上的右键会被 onContextMenu 阻止冒泡到文档） ---
function onDocContextMenu(e) {
  if (!contextMenu.value.show) return
  if (e.target instanceof Element && e.target.closest('.context-menu')) return
  closeMenus()
}

// --- Esc 也收起菜单（与桌面其它右键菜单的交互习惯一致） ---
function onDocKeydown(e) {
  if (e.key === 'Escape' && contextMenu.value.show) closeMenus()
}

// --- 在用户行上右键：记录点击位置与命中的用户 ---
function onContextMenu(e, u) {
  e.stopPropagation()   // 阻止冒泡，避免文档级 contextmenu 监听把刚打开的菜单又立刻关掉
  contextMenu.value = { show: true, x: e.clientX, y: e.clientY, item: u }
}

// --- 菜单项：重置密码 ---
function menuResetPwd() {
  const u = contextMenu.value.item
  closeMenus()
  if (u) openResetPwd(u)
}

// --- 菜单项：模块授权 ---
function menuPermEdit() {
  const u = contextMenu.value.item
  closeMenus()
  if (u) openPermEdit(u)
}

// --- 菜单项：提升 / 降级角色 ---
function menuToggleRole() {
  const u = contextMenu.value.item
  closeMenus()
  if (u) toggleRole(u)
}

// --- 菜单项：删除用户 ---
function menuDelete() {
  const u = contextMenu.value.item
  closeMenus()
  if (u) del(u)
}

onMounted(() => {
  refresh()
  loadModules()   // 模块清单用于创建/授权弹窗的多选渲染（失败时静默，不阻塞列表）
  timer = setInterval(refresh, 10000)
  // 右键菜单的文档级收起监听：窗口打开期间生效
  document.addEventListener('mousedown', onDocMouseDown)
  document.addEventListener('contextmenu', onDocContextMenu)
  document.addEventListener('keydown', onDocKeydown)
})   // 打开即拉列表，之后每 10 秒自动同步
onUnmounted(() => {
  clearInterval(timer)   // 窗口关闭后停掉自动刷新
  // 同步移除文档级监听，避免窗口关闭后残留监听（内存泄漏 / 误收起其它菜单）
  document.removeEventListener('mousedown', onDocMouseDown)
  document.removeEventListener('contextmenu', onDocContextMenu)
  document.removeEventListener('keydown', onDocKeydown)
})
</script>

<style scoped>
.role-pill {
  display: inline-block;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 600;
}
.role-pill.admin { background: rgba(10, 132, 255, 0.12); color: #0a3d7a; }
.role-pill.user { background: rgba(0, 0, 0, 0.06); color: #1d1d1f; }

/* --- 模块授权（受限管理员）多选区 --- */
.perm-block {
  margin-bottom: 12px;
}

.perm-list {
  max-height: 190px;
  overflow: auto;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 8px;
  padding: 8px 10px;
  background: #fafafa;
}

.perm-actions {
  display: flex;
  gap: 10px;
  margin-bottom: 6px;
}

.btn-link {
  border: none;
  background: none;
  padding: 0;
  font-size: 11px;
  color: #0a84ff;
  cursor: pointer;
  font-family: inherit;
}
.btn-link:hover { text-decoration: underline; }

.perm-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #1d1d1f;
  padding: 2px 0;
  cursor: pointer;
}
.perm-item input { width: auto; }

.menu-item { padding: 8px 12px; font-size: 12px; cursor: pointer; }
.menu-item:hover { background: #f5f5f7; }
.menu-item.danger { color: #c0392b; }
.context-menu {
  position: fixed;
  background: #fff;
  border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
  z-index: 200;
  min-width: 140px;
  padding: 4px 0;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.32);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10000;
}

.modal {
  width: 360px;
  background: #ffffff;
  border-radius: 14px;
  padding: 22px 22px 16px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.32);
  user-select: none;
}

.modal-title {
  font-size: 14px;
  font-weight: 700;
  color: #1d1d1f;
  margin-bottom: 14px;
}

.field {
  display: block;
  margin-bottom: 12px;
}

.field.checkbox {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #1d1d1f;
}

.field.checkbox input { width: auto; }

.field .label {
  display: block;
  font-size: 11px;
  color: #6e6e73;
  font-weight: 600;
  margin-bottom: 4px;
}

.field input, .field select {
  width: 100%;
  padding: 8px 10px;
  font-size: 13px;
  font-family: inherit;
  border: 1px solid rgba(0, 0, 0, 0.1);
  border-radius: 8px;
  outline: none;
  background: #ffffff;
  color: #1d1d1f;
}

.field input:focus, .field select:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10, 132, 255, 0.18);
}

.error {
  color: #c0392b;
  font-size: 12px;
  background: rgba(255, 59, 48, 0.08);
  border: 1px solid rgba(255, 59, 48, 0.2);
  border-radius: 8px;
  padding: 6px 10px;
  margin-bottom: 10px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}

.btn-primary {
  padding: 8px 14px;
  font-size: 12px;
  font-weight: 600;
  color: #ffffff;
  background: #0a84ff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}
.btn-primary:hover:not(:disabled) { background: #006ee6; }
.btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
