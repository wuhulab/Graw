<!--
  ContainerRelatedDialog.vue — 容器关联资源
  ==========================================================
  业务作用：
    查看一个容器关联的网络、挂载卷、镜像与 compose 项目/服务归属，
    方便在删除容器或排查依赖前确认影响面。
  后端模块：
    GET /api/docker/containers/{id}/related。
  打开方式：
    DockerWindow 容器右键菜单「关联资源」。
-->
<template>
  <div v-if="show" class="mask" @click.self="$emit('close')">
    <div class="dialog">
      <div class="head">{{ $t('docker.p1.relatedTitle', { name: container?.name || '' }) }}</div>
      <div class="body">
        <div v-if="loading" class="muted">{{ $t('common.loading') }}</div>
        <template v-else-if="data">
          <div class="kv"><span class="k">{{ $t('docker.p1.image') }}</span><span class="v mono">{{ data.image || '-' }}</span></div>
          <div class="kv"><span class="k">{{ $t('docker.p1.networks') }}</span><span class="v mono">{{ (data.networks || []).join(', ') || '-' }}</span></div>
          <div class="kv"><span class="k">{{ $t('docker.p1.composeOwner') }}</span><span class="v mono">{{ data.compose_project || '-' }}{{ data.compose_service ? ' / ' + data.compose_service : '' }}</span></div>
          <div class="section">{{ $t('docker.p1.mounts') }}</div>
          <table class="tbl">
            <thead><tr><th>{{ $t('docker.p1.type') }}</th><th>{{ $t('docker.p1.source') }}</th><th>{{ $t('docker.p1.destination') }}</th></tr></thead>
            <tbody>
              <tr v-for="(m, i) in data.mounts || []" :key="i">
                <td>{{ m.type }}</td><td class="mono">{{ m.name || m.source }}</td><td class="mono">{{ m.destination }}</td>
              </tr>
              <tr v-if="!(data.mounts || []).length"><td colspan="3" class="muted">{{ $t('docker.p1.noMounts') }}</td></tr>
            </tbody>
          </table>
        </template>
        <div v-else-if="err" class="err">{{ err }}</div>
      </div>
      <div class="foot"><button class="btn" @click="$emit('close')">{{ $t('common.close') }}</button></div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { dockerApi } from '../../api'

const props = defineProps({ show: Boolean, container: Object })
defineEmits(['close'])
const loading = ref(false)
const err = ref('')
const data = ref(null)

watch(() => [props.show, props.container?.id], async ([show]) => {
  if (!show || !props.container?.id) return
  loading.value = true
  err.value = ''
  data.value = null
  try {
    data.value = await dockerApi.related(props.container.id)
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.mask { position: absolute; inset: 0; background: rgba(15, 23, 42, .55); display: flex; align-items: center; justify-content: center; padding: 24px; z-index: 60; }
.dialog { width: 100%; max-width: 620px; max-height: 90%; background: var(--g-surface); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,.3); }
.head { padding: 11px 14px; border-bottom: 1px solid var(--g-border); font-weight: 700; font-size: 13.5px; background: var(--g-surface-muted); }
.body { padding: 12px 14px; overflow-y: auto; flex: 1; min-height: 0; }
.foot { padding: 10px 14px; border-top: 1px solid var(--g-border); display: flex; justify-content: flex-end; background: var(--g-surface-muted); }
.kv { display: flex; gap: 10px; font-size: 12px; margin-bottom: 5px; }
.kv .k { width: 140px; color: var(--g-text-muted); flex-shrink: 0; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.section { font-weight: 700; font-size: 12.5px; margin: 12px 0 6px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th, .tbl td { border-bottom: 1px solid var(--g-border); padding: 5px 6px; text-align: left; }
.tbl th { color: var(--g-text-muted); font-weight: 600; }
.muted { color: var(--g-text-muted); }
.err { color: #b91c1c; font-size: 12px; }
</style>
