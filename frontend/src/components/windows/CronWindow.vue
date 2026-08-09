<template>
  <div class="cron-window">
    <div class="toolbar">
      <button class="btn primary" @click="openCreate"><Plus :size="14" /> 添加任务</button>
      <span class="hint">平台: {{ platform }}</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr><th>名称</th><th>周期</th><th>命令</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id">
            <td>{{ t.name }}</td>
            <td class="mono">{{ t.schedule }}</td>
            <td class="mono">{{ t.command }}</td>
            <td><span class="badge" :class="t.enabled ? 'ok' : 'off'">{{ t.enabled ? '启用' : '停用' }}</span></td>
            <td class="actions">
              <button class="iconbtn" title="立即执行" @click="runNow(t)"><Play :size="14" /></button>
              <button class="iconbtn" title="停用/启用" @click="toggleEnable(t)"><Power :size="14" /></button>
              <button class="iconbtn danger" title="删除" @click="remove(t)"><Trash2 :size="14" /></button>
            </td>
          </tr>
          <tr v-if="tasks.length === 0"><td colspan="5" class="empty">暂无计划任务</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="showModal" class="modal-overlay" @click.self="showModal=false">
      <div class="modal">
        <h3>添加计划任务</h3>
        <div class="form">
          <label>任务名称</label>
          <input v-model="form.name" placeholder="备份数据库" />
          <label>Cron 表达式（分 时 日 月 周）</label>
          <input v-model="form.schedule" placeholder="0 3 * * *" />
          <label>执行命令</label>
          <textarea v-model="form.command" rows="3" placeholder="bash 命令或脚本路径" />
          <div class="actions">
            <button class="btn" @click="showModal=false">取消</button>
            <button class="btn primary" @click="save">保存</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { cronApi } from '../../api'
import { Plus, Play, Power, Trash2 } from 'lucide-vue-next'

const tasks = ref([])
const platform = ref('')
const showModal = ref(false)
const form = ref({ name: '', schedule: '0 3 * * *', command: '' })

async function load() {
  const data = await cronApi.list()
  tasks.value = data.tasks || []
  platform.value = data.platform || ''
}

function openCreate() {
  form.value = { name: '', schedule: '0 3 * * *', command: '' }
  showModal.value = true
}

async function save() {
  await cronApi.create({ name: form.value.name, schedule: form.value.schedule, command: form.value.command })
  showModal.value = false
  await load()
}

async function runNow(t) {
  await cronApi.run(t.id)
  alert('任务已触发执行')
}

async function toggleEnable(t) {
  await cronApi.update(t.id, { enabled: !t.enabled })
  await load()
}

async function remove(t) {
  if (!confirm('确定删除此任务？')) return
  await cronApi.delete(t.id)
  await load()
}

onMounted(load)
</script>

<style scoped>
.cron-window { padding: 10px; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.hint { color: #6e6e73; font-size: 12px; }
.table-wrap { overflow: auto; max-height: 420px; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
th { background: #f9fafb; position: sticky; top: 0; }
.mono { font-family: ui-monospace, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.actions { display: flex; gap: 4px; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; }
.iconbtn:hover { background: #f9fafb; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
.empty { text-align: center; color: #9ca3af; padding: 24px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 480px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input, .form textarea { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
