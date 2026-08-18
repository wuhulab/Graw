<template>
  <div style="display:flex; flex-direction:column; height:100%; background:#f5f5f7;">
    <div style="flex:1; overflow:auto; padding:16px; display:flex; flex-direction:column; gap:14px;">
      <div class="block">
        <div class="block-title">{{ $t('settings.title') }}</div>
        <button class="btn" @click="emit('openUsers')" :disabled="!isAdmin()">{{ $t('settings.openUsers') }}</button>
        <span v-if="!isAdmin()" style="font-size:11px;color:#6e6e73;margin-left:8px;">{{ $t('common.adminOnly') }}</span>
      </div>

      <!-- ShunX 安全入口管理（仅管理员） -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">{{ $t('settings.shunxTitle') }}</div>
        <div class="row" style="flex-wrap:wrap; gap:6px;">
          <span class="status-dot" :class="currentEntry ? 'on' : 'off'"></span>
          <span style="font-size:12px;color:#1d1d1f;">
            {{ statusText }}
          </span>
        </div>
        <div class="row" style="flex-direction:column; align-items:stretch; gap:8px;">
          <input v-model="entryPath" :placeholder="$t('settings.shunxPlaceholder')" spellcheck="false" @keyup.enter="saveEntry" />
          <div style="display:flex; gap:8px;">
            <button class="btn" :disabled="saving" @click="saveEntry">{{ saving ? $t('settings.saveSaving') : $t('settings.save') }}</button>
            <button class="btn btn-danger" v-if="currentEntry" :disabled="saving" @click="clearEntry">{{ $t('settings.clearEntry') }}</button>
          </div>
          <div v-if="msg" :class="['msg', msgType]">{{ msg }}</div>
        </div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-top:4px;">
          {{ $t('settings.entryHint', { url: origin + '/' + (currentEntry || '...') }) }}
        </div>
      </div>

      <!-- 多机（多节点）管理（仅管理员） -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">{{ $t('nodes.title') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('nodes.subtitle') }}</div>

        <!-- 当前管理主机 -->
        <div class="row" style="gap:8px;">
          <span class="status-dot" style="background:#0a7d3b;"></span>
          <span style="font-size:12px;color:#1d1d1f;">{{ $t('nodes.currentLabel') }}</span>
          <span style="font-size:12px;font-weight:600;color:#1d1d1f;">{{ current.name || currentId }}</span>
          <span :class="['tag', current.type === 'ssh' ? 'tag-remote' : 'tag-local']">{{ current.type === 'ssh' ? $t('nodes.remoteBadge') : $t('nodes.localBadge') }}</span>
        </div>

        <!-- 节点列表 -->
        <div v-if="nodesList.length" style="display:flex;flex-direction:column;gap:6px;margin-top:4px;">
          <div v-for="n in nodesList" :key="n.id" class="node-item">
            <label class="switch-label" style="flex:1;min-width:0;">
              <input type="radio" name="currentHost" :value="n.id" :checked="n.id === currentId" @change="switchNode(n)" />
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                {{ n.name }}
                <span v-if="n.type === 'ssh'" style="color:#8e8e93;font-size:11px;">{{ n.user }}@{{ n.host }}:{{ n.port }}</span>
              </span>
            </label>
            <span v-if="n.id === currentId" class="tag tag-current">{{ $t('nodes.current') }}</span>
            <button class="btn btn-mini" @click="testNode(n)">{{ testingId === n.id ? $t('nodes.testing') : $t('nodes.test') }}</button>
            <button class="btn btn-mini" v-if="n.type === 'ssh'" @click="startEdit(n)">{{ $t('nodes.edit') }}</button>
            <button class="btn btn-mini btn-danger" v-if="n.type === 'ssh'" @click="removeNode(n)">{{ $t('nodes.delete') }}</button>
          </div>
        </div>
        <div v-else style="font-size:12px;color:#8e8e93;padding:6px 0;">{{ $t('nodes.noNodes') }}</div>

        <div v-if="!showEditor" style="margin-top:8px;">
          <button class="btn" @click="startAdd">{{ $t('nodes.addNode') }}</button>
        </div>

        <!-- 添加 / 编辑 SSH 节点表单 -->
        <div v-if="showEditor" class="editor">
          <div class="row" style="flex-direction:column;align-items:stretch;gap:6px;">
            <input v-model="form.name" :placeholder="$t('nodes.namePlaceholder')" spellcheck="false" />
            <div class="row" style="gap:6px;">
              <input v-model="form.host" :placeholder="$t('nodes.hostPlaceholder')" spellcheck="false" style="flex:1;" />
              <input v-model.number="form.port" type="number" placeholder="22" style="width:70px;" />
            </div>
            <input v-model="form.user" :placeholder="$t('nodes.userPlaceholder')" spellcheck="false" />
            <div class="row" style="gap:12px;">
              <label class="switch-label"><input type="radio" name="auth" value="password" v-model="form.auth" /><span>{{ $t('nodes.authPassword') }}</span></label>
              <label class="switch-label"><input type="radio" name="auth" value="key" v-model="form.auth" /><span>{{ $t('nodes.authKey') }}</span></label>
            </div>
            <input v-if="form.auth === 'password'" v-model="form.password" type="password" :placeholder="$t('nodes.passwordPlaceholder')" spellcheck="false" />
            <input v-else v-model="form.key_path" :placeholder="$t('nodes.keyPathPlaceholder')" spellcheck="false" />
            <div style="font-size:11px;color:#8e8e93;">{{ form.auth === 'password' ? $t('nodes.passwordHint') : $t('nodes.keyHint') }}</div>
            <div style="display:flex;gap:8px;">
              <button class="btn" :disabled="saving" @click="saveNode">{{ saving ? $t('settings.saveSaving') : $t('nodes.save') }}</button>
              <button class="btn btn-mini" @click="cancelEdit">{{ $t('nodes.cancel') }}</button>
            </div>
            <div v-if="editorMsg" :class="['msg', msgType]">{{ editorMsg }}</div>
          </div>
        </div>
      </div>

      <div class="block">
        <div class="block-title">{{ $t('settings.panelTitle') }}</div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.showTaskbarText" />
            <span>{{ $t('settings.showTaskbarText') }}</span>
          </label>
        </div>
        <div class="row">
          <label class="switch-label">
            <input type="checkbox" v-model="settings.taskbarTextOnly" />
            <span>{{ $t('settings.taskbarTextOnly') }}</span>
          </label>
        </div>
      </div>

      <!-- 界面语言 -->
      <div class="block">
        <div class="block-title">{{ $t('settings.language') }}</div>
        <div class="row" style="flex-wrap:wrap; gap:6px;">
          <label class="switch-label" v-for="lang in LANGUAGES" :key="lang.code" :style="{ fontWeight: settings.locale === lang.code ? 700 : 400 }">
            <input type="radio" name="locale" :value="lang.code" :checked="settings.locale === lang.code" @change="changeLocale(lang.code)" />
            <span>{{ lang.name }}</span>
          </label>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { settings } from '../../store/settings'
import { isAdmin } from '../../store/auth'
import { nodesApi, shunxApi } from '../../api'
import { nodes as nodesStore, refreshNodes, setCurrentNode } from '../../store/nodes'
import { LANGUAGES, setLocale } from '../../locales'

const { t } = useI18n()
const emit = defineEmits(['openUsers'])

// ShunX 安全入口状态
const entryPath = ref('')
const currentEntry = ref('')
const saving = ref(false)
const msg = ref('')
const msgType = ref('')
const origin = computed(() => window.location.origin)

const statusText = computed(() => {
  if (!currentEntry.value) return t('settings.shunxNotSet')
  return t('settings.shunxEnabled', { path: currentEntry.value })
})

// ---- 多机（多节点）管理 ----
const nodesList = ref([])
const currentId = ref('local')
const current = computed(() => {
  const cur = nodesList.value.find((n) => n.id === currentId.value)
  return cur || { id: 'local', name: 'local', type: 'local' }
})
const showEditor = ref(false)
const editingId = ref(null)
const form = reactive({ id: '', name: '', host: '', port: 22, user: '', auth: 'password', password: '', key_path: '' })
const savingNode = ref(false)
const testingId = ref('')
const editorMsg = ref('')
const editorMsgType = ref('')

async function loadNodes() {
  try {
    await refreshNodes()
    nodesList.value = nodesStore.list
    currentId.value = nodesStore.currentId
  } catch (e) {
    editorMsg.value = t('nodes.loadFailed', { error: e?.response?.data?.detail || e.message })
    editorMsgType.value = 'err'
  }
}

function startAdd() {
  editingId.value = null
  Object.assign(form, { id: '', name: '', host: '', port: 22, user: '', auth: 'password', password: '', key_path: '' })
  editorMsg.value = ''
  showEditor.value = true
}

function startEdit(n) {
  editingId.value = n.id
  Object.assign(form, {
    id: n.id,
    name: n.name,
    host: n.host,
    port: n.port,
    user: n.user,
    auth: n.auth,
    password: '',
    key_path: n.key_path || '',
  })
  editorMsg.value = ''
  showEditor.value = true
}

function cancelEdit() {
  showEditor.value = false
  editorMsg.value = ''
}

async function saveNode() {
  if (savingNode.value) return
  const name = (form.name || '').trim()
  const host = (form.host || '').trim()
  const user = (form.user || '').trim()
  if (!name) return editorError(t('nodes.nameRequired'))
  if (!host) return editorError(t('nodes.hostRequired'))
  if (!user) return editorError(t('nodes.userRequired'))
  if (form.auth === 'key' && !(form.key_path || '').trim()) return editorError(t('nodes.keyRequired'))
  editorMsg.value = ''
  editorMsgType.value = ''
  savingNode.value = true
  try {
    const body = {
      name,
      host,
      port: form.port || 22,
      user,
      auth: form.auth,
      password: form.password || '',
      key_path: (form.key_path || '').trim(),
    }
    if (editingId.value) {
      await nodesApi.update(editingId.value, body)
    } else {
      await nodesApi.create(body)
    }
    await loadNodes()
    showEditor.value = false
    editorMsg.value = t('nodes.saved')
    editorMsgType.value = 'ok'
  } catch (e) {
    editorError(t('nodes.saveFailed', { error: e?.response?.data?.detail || e.message }))
  } finally {
    savingNode.value = false
  }
}

function editorError(msg) {
  editorMsg.value = msg
  editorMsgType.value = 'err'
  return false
}

async function switchNode(n) {
  if (n.id === currentId.value) return
  try {
    await setCurrentNode(n.id)
    currentId.value = nodesStore.currentId
    editorMsg.value = t('nodes.switched', { name: n.name })
    editorMsgType.value = 'ok'
  } catch (e) {
    editorError(t('nodes.switchFailed', { error: e?.response?.data?.detail || e.message }))
  }
}

async function testNode(n) {
  testingId.value = n.id
  try {
    const res = await nodesApi.test(n.id)
    editorMsg.value = res.ok ? t('nodes.testOk') : t('nodes.testFail', { error: res.message || '' })
    editorMsgType.value = res.ok ? 'ok' : 'err'
  } catch (e) {
    editorError(t('nodes.testFail', { error: e?.response?.data?.detail || e.message }))
  } finally {
    testingId.value = ''
  }
}

async function removeNode(n) {
  if (!confirm(t('nodes.confirmDelete', { name: n.name }))) return
  try {
    await nodesApi.delete(n.id)
    await loadNodes()
  } catch (e) {
    editorError(t('nodes.deleteFailed', { error: e?.response?.data?.detail || e.message }))
  }
}

onMounted(async () => {
  try {
    const config = await shunxApi.config()
    currentEntry.value = config.entry_path || ''
    entryPath.value = currentEntry.value
  } catch (e) {
    currentEntry.value = ''
  }
  if (isAdmin()) loadNodes()
})

// 切换界面语言
function changeLocale(code) {
  setLocale(code)
}

async function saveEntry() {
  if (saving.value) return
  saving.value = true
  msg.value = ''
  try {
    const res = await shunxApi.update(entryPath.value)
    const config = res.config || {}
    currentEntry.value = config.entry_path || ''
    entryPath.value = currentEntry.value
    msg.value = currentEntry.value
      ? t('settings.entrySet', { url: `${origin.value}/${currentEntry.value}` })
      : t('settings.entryCleared')
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('settings.saveFailed')
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}

async function clearEntry() {
  if (saving.value) return
  if (!confirm(t('settings.clearConfirm'))) return
  saving.value = true
  msg.value = ''
  try {
    await shunxApi.update('')
    currentEntry.value = ''
    entryPath.value = ''
    msg.value = t('settings.entryCleared')
    msgType.value = 'ok'
  } catch (e) {
    msg.value = e?.response?.data?.detail || t('settings.clearFailed')
    msgType.value = 'err'
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.block {
  background: #fff;
  border-radius: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(0,0,0,0.06);
}
.block-title {
  font-size: 12px;
  font-weight: 600;
  color: #1d1d1f;
  margin-bottom: 10px;
}
.row {
  display: flex;
  align-items: center;
  padding: 6px 0;
  font-size: 12px;
}
.switch-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: #1d1d1f;
}
.switch-label input {
  width: 16px;
  height: 16px;
  cursor: pointer;
}
.btn {
  padding: 6px 14px;
  font-size: 12px;
  color: #fff;
  background: #0a84ff;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}
.btn:hover:not(:disabled) { background: #006ee6; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.btn-danger {
  background: #e5484d;
}
.btn-danger:hover:not(:disabled) { background: #d63d42; }
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.status-dot.on { background: #0a7d3b; }
.status-dot.off { background: #c0392b; }
input {
  width: 100%;
  padding: 8px 10px;
  font-size: 12px;
  font-family: inherit;
  color: #1d1d1f;
  border: 1px solid rgba(0,0,0,0.12);
  border-radius: 8px;
  outline: none;
  box-sizing: border-box;
}
input:focus {
  border-color: #0a84ff;
  box-shadow: 0 0 0 3px rgba(10,132,255,0.15);
}
.msg {
  font-size: 12px;
  border-radius: 6px;
  padding: 6px 8px;
}
.msg.ok {
  color: #0a7d3b;
  background: rgba(10,132,255,0.08);
  border: 1px solid rgba(10,132,255,0.25);
}
.msg.err {
  color: #c0392b;
  background: rgba(255,59,48,0.08);
  border: 1px solid rgba(255,59,48,0.2);
}
/* ---- 多机（多节点）管理 ---- */
.node-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid rgba(0,0,0,0.05);
}
.tag {
  font-size: 10px;
  padding: 1px 7px;
  border-radius: 999px;
  flex-shrink: 0;
}
.tag-local {
  color: #0a7d3b;
  background: rgba(10,125,59,0.12);
}
.tag-remote {
  color: #0a84ff;
  background: rgba(10,132,255,0.12);
}
.tag-current {
  color: #fff;
  background: #0a84ff;
}
.btn-mini {
  padding: 3px 10px;
  font-size: 11px;
}
.editor {
  margin-top: 10px;
  padding: 10px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid rgba(0,0,0,0.06);
}
</style>
