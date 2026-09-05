<!--
  服务监控窗口
  业务：监控 TCP 端口 / 进程 / systemd 服务状态，故障与恢复自动推送通知中心；支持增删改与测试。
  后端模块：/api/svcmonitor
  关键状态：items（监控项列表）、confirm（删除高危二次确认）；添加/编辑表单已改为独立窗口
           ServiceMonitorFormWindow（保存后经 formBus.svcmonitor 刷新本列表）。
  打开方式：独立「服务监控」入口挂载
-->
<template>
  <div class="svcmonitor-window">
    <!-- 顶部：汇总状态 -->
    <div class="toolbar">
      <div class="global-status">
        <span class="status-badge" :class="downCount > 0 ? 'down' : 'ok'">
          <Server :size="14" /> 正常 {{ upCount }} / 异常 {{ downCount }} / 共 {{ items.length }}
        </span>
        <span class="hint">故障与恢复会自动推送到通知中心配置的渠道</span>
      </div>
      <div class="toolbar-actions">
        <button class="btn" :disabled="loading" @click="loadAll"><RefreshCw :size="14" /> 刷新</button>
      </div>
    </div>

    <!-- 列表 -->
    <div class="table-toolbar">
      <button class="btn primary" @click="emit('openServiceMonitorForm', { item: null })"><Plus :size="14" /> 添加监控</button>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="items.length === 0" class="empty">
      <Server :size="40" style="color:#9ca3af;" />
      <div>还没有监控项，添加一个端口 / 进程 / systemd 服务开始检测</div>
    </div>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>目标</th>
            <th>间隔</th>
            <th>状态</th>
            <th>详情</th>
            <th>最近检查</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="i in items" :key="i.id">
            <td>{{ i.name }}<div class="sub">{{ i.enabled ? '监控中' : '已停用' }}</div></td>
            <td>{{ kindLabel(i.kind) }}</td>
            <td class="mono" :title="i.target">{{ i.target }}</td>
            <td>{{ i.interval_seconds }}s</td>
            <td>
              <span v-if="i.last_status === 'ok'" class="badge ok">正常</span>
              <span v-else-if="i.last_status === 'down'" class="badge danger">异常</span>
              <span v-else-if="i.last_status === 'unknown'" class="badge off">不支持</span>
              <span v-else class="badge off">未检测</span>
            </td>
            <td class="mono" :title="i.last_detail">{{ i.last_detail || '—' }}</td>
            <td class="mono">{{ fmtTime(i.last_checked_at) }}</td>
            <td class="actions-cell">
              <button class="btn mini" :disabled="busy" @click="doTest(i)">测试</button>
              <template v-if="i.kind === 'service'">
                <!-- 服务处置（P0）：仅 systemd 服务条目可启停/重启/设置自启；启停用主色/危险色区分 -->
                <button
                  v-if="i.last_status !== 'ok'"
                  class="btn mini primary"
                  :disabled="busy"
                  @click="doServiceAction(i, 'start')"
                >启动</button>
                <button
                  v-else
                  class="btn mini danger-text"
                  :disabled="busy"
                  @click="doServiceAction(i, 'stop')"
                >停止</button>
                <button class="btn mini" :disabled="busy" @click="doServiceAction(i, 'restart')">重启</button>
                <button
                  class="btn mini"
                  :class="{ 'autostart-on': i.is_enabled === true }"
                  :title="i.is_enabled === true ? '已设置开机自启，点击关闭' : '未开机自启，点击开启'"
                  :disabled="busy"
                  @click="toggleAutostart(i)"
                >{{ i.is_enabled === true ? '已自启' : '自启' }}</button>
              </template>
              <button class="btn mini" :disabled="busy" @click="toggleItem(i)">{{ i.enabled ? '停用' : '启用' }}</button>
              <button class="btn mini" :disabled="busy" @click="emit('openServiceMonitorForm', { item: i })">编辑</button>
              <button class="btn mini danger-text" :disabled="busy" @click="doDelete(i)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
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
import { ref, computed, onMounted, watch } from 'vue'   // Composition API：响应式、计算属性、挂载、表单总线监听
import { Server, RefreshCw, Plus } from 'lucide-vue-next'    // 图标集合
import { svcmonitorApi } from '../../api'                    // 服务监控后端接口封装
import ConfirmDialog from '../ConfirmDialog.vue'             // 高危操作二次确认弹窗（输入面板密码）
// 表单保存信号：独立「添加 / 编辑监控项」窗口保存成功后刷新本列表
import { formBus } from '../../store/formBus'

const loading = ref(false)
const busy = ref(false)
const items = ref([])
// 高风险操作二次确认状态（删除监控项需输入面板密码）
const confirm = ref({ show: false, target: null })

const emit = defineEmits(['openServiceMonitorForm'])   // 打开独立「添加/编辑监控项」表单窗口（props 传 item）

// 添加 / 编辑表单已改为独立窗口承载：保存成功后 bumpForm('svcmonitor') 触发此处重载
watch(() => formBus.svcmonitor, loadAll)

const upCount = computed(() => items.value.filter((i) => i.last_status === 'ok').length)
const downCount = computed(() => items.value.filter((i) => i.last_status === 'down').length)

// 类型标签
const kindLabel = (k) => ({ port: '端口', process: '进程', service: '服务' }[k] || k)

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function loadAll() {
  loading.value = true
  try {
    const r = await svcmonitorApi.items()
    items.value = (r && r.items) || []
  } catch (e) {
    alert('加载失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

async function doTest(i) {
  busy.value = true
  try {
    const r = await svcmonitorApi.test(i.id)
    const ok = r.status === 'ok'
    const label = ok ? '正常' : (r.status === 'unknown' ? '环境不支持' : '异常')
    alert(`${i.name}：${label}（${r.detail || '—'}）`)
    await loadAll()
  } catch (e) {
    alert('测试失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

async function toggleItem(i) {
  busy.value = true
  try {
    await svcmonitorApi.updateItem(i.id, { enabled: !i.enabled })
    await loadAll()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

// 服务处置（P0）：启动/停止/重启/自启（仅 kind=service 条目渲染对应按钮）
// stop/restart 属影响面较大的操作，先弹确认；启动为正向恢复操作直接执行。
async function doServiceAction(i, action) {
  const label = { start: '启动', stop: '停止', restart: '重启' }[action] || action
  if (action === 'stop' || action === 'restart') {
    const warn = action === 'stop' ? '停止后服务将不可用。' : '重启期间服务会短暂中断。'
    if (!window.confirm(`确认对服务「${i.target}」执行「${label}」？${warn}`)) return
  }
  busy.value = true
  try {
    const r = await svcmonitorApi.action(i.id, { action })
    alert(r.detail || `${label}完成`)
    await loadAll()
  } catch (e) {
    alert(e.response?.data?.detail || e.message || '操作失败')
  } finally {
    busy.value = false
  }
}

// 开机自启开关：按当前已知状态取反（enable/disable），成功后回写 is_enabled
async function toggleAutostart(i) {
  const wantEnable = i.is_enabled !== true
  const label = wantEnable ? '设置开机自启' : '取消开机自启'
  if (!window.confirm(`确认对服务「${i.target}」${label}？`)) return
  busy.value = true
  try {
    const r = await svcmonitorApi.action(i.id, { action: wantEnable ? 'enable' : 'disable' })
    if (r && typeof r.is_enabled !== 'undefined') i.is_enabled = r.is_enabled
    alert(r.detail || `${label}完成`)
    await loadAll()
  } catch (e) {
    alert(e.response?.data?.detail || e.message || '操作失败')
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
  confirm.value.show = false
  if (!i) return
  busy.value = true
  try {
    await svcmonitorApi.deleteItem(i.id)
    await loadAll()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.svcmonitor-window { padding: 0; display: flex; flex-direction: column; height: 100%; box-sizing: border-box; } /* 内嵌聚合窗口：外边距由父容器提供 */
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
.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; white-space: normal; max-width: 220px; }
.sub { font-size: 10px; color: #888; }
.actions-cell { display: flex; align-items: center; gap: 4px; white-space: nowrap; }

.badge { padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.danger { background: #fee2e2; color: #b91c1c; }
.badge.off { background: #f3f4f6; color: #6b7280; }

.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 6px; }
.btn:hover:not(:disabled) { background: #f9fafb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; border-color: #111827; color: #fff; }
.btn.primary:hover:not(:disabled) { background: #1f2937; }
.btn.mini { padding: 3px 8px; font-size: 12px; }
.btn.danger-text { color: #b91c1c; }
.btn.autostart-on { background: #d1fae5; color: #065f46; border-color: #a7f3d0; }

.empty { text-align: center; color: #9ca3af; padding: 40px 20px; display: flex; flex-direction: column; align-items: center; gap: 10px; font-size: 13px; }
</style>
