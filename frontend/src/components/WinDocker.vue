<template>
  <div class="docker-wrap">
    <div class="docker-toolbar">
      <button class="win7-btn2" @click="loadContainers">刷新</button>
      <span class="docker-stats">容器: {{ containers.length }} | 镜像: {{ imageCount }}</span>
    </div>
    <div class="docker-table-wrap">
      <table class="docker-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>名称</th>
            <th>镜像</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in containers" :key="c.id">
            <td>{{ c.id }}</td>
            <td>{{ c.name }}</td>
            <td>{{ c.image }}</td>
            <td>
              <span class="status-badge" :class="c.state === 'running' ? 'green' : 'red'">{{ c.status }}</span>
            </td>
            <td>
              <button class="win7-btn2" :disabled="c.state === 'running'" @click="startC(c.id)">启动</button>
              <button class="win7-btn2" :disabled="c.state !== 'running'" @click="stopC(c.id)">停止</button>
              <button class="win7-btn2" @click="restartC(c.id)">重启</button>
              <button class="win7-btn2 danger" @click="removeC(c.id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="error" class="docker-error">{{ error }}</div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const containers = ref([])
const imageCount = ref(0)
const error = ref('')

async function loadContainers() {
  error.value = ''
  try {
    const r = await fetch('/api/docker/containers?all=true')
    if (!r.ok) { error.value = 'Docker 不可用'; return }
    containers.value = await r.json()
    const ir = await fetch('/api/docker/info')
    if (ir.ok) {
      const info = await ir.json()
      imageCount.value = info.images
    }
  } catch (e) { error.value = 'Docker 不可用' }
}
async function startC(id) {
  await fetch(`/api/docker/containers/${id}/start`, { method: 'POST' })
  await loadContainers()
}
async function stopC(id) {
  await fetch(`/api/docker/containers/${id}/stop`, { method: 'POST' })
  await loadContainers()
}
async function restartC(id) {
  await fetch(`/api/docker/containers/${id}/restart`, { method: 'POST' })
  await loadContainers()
}
async function removeC(id) {
  await fetch(`/api/docker/containers/${id}`, { method: 'DELETE' })
  await loadContainers()
}

onMounted(loadContainers)
</script>

<style scoped>
.docker-wrap { height: 100%; display: flex; flex-direction: column; background: #fff; }
.docker-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: #f5f5f7;
  border-bottom: 1px solid rgba(0,0,0,0.06);
}
.docker-stats {
  font-size: 12px;
  color: #6e6e73;
}
.docker-table-wrap { flex: 1; overflow: auto; }
.docker-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.docker-table th, .docker-table td {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid rgba(0,0,0,0.06);
}
.docker-table th {
  background: #f5f5f7;
  font-weight: 600;
  color: #1d1d1f;
  position: sticky;
  top: 0;
  z-index: 1;
}
.docker-table tr:hover td { background: #f5f5f7; }
.status-badge {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}
.status-badge.green { background: rgba(40, 200, 64, 0.15); color: #1d7a2a; }
.status-badge.red { background: rgba(255, 59, 48, 0.15); color: #c5271e; }
.docker-error {
  color: #ff3b30;
  padding: 20px;
  text-align: center;
  font-size: 13px;
}
.win7-btn2 {
  border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px;
  background: #fff;
  color: #1d1d1f;
  padding: 4px 12px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  margin-right: 4px;
}
.win7-btn2:hover { background: #f5f5f7; }
.win7-btn2:active { background: #ebebed; }
.win7-btn2:disabled { opacity: 0.5; cursor: default; }
.win7-btn2.danger {
  background: #ff3b30;
  color: #fff;
  border-color: transparent;
}
.win7-btn2.danger:hover { background: #e0342a; }
</style>
