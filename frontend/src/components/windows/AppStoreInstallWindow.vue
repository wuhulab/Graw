<template>
  <div class="install-window">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <span class="title"><Download :size="15" /> {{ $t('appinstall.title', { name: appDisplayName }) }}</span>
      <span class="badge mono">{{ fmtVersion(installForm.version) }}</span>
      <button class="btn" style="margin-left:auto;" @click="emit('close')">{{ $t('common.close') }}</button>
    </div>

    <div class="body">
      <!-- 版本警告横幅 -->
      <div v-if="app?.warn" class="warn-banner">
        <AlertTriangle :size="14" /> {{ app.warn }}
      </div>

      <div class="form-grid">
        <!-- Graw 维护应用名称：默认 <app-id>-<随机6位> -->
        <label class="field">
          <span class="field-label">{{ $t('appinstall.appNameLabel') }} <b class="req">*</b></span>
          <input v-model.trim="installForm.app_name" class="inp mono" placeholder="如: my-uptime"
                 :class="{ err: appNameError }" @input="appNameError = ''" />
          <span class="field-hint">{{ $t('appinstall.appNameHint') }}</span>
        </label>

        <!-- 版本 -->
        <label class="field" v-if="(app?.versions || []).length > 0">
          <span class="field-label">{{ $t('appinstall.version') }}</span>
          <select v-model="installForm.version" class="inp">
            <option v-for="v in app?.versions" :key="v.tag" :value="v.tag">
              {{ v.label }}（{{ v.tag }}）
            </option>
          </select>
          <span class="field-hint">{{ $t('appinstall.defaultVersionHint') }}</span>
        </label>

        <!-- 端口（支持多端口分别映射） -->
        <div class="field">
          <span class="field-label">{{ $t('appinstall.portsLabel') }}</span>
          <div v-if="app?.ports?.length" class="port-list">
            <div v-for="p in installForm.ports" :key="p.container" class="port-row">
              <span class="port-container" :title="portLabel(p.container)">
                {{ p.container }}<em v-if="portLabel(p.container)"> · {{ portLabel(p.container) }}</em>
              </span>
              <input v-model.number="p.external" type="number" min="1" max="65535" class="inp port-inp"
                     :placeholder="$t('appinstall.portDefault', { port: p.container })" />
            </div>
            <span class="field-hint">{{ $t('appinstall.portsHint') }}</span>
          </div>
          <span class="field-hint" v-else>{{ $t('appinstall.noPorts') }}</span>
        </div>

        <!-- 时区 -->
        <label class="field">
          <span class="field-label">{{ $t('appinstall.timezone') }}</span>
          <input v-model="installForm.timezone" class="inp mono" list="tz-list" placeholder="Asia/Shanghai" />
          <datalist id="tz-list">
            <option v-for="z in commonTimezones" :key="z" :value="z" />
          </datalist>
          <span class="field-hint">{{ $t('appinstall.timezoneHint') }}</span>
        </label>

        <!-- 容器名称 -->
        <label class="field">
          <span class="field-label">{{ $t('appinstall.containerName') }}</span>
          <input v-model.trim="installForm.container_name" class="inp mono" :placeholder="$t('appinstall.containerNamePlaceholder')"
                 @input="containerTouched = true" />
          <span class="field-hint">{{ $t('appinstall.containerNameHint') }}</span>
        </label>

        <!-- 重启规则 -->
        <label class="field">
          <span class="field-label">{{ $t('appinstall.restartRule') }}</span>
          <select v-model="installForm.restart" class="inp">
            <option value="always">{{ $t('appinstall.restartAlways') }}</option>
            <option value="unless-stopped">{{ $t('appinstall.restartUnlessStopped') }}</option>
            <option value="on-failure">{{ $t('appinstall.restartOnFailure') }}</option>
            <option value="no">{{ $t('appinstall.restartNo') }}</option>
          </select>
        </label>

        <!-- CPU 限制 -->
        <label class="field">
          <span class="field-label">{{ $t('appinstall.cpuLimit') }}</span>
          <input v-model.number="installForm.cpu_limit" type="number" min="0" step="0.5" class="inp" placeholder="0" />
          <span class="field-hint">{{ $t('appinstall.cpuLimitHint') }}</span>
        </label>

        <!-- 内存限制 -->
        <label class="field">
          <span class="field-label">{{ $t('appinstall.memoryLimit') }}</span>
          <input v-model.number="installForm.mem_limit_mb" type="number" min="0" step="64" class="inp" placeholder="0" />
          <span class="field-hint">{{ $t('appinstall.memoryLimitHint') }}</span>
        </label>
      </div>

      <!-- 选项 -->
      <div class="options">
        <label class="check-label">
          <input type="checkbox" v-model="installForm.expose_port" :disabled="!exposePortsText || exposePortsText === '—'" />
          <span>{{ $t('appinstall.allowExternalAccess', { ports: exposePortsText }) }}</span>
        </label>
        <label class="check-label">
          <input type="checkbox" v-model="installForm.pull" />
          <span>{{ $t('appinstall.pullImage') }}</span>
        </label>
        <button class="btn" @click="openComposeEditor" :disabled="composeLoading">
          <Pencil :size="13" /> {{ composeLoading ? $t('common.loading') : $t('appinstall.editCompose') }}
        </button>
        <span v-if="composeEdited" class="edited-hint"><CheckCircle2 :size="12" /> {{ $t('appinstall.edited') }}</span>
      </div>

      <div v-if="appNameError" class="error-banner">{{ appNameError }}</div>

      <div class="actions">
        <button class="btn" @click="emit('close')">{{ $t('common.cancel') }}</button>
        <button class="btn primary" @click="doInstall">{{ $t('appinstall.confirmInstall') }}</button>
      </div>
    </div>

    <!-- 版本安全警告居中弹窗（应用存在 warn 元数据时弹出） -->
    <div v-if="showWarnModal" class="modal-overlay">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-head"><AlertTriangle :size="18" /> {{ $t('appinstall.warnTitle') }}</div>
        <p class="modal-msg">{{ app?.warn }}</p>
        <div class="modal-actions">
          <button class="btn" @click="emit('close')">{{ $t('common.cancel') }}</button>
          <button class="btn primary" @click="showWarnModal = false">{{ $t('appinstall.iUnderstand') }}</button>
        </div>
      </div>
    </div>

    <!-- 未勾选外部放行确认弹窗（非数据库应用安装时弹出） -->
    <div v-if="showNoExposeConfirm" class="modal-overlay">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-head"><AlertTriangle :size="18" /> {{ $t('appinstall.exposeTitle') }}</div>
        <p class="modal-msg">{{ $t('appinstall.exposeConfirm') }}</p>
        <div class="modal-actions">
          <button class="btn" @click="showNoExposeConfirm = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" @click="proceedInstall">{{ $t('appinstall.iUnderstandAndInstall') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { appStoreApi } from '../../api'
import { appStoreComposeState } from '../../store/appStoreCompose'
import { localizedName, localizedPortLabel } from '../../appStoreL10n'
import { Download, Pencil, CheckCircle2, AlertTriangle } from 'lucide-vue-next'

const { t, locale } = useI18n()

const props = defineProps({ app: Object })
const emit = defineEmits(['close', 'openComposeEditor', 'openInstallLog'])

// 应用显示名称：优先索引内嵌翻译（i18n.<locale>.yml），
// 其次前端语言包内 appNames 覆盖，最后回退索引默认名称
const appDisplayName = computed(() =>
  localizedName(props.app, locale.value) || t('appstore.appNames.' + props.app.id, props.app?.name || '')
)

const commonTimezones = [
  'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Tokyo', 'Asia/Singapore', 'Asia/Seoul',
  'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles',
  'Australia/Sydney', 'UTC'
]

// 版本号显示：部分应用 tag 自带 v 前缀（如 AList v3.40.0），避免重复显示 "vv"
function fmtVersion(v) {
  if (!v) return ''
  return String(v).startsWith('v') ? String(v) : 'v' + String(v)
}

const appNameError = ref('')
const composeLoading = ref(false)
const composeEdited = ref(false)
// 版本安全警告居中弹窗：应用存在 warn 元数据时打开即弹出，确认后才可安装
const showWarnModal = ref(Boolean(props.app?.warn))
// 未勾选外部放行确认弹窗：非数据库应用、未勾选外部放行时安装前弹出确认
const showNoExposeConfirm = ref(false)

// 数据库类应用（分类含「数据库」）不弹外部放行确认：数据库通常仅内网访问
const isDatabaseApp = computed(() => String(props.app?.category || '').includes('数据库'))

const APP_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/

// 随机6位hex
function randHex() {
  return Math.random().toString(16).slice(2, 8)
}

const installForm = reactive({
  // 默认应用名称：<app-id>-<随机6位hex>，避免重名冲突
  app_name: (props.app?.id || 'app') + '-' + randHex(),
  // 默认版本：版本列表第一个（即最新），否则回退 app.version / latest
  version: props.app?.versions?.[0]?.tag || props.app?.version || 'latest',
  // 多端口映射：为应用声明的每个容器端口预填外部端口（默认与容器端口相同，可分别修改）
  ports: (props.app?.ports || []).map(p => ({ container: p.container, external: p.container ?? null })),
  // 兼容旧字段：第一个端口的宿主端口
  port: props.app?.ports?.[0]?.container ?? null,
  timezone: 'Asia/Shanghai',
  container_name: '',
  expose_port: false,
  restart: 'always',
  cpu_limit: 0,
  mem_limit_mb: 0,
  pull: true,
  compose: null
})

// 端口 label 查找：返回容器端口对应的说明（如 "Web 界面"）。
// 优先索引内嵌翻译（i18n.<locale>.yml 的 ports），否则回退 data.yml 默认 label
function portLabel(container) {
  const localized = localizedPortLabel(props.app, container, locale.value)
  if (localized) return localized
  const p = (props.app?.ports || []).find(x => x.container === container)
  return p?.label || ''
}

// 防火墙放行端口文案：拼接所有已填写的外部端口
const exposePortsText = computed(() => {
  const list = installForm.ports.filter(p => p.external).map(p => p.external)
  return list.length ? list.join(', ') : '—'
})

function doInstall() {
  const name = installForm.app_name
  if (!name) { appNameError.value = t('appinstall.errNameRequired'); return }
  if (!APP_NAME_RE.test(name)) {
    appNameError.value = t('appinstall.errNameFormat')
    return
  }
  appNameError.value = ''

  // 除数据库类应用外，未勾选外部放行时先弹出确认：外部可能无法访问
  if (!installForm.expose_port && !isDatabaseApp.value) {
    showNoExposeConfirm.value = true
    return
  }
  proceedInstall()
}

function proceedInstall() {
  showNoExposeConfirm.value = false

  const name = installForm.app_name
  const composeContent = (appStoreComposeState.content && appStoreComposeState.appId === props.app.id)
    ? appStoreComposeState.content
    : installForm.compose

  // 多端口映射：过滤掉留空的端口，取第一个作为兼容旧字段的 port
  const effectivePorts = installForm.ports
    .filter(p => p.external)
    .map(p => ({ container: p.container, external: p.external }))

  const request = {
    app_id: props.app.id,
    app_name: name,
    version: installForm.version,
    port: effectivePorts[0]?.external ?? null,
    ports: effectivePorts.length ? effectivePorts : null,
    timezone: installForm.timezone || 'Asia/Shanghai',
    container_name: installForm.container_name || null,
    expose_port: installForm.expose_port,
    restart: installForm.restart,
    cpu_limit: installForm.cpu_limit || 0,
    mem_limit_mb: installForm.mem_limit_mb || 0,
    pull: installForm.pull,
    compose: composeContent
  }

  // 打开独立「安装日志」窗口执行安装，并关闭配置表单窗口
  emit('openInstallLog', { app: props.app, request })
  emit('close')
}

async function openComposeEditor() {
  composeLoading.value = true
  try {
    // 优先使用共享状态中的内容（已被编辑器保存过）
    let content = (appStoreComposeState.content && appStoreComposeState.appId === props.app.id)
      ? appStoreComposeState.content
      : installForm.compose

    // 未加载过则从远程拉取
    if (!content) {
      const r = await appStoreApi.compose(props.app.id)
      content = r.compose
      installForm.compose = content
    }

    // 写入共享状态，打开编辑器窗口
    appStoreComposeState.appId = props.app.id
    appStoreComposeState.content = content
    emit('openComposeEditor', { appId: props.app.id, compose: content })
  } catch (e) {
    alert(t('appcompose.fetchFailed', { error: e.response?.data?.detail || e.message }))
  } finally {
    composeLoading.value = false
  }
}

// 监听 compose 编辑器保存后的回调（通过共享状态）
// 在下次打开编辑器时自动读取最新内容
onMounted(() => {
  // 如果共享状态有当前应用的内容，标记已编辑状态
  if (appStoreComposeState.content && appStoreComposeState.appId === props.app.id) {
    composeEdited.value = true
    installForm.compose = appStoreComposeState.content
  }
})
</script>

<style scoped>
.install-window { position: relative; display: flex; flex-direction: column; height: 100%; overflow: hidden; }

.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
.title { font-weight: 700; font-size: 13.5px; display: inline-flex; align-items: center; gap: 6px; }
.badge { font-size: 11px; background: #eef2ff; color: #4338ca; padding: 1px 8px; border-radius: 999px; }

.body { flex: 1; overflow-y: auto; padding: 14px 16px; }

/* 版本警告横幅 */
.warn-banner {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: 12px;
  padding: 8px 10px;
  background: #fffbeb;
  color: #92400e;
  border: 1px solid #fde68a;
  border-radius: 6px;
  font-size: 12.5px;
  line-height: 1.5;
}

.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 14px; }
.field { display: flex; flex-direction: column; gap: 3px; }
.field-label { font-size: 12px; color: #374151; }
.field-label .req { color: #dc2626; }
.inp { padding: 6px 8px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 12.5px; outline: none; background: #fff; }
.inp:focus { border-color: #6366f1; box-shadow: 0 0 0 2px rgba(99,102,241,.15); }
.inp.err { border-color: #dc2626; }
.inp:disabled { background: #f9fafb; color: #9ca3af; }
.field-hint { font-size: 11px; color: #9ca3af; }

/* 多端口映射 */
.port-list { display: flex; flex-direction: column; gap: 6px; }
.port-row { display: flex; align-items: center; gap: 8px; }
.port-container {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  color: #374151;
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.port-container em { font-style: normal; font-size: 11px; color: #9ca3af; }
.port-inp { width: 110px; flex-shrink: 0; }

.options { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-top: 14px; }
.check-label { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: #374151; cursor: pointer; }
.edited-hint { display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px; color: #047857; }

.error-banner { margin-top: 10px; padding: 6px 10px; background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; border-radius: 6px; font-size: 12px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }

/* 版本安全警告居中弹窗 */
.modal-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.45);
  z-index: 50;
  padding: 20px;
}
.modal {
  width: 100%;
  max-width: 380px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.25);
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.modal-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 14px;
  color: #92400e;
}
.modal-msg {
  font-size: 13px;
  line-height: 1.7;
  color: #374151;
  margin: 0;
}
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>