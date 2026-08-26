<!--
  SSL 证书窗口（SSL Certificates）

  这个窗口做什么：
    网站 SSL 证书的管理页。列出服务器上已存在的证书，管理员可以：
      - 上传已有证书（cert 与 key 两个文件）；
      - 通过 Let's Encrypt 为域名签发新证书；
      - 删除不再使用的证书。
    顶部还会显示 certbot 是否已安装——没装就不能走自动签发。
    它同时被「网站」窗口（SitesWindow）的「SSL证书」页签内嵌复用，
    管理员配好站点后在同一窗口里就能签发 / 上传证书。

  用到的后端模块：
    /api/ssl/*（管理员权限）——list 证书列表、upload 上传证书、
    letsencrypt 用 certbot 自动签发、{id} 删除证书。

  关键状态：
    certs       证书列表，表格数据源
    certbot     服务器上 certbot 是否可用（决定自动签发入口提示）
    showUpload / upForm   上传证书弹窗与表单
    showLE / leForm       Let's Encrypt 签发弹窗与表单
    confirm     删除证书的二次确认状态

  怎么被打开：
    桌面「SSL 证书」应用，或「网站」窗口内的「SSL证书」页签。
-->
<template>
  <div class="ssl-window">
    <!-- 工具栏：上传证书 / Let's Encrypt 签发 / certbot 安装状态 -->
    <div class="toolbar">
      <button class="btn primary" @click="showUpload=true">{{ $t('ssl.upload') }}</button>
      <button class="btn primary" @click="showLE=true">{{ $t('ssl.letsEncrypt') }}</button>
      <span class="hint">{{ $t('ssl.certbot', { status: $t(certbot ? 'database.installed' : 'database.notInstalled') }) }}</span>
    </div>
    <!-- 证书列表：一行一张证书，右侧垃圾桶即删除入口 -->
    <div class="table-wrap">
      <table>
        <thead><tr><th>{{ $t('ssl.name') }}</th><th>{{ $t('ssl.domains') }}</th><th>{{ $t('ssl.type') }}</th><th>{{ $t('ssl.path') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="c in certs" :key="c.id">
            <td>{{ c.name }}</td>
            <td>{{ (c.domains || []).join(', ') }}</td>
            <td><span class="badge" :class="c.type==='letsencrypt'?'ok':'info'">{{ c.type }}</span></td>
            <td class="mono">{{ c.cert_path }}</td>
            <td><button class="iconbtn danger" @click="remove(c.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="certs.length===0"><td colspan="5" class="empty">{{ $t('ssl.noCerts') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 上传证书弹窗：cert / key 两个文件一起提交 -->
    <div v-if="showUpload" class="modal-overlay" @click.self="showUpload=false">
      <div class="modal">
        <h3>{{ $t('ssl.uploadTitle') }}</h3>
        <div class="form">
          <label>{{ $t('ssl.uploadName') }}</label><input v-model="upForm.name" />
          <label>{{ $t('ssl.uploadDomains') }}</label><input v-model="upForm.domains" />
          <label>{{ $t('ssl.certFile') }}</label><input type="file" @change="e=>upForm.cert=e.target.files[0]" />
          <label>{{ $t('ssl.keyFile') }}</label><input type="file" @change="e=>upForm.key=e.target.files[0]" />
          <div class="actions">
            <button class="btn" @click="showUpload=false">{{ $t('common.cancel') }}</button>
            <button class="btn primary" @click="doUpload">{{ $t('ssl.upload') }}</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Let's Encrypt 签发弹窗：填域名（逗号分隔）与注册邮箱 -->
    <div v-if="showLE" class="modal-overlay" @click.self="showLE=false">
      <div class="modal">
        <h3>{{ $t('ssl.leTitle') }}</h3>
        <div class="form">
          <label>{{ $t('ssl.leDomains') }}</label><input v-model="leForm.domains" placeholder="example.com,www.example.com" />
          <label>{{ $t('ssl.leEmail') }}</label><input v-model="leForm.email" placeholder="admin@example.com" />
          <div class="actions">
            <button class="btn" @click="showLE=false">{{ $t('common.cancel') }}</button>
            <button class="btn primary" @click="doLE">{{ $t('ssl.apply') }}</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'         // 响应式状态、挂载钩子
import { useI18n } from 'vue-i18n'           // 取 t()，所有界面文案跟随面板语言
import { sslApi } from '../../api'           // SSL 证书后端能力：/api/ssl/* 的封装
import { Trash2 } from 'lucide-vue-next'     // 表格里删除证书的垃圾桶图标

const { t } = useI18n()

const certs = ref([])                 // 证书列表，表格数据源
const certbot = ref(false)            // 服务器上是否装了 certbot（false 时 Let's Encrypt 签发不可用）
const showUpload = ref(false)         // 上传证书弹窗是否展开
const showLE = ref(false)             // Let's Encrypt 签发弹窗是否展开
const upForm = ref({ name: '', domains: '', cert: null, key: null })    // 上传表单；cert/key 是所选文件对象
const leForm = ref({ domains: '', email: '' })                          // 签发表单：逗号分隔的域名 + 注册邮箱

// --- 拉取证书列表与 certbot 状态 ---
async function load() {
  const data = await sslApi.list()
  certs.value = data.certs || []    // 后端无 certs 字段时兜空数组，避免表格报错
  certbot.value = data.certbot
}

// --- 上传已有证书（cert/key 两个文件走 multipart） ---
async function doUpload() {
  const fd = new FormData()
  fd.append('name', upForm.value.name)
  fd.append('domains', upForm.value.domains)
  fd.append('cert', upForm.value.cert)
  fd.append('key', upForm.value.key)
  await sslApi.upload(fd)
  showUpload.value = false
  await load()    // 上传成功后刷新列表
}

// --- Let's Encrypt 签发：把逗号分隔的域名拆成数组再提交 ---
async function doLE() {
  const domains = leForm.value.domains.split(',').map(d=>d.trim()).filter(Boolean)   // 去空白、去空项，避免把空串当域名
  await sslApi.letsencrypt({ domains, email: leForm.value.email })
  showLE.value = false
  await load()    // 签发成功后刷新列表
}

// 删除证书：高风险操作，先弹出密码二次确认框
function remove(c) {
  confirm.value = { show: true, target: c }
}

// 面板密码校验通过后真正执行删除
async function doRemove() {
  const c = confirm.value.target
  confirm.value.show = false   // 先收起确认框，避免删除期间重复触发
  if (!c) return               // 无待删目标（异常触发）时直接退出
  try {
    await sslApi.delete(c.id)
    await load()
  } catch (e) {
    alert(e?.response?.data?.detail || e.message || t('common.error'))
  }
}

onMounted(load)   // 窗口一打开就拉一次证书列表
</script>

<style scoped>
.ssl-window { padding: 0; } /* 内嵌于「网站」聚合视图：外边距由父容器提供，上栏与父边缘平齐 */
.toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.hint { color: #6e6e73; font-size: 12px; margin-left: auto; }
.table-wrap { overflow: auto; max-height: 420px; border: 1px solid #e5e7eb; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; text-align: left; }
th { background: #f9fafb; position: sticky; top: 0; }
.mono { font-family: ui-monospace, Menlo, Monaco, Consolas, monospace; font-size: 11px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.info { background: #dbeafe; color: #1e40af; }
.empty { text-align: center; color: #9ca3af; padding: 24px; }
.btn { padding: 6px 12px; border: 1px solid #d1d5db; background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; }
.btn.primary { background: #111827; color: #fff; border-color: #111827; }
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.35); display: flex; align-items: center; justify-content: center; z-index: 2000; }
.modal { background: #fff; border-radius: 12px; padding: 16px; width: 460px; max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.15); }
.modal h3 { margin: 0 0 12px; font-size: 16px; }
.form { display: flex; flex-direction: column; gap: 10px; }
.form label { font-size: 12px; color: #374151; }
.form input { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 13px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
