<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <button class="btn" @click="refresh">刷新</button>
      <span v-if="loading" style="margin-left:auto;color:#888;">加载中...</span>
      <span v-else style="margin-left:auto;color:#888;">共 {{ partitions.length }} 个分区</span>
    </div>

    <div style="flex:1; overflow:auto;">
      <!-- 分区表格 -->
      <table class="dt">
        <thead>
          <tr>
            <th style="width:80px;">磁盘</th>
            <th style="width:110px;">分区名称</th>
            <th style="width:90px;">大小</th>
            <th style="width:100px;">已用</th>
            <th style="width:100px;">可用</th>
            <th style="width:130px;">使用率</th>
            <th>挂载目录</th>
            <th style="width:90px;">文件系统</th>
            <th style="width:150px;">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(p, i) in partitions" :key="i">
            <td>
              {{ p.diskName }}
              <span v-if="p.system" class="badge badge-sys">系统盘</span>
            </td>
            <td>{{ p.name }}</td>
            <td>{{ p.size_display || formatBytes(p.size) }}</td>
            <td>{{ p.used_display || (p.used ? formatBytes(p.used) : '-') }}</td>
            <td>{{ p.avail_display || (p.available ? formatBytes(p.available) : '-') }}</td>
            <td>
              <div v-if="p.percent != null" class="usage-wrap">
                <div class="usage-bar">
                  <div class="usage-fill" :style="{ width: Math.min(100, p.percent) + '%', background: p.percent >= 90 ? '#e03e3e' : p.percent >= 70 ? '#f5a623' : '#22b8cf' }"></div>
                </div>
                <span class="usage-text">{{ p.percent }}%</span>
              </div>
              <span v-else>-</span>
            </td>
            <td>{{ p.mountpoint || '-' }}</td>
            <td>{{ p.fstype || '-' }}</td>
            <td>
              <span v-if="p.system" class="op-disabled" title="当前磁盘为系统盘，无法进行操作">无法操作</span>
              <button v-else-if="!p.mountpoint" class="btn action" @click="openMount(p)">挂载</button>
              <span v-else class="op-mounted">已挂载</span>
            </td>
          </tr>
          <tr v-if="partitions.length === 0">
            <td colspan="9" class="empty-cell">未获取到磁盘分区信息</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 挂载弹窗 -->
    <Teleport to="body">
      <div v-if="mountDlg.show" class="dlg-mask" @click.self="mountDlg.show = false">
        <div class="dlg">
          <div class="dlg-title">挂载分区 {{ mountDlg.name }}</div>
          <div class="dlg-body">
            <div class="field">
              <label>设备</label>
              <input :value="mountDlg.device" disabled />
            </div>
            <div class="field">
              <label>挂载目录</label>
              <input v-model="mountDlg.mountpoint" placeholder="/mnt/data" />
            </div>
            <div v-if="mountDlg.msg" class="msg" :class="{ err: mountDlg.err }">{{ mountDlg.msg }}</div>
          </div>
          <div class="dlg-foot">
            <button class="btn" @click="mountDlg.show = false">取消</button>
            <button class="btn primary" :disabled="mountDlg.busy" @click="doMount">挂载</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { disksApi, formatBytes } from '../../api'

// 磁盘列表（内含分区），加载状态与挂载弹窗状态
const disks = ref([])
const loading = ref(false)
const mountDlg = ref({ show: false, name: '', device: '', mountpoint: '/mnt/', msg: '', err: false, busy: false })
let timer = null

// 把所有磁盘的分区平铺成单层列表，便于直接展示
const partitions = computed(() => {
  const out = []
  for (const d of disks.value) {
    for (const p of d.parts || []) {
      out.push({ ...p, diskName: d.name, diskType: d.type, system: p.system })
    }
  }
  return out
})

// 刷新磁盘与分区信息
async function refresh() {
  loading.value = true
  try {
    const data = await disksApi.list()
    disks.value = data.disks || []
  } catch (e) {
    console.error(e)
    alert('获取磁盘信息失败：' + (e.response?.data?.detail || e.message))
  } finally {
    loading.value = false
  }
}

// 打开挂载弹窗
function openMount(p) {
  mountDlg.value = { show: true, name: p.name, device: p.device, mountpoint: '/mnt/' + p.name, msg: '', err: false, busy: false }
}

// 执行挂载
async function doMount() {
  const d = mountDlg.value
  if (!d.mountpoint.trim()) {
    d.msg = '请输入挂载目录'
    d.err = true
    return
  }
  d.busy = true
  d.msg = ''
  try {
    const res = await disksApi.mount(d.device, d.mountpoint.trim())
    if (res.ok) {
      d.msg = res.message
      d.err = false
      await refresh()
      setTimeout(() => { d.show = false }, 800)
    } else {
      d.msg = res.message || '挂载失败'
      d.err = true
    }
  } catch (e) {
    d.msg = '挂载失败：' + (e.response?.data?.detail || e.message)
    d.err = true
  } finally {
    d.busy = false
  }
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.badge { font-size: 10px; padding: 1px 6px; border-radius: 4px; color: #fff; margin-left: 4px; vertical-align: 1px; }
.badge-sys { background: #f5a623; }
.usage-wrap { display: flex; align-items: center; gap: 6px; }
.usage-bar { flex: 1; height: 6px; background: #eee; border-radius: 3px; overflow: hidden; min-width: 60px; }
.usage-fill { height: 100%; border-radius: 3px; transition: width .2s; }
.usage-text { font-size: 11px; color: #555; width: 40px; text-align: right; }
.op-disabled { color: #b0b0b6; font-size: 12px; }
.op-mounted { color: #22b8cf; font-size: 12px; }
.btn.action { font-size: 11px; padding: 2px 10px; }
.empty-cell { text-align: center; color: #999; font-size: 12px; padding: 16px; }
.dlg-mask { position: fixed; inset: 0; background: rgba(0,0,0,.35); display: flex; align-items: center; justify-content: center; z-index: 300; }
.dlg { background: #fff; border-radius: 12px; width: 420px; box-shadow: 0 12px 40px rgba(0,0,0,.2); overflow: hidden; }
.dlg-title { font-weight: 700; color: #0a3d7a; padding: 14px 18px; border-bottom: 1px solid #eee; }
.dlg-body { padding: 16px 18px; }
.field { margin-bottom: 12px; display: flex; flex-direction: column; gap: 4px; }
.field label { font-size: 12px; color: #555; }
.field input { padding: 7px 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; background: #fafafa; }
.field input:not(:disabled):focus { outline: none; border-color: #22b8cf; background: #fff; }
.msg { font-size: 12px; margin-top: 6px; }
.msg.err { color: #e03e3e; }
.dlg-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 18px; border-top: 1px solid #eee; }
.btn.primary { background: #22b8cf; color: #fff; }
.btn.primary:hover { background: #1ba5ba; }
.btn.primary:disabled { opacity: .5; cursor: not-allowed; }
</style>