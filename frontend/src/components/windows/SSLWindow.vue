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
    <!-- 工具栏：上传证书 / Let's Encrypt 签发（均打开独立表单窗口） / certbot 安装状态 -->
    <div class="ui-toolbar">
      <button class="ui-btn primary" @click="emit('openSslUpload')">{{ $t('ssl.upload') }}</button>
      <button class="ui-btn primary" @click="emit('openSslLeForm')">{{ $t('ssl.letsEncrypt') }}</button>
      <span class="ui-hint right">{{ $t('ssl.certbot', { status: $t(certbot ? 'database.installed' : 'database.notInstalled') }) }}</span>
    </div>
    <!-- 证书列表：一行一张证书，右侧垃圾桶即删除入口 -->
    <div class="ui-table-wrap">
      <table>
        <thead><tr><th>{{ $t('ssl.name') }}</th><th>{{ $t('ssl.domains') }}</th><th>{{ $t('ssl.type') }}</th><th>{{ $t('ssl.path') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
        <tbody>
          <tr v-for="c in certs" :key="c.id">
            <td>{{ c.name }}</td>
            <td>{{ (c.domains || []).join(', ') }}</td>
            <td><span class="ui-badge" :class="c.type==='letsencrypt'?'ok':'off'">{{ c.type }}</span></td>
            <td class="ui-mono">{{ c.cert_path }}</td>
            <td><button class="iconbtn danger" @click="remove(c.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="certs.length===0"><td colspan="5" class="ui-empty">{{ $t('ssl.noCerts') }}</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 上传证书 / Let's Encrypt 签发已拆分为独立窗口（SslUploadWindow /
         SslLeFormWindow），避免误触遮罩丢失已填内容 -->
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'         // 响应式状态、挂载钩子、信号监听
import { useI18n } from 'vue-i18n'           // 取 t()，所有界面文案跟随面板语言
import { sslApi } from '../../api'           // SSL 证书后端能力：/api/ssl/* 的封装
import { Trash2 } from 'lucide-vue-next'     // 表格里删除证书的垃圾桶图标
import { formBus } from '../../store/formBus'   // 表单保存信号：独立表单窗口保存成功后刷新

const { t } = useI18n()

// openSslUpload 打开上传证书窗口；openSslLeForm 打开 Let's Encrypt 签发窗口
const emit = defineEmits(['openSslUpload', 'openSslLeForm'])

const certs = ref([])                 // 证书列表，表格数据源
const certbot = ref(false)            // 服务器上是否装了 certbot（false 时 Let's Encrypt 签发不可用）

// 上传/签发表单已拆分为独立窗口：保存成功后 bumpForm('ssl') 触发此处重载
watch(() => formBus.ssl, load)

// 删除证书：高风险操作，先弹出密码二次确认框
const confirm = ref({ show: false, target: null })

// --- 拉取证书列表与 certbot 状态 ---
async function load() {
  const data = await sslApi.list()
  certs.value = data.certs || []    // 后端无 certs 字段时兜空数组，避免表格报错
  certbot.value = data.certbot
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
.iconbtn { padding: 4px; border: 1px solid #e5e7eb; background: #fff; border-radius: 6px; cursor: pointer; }
.iconbtn.danger:hover { background: #fee2e2; border-color: #fca5a5; }
</style>
