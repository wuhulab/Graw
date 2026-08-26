<!--
  Desktop.vue — 桌面主界面
  作用：面板登录后的「桌面」根视图。左侧是功能快捷方式（Docker / 进程 / 文件 /
        终端），右侧实时展示系统四项核心指标（负载 / CPU / 内存 / 存储）的环形图，
        以及流量 / 磁盘IO 实时监控曲线、系统信息与备忘录面板。
  数据：通过 WebSocket（/api/system/ws）实时接收后端指标推送，并一次性拉取
        /api/system/info 补充主机名 / 平台 / 运行时间等静态信息。
  打开方式：由 App.vue 渲染，是桌面环境的固定主页（不经由任务栏打开）。
-->
<template>
  <div class="desktop-layout">
    <!-- 左栏：功能快捷入口，点击即打开对应功能窗口 -->
    <div class="shortcuts-col">
      <div class="shortcut-item" @click="open('docker','Docker 管理',{width:900,height:600})">
        <div class="shortcut-icon"><Container :size="32" /></div>
        <span>Docker</span>
      </div>
      <div class="shortcut-item" @click="open('process','进程管理',{width:900,height:600})">
        <div class="shortcut-icon"><BarChart3 :size="32" /></div>
        <span>进程管理</span>
      </div>
      <div class="shortcut-item" @click="open('file','文件管理',{width:900,height:600})">
        <div class="shortcut-icon"><Folder :size="32" /></div>
        <span>文件管理</span>
      </div>
      <div class="shortcut-item" @click="open('terminal','终端',{width:800,height:520})">
        <div class="shortcut-icon"><Terminal :size="32" /></div>
        <span>终端</span>
      </div>
    </div>
    <!-- 右栏：实时指标卡片区 -->
    <div class="cards-col">
      <!-- 第一行：四项核心指标环形图 -->
      <div class="card-row rings">
        <CardRing title="负载" metric="load" :data="metrics.load" />
        <CardRing title="CPU" metric="cpu" :data="metrics.cpu" />
        <CardRing title="内存" metric="memory" :data="metrics.memory" />
        <CardRing title="存储" metric="disk" :data="metrics.disk" />
      </div>
      <!-- 第二行：实时流量 / 磁盘IO 曲线 -->
      <div class="card-row monitor">
        <CardMonitor :metrics="metrics" />
      </div>
      <!-- 第三行：系统信息 / 备忘录 -->
      <div class="card-row info">
        <CardSwitch :metrics="metrics" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, onMounted, onBeforeUnmount } from 'vue'                          // Vue 响应式与生命周期
import { desktop } from '../store/desktop.js'                                         // 桌面窗口状态单例（打开 / 激活 / 最小化窗口）
import CardRing from './CardRing.vue'                                                 // 环形百分比卡片（负载 / CPU / 内存 / 存储）
import CardMonitor from './CardMonitor.vue'                                           // 实时监控曲线卡片（流量 / 磁盘IO）
import CardSwitch from './CardSwitch.vue'                                             // 系统信息 / 备忘录切换卡片
import { Container, BarChart3, Folder, Terminal } from 'lucide-vue-next'              // 功能入口图标

// 打开一个功能窗口（type 决定渲染哪个窗口组件）
function open(type, title, opts) {
  desktop.open(type, title, opts)
}

// 实时指标数据容器，由 WS 推送持续刷新，供右侧各卡片读取
const metrics = reactive({
  load: 0,            // 系统负载（1 分钟均值，已百分比化）
  cpu: 0,             // CPU 使用率 %
  memory: 0,          // 内存使用率 %
  disk: 0,            // 磁盘使用率 %
  netSent: 0,         // 网络发送速率（字节/秒）
  netRecv: 0,         // 网络接收速率（字节/秒）
  dioRead: 0,         // 磁盘读取速率（字节/秒）
  dioWrite: 0,        // 磁盘写入速率（字节/秒）
  memoryTotal: 1,     // 内存总量（默认 1 防除零）
  memoryUsed: 0,      // 已用内存
  diskTotal: 1,       // 磁盘总量（默认 1 防除零）
  diskUsed: 0,        // 已用磁盘
  uptime: 0,          // 系统已运行秒数
  hostname: '',       // 主机名
  platform: '',       // 平台 + 架构（如 "Linux x86_64"）
})

// --- 实时指标 WebSocket 连接 ---
let ws = null
let systemInfoFetched = false   // 静态信息是否已拉取（只拉一次）

// 拉取主机静态信息（主机名 / 平台 / 运行时间），WS 仅推送动态指标
async function fetchSystemInfo() {
  try {
    const r = await fetch('/api/system/info')
    const data = await r.json()
    metrics.hostname = data.hostname
    metrics.platform = data.platform + ' ' + data.arch
    metrics.uptime = data.uptime
    systemInfoFetched = true
  } catch (e) {
    // 失败不阻塞主流程，WS 仍会推送动态指标
  }
}

function connect() {
  // 按页面协议自动选择 ws / wss，避免混合内容被浏览器拦截
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/system/ws`)
  ws.onmessage = ev => {
    const d = JSON.parse(ev.data)
    metrics.load = Math.min(d.load1 || 0, 100)   // 负载百分比化并封顶 100，便于环形图展示
    metrics.cpu = d.cpu
    metrics.memory = d.memory
    metrics.disk = d.disk
    metrics.memoryTotal = d.memory_total
    metrics.memoryUsed = d.memory_used
    metrics.diskTotal = d.disk_total
    metrics.diskUsed = d.disk_used
    metrics.netSent = d.net_sent
    metrics.netRecv = d.net_recv
    metrics.dioRead = d.dio_read
    metrics.dioWrite = d.dio_write
    if (!systemInfoFetched) fetchSystemInfo()    // 首帧到达时补拉一次静态信息
  }
  ws.onclose = () => {
    setTimeout(connect, 3000)   // 断线 3 秒后自动重连，保证监控不中断
  }
}

onMounted(() => {
  fetchSystemInfo()
  connect()
})

onBeforeUnmount(() => {
  if (ws) ws.close()   // 离开桌面时关闭 WS，避免泄漏连接
})
</script>

<style scoped>
.desktop-layout {
  display: flex;
  width: 100%;
  height: calc(100% - 88px);
  padding: 16px;
  box-sizing: border-box;
  gap: 14px;
}
.shortcuts-col {
  width: 90px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  padding-top: 10px;
}
.cards-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-right: 10px;
}
.card-row {
  display: flex;
  gap: 10px;
}
.card-row.rings {
  height: 28%;
  min-height: 140px;
}
.card-row.monitor {
  height: 36%;
  min-height: 160px;
}
.card-row.info {
  flex: 1;
  min-height: 140px;
}
</style>
