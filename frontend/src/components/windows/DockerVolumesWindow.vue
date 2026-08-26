<!--
  Docker 数据卷 / 网络管理窗口（后端 /api/dockervolumes + /api/docker 模块）
  作用：在一个窗口内切换查看 Docker 数据卷（volume）与网络（network），并支持删除。
  后端模块：/api/dockervolumes（volumes 列表与删除）、/api/docker（networks 列表与删除）。
  关键状态：view（当前视图：volumes / networks）、volumes / networks（列表）、confirm（删除二次确认）。
  删除（卷/网络）属于高风险操作，需输入面板密码（ConfirmDialog 的 password 模式）确认。
  打开方式：Docker 窗口内的「数据卷」「网络」标签页进入（独立子窗口）。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 工具栏：视图下拉切换（数据卷 / 网络） -->
    <div class="toolbar">
      <select v-model="view" class="view-select" @change="onViewChange">
        <option value="volumes">数据卷</option>
        <option value="networks">网络</option>
      </select>
      <button class="btn" @click="onViewChange">刷新</button>
      <span v-if="loading" style="margin-left:auto;color:#888;">加载中...</span>
    </div>

    <div style="flex:1; overflow:auto;">
      <!-- ================= 数据卷视图 ================= -->
      <div v-if="view === 'volumes'">
        <table class="dt">
          <thead>
            <tr>
              <th>名称</th>
              <th style="width:120px;">驱动</th>
              <th>挂载点</th>
              <th style="width:80px;">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="v in volumes" :key="v.name">
              <td>
                {{ v.name }}
                <div style="font-size:10px;color:#888;">{{ v.mountpoint || '-' }}</div>
              </td>
              <td>{{ v.driver }}</td>
              <td style="font-family:monospace;font-size:11px;">{{ v.mountpoint || '-' }}</td>
              <td><button class="btn sm danger" @click="removeVolumeItem(v)">删除</button></td>
            </tr>
            <tr v-if="!volumes.length">
              <td colspan="4"><div class="empty">暂无数据卷</div></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- ================= 网络视图 ================= -->
      <div v-else>
        <table class="dt">
          <thead>
            <tr>
              <th>名称</th>
              <th>驱动</th>
              <th style="width:150px;">子网</th>
              <th style="width:150px;">网关</th>
              <th style="width:150px;">创建时间</th>
              <th style="width:80px;">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="n in networks" :key="n.name">
              <td>{{ n.name }}</td>
              <td>{{ n.driver }}</td>
              <td style="font-family:monospace;font-size:11px;">{{ n.subnets.map(s => s.subnet).join(', ') || '-' }}</td>
              <td style="font-family:monospace;font-size:11px;">{{ n.subnets.map(s => s.gateway).join(', ') || '-' }}</td>
              <td>{{ formatTime(n.created) }}</td>
              <td><button class="btn sm danger" @click="removeNetworkItem(n)">删除</button></td>
            </tr>
            <tr v-if="!networks.length">
              <td colspan="6"><div class="empty">暂无网络</div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除数据卷/网络需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="confirm.title"
      :message="confirm.message"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      confirm-label="删除"
      @confirm="doConfirmDanger"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
// Docker API：volumes / networks 列表与删除
import { ref, onMounted } from 'vue'
import { dockerApi } from '../../api'
// 高风险操作统一的「输入面板密码」二次确认弹窗
import ConfirmDialog from '../ConfirmDialog.vue'

// 当前视图：volumes（数据卷）/ networks（网络）
const view = ref('volumes')
const loading = ref(false)

// ---------- 数据卷：Docker 卷列表 ----------
const volumes = ref([])
// ---------- 网络：Docker 网络列表 ----------
const networks = ref([])
// 高风险操作二次确认状态（删除数据卷/网络时记录待执行动作）
const confirm = ref({ show: false, title: '', message: '', action: null })

// ---------- 视图切换与数据加载 ----------
async function onViewChange() {
  loading.value = true
  try {
    if (view.value === 'volumes') await loadVolumes()
    else await loadNetworks()
  } finally {
    loading.value = false
  }
}

// --- 动作：加载数据卷列表（/api/dockervolumes） ---
async function loadVolumes() {
  volumes.value = await dockerApi.volumes()
}

// --- 动作：加载网络列表（/api/docker/networks） ---
async function loadNetworks() {
  networks.value = await dockerApi.networks()
}

// ---------- 删除操作（均走密码二次确认） ----------
function removeVolumeItem(v) {
  // 高风险：删除数据卷会丢失卷内数据，先弹出密码确认框
  confirm.value = {
    show: true,
    title: '删除数据卷确认',
    message: `确认删除数据卷「${v.name}」？\n使用中的数据卷无法删除。\n请输入面板密码以确认。`,
    action: { type: 'volume', name: v.name }
  }
}

function removeNetworkItem(n) {
  // 高风险：删除网络会断开容器连接，先弹出密码确认框
  confirm.value = {
    show: true,
    title: '删除网络确认',
    message: `确认删除网络「${n.name}」？\n使用中的网络无法删除。\n请输入面板密码以确认。`,
    action: { type: 'network', name: n.name }
  }
}

// --- 动作：密码校验通过后真正执行删除并刷新列表 ---
async function doConfirmDanger() {
  const a = confirm.value.action
  confirm.value.show = false
  // 用户取消或无待执行动作则提前返回
  if (!a) return
  try {
    if (a.type === 'volume') {
      await dockerApi.removeVolume(a.name)   // 调用 /api/dockervolumes/<name>/remove
      await loadVolumes()
    } else if (a.type === 'network') {
      await dockerApi.removeNetwork(a.name)  // 调用 /api/docker/networks/<name>/remove
      await loadNetworks()
    }
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 资源显示辅助：Unix 时间戳 / 字符串 → 本地可读时间 ----------
function formatTime(t) {
  if (!t) return '-'
  let d = t
  if (typeof t === 'number') d = new Date(t * 1000)   // 后端常返回秒级时间戳，需 ×1000
  else if (typeof t === 'string') d = new Date(t)
  if (isNaN(d.getTime())) return String(t)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => {
  // 打开即加载当前标签的数据（默认 volumes）
  onViewChange()
})
</script>

<style scoped>
.empty { text-align: center; color: #9ca3af; padding: 40px; }
.view-select {
  padding: 4px 8px; border: 1px solid #d1d5db; border-radius: 6px;
  font-size: 13px; background: #fff; color: #111827; cursor: pointer;
}
.btn.sm { padding: 2px 8px; font-size: 11px; margin-right: 4px; }
.btn.sm.danger { color: #b91c1c; }
</style>
