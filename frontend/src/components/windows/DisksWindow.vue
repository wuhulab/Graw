<!--
  磁盘管理窗口（后端 /api/disks 模块）
  作用：列出本机块设备与分区（容量 / 使用率 / 挂载点 / 文件系统），并允许对未挂载的
        非系统盘分区执行挂载（挂载到指定目录）。
  后端模块：/api/disks（list 取块设备与分区信息、mount 挂载到指定目录）。
  关键状态：disks（原始磁盘列表）、partitions（平铺后的分区视图）、mountDlg（挂载弹窗）、5s 轮询刷新。
  打开方式：桌面「磁盘」卡片或侧边栏入口；系统盘分区标记 system，禁止挂载操作。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 顶部工具栏：刷新 + 分区计数 -->
    <div class="toolbar">
      <button class="btn" @click="refresh">{{ $t('disks.refresh') }}</button>
      <span v-if="loading" style="margin-left:auto;color:#888;">{{ $t('common.loading') }}</span>
      <span v-else style="margin-left:auto;color:#888;">{{ $t('disks.total', { count: partitions.length }) }}</span>
    </div>

    <div style="flex:1; overflow:auto;">
      <!-- 分区表格 -->
      <table class="dt">
        <thead>
          <tr>
            <th style="width:80px;">{{ $t('disks.disk') }}</th>
            <th style="width:110px;">{{ $t('disks.partition') }}</th>
            <th style="width:90px;">{{ $t('disks.size') }}</th>
            <th style="width:100px;">{{ $t('disks.used') }}</th>
            <th style="width:100px;">{{ $t('disks.available') }}</th>
            <th style="width:130px;">{{ $t('disks.usage') }}</th>
            <th style="width:130px;white-space:nowrap;">{{ $t('disks.mountPoint') }}</th>
            <th style="width:90px;">{{ $t('disks.filesystem') }}</th>
            <th style="width:150px;">{{ $t('disks.action') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(p, i) in partitions" :key="i">
            <td style="white-space:nowrap;">
              {{ p.diskName }}
              <span v-if="p.system" class="badge badge-sys">{{ $t('disks.system') }}</span>
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
            <td style="white-space:nowrap;">{{ p.mountpoint || '-' }}</td>
            <td>{{ p.fstype || '-' }}</td>
            <td>
              <span v-if="p.system" class="op-disabled" :title="$t('disks.cannotOperateTitle')">{{ $t('disks.cannotOperate') }}</span>
              <button v-else-if="!p.mountpoint" class="btn action" @click="openMount(p)">{{ $t('disks.mount') }}</button>
              <span v-else class="op-mounted">{{ $t('disks.mounted') }}</span>
            </td>
          </tr>
          <tr v-if="partitions.length === 0">
            <td colspan="9" class="empty-cell">{{ $t('disks.empty') }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 挂载弹窗 -->
    <Teleport to="body">
      <div v-if="mountDlg.show" class="dlg-mask" @click.self="mountDlg.show = false">
        <div class="dlg">
          <div class="dlg-title">{{ $t('disks.mountTitle', { name: mountDlg.name }) }}</div>
          <div class="dlg-body">
            <div class="field">
              <label>{{ $t('disks.deviceLabel') }}</label>
              <input :value="mountDlg.device" disabled />
            </div>
            <div class="field">
              <label>{{ $t('disks.mountPointLabel') }}</label>
              <input v-model="mountDlg.mountpoint" placeholder="/mnt/data" />
            </div>
            <div v-if="mountDlg.msg" class="msg" :class="{ err: mountDlg.err }">{{ mountDlg.msg }}</div>
          </div>
          <div class="dlg-foot">
            <button class="btn" @click="mountDlg.show = false">{{ $t('common.cancel') }}</button>
            <button class="btn primary" :disabled="mountDlg.busy" @click="doMount">{{ $t('disks.mount') }}</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
// 引入响应式状态与生命周期钩子
import { ref, computed, onMounted, onUnmounted } from 'vue'
// 国际化：界面文案统一走 i18n key
import { useI18n } from 'vue-i18n'
// 磁盘管理 API：封装 /api/disks 请求（list / mount）
import { disksApi, formatBytes } from '../../api'

const { t } = useI18n()
// 磁盘列表（后端返回的原始结构，含每块磁盘的分区）
const disks = ref([])
// 是否正在加载
const loading = ref(false)
// 挂载弹窗状态：show 控制显隐，device/mountpoint 为挂载参数，msg/err/busy 控制提示与提交中
const mountDlg = ref({ show: false, name: '', device: '', mountpoint: '/mnt/', msg: '', err: false, busy: false })
// 轮询定时器句柄，卸载时清理
let timer = null

// --- 派生视图：把每块磁盘的分区平铺成单层列表，便于表格直接渲染 ---
const partitions = computed(() => {
  const out = []
  for (const d of disks.value) {
    for (const p of d.parts || []) {
      out.push({ ...p, diskName: d.name, diskType: d.type, system: p.system })
    }
  }
  return out
})

// --- 动作：拉取磁盘与分区信息 ---
async function refresh() {
  loading.value = true
  try {
    const data = await disksApi.list()          // 调用 /api/disks/list 获取块设备与分区
    disks.value = data.disks || []              // 保存原始磁盘列表（computed 会拍平成分区行）
  } catch (e) {
    console.error(e)
    alert(t('disks.loadFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    loading.value = false
  }
}

// --- 动作：打开挂载弹窗并预填设备路径 ---
function openMount(p) {
  mountDlg.value = { show: true, name: p.name, device: p.device, mountpoint: '/mnt/' + p.name, msg: '', err: false, busy: false }
}

// --- 动作：调用后端执行挂载 ---
async function doMount() {
  const d = mountDlg.value
  // 挂载点不能为空：没有目标目录挂载无意义
  if (!d.mountpoint.trim()) {
    d.msg = t('disks.mountPointRequired')
    d.err = true
    return
  }
  d.busy = true
  d.msg = ''
  try {
    const res = await disksApi.mount(d.device, d.mountpoint.trim())   // 调用 /api/disks/mount
    if (res.ok) {
      d.msg = res.message
      d.err = false
      await refresh()
      setTimeout(() => { d.show = false }, 800)     // 稍候再关弹窗，让用户看到成功提示
    } else {
      d.msg = res.message || t('disks.mountFailedFallback')
      d.err = true
    }
  } catch (e) {
    d.msg = t('disks.mountFailed', { error: e.response?.data?.detail || e.message })
    d.err = true
  } finally {
    d.busy = false
  }
}

// 打开即加载数据，并每 5 秒轮询刷新（贴近实时监控磁盘使用率）
onMounted(() => {
  refresh()
  timer = setInterval(refresh, 5000)   // 5s 轮询：磁盘状态变化需及时反映
})
onUnmounted(() => clearInterval(timer))   // 窗口关闭时清理定时器，避免内存/请求泄漏
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