<!--
  WinProcess.vue — 进程管理器窗口
  作用：实时查看服务器进程列表（PID/名称/用户/状态/CPU%/MEM%），支持按名称搜索，
        并对选中进程执行 KILL（强制终止）或 TERM（优雅终止）。
  数据：列表由 /api/process/list 返回，KILL/TERM 经对应接口下发到后端。
  打开方式：桌面快捷方式或开始菜单的「进程管理」。
-->
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
import { ref, onMounted } from 'vue'                  // Vue 响应式与生命周期

const search = ref('')            // 进程名搜索关键字
const processes = ref([])         // 当前进程列表

// 拉取进程列表（按关键字过滤）
async function loadProcesses() {
  try {
    const r = await fetch('/api/process/list?search=' + encodeURIComponent(search.value))
    processes.value = await r.json()
  } catch (e) {}                  // 拉取失败静默，保留上一次列表
}
// 强制终止（SIGKILL）：不可被进程捕获，立即结束
async function killProcess(pid) {
  await fetch(`/api/process/${pid}/kill`, { method: 'POST' })
  await loadProcesses()           // 操作后刷新列表
}
// 优雅终止（SIGTERM）：通知进程自行清理后退出
async function terminateProcess(pid) {
  await fetch(`/api/process/${pid}/terminate`, { method: 'POST' })
  await loadProcesses()
}

onMounted(loadProcesses)          // 打开窗口即加载进程列表
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
