<template>
  <div style="display:flex; flex-direction:column; height:100%;" @click="closeMenus">
    <div class="toolbar">
      <button class="btn" @click="refresh">刷新</button>
      <span style="margin-left:8px; color:#0a3d7a;" v-if="status">
        <template v-if="status.available">
          Docker {{ status.server_version }} · 容器 {{ status.containers_running }}/{{ status.containers }} · 镜像 {{ status.images }}
        </template>
        <template v-else>
          Docker 不可用：{{ status.reason }}
        </template>
      </span>
      <span v-if="loading" style="margin-left:auto;color:#888;">加载中...</span>
      <span class="hint">右键点击容器打开操作菜单</span>
    </div>
    <div style="flex:1; overflow:auto;">
      <div v-if="status && !status.available" class="empty">
        无法连接到 Docker。请确认 Docker 服务正在运行，并且当前用户具有访问权限。
      </div>
      <table v-else class="dt">
        <thead>
          <tr>
            <th>名称</th>
            <th>镜像</th>
            <th>状态</th>
            <th style="width:100px;">CPU</th>
            <th style="width:160px;">内存</th>
            <th>端口</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in containers" :key="c.id" @contextmenu.prevent="onContextMenu($event, c)">
            <td>{{ c.name }}<div style="font-size:10px;color:#888;">{{ c.id }}</div></td>
            <td>{{ c.image }}</td>
            <td><span :style="{ color: c.state === 'running' ? '#2a8f3c' : '#a04040' }">{{ c.state }}</span></td>
            <td>
              <template v-if="c.state === 'running'">
                <span :style="{ color: cpuColor(c.cpu_percent) }">{{ c.cpu_percent }}%</span>
              </template>
              <span v-else style="color:#aaa;">—</span>
            </td>
            <td>
              <template v-if="c.state === 'running' && c.mem_usage">
                <div>{{ c.mem_usage }}</div>
                <div class="mem-bar"><div class="mem-fill" :style="{ width: memBarWidth(c.mem_percent), background: cpuColor(c.mem_percent) }"></div></div>
              </template>
              <span v-else style="color:#aaa;">—</span>
            </td>
            <td style="font-family:monospace;font-size:11px;">{{ c.ports.join(', ') || '-' }}</td>
          </tr>
          <tr v-if="!containers.length && status && status.available">
            <td colspan="6"><div class="empty">暂无容器</div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 右键菜单 -->
    <Teleport to="body">
      <div v-if="ctxMenu.show" class="context-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }" @click.stop>
        <div class="menu-header">{{ ctxMenu.item?.name }}</div>
        <div class="menu-divider"></div>
        <div class="menu-item" v-if="ctxMenu.item?.state !== 'running'" @click="menuAct('start')">启动</div>
        <div class="menu-item" v-if="ctxMenu.item?.state === 'running'" @click="menuAct('stop')">停止</div>
        <div class="menu-item" v-if="ctxMenu.item?.state === 'running'" @click="menuAct('restart')">重启</div>
        <div class="menu-item" @click="menuLogs">查看日志</div>
        <div class="menu-divider"></div>
        <div class="menu-item danger" @click="menuAct('remove')">删除容器</div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { dockerApi } from '../../api'

const emit = defineEmits(['openLogs'])

const status = ref(null)
const containers = ref([])
const loading = ref(false)
const ctxMenu = ref({ show: false, x: 0, y: 0, item: null })
let timer = null

async function refresh() {
  loading.value = true
  try {
    status.value = await dockerApi.status()
    if (status.value.available) {
      containers.value = await dockerApi.containers()
    } else {
      containers.value = []
    }
  } catch (e) {
    status.value = { available: false, reason: e.message }
  } finally {
    loading.value = false
  }
}

async function act(id, action) {
  if (action === 'remove' && !confirm('确认删除该容器？')) return
  try {
    await dockerApi.action(id, action)
    await refresh()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 右键菜单 ----------
function closeMenus() {
  ctxMenu.value.show = false
}

function onContextMenu(e, c) {
  const x = Math.min(e.clientX, window.innerWidth - 180)
  const y = Math.min(e.clientY, window.innerHeight - 240)
  ctxMenu.value = { show: true, x, y, item: c }
}

function menuAct(action) {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) act(it.id, action)
}

function menuLogs() {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) emit('openLogs', { id: it.id, name: it.name })
}

// ---------- 资源显示辅助 ----------
function cpuColor(pct) {
  const v = Number(pct) || 0
  if (v >= 80) return '#b91c1c'
  if (v >= 50) return '#b45309'
  return '#2a8f3c'
}

function memBarWidth(pct) {
  return Math.min(Number(pct) || 0, 100) + '%'
}

onMounted(() => {
  refresh()
  timer = setInterval(refresh, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.hint { margin-left: auto; color: #888; font-size: 11px; }
.empty { text-align: center; color: #9ca3af; padding: 40px; }
.mem-bar { width: 120px; height: 5px; background: #e5e7eb; border-radius: 3px; margin-top: 3px; overflow: hidden; }
.mem-fill { height: 100%; border-radius: 3px; transition: width 0.4s; }

.context-menu {
  position: fixed; background: #fff; border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  z-index: 3000; min-width: 150px; padding: 4px 0;
}
.menu-header { padding: 8px 14px; font-size: 12px; font-weight: 600; color: #111827; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 200px; }
.menu-item { padding: 8px 14px; font-size: 12.5px; cursor: pointer; color: #374151; }
.menu-item:hover { background: #f5f5f7; }
.menu-item.danger { color: #b91c1c; }
.menu-item.danger:hover { background: #fef2f2; }
.menu-divider { height: 1px; background: #e5e7eb; margin: 4px 0; }
</style>
