<!--
  SSH 端口转发窗口（PortForwardWindow）
  业务：把「远程节点的某个端口」转发到「面板所在主机的本地端口」，本地工具
        （Navicat / redis-cli）即可直连远程服务，免开防火墙/跳板。
  后端模块：portforwardApi（list/running/create/toggle/remove）
  关键状态：items（已配置条目）、runningMap（id→运行态）
  打开方式：桌面「端口转发」入口（管理员）；新建表单为独立窗口 PortForwardFormWindow。
-->
<template>
  <div class="pf-window">
    <div class="ui-toolbar">
      <button class="ui-btn primary" @click="emit('openPortForwardForm')">{{ $t('pf.create') }}</button>
      <button class="ui-btn" @click="loadAll">{{ $t('common.refresh') }}</button>
      <span class="ui-hint right">{{ $t('pf.localHint') }}</span>
    </div>

    <div class="pf-body">
      <div v-if="items.length === 0" class="ui-empty">
        {{ $t('pf.empty') }}
      </div>
      <div v-for="it in items" :key="it.id" class="ui-card pf-row">
        <div class="pf-row-head">
          <b style="font-size:13px;">{{ it.name || it.id }}</b>
          <span class="ui-badge" :class="runStatus(it).active ? 'ok' : 'off'">
            {{ runStatus(it).active ? $t('pf.running') : $t('pf.stopped') }}
          </span>
        </div>
        <div class="pf-row-addr">
          <span class="ui-mono">127.0.0.1:{{ it.local_port }}</span>
          <span class="arrow">→</span>
          <span class="ui-mono">{{ it.remote_host }}:{{ it.remote_port }}</span>
          <span class="node">{{ $t('pf.node') }}: {{ it.node_id }}</span>
        </div>
        <div v-if="runStatus(it).stats" class="pf-row-stats">
          {{ $t('pf.conns') }}: {{ runStatus(it).stats.conns }} · {{ $t('pf.traffic') }}: {{ formatBytes(runStatus(it).stats.bytes_in) }} ↑ / {{ formatBytes(runStatus(it).stats.bytes_out) }} ↓
        </div>
        <div class="pf-row-actions">
          <button class="ui-btn mini" @click="doToggle(it)">{{ runStatus(it).active ? $t('pf.stop') : $t('pf.start') }}</button>
          <button class="ui-btn mini danger" @click="doRemove(it)">{{ $t('common.delete') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'                 // 响应式 + 挂载刷新 + 信号监听
import { useI18n } from 'vue-i18n'                          // 国际化
import { portforwardApi, formatBytes } from '../../api'     // 接口 + 字节格式化
import { refreshNodes } from '../../store/nodes'            // 刷新节点列表（独立表单窗口自取节点）
import { formBus } from '../../store/formBus'               // 表单保存信号：新建成功后刷新列表

const { t } = useI18n()

// openPortForwardForm 打开独立「新建端口转发」窗口
const emit = defineEmits(['openPortForwardForm'])

const items = ref([])         // 已配置条目
const runningMap = ref({})    // 运行中隧道：id → tunnel 状态

// 新建表单已拆分为独立窗口：保存成功后 bumpForm('portforward') 触发此处重载
watch(() => formBus.portforward, loadAll)

// 取某条目的运行状态（无则 stopped）
function runStatus(it) {
  return runningMap.value[it.id] || { active: false }
}

async function loadAll() {
  try {
    const [it, run] = await Promise.all([portforwardApi.list(), portforwardApi.running()])
    items.value = it.items || []
    const map = {}
    for (const x of run.tunnels || []) map[x.id] = x
    runningMap.value = map
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

async function doToggle(it) {
  try {
    await portforwardApi.toggle(it.id)
    await loadAll()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

async function doRemove(it) {
  if (!confirm(t('pf.deleteConfirm'))) return
  try {
    await portforwardApi.remove(it.id)
    await loadAll()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

onMounted(async () => {
  await refreshNodes()
  await loadAll()
})
</script>

<style scoped>
.pf-window { display: flex; flex-direction: column; height: 100%; padding: 10px; box-sizing: border-box; }
.pf-body { flex: 1; overflow: auto; padding: 0 4px 12px; }
.pf-row { margin-bottom: 10px; display: flex; flex-direction: column; gap: 6px; }
.pf-row-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pf-row-addr { font-size: 12px; color: #455; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pf-row-addr .arrow { color: #999; }
.pf-row-addr .node { margin-left: 10px; }
.pf-row-stats { font-size: 11px; color: #888; }
.pf-row-actions { display: flex; gap: 6px; }
</style>