<!--
  内网穿透（FRP）窗口（后端 /api/frp 模块）
  作用：配置并管理 FRP 服务端 / 客户端（frps/frpc）：切换运行模式、编辑 toml 配置、
        维护客户端代理规则（tcp/udp/http/https）、启动 / 停止 / 重启 FRP 进程。
  后端模块：/api/frp（status 状态、config 读写配置、save 保存、switch_mode 切换模式、
            preview 生成 toml 预览、start/stop/restart 进程控制、proxies 增删改）。
  关键状态：mode（server/client）、server/client（配置表单）、proxies（代理列表）、
            bins（frps/frpc 可执行文件路径）、running/installed（进程与安装状态）。
  删除代理为高风险操作，代码预留面板密码二次确认流程（delProxy → doDeleteProxy）。
  打开方式：桌面「内网穿透」卡片。
-->
<template>
  <div class="frp-window">
    <!-- 顶部工具条：模式切换 + 状态 + 进程控制 -->
    <div class="toolbar">
      <label class="mode-label">{{ $t('frp.mode') }}</label>
      <select v-model="mode" @change="onModeChange">
        <option value="server">{{ $t('frp.modeServer') }}</option>
        <option value="client">{{ $t('frp.modeClient') }}</option>
      </select>

      <span class="badge" :class="installed ? 'ok' : 'warn'">{{ $t(installed ? 'frp.installed' : 'frp.notInstalled') }}</span>
      <span class="badge" :class="running ? 'ok' : 'off'">{{ $t(running ? 'frp.running' : 'frp.stopped') }}</span>
      <span class="badge neutral">{{ cfgPath }}</span>

      <div class="spacer"></div>
      <button class="btn" title="刷新" @click="load"><RotateCw :size="14" /></button>
      <button class="btn primary" @click="preview">{{ $t('frp.preview') }}</button>
      <button class="btn primary" @click="save">{{ $t('common.save') }}</button>
      <button class="btn" @click="proc('start')" :disabled="running"><Play :size="14" /> {{ $t('frp.start') }}</button>
      <button class="btn" @click="proc('stop')" :disabled="!running"><Square :size="14" /> {{ $t('frp.stop') }}</button>
      <button class="btn warn" @click="proc('restart')"><RotateCw :size="14" /> {{ $t('frp.restart') }}</button>
    </div>

    <p v-if="statusMsg" class="status-msg">{{ statusMsg }}</p>

    <!-- 可执行文件路径（frps / frpc）：不填则从 PATH 探测 -->
    <div class="bin-row">
      <span class="bin-label">{{ $t('frp.binPath') }}</span>
      <input class="bin-input" v-model="bins.serverBin" :placeholder="$t('frp.binServerPlaceholder')" />
      <input class="bin-input" v-model="bins.clientBin" :placeholder="$t('frp.binClientPlaceholder')" />
    </div>

    <!-- 服务端模式配置 -->
    <section v-if="mode === 'server'">
      <h4>{{ $t('frp.serverConfig') }}</h4>
      <div class="form-grid">
        <div class="field"><label>{{ $t('frp.bindAddr') }}</label><input v-model="server.bindAddr" /></div>
        <div class="field"><label>{{ $t('frp.bindPort') }}</label><input v-model.number="server.bindPort" type="number" /></div>
        <div class="field"><label>{{ $t('frp.token') }}</label><input v-model="server.token" :placeholder="$t('frp.tokenPlaceholder')" /></div>
        <div class="field"><label>{{ $t('frp.configPath') }}</label><input v-model="server.configPath" :placeholder="$t('frp.configPathPlaceholder', { p: '/etc/frp/frps.toml' })" /></div>
        <div class="field"><label>{{ $t('frp.logLevel') }}</label>
          <select v-model="server.logLevel"><option v-for="l in logLevels" :key="l" :value="l">{{ l }}</option></select>
        </div>
        <div class="field"><label>{{ $t('frp.dashboardPort') }}</label><input v-model.number="server.dashboardPort" type="number" /></div>
        <div class="field"><label>{{ $t('frp.dashboardAddr') }}</label><input v-model="server.dashboardAddr" /></div>
        <div class="field"><label>{{ $t('frp.dashboardUser') }}</label><input v-model="server.dashboardUser" /></div>
        <div class="field"><label>{{ $t('frp.dashboardPwd') }}</label><input v-model="server.dashboardPwd" /></div>
      </div>
    </section>

    <!-- 客户端模式配置 -->
    <section v-else>
      <h4>{{ $t('frp.clientConfig') }}</h4>
      <div class="form-grid">
        <div class="field"><label>{{ $t('frp.serverAddr') }}</label><input v-model="client.serverAddr" :placeholder="$t('frp.serverAddrPlaceholder')" /></div>
        <div class="field"><label>{{ $t('frp.serverPort') }}</label><input v-model.number="client.serverPort" type="number" /></div>
        <div class="field"><label>{{ $t('frp.token') }}</label><input v-model="client.token" :placeholder="$t('frp.tokenPlaceholder')" /></div>
        <div class="field"><label>{{ $t('frp.configPath') }}</label><input v-model="client.configPath" :placeholder="$t('frp.configPathPlaceholder', { p: '/etc/frp/frpc.toml' })" /></div>
        <div class="field"><label>{{ $t('frp.logLevel') }}</label>
          <select v-model="client.logLevel"><option v-for="l in logLevels" :key="l" :value="l">{{ l }}</option></select>
        </div>
        <div class="field toggle"><label><input type="checkbox" v-model="client.loginFailExit" /> {{ $t('frp.loginFailExit') }}</label></div>
      </div>

      <div class="section-head">
        <h4>{{ $t('frp.proxies') }}</h4>
        <button class="btn primary" @click="emit('openFrpProxyForm', { proxy: null })"><Plus :size="14" /> {{ $t('frp.addProxy') }}</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{{ $t('frp.name') }}</th><th>{{ $t('frp.type') }}</th><th>{{ $t('frp.localEndpoint') }}</th>
              <th>{{ $t('frp.remote') }}</th><th>{{ $t('frp.remark') }}</th><th>{{ $t('frp.status') }}</th><th>{{ $t('common.action') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in proxies" :key="p.id" :class="{ muted: !p.enabled }">
              <td>{{ p.name }}</td><td>{{ p.type }}</td>
              <td>{{ p.localIp }}:{{ p.localPort }}</td>
              <td>
                <span v-if="p.type==='tcp'||p.type==='udp'">{{ p.remotePort }}</span>
                <span v-else>{{ p.customDomains }}</span>
              </td>
              <td>{{ p.remark }}</td>
              <td><span class="badge" :class="p.enabled ? 'ok' : 'off'">{{ $t(p.enabled ? 'frp.enabled' : 'frp.disabled') }}</span></td>
              <td class="ops">
                <button class="iconbtn" :title="$t('frp.toggle')" @click="toggleProxy(p)"><Power :size="14" /></button>
                <button class="iconbtn" :title="$t('common.edit')" @click="emit('openFrpProxyForm', { proxy: p })"><Pencil :size="14" /></button>
                <button class="iconbtn danger" :title="$t('common.delete')" @click="delProxy(p)"><Trash2 :size="14" /></button>
              </td>
            </tr>
            <tr v-if="proxies.length === 0"><td colspan="7" class="empty">{{ $t('frp.noProxies') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 配置预览弹窗 -->
    <div v-if="showPreview" class="modal-overlay" @click.self="showPreview=false">
      <div class="modal wide">
        <h3>{{ $t('frp.preview') }}</h3>
        <pre class="toml">{{ previewText }}</pre>
        <div class="actions"><button class="btn primary" @click="showPreview=false">{{ $t('common.close') }}</button></div>
      </div>
    </div>
  </div>
</template>

<script setup>
// 响应式状态、计算属性与生命周期钩子
import { ref, watch, onMounted } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// FRP API：status/config/save/switch_mode/preview/start/stop/restart + 代理增删改
import { frpApi } from '../../api'
// 图标（刷新/播放/停止/添加/删除/编辑/开关）
import { RotateCw, Play, Square, Plus, Trash2, Pencil, Power } from 'lucide-vue-next'
// 表单保存信号：独立「新增/编辑代理」窗口保存成功后刷新本列表
import { formBus } from '../../store/formBus'

const { t } = useI18n()

const emit = defineEmits(['openFrpProxyForm'])   // 打开独立「代理规则」窗口（proxy: 编辑对象或 null）

const mode = ref('server')       // 运行模式：server（服务端）/ client（客户端）
const installed = ref(false)     // FRP 是否已安装（有无可用二进制）
const running = ref(false)       // FRP 进程是否运行中
const cfgPath = ref('')          // 当前使用的配置文件路径
const statusMsg = ref('')        // 顶部状态提示（加载/保存/进程操作结果）
const bins = ref({ serverBin: '', clientBin: '' })   // frps / frpc 可执行文件路径
const server = ref({ bindAddr: '0.0.0.0', bindPort: 7000, token: '', configPath: '', dashboardAddr: '127.0.0.1', dashboardPort: 0, dashboardUser: 'admin', dashboardPwd: '', logLevel: 'info' })
const client = ref({ serverAddr: '', serverPort: 7000, token: '', configPath: '', loginFailExit: true, logLevel: 'info' })
const proxies = ref([])          // 客户端代理规则列表
const logLevels = ['trace', 'debug', 'info', 'warn', 'error']   // 日志级别可选项

const showPreview = ref(false)   // 配置预览弹窗显隐
const previewText = ref('')      // 预览的 toml 文本

// 代理规则新增/编辑改由独立窗口承载：保存成功后 bumpForm('frp') 触发此处重载
watch(() => formBus.frp, load)

// 把后端返回的服务端配置合并进本地表单
function applyServer(d) {
  server.value = { ...server.value, ...(d.server || {}) }
}
// 把后端返回的客户端配置与代理列表合并进本地表单
function applyClient(d) {
  client.value = { ...client.value, ...(d.client || {}) }
  proxies.value = (d.client && d.client.proxies) || []
}
// 把后端返回的整体配置（模式/二进制路径/双方配置）一次性应用到本地
function applyAll(d) {
  mode.value = d.mode || 'server'
  bins.value.serverBin = d.serverBin || ''
  bins.value.clientBin = d.clientBin || ''
  applyServer(d)
  applyClient(d)
}

// --- 动作：加载 FRP 状态与完整配置 ---
async function load() {
  statusMsg.value = ''
  try {
    const s = await frpApi.status()   // 调用 /api/frp/status
    installed.value = s.installed
    running.value = s.running
    cfgPath.value = s.configPath || ''
    const cfg = await frpApi.config()   // 调用 /api/frp/config
    applyAll(cfg)
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

// --- 动作：把本地表单整体保存到后端 ---
async function save() {
  try {
    const payload = {
      mode: mode.value,
      serverBin: bins.value.serverBin,
      clientBin: bins.value.clientBin,
      server: server.value,
      client: { ...client.value, proxies: proxies.value }
    }
    const d = await frpApi.save(payload)   // 调用 /api/frp/save
    applyAll(d)
    statusMsg.value = t('frp.saved')
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

// --- 动作：切换 server/client 模式并同步后端 ---
async function onModeChange() {
  try {
    const d = await frpApi.switchMode(mode.value)   // 调用 /api/frp/switch_mode
    applyAll(d)
    await load()
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

// --- 动作：启动/停止/重启 FRP 进程（按 action 名调用对应接口） ---
async function proc(action) {
  statusMsg.value = ''
  try {
    const fn = frpApi[action]
    const d = await fn()
    running.value = d.running
    statusMsg.value = d.message || t('frp.done')
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

// --- 动作：生成并预览当前配置对应的 toml 文本 ---
async function preview() {
  try {
    const p = await frpApi.preview()   // 调用 /api/frp/preview
    previewText.value = p.toml
    showPreview.value = true
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

// --- 动作：启用/停用代理（向后端传反向状态） ---
async function toggleProxy(p) {
  try {
    await frpApi.toggleProxy(p.id, !p.enabled)
    const cfg = await frpApi.config()
    applyAll(cfg)
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

// 删除代理：高风险操作，先弹出密码二次确认框
function delProxy(p) {
  confirm.value = { show: true, target: p }
}

// 面板密码校验通过后真正执行删除
async function doDeleteProxy() {
  const p = confirm.value.target
  confirm.value.show = false
  if (!p) return   // 无待删除目标则提前返回
  try {
    await frpApi.deleteProxy(p.id)   // 调用 /api/frp 代理删除接口
    const cfg = await frpApi.config()
    applyAll(cfg)
  } catch (e) {
    statusMsg.value = (e?.response?.data?.detail) || e.message || t('common.error')
  }
}

onMounted(load)
</script>

<style scoped>
.frp-window { padding: 10px; }
.toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.mode-label { font-size: 13px; color: #374151; }
.spacer { flex: 1; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.warn { background: #fef3c7; color: #92400e; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.badge.neutral { background: #eef2ff; color: #3730a3; }
.status-msg { color: #155e75; font-size: 12px; margin: 4px 0 8px; }
.bin-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; background: #f9fafb; padding: 8px 10px; border-radius: 8px; }
.bin-label { font-size: 12px; color: #374151; white-space: nowrap; }
.bin-input { flex: 1; min-width: 220px; }
h4 { margin: 12px 0 8px; font-size: 14px; }
.section-head { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field label { font-size: 12px; color: #374151; }
.field.toggle { justify-content: flex-end; }
.field.toggle label { display: flex; align-items: center; gap: 6px; padding-bottom: 8px; }
input, select { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.table-wrap { overflow: auto; max-height: 300px; border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 6px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; white-space: nowrap; }
th { background: #f9fafb; position: sticky; top: 0; }
tr.muted { opacity: 0.55; }
.empty { text-align: center; color: #9ca3af; padding: 20px; }
.ops { display: flex; gap: 6px; }
.btn { display: inline-flex; align-items: center; gap: 4px; padding: 6px 10px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn.warn { background: #fff7ed; color: #c2410c; border-color: #fdba74; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; display: inline-flex; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 440px; max-width: 92vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal.wide { width: 640px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
.toml { background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px; max-height: 60vh; overflow: auto; font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-all; }
</style>