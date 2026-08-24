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
import { ref, onMounted } from 'vue'
import { dockerApi } from '../../api'
import ConfirmDialog from '../ConfirmDialog.vue'

// 当前视图：volumes / networks
const view = ref('volumes')
const loading = ref(false)

// ---------- 数据卷 ----------
const volumes = ref([])
// ---------- 网络 ----------
const networks = ref([])
// 高风险操作二次确认状态（删除数据卷/网络）
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

async function loadVolumes() {
  volumes.value = await dockerApi.volumes()
}

async function loadNetworks() {
  networks.value = await dockerApi.networks()
}

// ---------- 删除操作（均走密码二次确认） ----------
function removeVolumeItem(v) {
  confirm.value = {
    show: true,
    title: '删除数据卷确认',
    message: `确认删除数据卷「${v.name}」？\n使用中的数据卷无法删除。\n请输入面板密码以确认。`,
    action: { type: 'volume', name: v.name }
  }
}

function removeNetworkItem(n) {
  confirm.value = {
    show: true,
    title: '删除网络确认',
    message: `确认删除网络「${n.name}」？\n使用中的网络无法删除。\n请输入面板密码以确认。`,
    action: { type: 'network', name: n.name }
  }
}

async function doConfirmDanger() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return
  try {
    if (a.type === 'volume') {
      await dockerApi.removeVolume(a.name)
      await loadVolumes()
    } else if (a.type === 'network') {
      await dockerApi.removeNetwork(a.name)
      await loadNetworks()
    }
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 资源显示辅助 ----------
function formatTime(t) {
  if (!t) return '-'
  let d = t
  if (typeof t === 'number') d = new Date(t * 1000)
  else if (typeof t === 'string') d = new Date(t)
  if (isNaN(d.getTime())) return String(t)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => {
  // 打开即加载当前标签的数据
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
