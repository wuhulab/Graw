<!--
  Docker 总控窗口（后端 /api/docker + /api/dockervolumes + /api/containeredit 模块）
  作用：服务器 Docker 管理的统一入口，含多子视图：容器（启停/重启/日志/终端/备份/升级/
        制作镜像/备注/标星）、镜像（拉取/构建/打标签/删除）、数据卷、网络、compose 编排、引擎配置。
  后端模块：/api/docker（容器/镜像/网络/compose/配置）、/api/dockervolumes（数据卷）、/api/containeredit（容器资源编辑）。
  关键状态：view（当前子视图）、containers/status（由共享 docker store 经 watch 回填）、各弹窗与右键菜单状态。
  打开方式：桌面「Docker」卡片；容器列表走共享 store（多窗口共用一轮询连接，避免重复拉取）。
  删除容器/镜像/网络/卷均为高风险操作，需输入面板密码（ConfirmDialog）确认。
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;" @click="closeMenus">
    <!-- 工具栏：视图下拉切换 -->
    <div class="toolbar">
      <select v-model="view" class="view-select" @change="onViewChange">
        <option value="containers">容器</option>
        <option value="images">镜像</option>
        <option value="volumes">数据卷</option>
        <option value="networks">网络</option>
        <option value="compose">编排</option>
        <option value="config">配置</option>
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
        <div class="img-toolbar">
          <button class="btn" @click="openPull"><Download :size="13" /> 拉取镜像</button>
          <button class="btn" @click="openBuild"><Hammer :size="13" /> 构建镜像</button>
        </div>
        <table class="dt">
          <thead>
            <tr>
              <th>标签</th>
              <th>镜像 ID</th>
              <th style="width:100px;">大小</th>
              <th style="width:150px;">创建时间</th>
              <th style="width:140px;">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="img in images" :key="img.id">
              <td>{{ img.tags.length ? img.tags.join(', ') : '&lt;none&gt;' }}</td>
              <td style="font-family:monospace;font-size:11px;">{{ img.id }}</td>
              <td>{{ formatBytes(img.size) }}</td>
              <td>{{ formatTime(img.created) }}</td>
              <td>
                <button class="btn sm" @click="openTag(img)">打标签</button>
                <button class="btn sm danger" @click="removeImageItem(img)">删除</button>
              </td>
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

      <!-- ================= 数据卷视图 ================= -->
      <div v-else-if="view === 'volumes'">
        <table class="dt">
          <thead>
            <tr>
              <th>名称</th>
              <th style="width:120px;">驱动</th>
              <th>挂载点</th>
              <th style="width:80px;">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="v in volumes" :key="v.name">
              <td>
                {{ v.name }}
                <div style="font-size:10px;color:#888;">{{ v.mountpoint || '-' }}</div>
              </td>
              <td>{{ v.driver }}</td>
              <td style="font-family:monospace;font-size:11px;">{{ v.mountpoint || '-' }}</td>
              <td><button class="btn sm danger" @click="removeVolumeItem(v)">删除</button></td>
            </tr>
            <tr v-if="!volumes.length">
              <td colspan="4"><div class="empty">暂无数据卷</div></td>
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
        <div class="menu-item" @click="menuEdit">编辑</div>
        <div class="menu-divider"></div>
        <div class="menu-item" @click="menuToggleStar">{{ ctxMenu.item?.starred ? '取消标星' : '标星' }}</div>
        <div class="menu-item" @click="menuEditNotes">备注笔记</div>
        <div class="menu-divider"></div>
        <div class="menu-item" @click="menuDetails">详细信息</div>
        <div class="menu-item" @click="menuStats">资源图表</div>
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

    <!-- 拉取镜像弹窗 -->
    <div v-if="pullDialog.show" class="note-mask" @click.self="pullDialog.show = false">
      <div class="note-dialog">
        <div class="note-title">拉取镜像</div>
        <input v-model="pullDialog.name" class="note-input" placeholder="镜像名，如 nginx:1.25 或 registry.example.com/app:2.0" spellcheck="false" />
        <div class="note-actions">
          <button class="btn" :disabled="busy" @click="pullDialog.show = false">取消</button>
          <button class="btn primary" :disabled="busy || !pullDialog.name.trim()" @click="doPull">{{ busy ? '拉取中…' : '拉取' }}</button>
        </div>
        <div v-if="pullDialog.err" class="err-text">{{ pullDialog.err }}</div>
      </div>
    </div>

    <!-- 打标签弹窗 -->
    <div v-if="tagDialog.show" class="note-mask" @click.self="tagDialog.show = false">
      <div class="note-dialog">
        <div class="note-title">打标签 · {{ tagDialog.id }}</div>
        <input v-model="tagDialog.repo" class="note-input" placeholder="目标仓库名，如 myapp / myregistry/myapp" spellcheck="false" />
        <input v-model="tagDialog.tag" class="note-input" placeholder="标签（默认 latest）" spellcheck="false" style="margin-top:8px;" />
        <div class="note-actions">
          <button class="btn" :disabled="busy" @click="tagDialog.show = false">取消</button>
          <button class="btn primary" :disabled="busy || !tagDialog.repo.trim()" @click="doTag">{{ busy ? '保存中…' : '保存' }}</button>
        </div>
        <div v-if="tagDialog.err" class="err-text">{{ tagDialog.err }}</div>
      </div>
    </div>

    <!-- 构建镜像弹窗 -->
    <div v-if="buildDialog.show" class="note-mask" @click.self="buildDialog.show = false">
      <div class="note-dialog">
        <div class="note-title">构建镜像</div>
        <input v-model="buildDialog.name" class="note-input" placeholder="镜像名，如 myapp（不含标签）" spellcheck="false" />
        <input v-model="buildDialog.tag" class="note-input" placeholder="标签（默认 latest）" spellcheck="false" style="margin-top:8px;" />
        <input v-model="buildDialog.context_dir" class="note-input" placeholder="构建上下文目录（宿主机绝对路径，需含 Dockerfile）" spellcheck="false" style="margin-top:8px;" />
        <div class="note-actions">
          <button class="btn" :disabled="busy" @click="buildDialog.show = false">取消</button>
          <button class="btn primary" :disabled="busy || !buildDialog.name.trim() || !buildDialog.context_dir.trim()" @click="doBuild">{{ busy ? '构建中…' : '构建' }}</button>
        </div>
        <div v-if="buildDialog.err" class="err-text">{{ buildDialog.err }}</div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除容器/镜像/网络需输入面板密码 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="password"
      :title="confirm.title"
      :message="confirm.message"
      input-label="输入面板密码确认"
      placeholder="请输入当前面板密码"
      confirm-label="删除"
      @confirm="doConfirmDanger"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
// 响应式状态、生命周期与计算属性
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
// 图标（拉取/构建按钮）
import { Download, Hammer } from 'lucide-vue-next'
// Docker API：容器/镜像/网络/compose/配置/数据卷
import { dockerApi } from '../../api'
// 共享 Docker store：多窗口共用一份容器数据与轮询连接（startDocker 启动轮询、refresh 触发刷新）
import { docker, startDocker, refresh as refreshDockerStore } from '../../store/docker'
// 高风险操作「输入面板密码」二次确认弹窗
import ConfirmDialog from '../ConfirmDialog.vue'

// 子窗口事件：打开日志 / 容器内终端 / 详情 / 文件 / 配置编辑器 / 资源图表 / 容器编辑
const emit = defineEmits(['openLogs', 'openContainerTerminal', 'openContainerDetails', 'openFiles', 'openDockerConfigEditor', 'openContainerStats', 'openContainerEdit'])

// 当前视图：containers / config / compose / images / networks / volumes
const view = ref('containers')
const loading = ref(false)

// ---------- 容器：Docker 守护状态与容器列表 ----------
const status = ref(null)        // Docker 引擎状态（可用与否、版本、运行数等）
const containers = ref([])      // 容器列表（由共享 store 经 watch 回填）
const ctxMenu = ref({ show: false, x: 0, y: 0, item: null })   // 右键操作菜单
const noteDialog = ref({ show: false, text: '', item: null })   // 备注笔记弹窗
// 高风险操作二次确认状态（删除容器/镜像/网络时记录待执行动作）
const confirm = ref({ show: false, title: '', message: '', action: null })

// ---------- 配置：Docker 引擎配置（镜像加速/私有仓库/iptables） ----------
const config = ref({})
const configMsg = ref('')
const configMsgErr = ref(false)
const form = ref({ mirror_enabled: false, mirrors: '', private_registries: '', iptables: true })

// ---------- 编排：compose 项目列表 ----------
const composeProjects = ref([])

// ---------- 镜像：镜像列表与各管理弹窗 ----------
const images = ref([])
const busy = ref(false)         // 弹窗内提交进行中（防重复点击）
const pullDialog = ref({ show: false, name: '', err: '' })     // 拉取镜像
const tagDialog = ref({ show: false, id: '', repo: '', tag: 'latest', err: '' })   // 打标签
const buildDialog = ref({ show: false, name: '', tag: 'latest', context_dir: '', err: '' })   // 构建镜像

// ---------- 网络：Docker 网络列表 ----------
const networks = ref([])
// ---------- 数据卷：Docker 数据卷列表 ----------
const volumes = ref([])

// 标星的容器优先显示在最上面（提升常用容器的可见性）
const sortedContainers = computed(() => {
  return [...containers.value].sort((a, b) => {
    if (a.starred !== b.starred) return a.starred ? -1 : 1   // 先按是否标星排序
    return (a.name || '').localeCompare(b.name || '')         // 同组内按名称排序
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
    else if (view.value === 'volumes') await loadVolumes()
    else await refreshContainers()    // 默认（containers）视图刷新容器
  } finally {
    loading.value = false
  }
}

// 当前视图的刷新入口（工具栏「刷新」按钮）
function refreshCurrent() {
  onViewChange()
}

// 刷新容器：委托给共享 store，结果经 watch 自动回填，避免重复轮询
async function refreshContainers() {
  await refreshDockerStore()
}

// --- 动作：加载 Docker 引擎配置（镜像加速/私有仓库/iptables） ---
async function loadConfig() {
  config.value = await dockerApi.config()     // 调用 /api/docker/config
  form.value = {
    mirror_enabled: !!config.value.mirror_enabled,
    // 数组字段用换行拼接成多行文本框内容
    mirrors: (config.value.mirrors || []).join('\n'),
    private_registries: (config.value.private_registries || []).join('\n'),
    iptables: !!config.value.iptables
  }
  configMsg.value = ''
}

// --- 动作：加载 compose 项目列表 ---
async function loadCompose() {
  composeProjects.value = await dockerApi.composeProjects()
}

// --- 动作：加载镜像列表 ---
async function loadImages() {
  images.value = await dockerApi.images()
}

// --- 动作：加载网络列表 ---
async function loadNetworks() {
  networks.value = await dockerApi.networks()
}

// ---------- 配置保存 ----------
async function saveConfig() {
  try {
    const body = {
      mirror_enabled: !!form.value.mirror_enabled,
      // 多行文本框按行拆回数组并去掉空行
      mirrors: form.value.mirrors.split('\n').map(s => s.trim()).filter(Boolean),
      private_registries: form.value.private_registries.split('\n').map(s => s.trim()).filter(Boolean),
      iptables: !!form.value.iptables
    }
    const r = await dockerApi.saveConfig(body)   // 调用 /api/docker/config（PUT）
    configMsgErr = false
    configMsg.value = `配置已保存 → ${r.config_path}` + (r.iptables_supported ? '' : '（iptables 仅记录，当前引擎不支持）')
  } catch (e) {
    configMsgErr = true
    configMsg.value = '保存失败：' + (e.response?.data?.detail || e.message)
  }
}

// 打开 Docker 配置文件（独立编辑器窗口），由父窗口接收事件创建
function openConfigEditor() {
  emit('openDockerConfigEditor')
}

// ---------- 编排操作 ----------
async function composeOp(p, action) {
  const label = { up: '启动', down: '停止', restart: '重启' }[action]
  // 停止是破坏性操作，先用原生确认框拦截；其余动作直接进入
  if (action === 'down' && !confirm(`确认停止 compose 项目「${p.name}」？`)) return
  try {
    await dockerApi.composeAction(p.name, action)   // 调用 /api/docker/compose/<name>/action
    alert(`${label}「${p.name}」成功`)
    await loadCompose()
  } catch (e) {
    alert(`${label}失败：` + (e.response?.data?.detail || e.message))
  }
}

// 删除镜像：高风险操作，先弹出密码二次确认框
function removeImageItem(img) {
  const tag = img.tags.length ? img.tags.join(', ') : img.id
  confirm.value = {
    show: true,
    title: '删除镜像确认',
    message: `删除镜像「${tag}」？此操作不可恢复。\n请输入面板密码以确认。`,
    action: { type: 'image', id: img.id }
  }
}

// ---------- 镜像管理：拉取 / 打标签 / 构建 ----------
function openPull() {
  pullDialog.value = { show: true, name: '', err: '' }
}

// --- 动作：拉取镜像（/api/docker/images/pull） ---
async function doPull() {
  if (busy.value) return   // 防重复提交
  busy.value = true
  pullDialog.value.err = ''
  try {
    const r = await dockerApi.pullImage(pullDialog.value.name.trim())
    alert('拉取成功' + (r.detail ? `：${String(r.detail).slice(0, 200)}` : ''))
    pullDialog.value.show = false
    await loadImages()
  } catch (e) {
    pullDialog.value.err = e.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

function openTag(img) {
  tagDialog.value = { show: true, id: img.id, repo: '', tag: 'latest', err: '' }
}

// --- 动作：给镜像打标签（/api/docker/images/<id>/tag） ---
async function doTag() {
  if (busy.value) return
  busy.value = true
  tagDialog.value.err = ''
  try {
    const r = await dockerApi.tagImage(tagDialog.value.id, tagDialog.value.repo.trim(), tagDialog.value.tag.trim() || 'latest')
    alert('打标签成功：' + r.image)
    tagDialog.value.show = false
    await loadImages()
  } catch (e) {
    tagDialog.value.err = e.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

function openBuild() {
  buildDialog.value = { show: true, name: '', tag: 'latest', context_dir: '', err: '' }
}

// --- 动作：从 Dockerfile 构建镜像（/api/docker/images/build） ---
async function doBuild() {
  if (busy.value) return
  busy.value = true
  buildDialog.value.err = ''
  try {
    const r = await dockerApi.buildImage({
      name: buildDialog.value.name.trim(),
      tag: buildDialog.value.tag.trim() || 'latest',
      context_dir: buildDialog.value.context_dir.trim(),
    })
    alert('构建成功：' + r.image)
    buildDialog.value.show = false
    await loadImages()
  } catch (e) {
    buildDialog.value.err = e.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

// --- 动作：加载数据卷列表（/api/dockervolumes） ---
async function loadVolumes() {
  volumes.value = await dockerApi.volumes()
}

// ---------- 网络操作 ----------
function removeNetworkItem(n) {
  // 高风险操作：删除网络需输入面板密码确认
  confirm.value = {
    show: true,
    title: '删除网络确认',
    message: `确认删除网络「${n.name}」？\n使用中的网络无法删除。\n请输入面板密码以确认。`,
    action: { type: 'network', name: n.name }
  }
}

// ---------- 数据卷操作 ----------
function removeVolumeItem(v) {
  // 高风险操作：删除数据卷需输入面板密码确认
  confirm.value = {
    show: true,
    title: '删除数据卷确认',
    message: `确认删除数据卷「${v.name}」？\n使用中的数据卷无法删除。\n请输入面板密码以确认。`,
    action: { type: 'volume', name: v.name }
  }
}

// --- 动作：密码校验通过后真正执行删除（容器/镜像/网络/卷） ---
async function doConfirmDanger() {
  const a = confirm.value.action
  confirm.value.show = false
  if (!a) return    // 无待执行动作则提前返回（用户取消）
  try {
    if (a.type === 'image') {
      await dockerApi.removeImage(a.id)        // 注意：此处走 removeImage（API 对象方法）
      await loadImages()
    } else if (a.type === 'volume') {
      await dockerApi.removeVolume(a.name)
      await loadVolumes()
    } else if (a.type === 'network') {
      await dockerApi.removeNetwork(a.name)
      await loadNetworks()
    } else if (a.type === 'container') {
      await dockerApi.action(a.id, 'remove')
      await refreshContainers()
    }
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 容器操作（右键菜单） ----------
// 普通容器动作直接执行；删除走密码二次确认分支
function act(id, action) {
  if (action === 'remove') {
    // 高风险操作：删除容器需输入面板密码确认
    const c = containers.value.find(x => x.id === id)
    confirm.value = {
      show: true,
      title: '删除容器确认',
      message: `删除容器「${c?.name || id}」后数据可能丢失。\n请输入面板密码以确认。`,
      action: { type: 'container', id }
    }
    return
  }
  doAct(id, action)
}

// --- 动作：对容器执行启停/重启等动作 ---
async function doAct(id, action) {
  try {
    await dockerApi.action(id, action)    // 调用 /api/docker/containers/<id>/action
    await refreshContainers()
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

function closeMenus() {
  ctxMenu.value.show = false
}

// 预估右键菜单高度：菜单项约 31px、header 约 34px、分隔线约 9px
function estimateMenuHeight(running) {
  const items = 12 + (running ? 1 : 0) // 13(运行) / 12(停止) 个菜单项（运行态多一个「停止」）
  return (items * 31) + 34 + (4 * 9) + 8
}

// 底部任务栏：距底部 12px + 高度 64px → 任务栏顶部约 innerHeight - 76
const TASKBAR_TOP_OFFSET = 76
const MENU_SIDE_MARGIN = 10

// --- 动作：弹出右键菜单并据此定位坐标（防超出视口/任务栏） ---
function onContextMenu(e, c) {
  const menuH = estimateMenuHeight(c.state === 'running')
  // 横向不超出右缘（菜单宽约 180px）
  const x = Math.max(MENU_SIDE_MARGIN, Math.min(e.clientX, window.innerWidth - 180 - MENU_SIDE_MARGIN))
  // 纵向：尽量贴近点击位置，但不允许菜单底部戳到下方任务栏（自动上移）
  const maxTop = window.innerHeight - TASKBAR_TOP_OFFSET - menuH - MENU_SIDE_MARGIN
  const y = Math.max(MENU_SIDE_MARGIN, Math.min(e.clientY, maxTop))
  ctxMenu.value = { show: true, x, y, item: c }
}

// 打开容器资源编辑窗口（由父窗口接收事件创建）
function menuEdit() {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) emit('openContainerEdit', { id: it.id, name: it.name })
}

// 执行右键菜单里的容器动作（start/stop/restart/remove）
function menuAct(action) {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) act(it.id, action)
}

// 打开容器日志窗口
function menuLogs() {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) emit('openLogs', { id: it.id, name: it.name })
}

// 打开容器资源图表窗口
function menuStats() {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) emit('openContainerStats', { id: it.id, name: it.name })
}

// 打开容器内终端（仅运行态可打开）
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

// 进入容器安装目录（仅应用商店安装的容器能定位 install_dir）
async function menuEnterDir() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  try {
    const info = await dockerApi.inspect(it.id)   // 取容器详情中的安装目录
    if (info.install_dir) {
      emit('openFiles', { path: info.install_dir })
    } else {
      alert('未找到该容器的安装目录（非应用商店安装的容器无法定位）')
    }
  } catch (e) {
    alert('获取安装目录失败：' + (e.response?.data?.detail || e.message))
  }
}

// 切换标星（菜单项）
async function menuToggleStar() {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) await toggleStar(it)
}

// --- 动作：标星/取消标星（持久化到后端） ---
async function toggleStar(c) {
  try {
    const r = await dockerApi.toggleStar(c.id)   // 调用 /api/docker/containers/<id>/star
    c.starred = r.starred
  } catch (e) {
    alert('操作失败：' + (e.response?.data?.detail || e.message))
  }
}

// 打开备注笔记编辑弹窗
function menuEditNotes() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  noteDialog.value = { show: true, text: it.note || '', item: it }
}

// --- 动作：保存容器备注笔记 ---
async function saveNotes() {
  const d = noteDialog.value
  try {
    await dockerApi.saveNotes(d.item.id, d.text)   // 调用 /api/docker/containers/<id>/notes
    d.item.note = d.text.trim()      // 就地更新列表中的备注，避免重新拉取
    d.show = false
  } catch (e) {
    alert('保存失败：' + (e.response?.data?.detail || e.message))
  }
}

// 打开容器详细信息窗口
function menuDetails() {
  const it = ctxMenu.value.item
  closeMenus()
  if (it) emit('openContainerDetails', { id: it.id, name: it.name })
}

// 备份容器文件系统为 tar 包（高风险，先原生确认）
async function menuBackup() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  if (!confirm(`确认备份容器「${it.name}」？\n将导出其文件系统为 tar 包保存到服务器。`)) return
  try {
    const r = await dockerApi.backup(it.id)   // 调用 /api/docker/containers/<id>/backup
    alert(`备份成功\n保存路径：${r.path}\n大小：${formatBytes(r.size)}`)
  } catch (e) {
    alert('备份失败：' + (e.response?.data?.detail || e.message))
  }
}

// 升级容器：重新拉取镜像并按原参数重建
async function menuUpgrade() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  if (!confirm(`确认升级容器「${it.name}」？\n将重新拉取镜像并按原参数重建容器（需容器未运行或允许重启）。`)) return
  try {
    const r = await dockerApi.upgrade(it.id)   // 调用 /api/docker/containers/<id>/upgrade
    alert(`升级成功\n镜像：${r.image}${r.new_container_id ? '\n新容器：' + r.new_container_id : ''}`)
    await refreshContainers()
  } catch (e) {
    alert('升级失败：' + (e.response?.data?.detail || e.message))
  }
}

// 制作镜像：把运行中容器提交为新镜像
async function menuCommit() {
  const it = ctxMenu.value.item
  closeMenus()
  if (!it) return
  const repo = prompt('请输入镜像名称（默认 graw-commit-<容器ID>）：', `graw-commit-${it.name || it.id}`)
  if (repo === null) return
  try {
    const r = await dockerApi.commit(it.id, repo.trim())   // 调用 /api/docker/containers/<id>/commit
    alert(`镜像制作成功\n镜像：${r.image}`)
  } catch (e) {
    alert('制作镜像失败：' + (e.response?.data?.detail || e.message))
  }
}

// ---------- 资源显示辅助 ----------
// CPU/内存使用率配色：≥80% 红、≥50% 橙、否则绿
function cpuColor(pct) {
  const v = Number(pct) || 0
  if (v >= 80) return '#b91c1c'    // 高负载用红色警示
  if (v >= 50) return '#b45309'
  return '#2a8f3c'
}

// 内存条宽度：限制在 0~100%
function memBarWidth(pct) {
  return Math.min(Number(pct) || 0, 100) + '%'
}

// 字节数 → 人类可读（B/KB/MB/GB/TB）
function formatBytes(bytes) {
  if (bytes == null || isNaN(bytes)) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return v.toFixed(v < 10 && i > 0 ? 2 : v < 100 ? 1 : 0) + ' ' + units[i]
}

// 时间戳 → 本地可读时间（兼容秒级数字与字符串）
function formatTime(t) {
  if (!t) return '-'
  let d = t
  if (typeof t === 'number') d = new Date(t * 1000)   // 后端常返回秒级时间戳
  else if (typeof t === 'string') d = new Date(t)
  if (isNaN(d.getTime())) return String(t)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

let stopWatch = null

onMounted(() => {
  // 1) 先立即回填上次缓存的容器快照，避免「打开后一直在加载」
  if (docker.hasCache) {
    status.value = docker.status
    containers.value = docker.containers
    loading.value = docker.loading
  }
  // 2) 订阅共享 store：多窗口共用同一份数据与同一轮询，避免重复连接
  stopWatch = watch(
    () => ({ s: docker.status, c: docker.containers, loading: docker.loading }),
    (v) => {
      status.value = v.s
      containers.value = v.c
      loading.value = v.loading
    },
    { deep: true }
  )
  // 3) 确保共享轮询已启动，并立即刷新一次最新数据
  startDocker()
  refreshContainers()
})
onUnmounted(() => {
  if (stopWatch) stopWatch()
  // 不停止共享轮询：其他仍打开的 Docker 窗口（或后台预热）继续接收更新
})
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
.note-input { width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; box-sizing: border-box; font-family: inherit; }
.err-text { color: #b91c1c; font-size: 12px; margin-top: 8px; }
.img-toolbar { display: flex; gap: 8px; margin-bottom: 8px; }
.note-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
