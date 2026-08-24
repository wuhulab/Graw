<template>
  <div class="proc-wrap">
    <div class="proc-toolbar">
      <input v-model="search" placeholder="搜索进程..." class="proc-search" @input="loadProcesses" />
      <button class="win7-btn2" @click="loadProcesses">刷新</button>
      <span class="proc-count">进程数: {{ processes.length }}</span>
    </div>
    <div class="proc-table-wrap">
      <table class="proc-table">
        <thead>
          <tr>
            <th>PID</th>
            <th>名称</th>
            <th>用户</th>
            <th>状态</th>
            <th>CPU%</th>
            <th>MEM%</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in processes" :key="p.pid">
            <td>{{ p.pid }}</td>
            <td>{{ p.name }}</td>
            <td>{{ p.username }}</td>
            <td>{{ p.status }}</td>
            <td>{{ p.cpu_percent.toFixed(1) }}</td>
            <td>{{ p.memory_percent.toFixed(1) }}</td>
            <td>
              <button class="win7-btn2 danger" @click="killProcess(p.pid)">KILL</button>
              <button class="win7-btn2" @click="terminateProcess(p.pid)">TERM</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const search = ref('')
const processes = ref([])

async function loadProcesses() {
  try {
    const r = await fetch('/api/process/list?search=' + encodeURIComponent(search.value))
    processes.value = await r.json()
  } catch (e) {}
}
async function killProcess(pid) {
  await fetch(`/api/process/${pid}/kill`, { method: 'POST' })
  await loadProcesses()
}
async function terminateProcess(pid) {
  await fetch(`/api/process/${pid}/terminate`, { method: 'POST' })
  await loadProcesses()
}

onMounted(loadProcesses)
</script>

<style scoped>
.proc-wrap { height: 100%; display: flex; flex-direction: column; background: #fff; }
.proc-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: #f5f5f7;
  border-bottom: 1px solid rgba(0,0,0,0.06);
}
.proc-search {
  flex: 1;
  border: 1px solid rgba(0,0,0,0.1);
  border-radius: 6px;
  padding: 5px 10px;
  font-size: 12px;
  background: #fff;
  color: #1d1d1f;
  outline: none;
}
.proc-search:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10, 132, 255, 0.2);
}
.proc-count {
  font-size: 12px;
  color: #6e6e73;
}
.proc-table-wrap {
  flex: 1;
  overflow: auto;
}
.proc-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.proc-table th, .proc-table td {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid rgba(0,0,0,0.06);
}
.proc-table th {
  background: #f5f5f7;
  font-weight: 600;
  color: #1d1d1f;
  position: sticky;
  top: 0;
  z-index: 1;
}
.proc-table tr:hover td { background: #f5f5f7; }
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
.win7-btn2.danger {
  background: #ff3b30;
  color: #fff;
  border-color: transparent;
}
.win7-btn2.danger:hover { background: #e0342a; }
</style>
