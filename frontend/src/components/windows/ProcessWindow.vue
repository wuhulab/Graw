<template>
  <div style="display:flex; flex-direction:column; height:100%;" @click="closeMenus">
    <div class="toolbar">
      <button class="btn" @click="refresh">刷新</button>
      <label style="font-size:11px;color:#0a3d7a;">排序：</label>
      <select v-model="sortBy" @change="refresh" style="font-size:11px;">
        <option value="cpu">CPU</option>
        <option value="memory">内存</option>
        <option value="pid">PID</option>
        <option value="name">名称</option>
      </select>
      <input type="text" v-model="filter" placeholder="过滤进程名..." style="max-width:200px;" />
      <span v-if="loading" style="margin-left:auto;color:#888;">加载中...</span>
      <span v-else style="margin-left:auto;color:#888;">共 {{ filteredList.length }} 项</span>
    </div>
    <div style="flex:1; overflow:auto;">
      <table class="dt">
        <thead>
          <tr>
            <th style="width:70px;">PID</th>
            <th>名称</th>
            <th style="width:120px;">用户</th>
            <th style="width:80px;">状态</th>
            <th style="width:80px;">CPU%</th>
            <th style="width:100px;">内存</th>
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
        <div class="menu-item" @click="menuKill">结束进程</div>
        <div class="menu-item" @click="menuForceKill">强制结束</div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { processApi, formatBytes } from '../../api'

const list = ref([])
const sortBy = ref('cpu')
const filter = ref('')
const loading = ref(false)
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
    list.value = await processApi.list(sortBy.value, 300)
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

async function kill(pid, force) {
  if (!confirm(`确认${force ? '强制' : ''}结束进程 ${pid}？`)) return
  try {
    await processApi.kill(pid, force)
    await refresh()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 3000)
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
