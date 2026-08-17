<template>
  <div class="store-window" @click="closePopovers">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="title-wrap">
        <span class="title"><Store :size="16" /> Graw 社区应用商店</span>
        <span class="badge" :class="indexState.source === 'remote' ? 'ok' : 'warn'">
          {{ indexState.source === 'remote' ? '远程索引' : '本地索引' }}
        </span>
        <span v-if="indexState.updated_at" class="updated">更新于 {{ fmtTime(indexState.updated_at) }}</span>
      </div>
      <!-- 分类筛选下拉 -->
      <select class="cat-select" v-model="selectedCategory" title="按分类筛选">
        <option value="">全部分类</option>
        <option v-for="cat in categories" :key="cat" :value="cat">{{ cat }}</option>
      </select>
      <!-- 搜索输入框 -->
      <input class="search-inp" type="text" v-model.trim="searchQuery"
             placeholder="搜索应用名称 / ID / 描述..." />
      <button class="btn" :disabled="loading" @click="loadIndex(false)">
        {{ loading ? '加载中...' : '刷新' }}
      </button>
      <button class="btn" @click="showConfigModal = true"><Settings2 :size="14" /> 索引地址</button>
    </div>

    <!-- 索引错误提示 -->
    <div v-if="indexState.error" class="error-banner">
      <AlertTriangle :size="14" /> {{ indexState.error }}
      <span class="hint">（已回退到本地索引，可点击"索引地址"配置远程地址）</span>
    </div>

    <!-- 加载 / 空状态 -->
    <div v-if="loading && apps.length === 0" class="empty">
      <Loader2 :size="36" class="spin" />
      <div>正在加载应用商店索引...</div>
    </div>
    <div v-else-if="apps.length === 0" class="empty">
      <Store :size="40" style="color:#6b7280;" />
      <div>应用商店暂无可用应用。</div>
    </div>
    <!-- 筛选无结果 -->
    <div v-else-if="filteredApps.length === 0" class="empty">
      <Search :size="40" style="color:#6b7280;" />
      <div>没有找到匹配的应用。</div>
      <div class="hint">试试更换分类或清空搜索关键词。</div>
    </div>

    <!-- 应用卡片网格（按分类 / 搜索过滤） -->
    <div v-else class="app-grid">
      <div v-for="app in filteredApps" :key="app.id" class="app-card">
        <div class="card-head">
          <img class="app-icon" :src="app.icon" alt="" loading="lazy"
               @error="e => e.target.style.visibility = 'hidden'" />
          <div class="card-titles">
            <div class="app-name">{{ app.name }}</div>
            <div class="app-id mono">{{ app.id }}</div>
          </div>
          <div class="card-actions">
            <a v-if="safeUrl(app.homepage)" class="icon-link" :href="safeUrl(app.homepage)" target="_blank" rel="noopener" title="官方网站"><Globe :size="14" /></a>
            <a v-if="safeUrl(app.source)" class="icon-link" :href="safeUrl(app.source)" target="_blank" rel="noopener" title="开源社区"><Github :size="14" /></a>
            <button v-if="app.source" class="readme-btn" title="查看 GitHub README" @click="emit('openReadme', app)"><BookOpen :size="14" /></button>
          </div>
        </div>

        <p class="app-desc">{{ app.description }}</p>

        <div class="card-foot">
          <div class="tags">
            <span class="tag" :title="'默认版本 ' + app.version">{{ fmtVersion(app.version) }}</span>
            <span v-for="a in (app.arch || []).slice(0, 3)" :key="a" class="tag arch">{{ a }}</span>
            <span v-if="app.ports && app.ports.length" class="tag port">
              <Container :size="11" /> {{ app.ports.map(p => p.container).join(', ') }}
            </span>
          </div>
          <button class="btn primary install" @click="emit('openAppInstall', app)">安装</button>
        </div>
      </div>
    </div>

    <!-- ============ 索引地址配置弹窗 ============ -->
    <div v-if="showConfigModal" class="modal-overlay" @click.self="showConfigModal = false">
      <div class="modal">
        <h3><Settings2 :size="16" /> 应用商店索引地址</h3>
        <p class="modal-desc">填写托管在 GitHub Pages 的 index.json 地址，留空则使用仓库内置的本地索引。</p>
        <input v-model.trim="configForm.index_url" class="inp mono" style="width:100%;"
               placeholder="https://&lt;owner&gt;.github.io/&lt;repo&gt;/index.json" />
        <div class="actions">
          <button class="btn" @click="showConfigModal = false">取消</button>
          <button class="btn primary" :disabled="savingConfig" @click="saveConfig">
            {{ savingConfig ? '保存中...' : '保存并刷新' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 首次进入免责声明弹窗 ============ -->
    <div v-if="showDisclaimer" class="disc-modal-overlay">
      <div class="disc-modal">
        <div class="disc-head">
          <ShieldAlert :size="20" />
          <span>Graw 社区应用商店 免责声明</span>
          <span class="disc-version">v1.1.0</span>
        </div>
        <div class="disc-body" ref="discBodyEl" @scroll="onDiscScroll">
          <pre class="disc-text">{{ DISCLAIMER_TEXT }}</pre>
        </div>
        <div class="disc-foot">
          <label class="disc-agree-label">
            <input type="checkbox" :disabled="!discScrolled" v-model="discAgreed" />
            <span>
              同意并继续使用：我已完整阅读、理解并接受本免责声明的全部内容。
              <em v-if="!discScrolled" style="color:#dc2626;">（请滚动到最底部以勾选）</em>
            </span>
          </label>
          <div class="disc-actions">
            <button class="btn" @click="emit('close')">拒绝并关闭</button>
            <button class="btn primary" :disabled="!discAgreed" @click="acceptDisclaimer">进入应用商店</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { appStoreApi } from '../../api'
import { Store, Settings2, Globe, Github, Container, AlertTriangle, Loader2, BookOpen, Search, ShieldAlert } from 'lucide-vue-next'

const emit = defineEmits(['openAppInstall', 'openReadme', 'close'])

// 外链协议白名单：homepage / source 来自索引数据（index_url 可指向任意
// 远程源，属不可信输入），Vue 3 的 :href 不会自动过滤 javascript: 协议，
// 恶意索引注入 javascript: 链接即可在点击时执行任意脚本窃取 token
function safeUrl(u) {
  return /^https?:\/\//i.test(u || '') ? u : ''
}

const apps = ref([])
const loading = ref(false)
const indexState = reactive({ source: '', updated_at: '', error: '' })

// 分类筛选 + 搜索
const selectedCategory = ref('')       // 空 = 全部分类
const searchQuery = ref('')            // 搜索关键词
// 分类展示顺序（与 data.yml category 字段保持一致）
const CATEGORY_ORDER = ['数据库/存储', '面板/网站', 'AI/开发', '网络/工具', '监控/运维', '开发/DevOps']

// 索引中存在应用的分类列表（按固定顺序，忽略空分类）
const categories = computed(() => {
  const seen = new Set(apps.value.map(a => a.category).filter(Boolean))
  return CATEGORY_ORDER.filter(c => seen.has(c))
})

// 按分类 + 搜索关键词过滤后的应用列表
const filteredApps = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return apps.value.filter(a => {
    // 分类过滤
    if (selectedCategory.value && a.category !== selectedCategory.value) return false
    // 搜索过滤：匹配名称 / ID / 描述
    if (!q) return true
    return (a.name || '').toLowerCase().includes(q)
        || (a.id || '').toLowerCase().includes(q)
        || (a.description || '').toLowerCase().includes(q)
  })
})

// 索引地址配置
const showConfigModal = ref(false)
const savingConfig = ref(false)
const configForm = reactive({ index_url: '' })

// ===================== 首次进入免责声明 =====================
const DISCLAIMER_KEY = 'graw_appstore_disclaimer_v1'   // 本地存储键（按文档版本）
const showDisclaimer = ref(false)                        // 免责弹窗是否显示
const discScrolled = ref(false)                          // 是否已滚动到底部
const discAgreed = ref(false)                            // 是否已勾选同意
const discBodyEl = ref(null)                             // 免责文本滚动容器

// 免责声明全文（与 Graw 社区应用商店 免责声明 1.1.0 一致）
const DISCLAIMER_TEXT = `前言

Graw 社区应用商店（以下简称"本商店"）是由 ShunX 公益母团队发起，由全球多国开源社区共同维护的去中心化应用索引平台。本商店致力于为开发者提供透明、安全、便捷的应用部署体验。在使用本商店服务前，请您仔细阅读以下免责条款。您对本商店的访问、浏览或使用，即视为您已阅读、理解并同意接受本声明的所有条款。

一、隐私保护承诺

1. 零数据采集：Graw 社区应用商店不会以任何形式采集、存储、上传或转交您的个人隐私数据（包括但不限于 IP 地址、设备信息、浏览记录、地理位置、联系方式等）。您无需担忧隐私泄露风险，本商店完全不需要独立的隐私政策，因为根本没有数据值得被保护。
2. 匿名化运行：您与商店之间的交互（如浏览应用列表、查看详情）均以匿名方式进行，不生成任何用户画像或行为追踪记录。
3. 第三方服务说明：若您通过本商店安装的应用（如 WordPress、Nextcloud 等）自身包含数据收集行为，该行为与本商店无关，您应参考该应用自身的隐私政策。

二、平台性质声明

1. 去中心化开源社区：本商店是一个多元化、去中心化的开源社区项目，由全球开发者志愿者共同维护。所有应用元数据（包括 docker-compose.yml、data.yml、README.md 等）均托管于公开的 Git 仓库（如 GitHub），并由社区成员通过 Pull Request 方式贡献。
2. 索引性质：本商店仅为索引服务提供方，并不实际存储、托管或分发任何应用软件本身。所有应用的实际文件来源为第三方仓库（如 Docker Hub、GitHub Releases 等），本商店仅提供指向这些资源的元数据链接。
3. 多源支持：本商店允许用户自由添加、切换或移除任何第三方应用源（包括但不限于自建源、社区源、组织源），用户对选择信任的源及其内容负全部责任。本商店不对任何第三方源的可用性、安全性或内容合法性作任何担保。
4. 非隶属关系：本商店与应用作者、第三方仓库及镜像源之间不存在任何隶属、代理或合作关系。应用作者独立对其作品负责。

三、用户责任与合规性

1. 法律合规自查：本商店的应用列表来源于全球多国开源社区，其中部分应用可能涉及特定国家/地区的技术出口管制、数据合规或内容审查相关规定。您应在安装或使用任何应用前，自行查阅并遵守您所在国家/地区的所有适用法律法规、进出口管制条例及行业规范。
2. 责任独立性：您通过本商店安装、配置或使用任何应用所产生的一切后果（包括但不限于数据丢失、服务中断、法律纠纷、行政处罚、刑事责任等）均由您独立承担。Graw 社区应用商店不对您的任何行为或后果承担任何形式的责任。
3. 应用质量声明：本商店不对应用列表中的任何软件做明示或暗示的担保，包括但不限于适销性、特定用途适用性、安全性、稳定性、无病毒及无侵权等。您应自行评估应用的质量和安全性。
4. 推荐与背书：本商店内任何应用的显示顺序、标签（如"精选"或"官方"）均不代表 Graw 社区对其的认可或背书。此类标签仅用于提升浏览体验，不构成任何法律意义上的推荐。

四、内容管理与下架规则

1. 国际法违规处理：本商店遵循严重国际法违规的下架原则，仅在以下情形下对应用索引进行下架处理：
   · 应用内容违反联合国宪章及公认的国际人道主义基本准则
   · 应用涉及全球公认的严重违法犯罪行为（如人口贩卖、恐怖主义融资等）
   · 受到具有明确国际法依据的官方制裁或禁令
2. 不可抗力处理：因下列不可抗力因素，本商店可能对某些应用进行临时或永久下架：
   · GitHub Pages、Docker Hub 等第三方服务中断或终止服务
   · 应用作者主动要求下架或其仓库被删除
   · 社区维护团队因技术原因无法继续维护相关索引
3. 国家法律例外声明：本商店不依照特定国家的法律进行单独管理，亦不承担主动审查或屏蔽特定国家/地区法律所禁止内容的责任。用户应自行遵守其所在地法律，若某项应用在您所在地区违法，您应主动避免安装和使用该应用。
4. 社区自治原则：应用列表的增删改由社区成员通过 PR 形式共同决策，Graw 社区保留因上述国际法或不可抗力因素进行紧急下架的权利，但不对下架决策的及时性、全面性承担保证责任。

五、知识产权与内容责任

1. 第三方版权：本商店中收录的应用图标、名称、标识、界面设计等，均属于其各自权利人的合法财产。本商店仅以技术中立原则进行索引，不对第三方知识产权侵权承担责任。
2. 社区内容：用户通过 GitHub Pull Request 方式提交的应用元数据或文档，其内容由提交者独立负责。Graw 社区保留对明显违反国际法或公序良俗的内容进行下架处理的权利，但不对社区成员提交内容的准确性、合法性及完整性承担主动审查义务。
3. 侵权投诉：若您认为本商店索引的内容侵犯了您的合法权益，请通过 GitHub Issue 或邮件方式联系社区维护团队，我们将在合理时间内进行审查并采取必要措施（如移除相关索引）。

六、Graw 的有限责任

1. 不可抗力免责：因下列原因导致本商店服务中断或无法访问的，Graw 社区不承担责任：
   · GitHub Pages、Docker Hub 等第三方服务故障
   · 自然灾害、战争、网络攻击、政府行为等不可抗力因素
   · 互联网基础设施故障或网络传输延迟
2. 无服务级别承诺：本商店以"现状"（AS IS）形式提供，不承诺任何形式的服务可用性、稳定性或连续性。Graw 社区没有义务确保商店服务的 24/7 不间断运行。
3. 赔偿上限：在任何情况下，Graw 社区及其成员不负任何相关责任。

七、其他条款

1. 变更权：Graw 社区保留随时修改本声明的权利。重大变更（如涉及用户责任或隐私保护）将通过 GitHub 仓库的公告栏或 Release 说明进行通知。变更生效后继续使用本商店即视为接受新声明。
2. 可分割性：若本声明的任何条款被有管辖权的法院认定为无效或不可执行，不影响其他条款的效力。
3. 语言效力：本声明以中文版本为准，翻译版本，仅供参考。
4. 联系我们：若有任何疑问，请通过 ShunX 公益母团队的 GitHub 仓库提交 Issue，或发送邮件至 s@shunx.top。

本声明的最终解释权归 Graw 社区及 ShunX 公益母团队所有。

文档版本：1.1.0 | 更新日期：2026年8月16日`

// 滚动容器滚动事件：滚动到底部后启用"同意并继续使用"
function onDiscScroll() {
  const el = discBodyEl.value
  if (!el) return
  discScrolled.value = el.scrollHeight - el.scrollTop - el.clientHeight < 8
}

// 同意免责声明：写入本地存储，后续进入不再弹出
function acceptDisclaimer() {
  localStorage.setItem(DISCLAIMER_KEY, '1')
  showDisclaimer.value = false
}

function fmtTime(t) {
  if (!t) return ''
  return String(t).replace('T', ' ').replace(/\.\d+.*$/, '')
}

// 版本号显示：部分应用 tag 自带 v 前缀（如 AList v3.40.0），避免重复显示 "vv"
function fmtVersion(v) {
  if (!v) return ''
  return String(v).startsWith('v') ? String(v) : 'v' + String(v)
}

async function loadIndex(refresh) {
  loading.value = true
  try {
    const r = await appStoreApi.index(refresh)
    apps.value = r.apps || []
    indexState.source = r.source || ''
    indexState.updated_at = r.updated_at || ''
    indexState.error = r.error || ''
  } catch (e) {
    indexState.error = e.response?.data?.detail || e.message
    apps.value = []
  } finally {
    loading.value = false
  }
}

function closePopovers() { /* 预留：点击空白收起下拉 */ }

async function saveConfig() {
  savingConfig.value = true
  try {
    await appStoreApi.saveConfig(configForm.index_url)
    showConfigModal.value = false
    await loadIndex(true)
  } catch (e) {
    alert('保存失败：' + (e.response?.data?.detail || e.message))
  } finally {
    savingConfig.value = false
  }
}

onMounted(async () => {
  try {
    const cfg = await appStoreApi.config()
    configForm.index_url = cfg.index_url || ''
  } catch (e) { /* 忽略 */ }
  await loadIndex(false)
  // 首次进入：检查是否已同意免责声明
  showDisclaimer.value = !localStorage.getItem(DISCLAIMER_KEY)
})
</script>

<style scoped>
.store-window { position: relative; display: flex; flex-direction: column; height: 100%; overflow: hidden; }

.toolbar { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap; }
.title-wrap { display: flex; align-items: center; gap: 8px; margin-right: auto; min-width: 0; }
.title { font-weight: 700; font-size: 13.5px; display: inline-flex; align-items: center; gap: 6px; }
.updated { color: #6b7280; font-size: 11.5px; }
.badge { font-size: 11px; padding: 1px 8px; border-radius: 999px; }
.badge.ok { background: #ecfdf5; color: #047857; }
.badge.warn { background: #fffbeb; color: #b45309; }

/* 分类下拉 */
.cat-select {
  font-size: 12px;
  padding: 4px 8px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #1d1d1f;
  cursor: pointer;
  max-width: 130px;
}
.cat-select:focus { outline: none; border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,.15); }

/* 搜索输入框 */
.search-inp {
  font-size: 12px;
  padding: 5px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #1d1d1f;
  width: 180px;
  min-width: 120px;
}
.search-inp::placeholder { color: #9ca3af; }
.search-inp:focus { outline: none; border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,.15); }

.error-banner { margin: 8px 12px 0; padding: 6px 10px; background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; border-radius: 6px; font-size: 12px; display: flex; align-items: center; gap: 6px; }
.error-banner .hint { color: #6b7280; }

.empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: #6b7280; font-size: 13px; }
.empty .hint { color: #9ca3af; font-size: 11.5px; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* 应用卡片网格 */
.app-grid { flex: 1; overflow-y: auto; padding: 12px; display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; align-content: start; }

.app-card { border: 1px solid #e5e7eb; border-radius: 10px; background: #fff; padding: 12px; display: flex; flex-direction: column; gap: 8px; transition: box-shadow .15s, border-color .15s; }
.app-card:hover { border-color: #c7d2fe; box-shadow: 0 4px 14px rgba(0,0,0,.08); }

.card-head { display: flex; align-items: center; gap: 10px; }
.app-icon { width: 42px; height: 42px; border-radius: 8px; object-fit: cover; background: #f3f4f6; }
.card-titles { min-width: 0; flex: 1; }
.app-name { font-weight: 700; font-size: 13.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.app-id { font-size: 11px; color: #9ca3af; }
.card-actions { display: flex; align-items: center; gap: 2px; }
.icon-link { color: #6b7280; padding: 4px; border-radius: 6px; display: inline-flex; }
.icon-link:hover { background: #f3f4f6; color: #2563eb; }
.readme-btn { display: inline-flex; align-items: center; color: #6b7280; background: transparent; border: none; padding: 4px; border-radius: 6px; cursor: pointer; }
.readme-btn:hover { background: #f3f4f6; color: #2563eb; }

.app-desc { font-size: 12px; color: #374151; line-height: 1.6; margin: 0; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; min-height: 58px; }

.card-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: auto; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.tag { font-size: 10.5px; background: #f3f4f6; color: #374151; padding: 1px 6px; border-radius: 999px; display: inline-flex; align-items: center; gap: 3px; }
.tag.arch { background: #eef2ff; color: #4338ca; }
.tag.port { background: #ecfdf5; color: #047857; }
.btn.install { flex-shrink: 0; }

/* 免责声明弹窗 */
.disc-modal-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.55);
  z-index: 60;
  padding: 24px;
}
.disc-modal {
  width: 100%;
  max-width: 620px;
  max-height: 92%;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.disc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  font-weight: 700;
  font-size: 14px;
  color: #1d1d1f;
  border-bottom: 1px solid #e5e7eb;
  background: #fafafa;
  flex-shrink: 0;
}
.disc-version {
  margin-left: auto;
  font-size: 11px;
  color: #6b7280;
  background: #f3f4f6;
  padding: 1px 8px;
  border-radius: 999px;
}
.disc-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px;
  min-height: 0;
}
.disc-text {
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.75;
  color: #374151;
  white-space: pre-wrap;
  word-break: break-word;
}
.disc-foot {
  padding: 12px 16px;
  border-top: 1px solid #e5e7eb;
  background: #fafafa;
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex-shrink: 0;
}
.disc-agree-label {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12.5px;
  color: #374151;
  cursor: pointer;
  line-height: 1.6;
}
.disc-agree-label input { margin-top: 2px; }
.disc-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
