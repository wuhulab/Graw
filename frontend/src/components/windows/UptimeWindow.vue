<!--
  站点可用性监控窗口（Uptime）

  这个窗口做什么：
    面板「可用性监控」功能。定期对网站 / 服务做 HTTP 探测，实时统计
    正常 / 异常数量，异常与恢复会自动推送到通知中心配置的渠道。管理员可以：
      - 添加 / 编辑监控项：URL、预期状态码、检查间隔、超时、启停；
      - 对单个监控项立即测试一次；
      - 删除不再需要的监控项（高风险操作，需输入面板密码）。
    列表展示每个监控项的最近状态、响应时间、最近检查时间与已宕机时长。

  用到的后端模块：
    /api/uptime/*（管理员权限）——items 列表、create / update 增改、
    {id}/test 立即探测、{id} 删除。探测由后端常驻任务执行，本窗口只读结果。

  关键状态：
    items       监控项列表
    form / editing   添加 / 编辑表单
    upCount / downCount   汇总的正常 / 异常数
    confirm     删除监控项的二次确认（需输入面板密码）

  怎么被打开：
    桌面「可用性监控」应用。
-->
<template>
  <div class="uptime-window">
    <!-- 顶部：汇总状态 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge" :class="downCount > 0 ? 'down' : 'ok'">
          <Activity :size="14" /> 正常 {{ upCount }} / 异常 {{ downCount }} / 共 {{ items.length }}
        </span>
        <span class="hint">宕机与恢复会自动推送到通知中心配置的渠道</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
      </div>
    </div>

    <!-- 列表 -->
    <div class="table-toolbar">
      <button class="btn primary" @click="openAdd"><Plus :size="14" /> 添加监控</button>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="items.length === 0" class="empty">
      <Activity :size="40" style="color:#9ca3af;" />
      <div>还没有监控项，添加一个网站或服务开始检测</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>地址</th>
            <th>预期码</th>
            <th>间隔</th>
            <th>状态</th>
            <th>响应时间</th>
            <th>最近检查</th>
            <th>连续异常</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="i in items" :key="i.id">
            <td>{{ i.name }}<div class="sub">{{ i.enabled ? '监控中' : '已停用' }}</div></td>
            <td class="mono" :title="i.url">{{ i.url }}</td>
            <td>{{ i.expect_status }}</td>
            <td>{{ i.interval_seconds }}s</td>
            <td>
              <span v-if="i.last_status === 'ok'" class="badge ok">正常</span>
              <span v-else-if="i.last_status === 'down'" class="badge danger">异常</span>
              <span v-else class="badge off">未检测</span>
            </td>
            <td>{{ i.last_latency_ms != null ? i.last_latency_ms + 'ms' : '—' }}</td>
            <td class="mono">{{ fmtTime(i.last_checked_at) }}</td>
            <td>{{ downSecText(i) }}</td>
            <td class="actions-cell">
              <button class="btn mini" :disabled="busy" @click="doTest(i)">测试</button>
              <button class="btn mini" :disabled="busy" @click="toggleItem(i)">{{ i.enabled ? '停用' : '启用' }}</button>
              <button class="btn mini" :disabled="busy" @click="openEdit(i)">编辑</button>
              <button class="btn mini danger-text" :disabled="busy" @click="doDelete(i)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 添加 / 编辑弹窗 -->
    <div v-if="formOpen" class="modal-overlay" @click.self="formOpen = false">
      <div class="modal">
        <h3><Activity :size="16" /> {{ editing ? '编辑监控项' : '添加监控项' }}</h3>

        <label class="field">
          <span class="label">名称</span>
          <input v-model.trim="form.name" maxlength="64" placeholder="如：官网 / API 服务" />
        </label>
        <label class="field">
          <span class="label">监控地址（http/https URL）</span>
          <input v-model.trim="form.url" placeholder="https://example.com" spellcheck="false" />
        </label>
        <div class="field-row">
          <label class="field">
            <span class="label">预期状态码</span>
            <input type="number" min="100" max="599" v-model.number="form.expect_status" />
          </label>
          <label class="field">
            <span class="label">检查间隔（秒）</span>
            <input type="number" min="10" max="86400" v-model.number="form.interval_seconds" />
          </label>
        </div>
        <div class="field-row">
          <label class="field">
            <span class="label">超时（秒）</span>
            <input type="number" min="1" max="60" v-model.number="form.timeout_seconds" />
          </label>
          <label class="field check" style="justify-content:flex-end;">
            <input type="checkbox" v-model="form.enabled" />
            <span>启用</span>
          </label>
        </div>
        <div v-if="formError" class="error">{{ formError }}</div>
        <div class="actions">
          <button class="btn" :disabled="saving" @click="formOpen = false">取消</button>
          <button class="btn primary" :disabled="saving" @click="saveForm">{{ saving ? '保存中…' : '保存' }}</button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除监控项需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      title="删除监控项确认"
      :message="`删除监控项「${confirm.target?.name}」后其历史记录也将清除。\n请输入面板密码以确认。`"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      confirm-label="删除"
      @confirm="doDeleteConfirmed"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'   // 响应式状态、表单对象、汇总计数、挂载钩子
import { Activity, RefreshCw, Plus } from 'lucide-vue-next'   // 状态 / 刷新 / 添加图标
import { uptimeApi } from '../../api'   // 可用性监控后端能力：/api/uptime/* 的封装
import ConfirmDialog from '../ConfirmDialog.vue'   // 高风险操作确认框（删除监控项要求输入面板密码）

const loading = ref(false)   // 列表加载中（首屏与空状态判断）
const busy = ref(false)      // 行内操作（测试 / 启停 / 删除）进行中，用于禁用按钮
const items = ref([])        // 监控项列表
// 高风险操作二次确认状态（删除监控项需输入面板密码）
const confirm = ref({ show: false, target: null })

const formOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const formError = ref('')
const form = reactive({
  name: '', url: '', expect_status: 200, timeout_seconds: 10, interval_seconds: 60, enabled: true,
})

const upCount = computed(() => items.value.filter((i) => i.last_status === 'ok').length)
const downCount = computed(() => items.value.filter((i) => i.last_status === 'down').length)

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

// --- 宕机时长展示：按秒/分钟/小时分级精简成可读文本 ---
function downSecText(i) {
  if (i.last_status !== 'down' || !i.down_since) return '—'   // 不在宕机状态或缺少起始时间就不显示时长
  const since = new Date(i.down_since)
  if (isNaN(since.getTime())) return '—'
  const sec = Math.floor((Date.now() - since.getTime()) / 1000)   // 距宕机起点已过去的秒数
  if (sec < 60) return `${sec}s`                                    // 60 = 一分钟内直接显示秒
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟`             // 3600 = 一小时内显示分钟
  return `${Math.floor(sec / 3600)} 小时`
}

// --- 拉取监控项列表 ---
async function loadAll() {
  loading.value = true
  try {
    const r = await uptimeApi.items()
    items.value = (r && r.items) || []    // 后端无 items 字段时兜空数组，避免表格报错
  } catch (e) {
    alert('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// --- 打开「添加」弹窗：重置表单到默认值 ---
function openAdd() {
  editing.value = null
  formError.value = ''
  Object.assign(form, { name: '', url: '', expect_status: 200, timeout_seconds: 10, interval_seconds: 60, enabled: true })   // 默认 200 期望码、60 秒间隔、开启
  formOpen.value = true
}

// --- 打开「编辑」弹窗：把监控项现有值灌回表单 ---
function openEdit(i) {
  editing.value = i
  formError.value = ''
  Object.assign(form, {
    name: i.name || '', url: i.url || '', expect_status: i.expect_status ?? 200,
    timeout_seconds: i.timeout_seconds ?? 10, interval_seconds: i.interval_seconds ?? 60,
    enabled: i.enabled !== false,
  })
  formOpen.value = true
}

async function saveForm() {
  if (saving.value) return   // 提交进行中直接退出，防止重复保存
  formError.value = ''
  if (!form.name.trim()) { formError.value = '请填写名称'; return }    // 名称必填
  if (!form.url.trim()) { formError.value = '请填写监控地址'; return }  // URL 必填，否则探测无从发起
  const body = {
    name: form.name.trim(), url: form.url.trim(), expect_status: form.expect_status,
    timeout_seconds: form.timeout_seconds, interval_seconds: form.interval_seconds, enabled: form.enabled,
  }
  saving.value = true
  try {
    if (editing.value) await uptimeApi.updateItem(editing.value.id, body)
    else await uptimeApi.createItem(body)
    formOpen.value = false
    await loadAll()   // 保存后刷新列表
  } catch (e) {
    formError.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

// --- 立即测试单个监控项：调后端实时探测一次并弹窗展示结果 ---
async function doTest(i) {
  busy.value = true
  try {
    const r = await uptimeApi.test(i.id)
    const ok = r.status === 'ok'
    const detail = ok ? `HTTP ${r.code}，${r.latency_ms != null ? r.latency_ms + 'ms' : '—'}` : (r.error || '不可访问')
    alert(`${i.name}：${ok ? '正常' : '异常'}（${detail}）`)
    await loadAll()
  } catch (e) {
    alert('测试失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// --- 启用 / 停用单个监控项：按当前状态取反 ---
async function toggleItem(i) {
  busy.value = true
  try {
    await uptimeApi.updateItem(i.id, { enabled: !i.enabled })
    await loadAll()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// 点击删除：弹出高风险操作二次确认（输入面板密码），不直接删除
function doDelete(i) {
  confirm.value = { show: true, target: i }
}

// 面板密码校验通过后执行真正的删除
async function doDeleteConfirmed() {
  const i = confirm.value.target
  confirm.value.show = false   // 先收起确认框，避免删除期间重复触发
  if (!i) return               // 无待删目标（异常触发）时直接退出
  busy.value = true
  try {
    await uptimeApi.deleteItem(i.id)   // 后端同时清除该监控项及其历史记录
    await loadAll()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)   // 打开即拉一次监控项列表
</script>

<style scoped>
.uptime-window { padding: 0; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; } /* 内嵌聚合窗口：外边距由父容器提供 */
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; justify-content: space-between; }
.global-status { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.status-badge.ok { background: #d1fae5; color: #065f46; }
.status-badge.down { background: #fee2e2; color: #b91c1c; }
.hint { color: #6e6e73; font-size: 12px; }
.toolbar-actions { display: flex; gap: 8px; }
.table-toolbar { display: flex; align-items: center; justify-content: flex-end; margin-bottom: 8px; }

.table-wrap { overflow: auto; border: 1px solid #e5e7eb; border-radius: 8px; flex: 1; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; vertical-align: middle; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tbody tr:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; }
.sub { font-size: 10px; color: #888; }
.actions-cell { display: flex; gap: 4px; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 12.5px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.mini { padding: 3px 8px; font-size: 11.5px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.danger-text { color: #b91c1c; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.badge.danger { background: #fee2e2; color: #b91c1c; font-weight: 600; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 18px; width: 560px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,0.18); max-height: 92vh; overflow: auto; }
.modal h3 { margin: 0 0 12px; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.field { display: block; margin-bottom: 10px; }
.field .label { display: block; font-size: 11px; color: #1d1d1f; font-weight: 600; margin-bottom: 5px; }
.field input { width: 100%; box-sizing: border-box; border: 1px solid rgba(0,0,0,0.15); border-radius: 8px; padding: 8px 10px; font-size: 12.5px; font-family: inherit; }
.field input:focus { outline: none; border-color: #0a84ff; box-shadow: 0 0 0 3px rgba(10,132,255,0.15); }
.field.check { display: flex; align-items: center; gap: 8px; }
.field.check input { width: auto; }
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }
.error { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
