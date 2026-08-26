<!--
  网站管理窗口（Sites）

  这个窗口做什么：
    面板里「网站」这个功能的主界面。它把服务器上由 Nginx / OpenResty 托管的站点
    列成一张表，让管理员可以：
      - 看到每个站点的名称、类型（静态 / 反向代理 / TCP-UDP 转发 / 子站点）、
        绑定域名、根目录或转发目标、监听端口，以及「是否启用」+「是否在线」两种状态；
      - 通过右键菜单启用/停用站点、打开配置编辑、查看 Nginx 生成的原始配置文本、删除站点；
      - 新建站点：先弹「选类型」卡片，选好后交给独立的「站点编辑」窗口去填表单。
    同一个窗口顶部还有一个「网站 / SSL证书」切换页，切到 SSL 时直接内嵌 SSLWindow，
    这样管理员配好站点后不用另开一个应用就能签发/上传证书。

  用到的后端模块：
    /api/sites/*（管理员权限）——list 拉列表、{id}/action 启停、{id}/config 读配置文本、
    {id}/delete 删除站点。全部经 src/api.js 的 sitesApi 转发，token 由请求拦截器注入。
    SSL 页签的接口调用发生在子组件 SSLWindow 内（/api/ssl/*）。

  关键状态：
    mode          当前页签，'sites' 看网站列表 / 'ssl' 看证书
    sites         后端返回的站点数组，表格数据源
    webServer     当前生效的 Web 服务器引擎名（nginx / openresty），只用于提示文案
    ctxMenu       右键菜单的显示位置与命中的那一行站点
    confirm       删除站点的二次确认对话框状态（需手打站点名）
    showTypePicker / pickedType   新建站点时的类型选择弹窗
    showConfig / configText / configSite   查看原始 Nginx 配置的只读弹窗

  怎么被打开：
    桌面图标 / 任务栏点开「网站」应用时由 App.vue 挂载。
    本窗口自身不承载创建和编辑表单，而是 emit('openSiteEdit') 让 App.vue 另开一个
    「站点编辑」窗口——因为站点表单字段多，塞进弹窗会挤，独立窗口还能拖到旁边对照着填。
-->
<template>
  <div class="sites-window" @click="closeCtx"><!-- 点空白处收起右键菜单 -->
    <!-- 视图切换：网站 / SSL证书 -->
    <div class="mode-tabs">
      <button class="tab" :class="{ active: mode === 'sites' }" @click="switchMode('sites')">{{ $t('sites.tabSites') }}</button>
      <button class="tab" :class="{ active: mode === 'ssl' }" @click="switchMode('ssl')">{{ $t('sites.tabSsl') }}</button>
    </div>

    <!-- 网站页签：工具栏（新建入口 + 当前引擎提示）+ 站点列表表格 -->
    <template v-if="mode === 'sites'">
      <div class="toolbar">
        <button class="btn primary" @click="openTypePicker"><Plus :size="14" /> {{ $t('sites.add') }}</button>
        <span class="hint">{{ $t('sites.webServer', { server: webServer || $t('sites.none') }) }}</span>
        <span class="hint right">{{ $t('sites.rightClickHint') }}</span>
      </div>
      <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ $t('sites.name') }}</th>
            <th>{{ $t('sites.type') }}</th>
            <th>{{ $t('sites.domain') }}</th>
            <th>{{ $t('sites.targetRoot') }}</th>
            <th>{{ $t('sites.port') }}</th>
            <th>{{ $t('sites.status') }}</th>
          </tr>
        </thead>
        <tbody>
          <!-- 一行一个站点；所有操作都收在右键菜单里，所以表格没有操作列 -->
          <tr v-for="s in sites" :key="s.id" @contextmenu.prevent="openCtx($event, s)" class="site-row">
            <td>{{ s.name }}</td>
            <td>
              <span class="type-badge">{{ typeLabel(s.type) }}</span>
              <!-- 从 1Panel 面板扫出来接管的站点单独打标，提醒管理员这不是本面板创建的 -->
              <span v-if="s.source === '1panel'" class="tag-1p">1Panel兼容</span>
            </td>
            <td>{{ displayServerName(s) }}</td>
            <td class="mono">{{ displayTarget(s) }}</td>
            <td>{{ s.port }}</td>
            <td>
              <!-- 两个状态是分开的：enabled 是配置层面有没有启用，online 是实际探测能不能访问 -->
              <span class="badge" :class="s.enabled ? 'ok' : 'off'">{{ s.enabled ? $t('sites.enabled') : $t('sites.disabled') }}</span>
              <span class="badge" :class="s.online ? 'ok' : 'warn'">{{ s.online ? $t('sites.online') : $t('sites.offline') }}</span>
            </td>
          </tr>
          <tr v-if="sites.length === 0">
            <td colspan="6" class="empty">{{ $t('sites.noSites') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    </template>

    <!-- SSL证书视图（合并自独立的「SSL」应用） -->
    <div v-else class="ssl-wrap">
      <SSLWindow />
    </div>

    <!-- 右键菜单：Teleport 到 body，避免被窗口容器的 overflow/层级裁掉 -->
    <Teleport to="body">
      <div v-if="ctxMenu.show" class="context-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }" @click.stop>
        <!-- 外部接管的站点（非本面板创建）只给「看」的能力，不提供启停与删除，防止改坏别人的配置 -->
        <template v-if="ctxMenu.site?.external">
          <div class="menu-header">{{ ctxMenu.site?.name }}</div>
          <div class="menu-divider"></div>
          <div class="menu-item" @click="menuEdit">{{ $t('sites.config') }}</div>
          <div class="menu-item" @click="menuViewConfig">{{ $t('sites.viewConfigHint') }}</div>
        </template>
        <!-- 面板自己创建的站点：完整操作集（启停 / 编辑 / 看配置 / 删除） -->
        <template v-else>
          <div class="menu-header">{{ ctxMenu.site?.name }}</div>
          <div class="menu-divider"></div>
          <div class="menu-item" @click="menuToggleEnable">{{ ctxMenu.site?.enabled ? $t('sites.disableAction') : $t('sites.enableAction') }}</div>
          <div class="menu-item" @click="menuEdit">{{ $t('sites.config') }}</div>
          <div class="menu-item" @click="menuViewConfig">{{ $t('sites.viewConfigHint') }}</div>
          <div class="menu-divider"></div>
          <div class="menu-item danger" @click="menuRemove">{{ $t('common.delete') }}</div>
        </template>
      </div>
    </Teleport>

    <!-- 类型选择弹窗：新建站点的第一步，先定类型再决定后续表单长什么样 -->
    <div v-if="showTypePicker" class="modal-overlay" @click.self="showTypePicker = false">
      <div class="modal type-modal">
        <h3>{{ $t('sites.selectType') }}</h3>
        <div class="type-grid">
          <div
            v-for="t in typeItems"
            :key="t.value"
            class="type-card"
            :class="{ active: t.value === pickedType }"
            @click="pickedType = t.value"
          >
            <component :is="t.icon" :size="22" />
            <div class="t-name">{{ t.label }}</div>
            <div class="t-desc">{{ t.desc }}</div>
          </div>
        </div>
        <div class="actions">
          <button class="btn" @click="showTypePicker = false">{{ $t('common.cancel') }}</button>
          <button class="btn primary" :disabled="!pickedType" @click="confirmType">{{ $t('common.next') }}</button>
        </div>
      </div>
    </div>

    <!-- 创建 / 编辑：由独立窗口承载（SitesWindow 仅负责打开） -->

    <!-- Config viewer：只读展示后端生成的 Nginx 站点配置，方便排查线上问题 -->
    <div v-if="showConfig" class="modal-overlay" @click.self="showConfig = false">
      <div class="modal wide">
        <h3>{{ $t('sites.viewConfig', { name: configSite?.name }) }}</h3>
        <pre class="code">{{ configText }}</pre>
        <div class="actions">
          <button class="btn" @click="showConfig = false">{{ $t('sites.close') }}</button>
        </div>
      </div>
    </div>

    <!-- 高风险操作二次确认：删除站点需输入站点名 -->
    <ConfirmDialog
      :show="confirm.show"
      mode="text"
      :title="t('confirmDanger.deleteSiteTitle')"
      :message="t('confirmDanger.deleteSiteMsg', { name: confirm.site?.name })"
      :required-text="confirm.site?.name || ''"
      :input-label="t('confirmDanger.inputNameLabel')"
      :placeholder="t('confirmDanger.inputNamePlaceholder', { name: confirm.site?.name })"
      :confirm-label="t('common.delete')"
      @confirm="doDeleteSite"
      @cancel="confirm.show = false"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, markRaw, computed, watch } from 'vue'   // 响应式状态、挂载钩子、图标组件免代理、派生列表、跨窗口变更监听
import { useI18n } from 'vue-i18n'                               // 取 t()，站点类型名等文案要跟随面板语言
import { sitesApi } from '../../api'                             // 网站管理后端能力：/api/sites/* 的封装
import { siteRevision } from '../../store/siteBus'               // 站点变更信号：别的窗口改完站点后通知本列表刷新
import ConfirmDialog from '../ConfirmDialog.vue'                 // 高风险操作确认框（删站点要求手打站点名）
import SSLWindow from './SSLWindow.vue'                          // SSL 页签直接复用证书管理窗口，避免功能重复实现
import {
  Plus, Globe, Share2, Network, Layers                          // 新建按钮 + 四种站点类型的示意图标
} from 'lucide-vue-next'

const { t } = useI18n()

// 视图模式：'sites' 网站 / 'ssl' SSL证书
const mode = ref('sites')

// --- 切换顶部页签（网站列表 / SSL 证书） ---
function switchMode(m) {
  mode.value = m
}

// 站点类型定义（文案走 i18n）
// 用 computed 而不是常量数组：语言切换后类型名/说明要立刻跟着变
// 图标组件用 markRaw 包一层，避免 Vue 把组件对象做成响应式代理（无意义且有性能开销）
const typeItems = computed(() => [
  { value: 'static', label: t('sites.static'), desc: t('sites.staticDesc'), icon: markRaw(Globe) },
  { value: 'proxy', label: t('sites.proxy'), desc: t('sites.proxyDesc'), icon: markRaw(Share2) },
  { value: 'tcpudp', label: t('sites.tcpudp'), desc: t('sites.tcpudpDesc'), icon: markRaw(Network) },
  { value: 'subsite', label: t('sites.subsite'), desc: t('sites.subsiteDesc'), icon: markRaw(Layers) }
])

// --- 把后端的类型码翻译成界面文案 ---
function typeLabel(type) {
  const item = typeItems.value.find(i => i.value === type)   // 在类型表里找对应项
  return item ? item.label : type || t('sites.static')       // 认不出的类型码原样显示；完全为空时按「静态」兜底
}

const sites = ref([])              // 站点列表，表格数据源
const webServer = ref('')          // 当前 Web 引擎名（nginx / openresty），只做提示展示
const showTypePicker = ref(false)  // 是否展开「选站点类型」弹窗
const pickedType = ref('')         // 弹窗里已选中的类型，未选时禁用「下一步」
const showConfig = ref(false)      // 是否展开配置查看弹窗
const configText = ref('')         // 后端返回的 Nginx 配置原文
const configSite = ref(null)       // 配置属于哪个站点，用于弹窗标题
// 右键菜单状态
const ctxMenu = ref({ show: false, x: 0, y: 0, site: null })
// 高风险操作二次确认状态：记录待删除的站点
const confirm = ref({ show: false, site: null })

// 通知 App 打开独立「站点编辑」窗口（创建 / 编辑共用）
const emit = defineEmits(['openSiteEdit'])

// 站点列表发生变更（独立窗口保存成功）后自动刷新
// siteRevision 是个全局计数器，站点编辑窗口保存成功就自增一次，本窗口据此重新拉列表，
// 免得管理员保存完还要手动刷新才看到新数据
let revisionInited = false
watch(siteRevision, () => {
  revisionInited = true
  load()
})

// --- 打开右键菜单（记录点击位置与命中的站点） ---
function openCtx(e, s) {
  const x = Math.min(e.clientX, window.innerWidth - 180)   // 180 是菜单大致宽度：靠右点击时把菜单往左收，别被窗口边缘截断
  const y = Math.min(e.clientY, window.innerHeight - 200)  // 200 是菜单大致高度：靠底部点击时同理往上收
  ctxMenu.value = { show: true, x, y, site: s }            // site 存整行数据，后续各菜单项都从这里取操作对象
}

// --- 收起右键菜单 ---
function closeCtx() {
  ctxMenu.value.show = false
}

// --- 菜单项：启用 / 停用站点 ---
// 下面四个 menuXxx 都遵循同一顺序：先取出站点 → 先关菜单 → 再执行动作。
// 先关菜单是必要的，因为后续动作可能弹窗或触发刷新，菜单留在屏幕上会盖住内容
function menuToggleEnable() {
  const s = ctxMenu.value.site
  closeCtx()
  if (s) toggleEnable(s)   // 菜单已关，此时 ctxMenu.site 不能再用，所以用前面取好的 s
}

// --- 菜单项：打开站点配置编辑 ---
function menuEdit() {
  const s = ctxMenu.value.site
  closeCtx()
  if (s) openEdit(s)
}

// --- 菜单项：查看 Nginx 原始配置 ---
function menuViewConfig() {
  const s = ctxMenu.value.site
  closeCtx()
  if (s) viewConfig(s)
}

// --- 菜单项：删除站点 ---
function menuRemove() {
  const s = ctxMenu.value.site
  closeCtx()
  if (s) remove(s)
}

// --- 删除站点第一步：只弹确认框，不真删 ---
function remove(s) {
  // 高风险操作二次确认：弹出对话框，要求输入站点名后才能删除
  confirm.value = { show: true, site: s }
}

// --- 删除站点第二步：确认框校验通过后真正下发删除 ---
async function doDeleteSite() {
  const s = confirm.value.site
  confirm.value.show = false      // 先收起确认框，避免删除请求期间用户重复点确认
  if (!s) return                  // 没有待删目标（异常触发）时直接退出，别对后端发空请求
  await sitesApi.delete(s.id)     // 后端会同时移除站点配置并 reload Web 服务
  await load()                    // 删完重新拉列表，让表格与服务器真实状态对齐
}

// --- 拉取站点列表与当前 Web 引擎 ---
async function load() {
  const data = await sitesApi.list()
  sites.value = data.sites || []             // 后端没装 Web 服务时可能没有 sites 字段，兜空数组以免表格渲染报错
  webServer.value = data.web_server || ''    // 空字符串会在界面上显示成「未安装」提示
}

// --- 展示 server_name / 域名：拼「域名」列要显示的文本 ---
function displayServerName(s) {
  if (s.type === 'subsite') {                        // 子站点的域名不是直接存的，要用「子域名 + 主域名」拼
    const sub = (s.subdomain || '').trim()
    const domain = (s.domain || '').trim()
    if (sub && domain) return `${sub}.${domain}`     // 指定了子域名：显示完整的 sub.example.com
    if (domain) return `*.${domain}`                 // 只有主域名：说明是泛解析，用 * 表示所有子域名都命中
  }
  return (s.domains || []).join(', ')                // 其它类型可绑多个域名，逗号连起来平铺显示
}

// --- 展示根目录 / 目标地址：不同站点类型看的是不同字段 ---
function displayTarget(s) {
  if (s.type === 'proxy') return s.reverse_proxy || '-'   // 反代站点关心的是转发到哪个后端地址
  if (s.type === 'tcpudp') return s.upstream || '-'       // 四层转发关心的是 upstream 目标
  return s.root || '-'                                     // 静态站点 / 子站点看的是磁盘根目录
}

// --- 点「新建站点」：打开类型选择弹窗 ---
function openTypePicker() {
  pickedType.value = ''        // 每次打开都清空上次的选择，避免误用上一轮残留的类型
  showTypePicker.value = true
}

// --- 类型选完点「下一步」：把创建流程交给站点编辑窗口 ---
function confirmType() {
  if (!pickedType.value) return   // 没选类型就没法决定表单字段，直接退出（按钮本身也是禁用态，这里是兜底）
  const type = pickedType.value   // 先把类型存进局部变量，因为下一行关弹窗会伴随状态重置
  showTypePicker.value = false
  // 打开独立「站点编辑」窗口承载创建表单
  emit('openSiteEdit', { mode: 'create', type })
}

// --- 打开某个已有站点的编辑表单 ---
function openEdit(s) {
  // 打开独立「站点编辑」窗口承载编辑表单
  emit('openSiteEdit', { mode: 'edit', site: s })   // 直接把整行数据带过去，编辑窗口就不用再查一次接口
}

// --- 启用 / 停用站点（同一入口按当前状态取反） ---
async function toggleEnable(s) {
  const action = s.enabled ? 'disable' : 'enable'   // 当前启用就下发停用，反之下发启用
  await sitesApi.action(s.id, action)               // 后端改软链/配置并 reload Web 服务
  await load()                                      // 重新拉列表，同步 enabled 与 online 两个状态标
}

// --- 查看站点的 Nginx 原始配置 ---
async function viewConfig(s) {
  const data = await sitesApi.config(s.id)
  configSite.value = data.site      // 用后端回传的站点信息做标题，保证展示的和读到的是同一份
  configText.value = data.config    // 配置正文，原样放进 <pre> 里展示
  showConfig.value = true           // 数据齐了才开弹窗，避免先弹出一个空白框
}

onMounted(load)   // 窗口一打开就拉一次站点列表
</script>

<style scoped>
.sites-window { padding: 10px; }
/* 视图切换标签（网站 / SSL证书） */
.mode-tabs { display: inline-flex; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; margin-bottom: 10px; }
.mode-tabs .tab { padding: 6px 14px; font-size: 13px; background: #fff; border: none; cursor: pointer; color: #6b7280; }
.mode-tabs .tab.active { background: #111827; color: #fff; }
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.hint { color: #6e6e73; font-size: 12px; }
.hint.right { margin-left: auto; }
.table-wrap { overflow: auto; max-height: 420px; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
th { background: #f9fafb; position: sticky; top: 0; }
.site-row { cursor: context-menu; }
.site-row:hover { background: #f9fafb; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; margin-right: 4px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.warn { background: #fef3c7; color: #92400e; }
.badge.off { background: #f3f4f6; color: #6b7280; }
.type-badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11px; background: #eef2ff; color: #4338ca; }
.tag-1p { display: inline-block; margin-left: 4px; padding: 1px 6px; border-radius: 6px; font-size: 10px; background: #fffbeb; color: #b45309; border: 1px solid #fcd34d; white-space: nowrap; }

/* 右键菜单 */
.context-menu {
  position: fixed; background: #fff; border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.15);
  z-index: 3000; min-width: 160px; padding: 4px 0;
}
.menu-header { padding: 8px 14px; font-size: 12px; font-weight: 600; color: #111827; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }
.menu-item { padding: 8px 14px; font-size: 12.5px; cursor: pointer; color: #374151; }
.menu-item:hover { background: #f5f5f7; }
.menu-item.readonly { color: #9ca3af; cursor: default; }
.menu-item.danger { color: #b91c1c; }
.menu-item.danger:hover { background: #fef2f2; }
.menu-divider { height: 1px; background: #e5e7eb; margin: 4px 0; }
.empty { text-align: center; color: #9ca3af; padding: 24px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 480px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal.wide { width: 720px; }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.form .actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
.radio-row { display: flex; gap: 16px; }
.radio { display: flex; align-items: center; gap: 4px; font-size: 13px; color: #111827; cursor: pointer; }
.type-modal { width: 560px; }
.type-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 14px; }
.type-card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; cursor: pointer; transition: all 0.15s; }
.type-card:hover { border-color: #94a3b8; background: #f9fafb; }
.type-card.active { border-color: #111827; background: #f3f4f6; }
.t-name { font-size: 14px; font-weight: 600; margin-top: 8px; }
.t-desc { font-size: 12px; color: #6b7280; margin-top: 2px; }
.code { background: #f3f4f6; padding: 12px; border-radius: 8px; font-size: 12px; overflow: auto; max-height: 360px; white-space: pre-wrap; word-break: break-all; }
</style>