<template>
  <div class="rt-create">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <span class="title"><Box :size="15" /> 创建运行环境 · {{ template?.label || selectedType }}</span>
      <button class="btn" style="margin-left:auto;" @click="emit('close')">关闭</button>
    </div>

    <div class="body">
      <div v-if="loadingTemplates" class="empty">模板加载中...</div>
      <div v-else-if="!templates.length" class="empty">模板不可用（后端未返回运行时模板）</div>

      <template v-else>
        <!-- 基础信息 -->
        <div class="form-grid">
          <label class="field span-2">
            <span class="field-label">名称 <b class="req">*</b></span>
            <input v-model.trim="form.name" class="inp" placeholder="如: 我的Python项目"
                   :class="{ err: err && !form.name }" />
          </label>

          <label class="field span-2">
            <span class="field-label">项目目录 <b class="req">*</b></span>
            <input v-model.trim="form.project_dir" class="inp mono"
                   placeholder="/www/myapp 或 C:\www\myapp" />
            <span class="field-hint">宿主机上的绝对路径，将挂载到容器内工作目录 {{ form.workdir }}</span>
          </label>

          <label class="field span-2">
            <span class="field-label">启动命令</span>
            <input v-model="form.start_command" class="inp mono" :placeholder="template?.suggest_cmd" />
            <span class="field-hint">在容器工作目录内执行的启动命令，留空使用 {{ template?.suggest_cmd }}</span>
          </label>

          <label class="field">
            <span class="field-label">应用（语言版本）<b class="req">*</b></span>
            <select v-model="form.app_version" class="inp">
              <option v-for="v in template?.versions" :key="v" :value="v">{{ v }}</option>
            </select>
            <span class="field-hint">对应镜像 {{ template?.image }}</span>
          </label>

          <label class="field">
            <span class="field-label">容器名称</span>
            <input v-model.trim="form.container_name" class="inp mono" placeholder="留空自动生成"
                   :class="{ err: err && form.container_name && !containerNameValid }" />
            <span class="field-hint">仅字母/数字/_/-/.，留空自动生成 graw-rt-&lt;名称&gt;</span>
          </label>

          <label class="field span-2">
            <span class="field-label">备注</span>
            <textarea v-model="form.notes" class="inp" rows="2" placeholder="可选，记录该运行环境的用途"></textarea>
          </label>
        </div>

        <!-- 高级配置 -->
        <div class="adv-head" @click="advOpen = !advOpen">
          <ChevronDown :size="14" :class="{ 'rot': advOpen }" />
          <span>高级配置</span>
          <span class="adv-count">端口 {{ form.ports.length }} · 环境变量 {{ form.env.length }} · 挂载 {{ form.mounts.length }} · 主机映射 {{ form.hosts.length }}</span>
        </div>

        <div v-if="advOpen" class="adv-body">
          <!-- 端口映射 -->
          <section class="adv-sec">
            <div class="sec-head">
              <span>端口映射</span>
              <button class="btn sm" @click="form.ports.push({ external: '', internal: '', protocol: 'tcp' })">添加端口</button>
            </div>
            <div v-if="form.ports.length" class="kv-list">
              <div v-for="(p, i) in form.ports" :key="i" class="kv-row">
                <input v-model.trim="p.external" class="inp sm mono" placeholder="外部端口" />
                <span class="colon">:</span>
                <input v-model.trim="p.internal" class="inp sm mono" placeholder="内部端口" />
                <select v-model="p.protocol" class="inp sm proto">
                  <option value="tcp">tcp</option>
                  <option value="udp">udp</option>
                </select>
                <button class="iconbtn danger" @click="form.ports.splice(i, 1)"><X :size="13" /></button>
              </div>
            </div>
            <span class="field-hint" v-else>未配置端口映射</span>
          </section>

          <!-- 环境变量 -->
          <section class="adv-sec">
            <div class="sec-head">
              <span>环境变量</span>
              <button class="btn sm" @click="form.env.push({ name: '', value: '' })">添加变量</button>
            </div>
            <div v-if="form.env.length" class="kv-list">
              <div v-for="(e, i) in form.env" :key="i" class="kv-row">
                <input v-model.trim="e.name" class="inp sm mono" placeholder="名称" />
                <span class="eq">=</span>
                <input v-model.trim="e.value" class="inp sm mono grow" placeholder="值" />
                <button class="iconbtn danger" @click="form.env.splice(i, 1)"><X :size="13" /></button>
              </div>
            </div>
            <span class="field-hint" v-else>未配置环境变量</span>
          </section>

          <!-- 挂载 -->
          <section class="adv-sec">
            <div class="sec-head">
              <span>挂载</span>
              <button class="btn sm" @click="form.mounts.push({ host: '', container: '', mode: 'rw' })">添加挂载</button>
            </div>
            <div v-if="form.mounts.length" class="kv-list">
              <div v-for="(m, i) in form.mounts" :key="i" class="kv-row">
                <input v-model.trim="m.host" class="inp sm mono grow" placeholder="本机目录" />
                <span class="colon">→</span>
                <input v-model.trim="m.container" class="inp sm mono grow2" placeholder="容器目录" />
                <select v-model="m.mode" class="inp sm proto">
                  <option value="rw">读写</option>
                  <option value="ro">只读</option>
                </select>
                <button class="iconbtn danger" @click="form.mounts.splice(i, 1)"><X :size="13" /></button>
              </div>
            </div>
            <span class="field-hint" v-else>未配置额外挂载（项目目录已自动挂载到 {{ form.workdir }}）</span>
          </section>

          <!-- 主机映射 -->
          <section class="adv-sec">
            <div class="sec-head">
              <span>主机映射</span>
              <button class="btn sm" @click="form.hosts.push({ hostname: '', ip: '' })">添加映射</button>
            </div>
            <div v-if="form.hosts.length" class="kv-list">
              <div v-for="(h, i) in form.hosts" :key="i" class="kv-row">
                <input v-model.trim="h.hostname" class="inp sm mono" placeholder="主机名" />
                <span class="colon">→</span>
                <input v-model.trim="h.ip" class="inp sm mono grow" placeholder="IP" />
                <button class="iconbtn danger" @click="form.hosts.splice(i, 1)"><X :size="13" /></button>
              </div>
            </div>
            <span class="field-hint" v-else>未配置主机名到 IP 的映射（--add-host）</span>
          </section>
        </div>

        <div v-if="err" class="error-banner">{{ err }}</div>

        <div class="actions">
          <button class="btn" @click="emit('close')">取消</button>
          <button class="btn primary" @click="save" :disabled="saving">
            <Loader2 v-if="saving" :size="14" class="spin" /> 创建并启动
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { runtimeApi } from '../../api'
import { Box, ChevronDown, X, Loader2 } from 'lucide-vue-next'

const props = defineProps({ type: { type: String, default: 'python' } })
const emit = defineEmits(['close', 'created'])

const selectedType = computed(() => props.type)
const templates = ref([])
const loadingTemplates = ref(true)
const advOpen = ref(false)
const saving = ref(false)
const err = ref('')

const template = computed(() => templates.value.find(t => t.type === selectedType.value) || null)

const form = reactive({
  name: '',
  project_dir: '',
  start_command: '',
  app_version: '',
  container_name: '',
  notes: '',
  ports: [],
  env: [],
  mounts: [],
  hosts: [],
  workdir: '/app'
})

const CONTAINER_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/
const containerNameValid = computed(() => !form.container_name || CONTAINER_NAME_RE.test(form.container_name))

function resetForm() {
  form.name = ''
  form.project_dir = ''
  form.start_command = ''
  form.app_version = (template.value?.default_version || template.value?.versions?.[0] || '')
  form.container_name = ''
  form.notes = ''
  form.ports = []
  form.env = []
  form.mounts = []
  form.hosts = []
  form.workdir = template.value?.workdir || '/app'
}

async function loadTemplates() {
  loadingTemplates.value = true
  try {
    const data = await runtimeApi.templates()
    templates.value = data.runtimes || []
    // 同步模板选择到 form
    form.app_version = template.value?.default_version || template.value?.versions?.[0] || ''
    form.workdir = template.value?.workdir || '/app'
    form.start_command = template.value?.suggest_cmd || ''
  } catch (e) {
    err.value = '加载运行时模板失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loadingTemplates.value = false
  }
}

function isAbs(p) {
  if (!p) return false
  return /^[A-Za-z]:[\\/]/.test(p) || /^[\\/]/.test(p)
}

async function save() {
  err.value = ''
  // 表单校验
  if (!form.name) { err.value = '请填写名称'; return }
  if (!form.project_dir.trim()) { err.value = '请填写项目目录'; return }
  if (!isAbs(form.project_dir.trim())) { err.value = '项目目录必须为绝对路径'; return }
  if (form.container_name && !containerNameValid.value) { err.value = '容器名称只能包含字母/数字/_/./-' ; return }

  const body = {
    type: selectedType.value,
    name: form.name,
    project_dir: form.project_dir,
    start_command: form.start_command,
    app_version: form.app_version,
    container_name: form.container_name,
    notes: form.notes,
    ports: form.ports.filter(p => p.external && p.internal).map(p => ({
      external: p.external, internal: p.internal, protocol: p.protocol
    })),
    env: form.env.filter(e => e.name).map(e => ({ name: e.name, value: e.value })),
    mounts: form.mounts.filter(m => m.host && m.container).map(m => ({
      host: m.host, container: m.container, mode: m.mode
    })),
    hosts: form.hosts.filter(h => h.hostname && h.ip).map(h => ({ hostname: h.hostname, ip: h.ip }))
  }

  saving.value = true
  try {
    const created = await runtimeApi.create(body)
    emit('created', created)
    emit('close')
  } catch (e) {
    err.value = '创建失败：' + (e.response?.data?.detail || e.message)
  } finally {
    saving.value = false
  }
}

onMounted(loadTemplates)
</script>

<style scoped>
.rt-create { position: relative; display: flex; flex-direction: column; height: 100%; overflow: hidden; }
.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
.title { font-weight: 700; font-size: 13.5px; display: inline-flex; align-items: center; gap: 6px; }
.body { flex: 1; overflow-y: auto; padding: 14px 16px; }
.empty { text-align: center; color: #9ca3af; padding: 40px; }

.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; }
.field { display: flex; flex-direction: column; gap: 3px; }
.field.span-2 { grid-column: span 2; }
.field-label { font-size: 12px; color: #374151; }
.field-label .req { color: #dc2626; }
.inp { padding: 6px 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12.5px; outline: none; background: #fff; font-family: inherit; }
.inp.mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; }
.inp:focus { border-color: #6366f1; box-shadow: 0 0 0 2px rgba(99,102,241,.15); }
.inp.err { border-color: #dc2626; }
.inp.sm { padding: 4px 6px; font-size: 12px; }
.inp.grow { flex: 1; min-width: 80px; }
.inp.grow2 { flex: 1.3; min-width: 90px; }
select.inp { cursor: pointer; }
.field-hint { font-size: 11px; color: #9ca3af; }

/* 高级配置 */
.adv-head { display: flex; align-items: center; gap: 6px; margin-top: 18px; padding: 8px 10px; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; color: #374151; user-select: none; }
.adv-head svg.rot { transform: rotate(180deg); }
.adv-count { margin-left: auto; font-weight: 400; font-size: 11px; color: #9ca3af; }
.adv-body { padding: 12px 2px; display: flex; flex-direction: column; gap: 16px; }
.adv-sec { display: flex; flex-direction: column; gap: 6px; }
.sec-head { display: flex; align-items: center; justify-content: space-between; font-size: 12.5px; font-weight: 600; color: #374151; }
.kv-list { display: flex; flex-direction: column; gap: 6px; }
.kv-row { display: flex; align-items: center; gap: 6px; }
.kv-row .colon, .kv-row .eq { color: #9ca3af; font-size: 12px; }
.kv-row .proto { width: 74px; }

.error-banner { margin-top: 12px; padding: 6px 10px; background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; border-radius: 6px; font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 6px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.primary:disabled { opacity: .6; cursor: not-allowed; }
.btn.sm { padding: 3px 8px; font-size: 11px; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; display: inline-flex; align-items: center; color: #6b7280; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; color: #b91c1c; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>