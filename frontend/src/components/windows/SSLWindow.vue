<template>
  <div class="ssl-window">
    <div class="toolbar">
      <button class="btn primary" @click="showUpload=true">上传证书</button>
      <button class="btn primary" @click="showLE=true">Let's Encrypt</button>
      <span class="hint">Certbot: {{ certbot ? '已安装' : '未安装' }}</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>名称</th><th>域名</th><th>类型</th><th>路径</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="c in certs" :key="c.id">
            <td>{{ c.name }}</td>
            <td>{{ (c.domains || []).join(', ') }}</td>
            <td><span class="badge" :class="c.type==='letsencrypt'?'ok':'info'">{{ c.type }}</span></td>
            <td class="mono">{{ c.cert_path }}</td>
            <td><button class="iconbtn danger" @click="remove(c.id)"><Trash2 :size="14"/></button></td>
          </tr>
          <tr v-if="certs.length===0"><td colspan="5" class="empty">暂无证书</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="showUpload" class="modal-overlay" @click.self="showUpload=false">
      <div class="modal">
        <h3>上传自定义证书</h3>
        <div class="form">
          <label>证书名称</label><input v-model="upForm.name" />
          <label>域名（逗号分隔）</label><input v-model="upForm.domains" />
          <label>证书文件 (.crt/.pem)</label><input type="file" @change="e=>upForm.cert=e.target.files[0]" />
          <label>私钥文件 (.key)</label><input type="file" @change="e=>upForm.key=e.target.files[0]" />
          <div class="actions">
            <button class="btn" @click="showUpload=false">取消</button>
            <button class="btn primary" @click="doUpload">上传</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showLE" class="modal-overlay" @click.self="showLE=false">
      <div class="modal">
        <h3>申请 Let's Encrypt</h3>
        <div class="form">
          <label>域名列表（逗号分隔）</label><input v-model="leForm.domains" placeholder="example.com,www.example.com" />
          <label>邮箱</label><input v-model="leForm.email" placeholder="admin@example.com" />
          <div class="actions">
            <button class="btn" @click="showLE=false">取消</button>
            <button class="btn primary" @click="doLE">申请</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { sslApi } from '../../api'
import { Trash2 } from 'lucide-vue-next'

const certs = ref([])
const certbot = ref(false)
const showUpload = ref(false)
const showLE = ref(false)
const upForm = ref({ name: '', domains: '', cert: null, key: null })
const leForm = ref({ domains: '', email: '' })

async function load() {
  const data = await sslApi.list()
  certs.value = data.certs || []
  certbot.value = data.certbot
}

async function doUpload() {
  const fd = new FormData()
  fd.append('name', upForm.value.name)
  fd.append('domains', upForm.value.domains)
  fd.append('cert', upForm.value.cert)
  fd.append('key', upForm.value.key)
  await sslApi.upload(fd)
  showUpload.value = false
  await load()
}

async function doLE() {
  const domains = leForm.value.domains.split(',').map(d=>d.trim()).filter(Boolean)
  await sslApi.letsencrypt({ domains, email: leForm.value.email })
  showLE.value = false
  await load()
}

async function remove(id) {
  if (!confirm('删除此证书？')) return
  await sslApi.delete(id)
  await load()
}

onMounted(load)
</script>

<style scoped>
.ssl-window { padding: 10px; }
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
