<!--
  ToolboxSystemPanel.vue — 工具箱「系统设置」
  ==========================================================
  业务作用：
    常用系统维护的集中入口：缓存清理、主机名、时区、DNS、/etc/hosts、
    Swap、NTP 时间同步。写系统文件前后端会备份为 *.graw.bak。
  后端模块：
    /api/toolbox/system（GET/POST）、/api/toolbox/cache/clean。
  打开方式：
    ToolboxWindow 的「系统设置」页签。
-->
<template>
  <div class="sys-panel">
    <div class="toolbar">
      <button class="btn" :disabled="loading" @click="load">{{ loading ? '加载中…' : '刷新' }}</button>
      <span v-if="msg" class="msg">{{ msg }}</span>
      <span v-if="err" class="err">{{ err }}</span>
    </div>

    <div class="grid">
      <div class="card">
        <div class="title">缓存清理</div>
        <label v-for="t in cacheOptions" :key="t.key" class="check">
          <input type="checkbox" v-model="cacheTargets" :value="t.key" /> {{ t.label }}
        </label>
        <button class="btn primary" :disabled="busy || !cacheTargets.length" @click="cleanCache">
          {{ busy ? '清理中…' : '开始清理' }}
        </button>
        <pre v-if="cacheOutput" class="out">{{ cacheOutput }}</pre>
      </div>

      <div class="card">
        <div class="title">主机名 / 时区</div>
        <label class="field">主机名
          <input v-model.trim="hostname" class="inp" spellcheck="false" />
        </label>
        <button class="btn" :disabled="busy" @click="save('hostname', hostname)">保存主机名</button>
        <label class="field" style="margin-top:10px;">时区
          <input v-model.trim="timezone" class="inp" placeholder="Asia/Shanghai" spellcheck="false" />
        </label>
        <button class="btn" :disabled="busy" @click="save('timezone', timezone)">保存时区</button>
        <div class="hint">当前 NTP：{{ info.ntp || '-' }}{{ info.ntp_service ? '（' + info.ntp_service + '）' : '' }}</div>
        <div class="row">
          <button class="btn" :disabled="busy" @click="save('ntp', 'true')">开启 NTP</button>
          <button class="btn" :disabled="busy" @click="save('ntp', 'false')">关闭 NTP</button>
        </div>
      </div>

      <div class="card">
        <div class="title">DNS</div>
        <label class="field">nameserver（每行一个）
          <textarea v-model="dnsText" class="area" rows="4" spellcheck="false" />
        </label>
        <button class="btn primary" :disabled="busy" @click="save('dns', dnsText)">保存 DNS</button>
        <div class="hint">写入 /etc/resolv.conf，原文件备份为 /etc/resolv.conf.graw.bak。</div>
      </div>

      <div class="card">
        <div class="title">/etc/hosts</div>
        <textarea v-model="hostsText" class="area hosts" rows="10" spellcheck="false" />
        <button class="btn primary" :disabled="busy" @click="save('hosts', hostsText)">保存 hosts</button>
      </div>

      <div class="card">
        <div class="title">Swap</div>
        <pre class="out">{{ info.swap || '当前没有启用 Swap' }}</pre>
        <label class="field">创建大小（MB）
          <input v-model.number="swapSize" type="number" min="128" max="65536" class="inp" />
        </label>
        <div class="row">
          <button class="btn primary" :disabled="busy" @click="save('swap', String(swapSize))">创建 / 启用</button>
          <button class="btn" :disabled="busy" @click="save('swapoff', '')">关闭并移除</button>
        </div>
        <div class="hint">创建 /swapfile-graw 并写入 /etc/fstab；关闭时同时移除 fstab 记录。</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { toolboxApi } from '../../api'

const loading = ref(false)
const busy = ref(false)
const msg = ref('')
const err = ref('')
const info = reactive({ hostname: '', timezone: '', dns: [], hosts: '', swap: '', ntp: '', ntp_service: '' })
const hostname = ref('')
const timezone = ref('')
const dnsText = ref('')
const hostsText = ref('')
const swapSize = ref(1024)
const cacheTargets = ref(['journal'])
const cacheOutput = ref('')
const cacheOptions = [
  { key: 'apt', label: 'apt 缓存' },
  { key: 'yum', label: 'yum 缓存' },
  { key: 'dnf', label: 'dnf 缓存' },
  { key: 'journal', label: 'systemd journal（保留 200M）' },
  { key: 'tmp', label: '/tmp 过期文件' },
]

function applyInfo(s) {
  Object.assign(info, s || {})
  hostname.value = info.hostname || ''
  timezone.value = info.timezone || ''
  dnsText.value = (info.dns || []).join('\n')
  hostsText.value = info.hosts || ''
}

async function load() {
  loading.value = true
  err.value = ''
  try {
    const r = await toolboxApi.system()
    applyInfo(r.system)
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}

async function save(action, value) {
  busy.value = true
  msg.value = ''
  err.value = ''
  try {
    const r = await toolboxApi.updateSystem(action, value)
    applyInfo(r.system)
    msg.value = '已保存：' + action
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

async function cleanCache() {
  if (!window.confirm('确定清理选中的缓存？该操作不可恢复。')) return
  busy.value = true
  cacheOutput.value = ''
  err.value = ''
  try {
    const r = await toolboxApi.cacheClean(cacheTargets.value)
    cacheOutput.value = Object.entries(r.results || {}).map(([k, v]) => `${k}: ${v.ok ? 'OK' : 'FAIL'}\n${v.output || ''}`).join('\n\n')
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.sys-panel { padding: 10px; height: 100%; overflow: auto; box-sizing: border-box; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.msg { font-size: 12px; color: #047857; }
.err { font-size: 12px; color: #b91c1c; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; align-items: start; }
.card { border: 1px solid var(--g-border); border-radius: 10px; background: var(--g-surface); padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.title { font-weight: 700; font-size: 13px; }
.check { display: flex; align-items: center; gap: 6px; font-size: 12px; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.inp, .area { padding: 6px 8px; border: 1px solid var(--g-border); border-radius: 8px; background: var(--g-surface); color: var(--g-text); font-size: 12px; }
.area { resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.hosts { min-height: 200px; }
.row { display: flex; gap: 8px; flex-wrap: wrap; }
.hint { font-size: 11.5px; color: var(--g-text-muted); }
.out { background: #0f1722; color: #d7e3f4; border-radius: 8px; padding: 8px; font-size: 11.5px; max-height: 160px; overflow: auto; white-space: pre-wrap; margin: 0; }
</style>
