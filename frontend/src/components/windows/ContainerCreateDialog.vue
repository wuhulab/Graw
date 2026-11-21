<!--
  ContainerCreateDialog.vue — 新建容器
  ==========================================================
  业务作用：
    不依赖 compose，直接从镜像创建一个容器：端口、环境变量、卷、网络、
    重启策略与启动命令；创建成功后通知父窗口刷新容器列表。
  后端模块：
    POST /api/docker/containers/create（handlers_docker_extra.go）。
  打开方式：
    DockerWindow 容器页签工具栏「创建容器」。
-->
<template>
  <div v-if="show" class="mask" @click.self="$emit('close')">
    <div class="dialog">
      <div class="head">{{ $t('docker.p1.createTitle') }}</div>
      <div class="body">
        <label class="field">{{ $t('docker.p1.image') }} *
          <input v-model.trim="form.image" class="inp" placeholder="nginx:1.25" spellcheck="false" />
        </label>
        <label class="field">{{ $t('docker.p1.containerName') }}
          <input v-model.trim="form.name" class="inp" placeholder="my-nginx" spellcheck="false" />
        </label>
        <label class="field">{{ $t('docker.p1.ports') }}
          <textarea v-model="form.ports" class="area" spellcheck="false" :placeholder="$t('docker.p1.portsHint')" />
        </label>
        <label class="field">{{ $t('docker.p1.env') }}
          <textarea v-model="form.env" class="area" spellcheck="false" placeholder="TZ=Asia/Shanghai" />
        </label>
        <label class="field">{{ $t('docker.p1.volumes') }}
          <textarea v-model="form.volumes" class="area" spellcheck="false" placeholder="/data/app:/app" />
        </label>
        <div class="row">
          <label class="field">{{ $t('docker.p1.network') }}
            <input v-model.trim="form.network" class="inp" placeholder="bridge" spellcheck="false" />
          </label>
          <label class="field">{{ $t('docker.p1.restart') }}
            <select v-model="form.restart" class="inp">
              <option value="unless-stopped">unless-stopped</option>
              <option value="always">always</option>
              <option value="on-failure">on-failure</option>
              <option value="no">no</option>
            </select>
          </label>
        </div>
        <label class="field">{{ $t('docker.p1.command') }}
          <input v-model.trim="form.command" class="inp" placeholder="sh -c ..." spellcheck="false" />
        </label>
        <div v-if="err" class="err">{{ err }}</div>
      </div>
      <div class="foot">
        <button class="btn" :disabled="saving" @click="$emit('close')">{{ $t('common.cancel') }}</button>
        <button class="btn primary" :disabled="saving || !form.image" @click="submit">
          {{ saving ? $t('common.loading') : $t('common.create') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { dockerApi } from '../../api'

defineProps({ show: Boolean })
const emit = defineEmits(['close', 'created'])
const form = reactive({ image: '', name: '', ports: '', env: '', volumes: '', network: '', restart: 'unless-stopped', command: '' })
const saving = ref(false)
const err = ref('')

function lines(text) {
  return String(text || '').split('\n').map(s => s.trim()).filter(Boolean)
}

function parsePorts(text) {
  const out = []
  for (const line of lines(text)) {
    const m = /^(\d+):(\d+)(?:\/(tcp|udp))?$/i.exec(line)
    if (!m) continue
    out.push({ host: Number(m[1]), container: Number(m[2]), protocol: (m[3] || 'tcp').toLowerCase() })
  }
  return out
}

function parseEnv(text) {
  const out = []
  for (const line of lines(text)) {
    const i = line.indexOf('=')
    if (i <= 0) continue
    out.push({ key: line.slice(0, i), value: line.slice(i + 1) })
  }
  return out
}

async function submit() {
  saving.value = true
  err.value = ''
  try {
    await dockerApi.createContainer({
      image: form.image,
      name: form.name,
      ports: parsePorts(form.ports),
      env: parseEnv(form.env),
      volumes: lines(form.volumes),
      network: form.network,
      restart: form.restart,
      command: form.command
    })
    emit('created')
    emit('close')
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.mask { position: absolute; inset: 0; background: rgba(15, 23, 42, .55); display: flex; align-items: center; justify-content: center; padding: 24px; z-index: 60; }
.dialog { width: 100%; max-width: 560px; max-height: 92%; background: var(--g-surface); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,.3); }
.head { padding: 11px 14px; border-bottom: 1px solid var(--g-border); font-weight: 700; font-size: 13.5px; background: var(--g-surface-muted); }
.body { padding: 12px 14px; overflow-y: auto; flex: 1; min-height: 0; }
.foot { padding: 10px 14px; border-top: 1px solid var(--g-border); display: flex; justify-content: flex-end; gap: 8px; background: var(--g-surface-muted); }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; margin-bottom: 9px; }
.inp, .area { padding: 6px 8px; border: 1px solid var(--g-border); border-radius: 8px; background: var(--g-surface); color: var(--g-text); font-size: 12px; }
.area { min-height: 56px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.err { color: #b91c1c; font-size: 12px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; padding: 6px 8px; }
</style>
