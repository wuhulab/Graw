<template>
  <div style="display:flex; flex-direction:column; height:100%;" @click="closeMenus">
    <!-- 工具栏：视图下拉切换 -->
    <div class="toolbar">
      <select v-model="view" class="view-select" @change="onViewChange">
        <option value="containers">容器</option>
        <option value="config">配置</option>
        <option value="compose">编排</option>
        <option value="images">镜像</option>
        <option value="networks">网络</option>
      </select>
      <button class="btn" @click="refreshCurrent">刷新</button>
      <span style="margin-left:8px; color:#0a3d7a;" v-if="status && view === 'containers'">
        <template v-if="status.available">
          Docker {{ status.server_version }} · 容器 {{ status.containers_running }}/{{ status.containers }} · 镜像 {{ status.images }}
        </template>
        <template v-else>
          Docker 不可用：{{ status.reason }}
        </template>
      </span>
      <span v-if="loading" style="margin-left:auto;color:#888;">加载中...</span>
      <span class="hint" v-if="view === 'containers'">右键点击容器打开操作菜单</span>
    </div>

    <div style="flex:1; overflow:auto;">
      <!-- ================= 容器视图 ================= -->
      <div v-if="view === 'containers'">
        <div v-if="status && !status.available" class="empty">
          无法连接到 Docker。请确认 Docker 服务正在运行，并且当前用户具有访问权限。
        </div>
        <table v-else class="dt">
          <thead>
            <tr>
              <th style="width:28px;">★</th>
              <th>名称</th>
              <th>镜像</th>
              <th>状态</th>
              <th style="width:100px;">CPU</th>
              <th style="width:160px;">内存</th>
              <th>端口</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in sortedContainers" :key="c.id" @contextmenu.prevent="onContextMenu($event, c)">
              <td style="text-align:center;">
                <span style="cursor:pointer;" :class="{ 'star-on': c.starred }" :title="c.starred ? '已标星' : '未标星'" @click.stop="toggleStar(c)">
                  {{ c.starred ? '★' : '☆' }}
                </span>
              </td>
              <td>
                {{ c.name }}
                <span v-if="c.note" style="margin-left:4px;" title="有备注">{{ c.note.length > 20 ? c.note.slice(0, 20) + '…' : c.note }}</span>
                <div style="font-size:10px;color:#888;">{{ c.id }}</div>
              </td>
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
              <td colspan="7"><div class="empty">暂无容器</div></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- ================= 配置视图 ================= -->
      <div v-else-if="view === 'config'" class="config-view">
        <div class="config-card">
          <div class="config-title">引擎配置 · {{ config.engine || '-' }}</div>
          <div class="config-path">配置文件：{{ config.config_path || '未知' }}（{{ config.config_type }}）</div>
          <div v-if="!config.iptables_supported" class="config-warn">
            当前引擎为 {{ config.engine }}，iptables 仅对 Docker（daemon.json）生效，此处仅保存为面板记录。
          </div>

          <label class="cfg-row check">
            <input type="checkbox" v-model="form.mirror_enabled" />
            <span>启用镜像加速</span>
          </label>

          <div class="cfg-row" v-if="form.mirror_enabled">
            <div class="cfg-label">镜像加速网址（每行一个）</div>
            <textarea v-model="form.mirrors" class="cfg-textarea" placeholder="https://docker.m.daocloud.io"></textarea>
          </div>

          <div class="cfg-row">
            <div class="cfg-label">私有仓库（每行一个，格式 地址[:端口]）</div>
            <textarea v-model="form.private_registries" class="cfg-textarea" placeholder="10.0.0.2:5000"></textarea>
          </div>

          <label class="cfg-row check">
            <input type="checkbox" v-model="form.iptables" :disabled="!config.iptables_supported" />
            <span>iptables</span>
          </label>

          <div class="cfg-actions">
            <button class="btn primary" @click="saveConfig">保存配置</button>
            <button class="btn" @click="openConfigEditor">打开 Docker 配置文件</button>
          </div>
          <div v-if="configMsg" class="cfg-msg" :class="{ err: configMsgErr }">{{ configMsg }}</div>
        </div>
      </div>

      <!-- ================= 编排视图 ================= -->
      <div v-else-if="view === 'compose'">
        <table class="dt">
          <thead>
            <tr>
              <th>项目</th>
              <th>服务</th>
              <th style="width:110px;">容器状态</th>
              <th style="width:180px;">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in composeProjects" :key="p.name">
              <td>
                {{ p.name }}
                <div style="font-size:10px;color:#888;">{{ p.path }}</div>
              </td>
              <td style="font-size:11px;">{{ p.services.join(', ') || '-' }}</td>
              <td>
                <span :style="{ color: p.running === p.total && p.total > 0 ? '#2a8f3c' : '#a04040' }">
                  {{ p.running }}/{{ p.total }}
                </span>
                <span v-if="p.total === 0" style="color:#aaa;">未运行</span>
              </td>
              <td>
                <button class="btn sm" @click="composeOp(p, 'up')">启动</button>
                <button class="btn sm" @click="composeOp(p, 'restart')">重启</button>
                <button class="btn sm" @click="composeOp(p, 'down')">停止</button>
              </td>
            </tr>
            <tr v-if="!composeProjects.length">
              <td colspan="4"><div class="empty">暂无 compose 项目</div></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- ================= 镜像视图 ================= -->
      <div v-else-if="view === 'images'">
        <table class="dt">
          <thead>
            <tr>
              <th>标签</th>
              <th>镜像 ID</th>
              <th style="width:100px;">大小</th>
              <th style="width:150px;">创建时间</th>
              <th style="width:80px;">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="img in images" :key="img.id">
              <td>{{ img.tags.length ? img.tags.join(', ') : '&lt;none&gt;' }}</td>
              <td style="font-family:monospace;font-size:11px;">{{ img.id }}</td>
              <td>{{ formatBytes(img.size) }}</td>
              <td>{{ formatTime(img.created) }}</td>
              <td><button class="btn sm danger" @click="removeImageItem(img)">删除</button></td>
            </tr>
            <tr v-if="!images.length">
              <td colspan="5"><div class="empty">暂无镜像</div></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- ================= 网络视图 ================= -->
      <div v-else-if="view === 'networks'">
        <table class="dt">
          <thead>
            <tr>
              <th>名称</th>
              <th>驱动</th>
              <th style="width:150px;">子网</th>
              <th style="width:150px;">网关</th>
              <th style="width:150px;">创建时间</th>
              <th style="width:80px;">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="n in networks" :key="n.name">
              <td>{{ n.name }}</td>
              <td>{{ n.driver }}</td>
              <td style="font-family:monospace;font-size:11px;">{{ n.subnets.map(s => s.subnet).join(', ') || '-' }}</td>
              <td style="font-family:monospace;font-size:11px;">{{ n.subnets.map(s => s.gateway).join(', ') || '-' }}</td>
              <td>{{ formatTime(n.created) }}</td>
              <td><button class="btn sm danger" @click="removeNetworkItem(n)">删除</button></td>
            </tr>
            <tr v-if="!networks.length">
              <td colspan="6"><div class="empty">暂无网络</div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 右键菜单（容器） -->
    <Teleport to="body">
      <div v-if="ctxMenu.show" class="context-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }" @click.stop>
        <div class="menu-header">{{ ctxMenu.item?.name }}</div>
        <div class="menu-divider"></div>
        <div class="menu-item" v-if="ctxMenu.item?.state !== 'running'" @click="menuAct('start')">启动</div>
        <div class="menu-item" v-if="ctxMenu.item?.state === 'running'" @click="menuAct('stop')">停止</div>
        <div class="menu-item" v-if="ctxMenu.item?.state === 'running'" @click="menuAct('restart')">重启</div>
        <div class="menu-item" @click="menuLogs">查看日志</div>
        <div class="menu-item" @click="menuEnterDir">进入安装目录</div>
        <div class="menu-item" @click="menuOpenTerminal">打开容器内终端</div>
        <div class="menu-divider"></div>
        <div class="menu-item" @click="menuToggleStar">{{ ctxMenu.item?.starred ? '取消标星' : '标星' }}</div>
        <div class="menu-item" @click="menuEditNotes">备注笔记</div>
        <div class="menu-divider"></div>
        <div class="menu-item" @click="menuDetails">详细信息</div>
        <div class="menu-item" @click="menuBackup">备份</div>
        <div class="menu-item" @click="menuUpgrade">升级</div>
        <div class="menu-item" @click="menuCommit">制作镜像</div>
        <div class="menu-divider"></div>
        <div class="menu-item danger" @click="menuAct('remove')">删除容器</div>
      </div>
    </Teleport>

    <!-- 备注笔记编辑弹窗 -->
    <div v-if="noteDialog.show" class="note-mask" @click.self="noteDialog.show = false">
      <div class="note-dialog">
        <div class="note-title">备注笔记 · {{ noteDialog.item?.name }}</div>
        <textarea v-model="noteDialog.text" class="note-textarea" placeholder="输入备注内容，留空保存将清除备注"></textarea>
        <div class="note-actions">
          <button class="btn" @click="noteDialog.show = false">取消</button>
          <button class="btn primary" @click="saveNotes">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { dockerApi } from '../../api'

const emit = defineEmits(['openLogs', 'openContainerTerminal', 'openContainerDetails', 'openFiles', 'openDockerConfigEditor'])

// 当前视图：containers / config / compose / images / networks
const view = ref('containers')
const loading = ref(false)

// ---------- 容器 ----------
const status = ref(null)
const containers = ref([])
const ctxMenu = ref({ show: false, x: 0, y: 0, item: null })
const noteDialog = ref({ show: false, text: '', item: null })
let timer = null

// ---------- 配置 ----------
const config = ref({})
const configMsg = ref('')
const configMsgErr = ref(false)
const form = ref({ mirror_enabled: false, mirrors: '', private_registries: '', iptables: true })

// ---------- 编排 ----------
const composeProjects = ref([])

// ---------- 镜像 ----------
const images = ref([])

// ---------- 网络 ----------
const networks = ref([])

// 标星的容器优先显示在最上面
const sortedContainers = computed(() => {
  return [...containers.value].sort((a, b) => {
    if (a.starred !== b.starred) return a.starred ? -1 : 1
    return (a.name || '').localeCompare(b.name || '')
  })
})

// ---------- 视图切换与数据加载 ----------
async function onViewChange() {
  loading.value = true
  try {
    if (view.value === 'config') await loadConfig()
    else if (view.value === 'compose') await loadCompose()
    else if (view.value === 'images') await loadImages()
    else if (view.value === 'networks') await loadNetworks()
    else await refreshContainers()
  } finally {
    loading.value = false
  }
}

function refreshCurrent() {
  onViewChange()
}

async function refreshContainers() {
  try {
    status.value = await dockerApi.status()
    if (status.value.available) {
      containers.value = await dockerApi.containers()
    } else {
      containers.value = []
    }
  } catch (e) {
    status.value = { available: false, reason: e.message }
  }
}

async function loadConfig() {
  config.value = await dockerApi.config()
  form.value = {
    mirror_enabled: !!config.value.mirror_enabled,
    mirrors: (config.value.mirrors || []).join('\n'),
    private_registries: (config.value.private_registries || []).join('\n'),
    iptables: !!config.value.iptables
  }
  configMsg.value = ''
}

async function loadCompose() {
  composeProjects.value = await dockerApi.composeProjects()
}

async function loadImages() {
  images.value = await dockerApi.images()
}

async function loadNetworks() {
  networks.value = await dockerApi.networks()
}

// ---------- 配置保存 ----------
async function saveConfig() {
  try {
    const body = {
      mirror_enabled: !!form.value.mirror_enabled,
      mirrors: form.value.mirrors.split('\n').map(s => s.trim()).filter(Boolean),
      private_registries: form.value.private_registries.split('\n').map(s => s.trim()).filter(Boolean),
      iptables: !!form.value.iptables
    }
    const r = await dockerApi.saveConfig(body)
    configMsgErr = false
    configMsg.value = `配置已保存 → ${r.config_path}` + (r.iptables_supported ? '' : '（iptables 仅记录，当前引擎不支持）')
  } catch (e) {
    configMsgErr = true
    configMsg.value = '保存失败：' + (e.response?.data?.detail || e.message)
  }
}

// 打开 Docker 配置文件（独立编辑器窗口）
function openConfigEditor() {
  emit('openDockerConfigEditor')
}

// ---------- 编排操作 ----------
async function composeOp(p, action) {
  const label = { up: '启动', down: '停止', restart: '重启' }[action]
  if (action === 'down' && !confirm(`确认停止 compose 项目「${p.name}」？`)) return
  try {
    await dockerApi.composeAction(p.name, action)
    alert(`${label}「${p.name}」成功`)
    await loadCompose()
  } catch (e) {
    alert(`${label}失败：` + (e.response?.data?.detail || e.message))
  }
}

// ---------- 镜像操作 ----------
async function removeImageItem(img) {
  const tag = img.tags.length ? img.tags.join(', ') : img.id
  if (!confirm(`确认删除镜像「${tag}」？`)) return
  try {
    await dockerApi.removeImage(img.id)
    await loadImages()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 网络操作 ----------
async function removeNetworkItem(n) {
  if (!confirm(`确认删除网络「${n.name}」？\n使用中的网络无法删除。`)) return
  try {
    await dockerApi.removeNetwork(n.name)
    await loadNetworks()
  } catch (e) {
    alert('删除失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 容器操作（右键菜单） ----------
async function act(id, action) {
  if (action === 'remove' && !confirm('确认删除该容器？')) return
  try {
    await dockerApi.action(id, action)
    await refreshContainers()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

function closeMenus() {
  ctxMenu.value.show = false
}

function onContextMenu(e, c) {
  const x = Math.min(e.clientX, window.innerWidth - 180)
  const y = Math.min(e.clientY, window.innerHeight - 260)
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

function menuOpenTerminal() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  if (it.state !== 'running') {
    alert('容器未运行，无法打开终端')
    return
  }
  emit('openContainerTerminal', { id: it.id, name: it.name })
}

async function menuEnterDir() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  try {
    const info = await dockerApi.inspect(it.id)
    if (info.install_dir) {
      emit('openFiles', { path: info.install_dir })
    } else {
      alert('未找到该容器的安装目录（非应用商店安装的容器无法定位）')
    }
  } catch (e) {
    alert('获取安装目录失败：' + (e.response?.data?.detail || e.message))
  }
}

async function menuToggleStar() {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) await toggleStar(it)
}

async function toggleStar(c) {
  try {
    const r = await dockerApi.toggleStar(c.id)
    c.starred = r.starred
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

function menuEditNotes() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  noteDialog.value = { show: true, text: it.note || '', item: it }
}

async function saveNotes() {
  const d = noteDialog.value
  try {
    await dockerApi.saveNotes(d.item.id, d.text)
    d.item.note = d.text.trim()
    d.show = false
  } catch (e) {
    alert('保存失败：' + (e.response?.data?.detail || e.message))
  }
}

function menuDetails() {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) emit('openContainerDetails', { id: it.id, name: it.name })
}

async function menuBackup() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  if (!confirm(`确认备份容器「${it.name}」？\n将导出其文件系统为 tar 包保存到服务器。`)) return
  try {
    const r = await dockerApi.backup(it.id)
    alert(`备份成功\n保存路径：${r.path}\n大小：${formatBytes(r.size)}`)
  } catch (e) {
    alert('备份失败：' + (e.response?.data?.detail || e.message))
  }
}

async function menuUpgrade() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  if (!confirm(`确认升级容器「${it.name}」？\n将重新拉取镜像并按原参数重建容器（需容器未运行或允许重启）。`)) return
  try {
    const r = await dockerApi.upgrade(it.id)
    alert(`升级成功\n镜像：${r.image}${r.new_container_id ? '\n新容器：' + r.new_container_id : ''}`)
    await refreshContainers()
  } catch (e) {
    alert('升级失败：' + (e.response?.data?.detail || e.message))
  }
}

async function menuCommit() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  const repo = prompt('请输入镜像名称（默认 graw-commit-<容器ID>）：', `graw-commit-${it.name || it.id}`)
  if (repo === null) return
  try {
    const r = await dockerApi.commit(it.id, repo.trim())
    alert(`镜像制作成功\n镜像：${r.image}`)
  } catch (e) {
    alert('制作镜像失败：' + (e.response?.data?.detail || e.message))
  }
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

function formatBytes(bytes) {
  if (bytes == null || isNaN(bytes)) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(v < 10 && i > 0 ? 2 : v < 100 ? 1 : 0) + ' ' + units[i]
}

function formatTime(t) {
  if (!t) return '-'
  let d = t
  if (typeof t === 'number') d = new Date(t * 1000)
  else if (typeof t === 'string') d = new Date(t)
  if (isNaN(d.getTime())) return String(t)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => {
  refreshContainers()
  // 仅容器视图定时刷新，其它视图按需加载
  timer = setInterval(() => {
    if (view.value === 'containers' && status.value?.available !== false) {
      refreshContainers()
    }
  }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.hint { margin-left: auto; color: #888; font-size: 11px; }
.empty { text-align: center; color: #9ca3af; padding: 40px; }
.mem-bar { width: 120px; height: 5px; background: #e5e7eb; border-radius: 3px; margin-top: 3px; overflow: hidden; }
.mem-fill { height: 100%; border-radius: 3px; transition: width 0.4s; }
.star-on { color: #d97706; }
.view-select {
  padding: 4px 8px; border: 1px solid #d1d5db; border-radius: 6px;
  font-size: 13px; background: #fff; color: #111827; cursor: pointer;
}
.btn.sm { padding: 2px 8px; font-size: 11px; margin-right: 4px; }
.btn.sm.danger { color: #b91c1c; }
.btn.primary { background: #0a3d7a; color: #fff; border-color: #0a3d7a; }

/* 配置视图 */
.config-view { padding: 14px; }
.config-card {
  max-width: 560px; border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; background: #fafafa;
}
.config-title { font-weight: 600; font-size: 14px; margin-bottom: 4px; }
.config-path { font-size: 11px; color: #6b7280; margin-bottom: 10px; font-family: Consolas, monospace; }
.config-warn {
  font-size: 12px; color: #b45309; background: #fef3c7; border: 1px solid #fde68a;
  border-radius: 6px; padding: 6px 10px; margin-bottom: 12px;
}
.cfg-row { margin: 10px 0; }
.cfg-row.check { display: flex; align-items: center; gap: 8px; font-size: 13px; cursor: pointer; }
.cfg-label { font-size: 12px; color: #374151; margin-bottom: 4px; }
.cfg-textarea {
  width: 100%; min-height: 56px; resize: vertical; padding: 8px;
  border: 1px solid #d1d5db; border-radius: 6px; font-size: 12.5px; box-sizing: border-box;
}
.cfg-actions { display: flex; gap: 8px; margin-top: 14px; }
.cfg-msg { margin-top: 10px; font-size: 12px; color: #2a8f3c; }
.cfg-msg.err { color: #b91c1c; }

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

/* 备注笔记弹窗 */
.note-mask { position: absolute; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.note-dialog { width: 420px; max-width: 90%; background: #fff; border-radius: 10px; padding: 16px; box-shadow: 0 12px 32px rgba(0,0,0,0.2); }
.note-title { font-weight: 600; margin-bottom: 10px; }
.note-textarea { width: 100%; height: 140px; resize: vertical; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; box-sizing: border-box; }
.note-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
