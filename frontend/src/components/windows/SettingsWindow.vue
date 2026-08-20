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

      <!-- Web 服务器引擎模式（NGINX / OpenResty）（仅管理员） -->
      <div class="block" v-if="isAdmin()">
        <div class="block-title">{{ $t('settings.webmode.title') }}</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">{{ $t('settings.webmode.desc') }}</div>
        <div class="row" style="flex-wrap:wrap; gap:16px;">
          <label class="switch-label">
            <input type="radio" name="webMode" value="nginx" v-model="wmMode" />
            <span>{{ $t('settings.webmode.nginx') }}</span>
          </label>
          <label class="switch-label">
            <input type="radio" name="webMode" value="openresty" v-model="wmMode" />
            <span>{{ $t('settings.webmode.openresty') }}</span>
          </label>
        </div>
        <!-- 可用性提示：两个引擎 + 当前配置目录 -->
        <div class="row" style="flex-direction:column; align-items:stretch; gap:4px;">
          <div style="font-size:11px;color:#8e8e93;">
            {{ $t('settings.webmode.bin', { bin: wmStatus.binary }) }}
            <span :class="['status-dot', wmStatus.available ? 'on' : 'off']"></span>
            {{ wmStatus.available ? $t('settings.webmode.installed') : $t('settings.webmode.notInstalled') }}
          </div>
          <div style="font-size:11px;color:#8e8e93;">
            {{ $t('settings.webmode.nginxBin') }}: <span :class="['status-dot', wmStatus.nginx_available ? 'on' : 'off']"></span>
            <span style="color:#1d1d1f;">{{ wmStatus.nginx_available ? $t('settings.webmode.installed') : $t('settings.webmode.notInstalled') }}</span>
            &nbsp;·&nbsp;
            {{ $t('settings.webmode.openrestyBin') }}: <span :class="['status-dot', wmStatus.openresty_available ? 'on' : 'off']"></span>
            <span style="color:#1d1d1f;">{{ wmStatus.openresty_available ? $t('settings.webmode.installed') : $t('settings.webmode.notInstalled') }}</span>
          </div>
          <div style="font-size:11px;color:#8e8e93;">
            {{ $t('settings.webmode.confDir', { dir: wmStatus.conf_base }) }}
          </div>
        </div>
        <div class="row" style="gap:8px;">
          <button class="btn" :disabled="wmsaving" @click="saveWebMode">
            {{ wmsaving ? $t('settings.saveSaving') : $t('settings.save') }}
          </button>
          <div v-if="wmmsg" :class="['msg', wmmsgType]" style="flex:1;">{{ wmmsg }}</div>
        </div>
      </div>

      <!-- 两步验证（2FA）：为当前账号开启 / 关闭 TOTP 动态口令 -->
      <div class="block">
        <div class="block-title">两步验证（2FA）</div>
        <div style="font-size:11px;color:#8e8e93;line-height:1.6;margin-bottom:8px;">
          开启后登录需输入密码 + 手机验证器（Google Authenticator 等）中的 6 位动态验证码
        </div>

        <template v-if="!otpState.enabled">
          <div class="row" style="align-items:center;">
            <span style="font-size:12.5px;">{{ otpState.has_secret ? '已生成密钥，扫描或手动输入后启用' : '尚未开启两步验证' }}</span>
            <button class="btn" :disabled="otpBusy" @click="otpSetup">{{ otpState.has_secret ? '重新生成密钥' : '开启两步验证' }}</button>
          </div>

          <!-- 密钥 / 二维码展示（setup 后） -->
          <div v-if="otpSecret" style="margin-top:10px;border:1px dashed #c7d2e0;border-radius:8px;padding:12px;background:#f6f8fb;">
            <img
              v-if="otpUri"
              :src="qrUrl(otpUri)"
              alt="2FA QR"
              style="width:120px;height:120px;border-radius:6px;margin-bottom:8px;"
              @error="otpUriQrFail = true"
            />
            <div v-if="otpUriQrFail" class="hint" style="color:#92400e;">无法加载二维码（离线），请手动添加：</div>
            <div class="mono" style="font-size:12px;word-break:break-all;">密钥：<code>{{ otpSecret }}</code></div>
            <div class="mono" style="font-size:11px;color:#6e6e73;word-break:break-all;margin-top:4px;">{{ otpUri }}</div>
            <div class="row" style="margin-top:10px;">
              <input v-model.trim="otpCode" placeholder="输入 6 位验证码" maxlength="6" inputmode="numeric" style="width:160px;" />
              <button class="btn btn-primary" :disabled="otpBusy || otpCode.length !== 6" @click="otpEnable">启用</button>
            </div>
          </div>
          <div v-if="otpMsg" :class="['msg', otpMsgType]" style="margin-top:8px;">{{ otpMsg }}</div>
        </template>

        <template v-else>
          <div class="row" style="align-items:center;">
            <span class="badge ok" style="padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;">已启用</span>
            <span style="font-size:12.5px;color:#6e6e73;">登录时需要动态验证码</span>
          </div>
          <div class="row" style="margin-top:8px;">
            <input v-model.trim="otpCode" placeholder="输入当前验证码以关闭" maxlength="6" inputmode="numeric" style="width:200px;" />
            <button class="btn btn-danger" :disabled="otpBusy || otpCode.length !== 6" @click="otpDisable">关闭两步验证</button>
          </div>
          <div v-if="otpMsg" :class="['msg', otpMsgType]" style="margin-top:8px;">{{ otpMsg }}</div>
        </template>
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

      <!-- 关于：项目与社区相关链接（外链新窗口打开，rel=noopener 防钓鱼） -->
      <div class="block">
        <div class="block-title">{{ $t('settings.about.title') }}</div>
        <div class="about-version" v-if="panelVersion">
          {{ $t('settings.about.version') }}: <span class="about-version-val">Graw v{{ panelVersion }}</span>
          <!-- 版本更新提示：发现新版显示一键更新；已最新则提示已是最新 -->
          <template v-if="updateAvailable">
            <span class="update-hint">→ {{ $t('settings.about.updateAvailable', { version: latestVersion }) }}</span>
            <button class="btn btn-update" :disabled="updating" @click="doUpdate">
              {{ updating ? $t('settings.about.updating') : $t('settings.about.updateNow') }}
            </button>
          </template>
          <span v-else-if="updateChecked" class="update-latest">{{ $t('settings.about.upToDate') }}</span>
        </div>
        <div v-if="updateMsg" :class="['msg', updateMsgType]" style="margin-bottom:8px;">{{ updateMsg }}</div>
        <div class="about-list">
          <a
            v-for="l in aboutLinks"
            :key="l.key"
            :href="l.url"
            target="_blank"
            rel="noopener noreferrer"
            class="about-link"
            :title="l.url"
          >
            <span class="about-name">{{ $t(l.nameKey) }}</span>
            <span class="about-url">{{ l.url }}</span>
          </a>
        </div>
      </div>
    </div>
  </div>

  <!-- 高风险操作二次确认：删除远程节点 / 清除安全入口等需输入面板密码 -->
  <ConfirmDialog
    :show="dangerConfirm.show"
    :mode="dangerConfirm.mode"
    :title="dangerConfirm.title"
    :message="dangerConfirm.message"
    :required-text="dangerConfirm.requiredText"
    :input-label="dangerConfirm.inputLabel"
    :placeholder="dangerConfirm.placeholder"
    :confirm-label="dangerConfirm.confirmLabel"
    @confirm="doConfirm"
    @cancel="dangerConfirm.show = false"
  />
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { settings } from '../../store/settings'
import { isAdmin } from '../../store/auth'
import { nodesApi, shunxApi, panelApi, updateApi, webmodeApi, authApi } from '../../api'
import { nodes as nodesStore, refreshNodes, setCurrentNode } from '../../store/nodes'
import { LANGUAGES, setLocale } from '../../locales'
import ConfirmDialog from '../ConfirmDialog.vue'

const { t } = useI18n()
const emit = defineEmits(['openUsers'])

// 高风险操作二次确认状态（删除远程节点 / 清除安全入口等需输入面板密码）
// 注意：不能命名为 confirm，否则会遮蔽全局 window.confirm（doUpdate 仍在用）
const dangerConfirm = ref({ show: false, mode: 'password', title: '', message: '', requiredText: '', inputLabel: '', placeholder: '', confirmLabel: '', action: null })

// ShunX 安全入口状态
const entryPath = ref('')
const currentEntry = ref('')
const saving = ref(false)
const msg = ref('')
const msgType = ref('')
const origin = computed(() => window.location.origin)

// ---- Web 服务器引擎模式（NGINX / OpenResty）----
const wmMode = ref('nginx')
const wmStatus = reactive({ binary: 'nginx', available: false, nginx_available: false, openresty_available: false, conf_base: '/etc/nginx' })
const wmsaving = ref(false)
const wmmsg = ref('')
const wmmsgType = ref('')

async function loadWebMode() {
  try {
    const s = await webmodeApi.status()
    wmMode.value = s.mode || 'nginx'
    Object.assign(wmStatus, {
      binary: s.binary || 'nginx',
      available: !!s.available,
      nginx_available: !!s.nginx_available,
      openresty_available: !!s.openresty_available,
      conf_base: s.conf_base || '/etc/nginx',
    })
    wmmsg.value = ''
  } catch (e) {
    // 非管理员/接口异常时静默，不阻塞设置窗口其它功能
  }
}

async function saveWebMode() {
  if (wmsaving.value) return
  wmsaving.value = true
  wmmsg.value = ''
  try {
    const s = await webmodeApi.setMode(wmMode.value)
    wmMode.value = s.mode || wmMode.value
    Object.assign(wmStatus, {
      binary: s.binary || 'nginx',
      available: !!s.available,
      nginx_available: !!s.nginx_available,
      openresty_available: !!s.openresty_available,
      conf_base: s.conf_base || '/etc/nginx',
    })
    wmmsg.value = t('settings.webmode.saved', { bin: wmStatus.binary, dir: wmStatus.conf_base })
    wmmsgType.value = 'ok'
  } catch (e) {
    wmmsg.value = e?.response?.data?.detail || t('settings.webmode.saveFailed')
    wmmsgType.value = 'err'
  } finally {
    wmsaving.value = false
  }
}

const statusText = computed(() => {
  if (!currentEntry.value) return t('settings.shunxNotSet')
  return t('settings.shunxEnabled', { path: currentEntry.value })
})

// ---- 两步验证（2FA）----
const otpState = reactive({ enabled: false, has_secret: false })
const otpSecret = ref('')
const otpUri = ref('')
const otpUriQrFail = ref(false)
const otpCode = ref('')
const otpBusy = ref(false)
const otpMsg = ref('')
const otpMsgType = ref('')

// 二维码用外部公共服务生成（失败时回退到手动输入密钥，不影响功能）
function qrUrl(uri) {
  return `https://api.qrserver.com/v1/create-qr-code/?size=240x240&data=${encodeURIComponent(uri)}`
}

async function loadOtpStatus() {
  try {
    const st = await authApi.me2faStatus()
    otpState.enabled = !!st.otp_enabled
    otpState.has_secret = !!st.has_secret
    otpMsg.value = ''
  } catch (e) {
    // 接口异常时静默，不阻塞设置窗口
  }
}

async function otpSetup() {
  otpBusy.value = true
  otpMsg.value = ''
  otpUriQrFail.value = false
  try {
    const r = await authApi.twoFaSetup()
    otpSecret.value = r.secret
    otpUri.value = r.otpauth_uri
    otpState.has_secret = true
    otpCode.value = ''
  } catch (e) {
    otpMsg.value = e?.response?.data?.detail || '生成失败'
    otpMsgType.value = 'err'
  } finally {
    otpBusy.value = false
  }
}

async function otpEnable() {
  if (otpBusy.value) return
  otpBusy.value = true
  otpMsg.value = ''
  try {
    await authApi.twoFaEnable(otpCode.value)
    otpState.enabled = true
    otpSecret.value = ''
    otpUri.value = ''
    otpCode.value = ''
    otpMsg.value = '两步验证已启用'
    otpMsgType.value = 'ok'
  } catch (e) {
    otpMsg.value = e?.response?.data?.detail || '启用失败'
    otpMsgType.value = 'err'
  } finally {
    otpBusy.value = false
  }
}

async function otpDisable() {
  if (otpBusy.value) return
  otpBusy.value = true
  otpMsg.value = ''
  try {
    await authApi.twoFaDisable(otpCode.value)
    otpState.enabled = false
    otpState.has_secret = false
    otpCode.value = ''
    otpMsg.value = '两步验证已关闭'
    otpMsgType.value = 'ok'
  } catch (e) {
    otpMsg.value = e?.response?.data?.detail || '关闭失败'
    otpMsgType.value = 'err'
  } finally {
    otpBusy.value = false
  }
}

// ---- 关于：项目与社区链接 ----
// nameKey 为多语言键，url 为固定外链；集中在此便于维护与扩展
const aboutLinks = [
  { key: 'github', nameKey: 'settings.about.githubSource', url: 'https://github.com/wuhulab/Graw' },
  { key: 'docker', nameKey: 'settings.about.docker', url: 'https://hub.docker.com/repository/docker/shunx/graw/general' },
  { key: 'wuhulab', nameKey: 'settings.about.wuhulab', url: 'https://github.com/wuhulab/' },
  { key: 'appstore', nameKey: 'settings.about.appStore', url: 'https://github.com/wuhulab/Graw-app-store' },
  { key: 'sponsor', nameKey: 'settings.about.sponsorFai', url: 'https://fai.shunx.top/' },
  { key: 'shunx', nameKey: 'settings.about.shunxTeam', url: 'https://www.shunx.top/' },
  { key: 'bili', nameKey: 'settings.about.bilibili', url: 'https://space.bilibili.com/3546925812943471' },
  { key: 'bili2', nameKey: 'settings.about.bilibili2', url: 'https://space.bilibili.com/3493133419546943' },
  { key: 'contributor', nameKey: 'settings.about.contributor', url: 'https://github.com/shunianssy' },
]

// 面板版本号：来自公开接口 /api/health，加载失败时静默留空（不阻塞页面）
const panelVersion = ref('')
async function loadVersion() {
  try {
    const res = await panelApi.health()
    panelVersion.value = res.version || ''
  } catch (e) {
    // 版本号获取失败不影响设置窗口其它功能，仅隐藏版本行
    panelVersion.value = ''
  }
}

// ---- 面板版本更新检测与一键更新 ----
// 仅管理员可更新（后端 /api/update 挂 ADMIN 依赖）；检测失败时静默隐藏更新区
const updateAvailable = ref(false)
const latestVersion = ref('')
const updateChecked = ref(false) // 是否已完成版本检测（区分「未检测」与「已是最新」）
const updating = ref(false)
const updateMsg = ref('')
const updateMsgType = ref('')

async function loadUpdateStatus() {
  try {
    const res = await updateApi.status()
    updateAvailable.value = !!res.update_available
    latestVersion.value = res.latest_version || ''
    updateChecked.value = true
  } catch (e) {
    // 网络/权限异常（如非管理员）时静默，不显示任何更新提示
    updateAvailable.value = false
    updateChecked.value = false
  }
}

async function doUpdate() {
  if (updating.value) return
  if (!confirm(t('settings.about.updateConfirm', { version: latestVersion.value }))) return
  updating.value = true
  updateMsg.value = ''
  try {
    const res = await updateApi.apply()
    updateMsg.value = res.message || t('settings.about.updateStarted')
    updateMsgType.value = 'ok'
  } catch (e) {
    updateMsg.value = e?.response?.data?.detail || t('settings.about.updateFailed')
    updateMsgType.value = 'err'
  } finally {
    updating.value = false
  }
}

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

function removeNode(n) {
  // 高风险操作：删除远程节点需输入面板密码确认后才真正执行
  dangerConfirm.value = {
    show: true,
    mode: 'password',
    title: t('confirmDanger.deleteNodeTitle'),
    message: t('confirmDanger.deleteNodeMsg', { name: n.name }),
    requiredText: '',
    inputLabel: t('confirmDanger.inputPwdLabel'),
    placeholder: t('confirmDanger.inputPwdPlaceholder'),
    confirmLabel: t('common.delete'),
    action: { type: 'node', node: n }
  }
}

onMounted(async () => {
  // 加载面板版本号（公开接口，与登录态无关）
  loadVersion()
  // 检测是否有新版本（管理员可触发一键更新）
  loadUpdateStatus()
  // 加载当前账号的两步验证状态
  loadOtpStatus()
  try {
    const config = await shunxApi.config()
    currentEntry.value = config.entry_path || ''
    entryPath.value = currentEntry.value
  } catch (e) {
    currentEntry.value = ''
  }
  if (isAdmin()) loadNodes()
  if (isAdmin()) loadWebMode()
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

function clearEntry() {
  // 高风险配置变更：清除安全入口后任何设备可直接访问登录页，需输入面板密码确认
  dangerConfirm.value = {
    show: true,
    mode: 'password',
    title: '清除安全入口确认',
    message: '清除 ShunX 安全入口后，任何设备将可直接访问登录页面。\n请输入面板密码以确认。',
    requiredText: '',
    inputLabel: t('confirmDanger.inputPwdLabel'),
    placeholder: t('confirmDanger.inputPwdPlaceholder'),
    confirmLabel: '清除',
    action: { type: 'entry' }
  }
}

// ConfirmDialog 密码校验通过后的回调：按 action.type 真正执行高风险操作
async function doConfirm() {
  const a = dangerConfirm.value.action
  dangerConfirm.value.show = false
  if (!a) return
  if (a.type === 'node') {
    try {
      await nodesApi.delete(a.node.id)
      await loadNodes()
    } catch (e) {
      editorError(t('nodes.deleteFailed', { error: e?.response?.data?.detail || e.message }))
    }
  } else if (a.type === 'entry') {
    await clearEntryNow()
  }
}

// 真正执行清除安全入口（密码已通过校验，原 clearEntry 的业务逻辑）
async function clearEntryNow() {
  if (saving.value) return
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
/* ---- 关于（About）---- */
.about-version {
  font-size: 12px;
  color: #1d1d1f;
  margin-bottom: 10px;
}
.about-version-val {
  font-weight: 600;
  color: #0a84ff;
}
/* 版本更新提示 */
.update-hint {
  margin-left: 6px;
  font-size: 11px;
  color: #e5484d;
}
.update-latest {
  margin-left: 6px;
  font-size: 11px;
  color: #0a7d3b;
}
.btn-update {
  margin-left: 8px;
  padding: 3px 12px;
  font-size: 11px;
  background: #e5484d;
}
.btn-update:hover:not(:disabled) { background: #d63d42; }
.btn-update:disabled { opacity: 0.6; cursor: not-allowed; }
.about-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.about-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  background: #fafafa;
  border: 1px solid rgba(0,0,0,0.05);
  border-radius: 8px;
  text-decoration: none;
  color: #1d1d1f;
  transition: background 0.15s;
}
.about-link:hover {
  background: rgba(10,132,255,0.06);
  border-color: rgba(10,132,255,0.25);
}
.about-name {
  font-size: 12px;
  font-weight: 500;
  flex-shrink: 0;
}
.about-url {
  font-size: 11px;
  color: #0a84ff;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}
</style>
