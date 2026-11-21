<!--
  DockerComposeTools.vue — 编排导入与模板库
  ==========================================================
  业务作用：
    在 Docker「编排」页签右键之外提供两个入口：
      1. 导入：粘贴 docker-compose.yml，落盘到 data/compose/<项目> 并启动；
      2. 模板库：把常用 compose 保存为模板，之后一键部署为新的编排项目。
  后端模块：
    /api/docker/compose/import、/api/docker/compose/templates 系列。
  打开方式：
    由 DockerWindow 在编排视图工具栏渲染；成功后 emit('changed') 刷新项目列表。
-->
<template>
  <span class="tools">
    <button class="btn" @click="openImport"><Upload :size="13" /> {{ $t('docker.p1.importCompose') }}</button>
    <button class="btn" @click="openTemplates"><LayoutTemplate :size="13" /> {{ $t('docker.p1.templates') }}</button>

    <!-- 导入编排 -->
    <div v-if="importShow" class="mask" @click.self="importShow = false">
      <div class="dialog">
        <div class="head">{{ $t('docker.p1.importCompose') }}</div>
        <div class="body">
          <label class="field">{{ $t('docker.p1.projectName') }} *
            <input v-model.trim="importForm.name" class="inp" placeholder="my-stack" spellcheck="false" />
          </label>
          <label class="field">{{ $t('docker.p1.composeFile') }}
            <textarea v-model="importForm.compose" class="area" spellcheck="false" />
          </label>
          <label class="check"><input type="checkbox" v-model="importForm.start" /> {{ $t('docker.p1.startAfterImport') }}</label>
          <div v-if="err" class="err">{{ err }}</div>
        </div>
        <div class="foot">
          <button class="btn" :disabled="busy" @click="importShow = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="busy || !importForm.name || !importForm.compose.trim()" @click="doImport">
            {{ busy ? $t('common.loading') : $t('docker.p1.importCompose') }}
          </button>
        </div>
      </div>
    </div>

    <!-- 模板库 -->
    <div v-if="templateShow" class="mask" @click.self="templateShow = false">
      <div class="dialog wide">
        <div class="head">{{ $t('docker.p1.templates') }}</div>
        <div class="body">
          <div class="save-box">
            <div class="save-title">{{ $t('docker.p1.saveTemplate') }}</div>
            <input v-model.trim="templateForm.name" class="inp" :placeholder="$t('docker.p1.templateName')" spellcheck="false" />
            <textarea v-model="templateForm.compose" class="area" spellcheck="false" />
            <button class="btn primary sm" :disabled="busy || !templateForm.name || !templateForm.compose.trim()" @click="saveTemplate">
              {{ $t('common.save') }}
            </button>
          </div>
          <table class="tbl">
            <thead><tr><th>{{ $t('common.name') }}</th><th>{{ $t('docker.p1.updated') }}</th><th style="width:170px;">{{ $t('common.action') }}</th></tr></thead>
            <tbody>
              <tr v-for="tpl in templates" :key="tpl.name">
                <td class="mono">{{ tpl.name }}</td>
                <td>{{ tpl.updated_at || tpl.created_at }}</td>
                <td>
                  <button class="btn sm primary" :disabled="busy" @click="deploy(tpl)">{{ $t('docker.p1.deploy') }}</button>
                  <button class="btn sm" :disabled="busy" @click="loadIntoForm(tpl)">{{ $t('common.edit') }}</button>
                  <button class="btn sm danger" :disabled="busy" @click="remove(tpl)">{{ $t('common.delete') }}</button>
                </td>
              </tr>
              <tr v-if="!templates.length"><td colspan="3" class="muted">{{ $t('docker.p1.noTemplates') }}</td></tr>
            </tbody>
          </table>
          <div v-if="err" class="err">{{ err }}</div>
        </div>
        <div class="foot">
          <button class="btn" @click="templateShow = false">{{ $t('common.close') }}</button>
        </div>
      </div>
    </div>
  </span>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { dockerApi } from '../../api'
import { Upload, LayoutTemplate } from 'lucide-vue-next'

const emit = defineEmits(['changed'])
const sample = 'services:\n  web:\n    image: nginx:latest\n    ports:\n      - "8080:80"\n'
const importShow = ref(false)
const templateShow = ref(false)
const busy = ref(false)
const err = ref('')
const templates = ref([])
const importForm = reactive({ name: '', compose: sample, start: true })
const templateForm = reactive({ name: '', compose: sample })

function openImport() {
  err.value = ''
  importShow.value = true
}

async function openTemplates() {
  err.value = ''
  templateShow.value = true
  await loadTemplates()
}

async function loadTemplates() {
  try {
    const r = await dockerApi.composeTemplates()
    templates.value = r.templates || []
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  }
}

async function doImport() {
  busy.value = true
  err.value = ''
  try {
    await dockerApi.importCompose({ name: importForm.name, compose: importForm.compose, start: importForm.start })
    importShow.value = false
    importForm.name = ''
    emit('changed')
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

async function saveTemplate() {
  busy.value = true
  err.value = ''
  try {
    await dockerApi.saveComposeTemplate({ name: templateForm.name, compose: templateForm.compose })
    await loadTemplates()
    templateForm.name = ''
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

function loadIntoForm(tpl) {
  templateForm.name = tpl.name
  templateForm.compose = tpl.compose || ''
}

async function deploy(tpl) {
  const project = window.prompt('项目名称', tpl.name)
  if (!project) return
  busy.value = true
  err.value = ''
  try {
    await dockerApi.deployComposeTemplate(tpl.name, { project, start: true })
    templateShow.value = false
    emit('changed')
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}

async function remove(tpl) {
  if (!window.confirm('删除模板 ' + tpl.name + ' ?')) return
  busy.value = true
  try {
    await dockerApi.deleteComposeTemplate(tpl.name)
    await loadTemplates()
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.tools { display: inline-flex; gap: 6px; }
.mask { position: absolute; inset: 0; background: rgba(15, 23, 42, .55); display: flex; align-items: center; justify-content: center; padding: 24px; z-index: 60; }
.dialog { width: 100%; max-width: 620px; max-height: 92%; background: var(--g-surface); border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 20px 50px rgba(0,0,0,.3); }
.dialog.wide { max-width: 820px; }
.head { padding: 11px 14px; border-bottom: 1px solid var(--g-border); font-weight: 700; font-size: 13.5px; background: var(--g-surface-muted); }
.body { padding: 12px 14px; overflow-y: auto; flex: 1; min-height: 0; }
.foot { padding: 10px 14px; border-top: 1px solid var(--g-border); display: flex; justify-content: flex-end; gap: 8px; background: var(--g-surface-muted); }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; margin-bottom: 9px; }
.inp, .area { padding: 6px 8px; border: 1px solid var(--g-border); border-radius: 8px; background: var(--g-surface); color: var(--g-text); font-size: 12px; }
.area { min-height: 220px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.check { display: flex; align-items: center; gap: 6px; font-size: 12px; margin-bottom: 6px; }
.err { color: #b91c1c; font-size: 12px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; padding: 6px 8px; white-space: pre-wrap; }
.save-box { border: 1px solid var(--g-border); border-radius: 10px; padding: 10px; margin-bottom: 12px; display: flex; flex-direction: column; gap: 8px; }
.save-title { font-weight: 700; font-size: 12.5px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tbl th, .tbl td { border-bottom: 1px solid var(--g-border); padding: 6px; text-align: left; }
.tbl th { color: var(--g-text-muted); font-weight: 600; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.muted { color: var(--g-text-muted); }
</style>
