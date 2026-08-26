<!--
  进程管理器窗口
  业务：实时列出主机进程（按 CPU/内存/PID/名称排序），支持右键结束进程（含强制结束，高危需面板密码二次确认）。
  后端模块：processApi（后端进程相关接口，随系统指标采集）
  关键状态：list（进程列表）、filteredList（过滤后视图）、confirm（结束进程高危二次确认）、timer（3 秒轮询）
  打开方式：独立「进程」入口挂载
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;" @click="closeMenus">
    <div class="toolbar">
      <button class="btn" @click="refresh">{{ $t('common.refresh') }}</button>
      <label style="font-size:11px;color:#0a3d7a;">{{ $t('process.sortLabel') }}</label>
      <select v-model="sortBy" @change="refresh" style="font-size:11px;">
        <option value="cpu">{{ $t('process.cpu') }}</option>
        <option value="memory">{{ $t('process.memory') }}</option>
        <option value="pid">{{ $t('process.pid') }}</option>
        <option value="name">{{ $t('process.name') }}</option>
      </select>
      <input type="text" v-model="filter" :placeholder="$t('process.searchPlaceholder')" style="max-width:200px;" />
      <span v-if="loading" style="margin-left:auto;color:#888;">{{ $t('common.loading') }}</span>
      <span v-else style="margin-left:auto;color:#888;">{{ $t('process.count', { count: filteredList.length }) }}</span>
    </div>
    <div style="flex:1; overflow:auto;">
      <table class="dt">
        <thead>
          <tr>
            <th style="width:70px;">{{ $t('process.pidCol') }}</th>
            <th>{{ $t('process.nameCol') }}</th>
            <th style="width:120px;">{{ $t('process.userCol') }}</th>
            <th style="width:80px;">{{ $t('process.statusCol') }}</th>
            <th style="width:80px;">{{ $t('process.cpuCol') }}</th>
            <th style="width:100px;">{{ $t('process.memoryCol') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in filteredList" :key="p.pid" @contextmenu.prevent="onContextMenu($event, p)">
            <td>{{ p.pid }}</td>
            <td>{{ p.name }}</td>
            <td>{{ p.username }}</td>
            <td>{{ p.status }}</td>
            <td>{{ p.cpu.toFixed(1) }}</td>
            <td>{{ formatBytes(p.memory) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <Teleport to="body">
      <div v-if="contextMenu.show" class="context-menu" :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }" @click.stop>
        <div class="menu-item" @click="menuKill">{{ $t('process.kill') }}</div>
        <div class="menu-item" @click="menuForceKill">{{ $t('process.forceKill') }}</div>
      </div>
    </Teleport>

    <!-- 高风险操作二次确认：结束进程需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="t('confirmDanger.deleteProcessTitle')"
      :message="t('confirmDanger.deleteProcessMsg', { pid: confirm.pid })"
      :input-label="t('confirmDanger.inputPwdLabel')"
      :placeholder="t('confirmDanger.inputPwdPlaceholder')"
      :confirm-label="confirm.force ? $t('process.forceKill') : $t('process.kill')"
      @confirm="doKill"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'   // 响应式、计算属性、挂载/卸载（清理轮询）
import { useI18n } from 'vue-i18n'                            // 国际化：取 t() 生成动态文案
import { processApi, formatBytes } from '../../api'           // 进程接口 + 字节格式化工具
import ConfirmDialog from '../ConfirmDialog.vue'              // 高危操作二次确认弹窗（输入面板密码）

const { t } = useI18n()
const list = ref([])
const sortBy = ref('cpu')
const filter = ref('')
const loading = ref(false)
// 高风险操作二次确认状态
const confirm = ref({ show: false, pid: 0, force: false })
const contextMenu = ref({ show: false, x: 0, y: 0, item: null })
let timer = null

const filteredList = computed(() => {
  if (!filter.value) return list.value
  const q = filter.value.toLowerCase()
  return list.value.filter(p => p.name.toLowerCase().includes(q) || String(p.pid).includes(q))
})

async function refresh() {
  loading.value = true
  try {
    list.value = await processApi.list(sortBy.value, 300)   // 300 = 单次拉取进程数上限，防止列表过长拖慢渲染
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function closeMenus() {
  contextMenu.value.show = false
}

function onContextMenu(e, p) {
  contextMenu.value = { show: true, x: e.clientX, y: e.clientY, item: p }
}

function menuKill() {
  const p = contextMenu.value.item
  closeMenus()
  if (p) kill(p.pid, false)
}

function menuForceKill() {
  const p = contextMenu.value.item
  closeMenus()
  if (p) kill(p.pid, true)
}

// --- 动作：结束进程（普通 / 强制） ---
function kill(pid, force) {
  // 高风险操作：结束进程需输入面板密码确认
  confirm.value = { show: true, pid, force }
}

async function doKill() {
  const pid = confirm.value.pid
  const force = confirm.value.force
  confirm.value.show = false
  if (!pid) return
  try {
    await processApi.kill(pid, force)
    await refresh()
  } catch (e) {
    alert(t('process.operationFailed', { error: e.response?.data?.detail || e.message }))
  }
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 3000)   // 每 3 秒轮询刷新，兼顾实时性与后端负载
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.menu-item { padding: 8px 12px; font-size: 12px; cursor: pointer; }
.menu-item:hover { background: #f5f5f7; }
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
</style>
