<template>
  <div class="desktop-layout">
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
    <div class="cards-col">
      <div class="card-row rings">
        <CardRing title="负载" metric="load" :data="metrics.load" />
        <CardRing title="CPU" metric="cpu" :data="metrics.cpu" />
        <CardRing title="内存" metric="memory" :data="metrics.memory" />
        <CardRing title="存储" metric="disk" :data="metrics.disk" />
      </div>
      <div class="card-row monitor">
        <CardMonitor :metrics="metrics" />
      </div>
      <div class="card-row info">
        <CardSwitch :metrics="metrics" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, onMounted, onBeforeUnmount } from 'vue'
import { desktop } from '../store/desktop.js'
import CardRing from './CardRing.vue'
import CardMonitor from './CardMonitor.vue'
import CardSwitch from './CardSwitch.vue'
import { Container, BarChart3, Folder, Terminal } from 'lucide-vue-next'

function open(type, title, opts) {
  desktop.open(type, title, opts)
}

const metrics = reactive({
  load: 0,
  cpu: 0,
  memory: 0,
  disk: 0,
  netSent: 0,
  netRecv: 0,
  dioRead: 0,
  dioWrite: 0,
  memoryTotal: 1,
  memoryUsed: 0,
  diskTotal: 1,
  diskUsed: 0,
  uptime: 0,
  hostname: '',
  platform: '',
})

let ws = null
let systemInfoFetched = false

async function fetchSystemInfo() {
  try {
    const r = await fetch('/api/system/info')
    const data = await r.json()
    metrics.hostname = data.hostname
    metrics.platform = data.platform + ' ' + data.arch
    metrics.uptime = data.uptime
    systemInfoFetched = true
  } catch (e) {
    // silent
  }
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/system/ws`)
  ws.onmessage = ev => {
    const d = JSON.parse(ev.data)
    metrics.load = Math.min(d.load1 || 0, 100)
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
    if (!systemInfoFetched) fetchSystemInfo()
  }
  ws.onclose = () => {
    setTimeout(connect, 3000)
  }
}

onMounted(() => {
  fetchSystemInfo()
  connect()
})

onBeforeUnmount(() => {
  if (ws) ws.close()
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
