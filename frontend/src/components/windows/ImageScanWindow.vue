<!--
  镜像漏洞扫描窗口（ImageScanWindow）
  业务：扫描本地 Docker 镜像内的软件包，与本地 advisory 库比对输出命中的
        CVE 列表；支持查看/导入 advisory（无外部 CVE 数据源）。
  后端模块：imgsafetyApi（scan / advisory / importAdvisory）
  关键状态：image（镜像引用）、result（扫描结果）、tab（扫描/advisory）
  打开方式：桌面「镜像扫描」入口（管理员）
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <button class="btn" :class="{ primary: tab === 'scan' }" @click="tab = 'scan'">{{ $t('imgscan.tabScan') }}</button>
      <button class="btn" :class="{ primary: tab === 'adv' }" @click="tab = 'adv'; loadAdvisory()">{{ $t('imgscan.tabAdvisory') }}</button>
    </div>

    <!-- 扫描 tab -->
    <div v-if="tab === 'scan'" style="flex:1; overflow:auto; padding: 12px;">
      <div style="display:flex; gap:8px; margin-bottom:10px;">
        <input v-model="image" type="text" :placeholder="$t('imgscan.imagePlaceholder')" style="flex:1; font-size:12px;" />
        <button class="btn primary" :disabled="scanning || !image.trim()" @click="doScan">
          {{ scanning ? $t('common.loading') : $t('imgscan.scan') }}
        </button>
      </div>
      <div v-if="result" class="summary">
        <span class="badge">{{ $t('imgscan.pkgCount') }}: {{ result.total_pkgs }}</span>
        <span class="badge" :class="result.findings.length ? 'warn' : 'ok'">
          {{ $t('imgscan.findingCount', { n: result.findings.length }) }}
        </span>
        <span v-if="result.cached" class="badge">{{ $t('imgscan.cached') }}</span>
      </div>
      <div v-if="result && result.findings.length">
        <table class="dt">
          <thead>
            <tr>
              <th>{{ $t('imgscan.severity') }}</th>
              <th>{{ $t('imgscan.cve') }}</th>
              <th>{{ $t('imgscan.pkg') }}</th>
              <th>{{ $t('imgscan.pkgVersion') }}</th>
              <th>{{ $t('imgscan.desc') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(f, i) in result.findings" :key="i">
              <td><span class="sev" :class="f.severity">{{ f.severity }}</span></td>
              <td class="mono">{{ f.cve }}</td>
              <td>{{ f.pkg }}</td>
              <td class="mono">{{ f.version }}</td>
              <td style="font-size:11px; color:#666;">{{ f.desc }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="result" style="color:#27ae60; font-size:12px; margin-top:6px;">{{ $t('imgscan.noFindings') }}</div>
    </div>

    <!-- advisory tab -->
    <div v-else style="flex:1; overflow:auto; padding: 12px;">
      <textarea
        v-model="advJson"
        rows="6"
        style="width:100%; font-family:Consolas,monospace; font-size:11px;"
        :placeholder="'[ { name, versions, cve, severity, desc }, ... ] 每项：name=软件包名，versions=版本约束(如 <= 3.0.14)'"
      />
      <div style="display:flex; gap:8px; margin:8px 0;">
        <button class="btn primary" :disabled="importing" @click="doImport">{{ importing ? $t('common.loading') : $t('imgscan.import') }}</button>
        <span v-if="importMsg" style="font-size:12px; color:#0a3d7a; align-self:center;">{{ importMsg }}</span>
      </div>
      <div style="font-size:11px; color:#888; margin-bottom:6px;">{{ $t('imgscan.advisoryCount', { n: advPkgs.length }) }}</div>
      <table class="dt">
        <thead>
          <tr>
            <th>{{ $t('imgscan.pkg') }}</th>
            <th>{{ $t('imgscan.constraint') }}</th>
            <th>{{ $t('imgscan.cve') }}</th>
            <th>{{ $t('imgscan.severity') }}</th>
            <th>{{ $t('imgscan.desc') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(a, i) in advPkgs" :key="i">
            <td>{{ a.name }}</td>
            <td class="mono">{{ a.versions }}</td>
            <td class="mono">{{ a.cve }}</td>
            <td><span class="sev" :class="a.severity">{{ a.severity }}</span></td>
            <td style="font-size:11px; color:#666;">{{ a.desc }}</td>
          </tr>
          <tr v-if="advPkgs.length === 0">
            <td colspan="5" style="text-align:center; color:#999;">{{ $t('imgscan.advEmpty') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'         // 响应式 + 挂载
import { useI18n } from 'vue-i18n'            // 国际化
import { imgsafetyApi } from '../../api'      // 扫描接口

const { t } = useI18n()
const tab = ref('scan')
const image = ref('')
const scanning = ref(false)
const result = ref(null)

const advJson = ref('')
const advPkgs = ref([])
const importing = ref(false)
const importMsg = ref('')

async function doScan() {
  scanning.value = true
  result.value = null
  try {
    result.value = await imgsafetyApi.scan(image.value.trim())
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  } finally {
    scanning.value = false
  }
}

async function loadAdvisory() {
  try {
    const res = await imgsafetyApi.advisory()
    advPkgs.value = res.packages || []
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

async function doImport() {
  if (!advJson.value.trim()) return
  let packages
  try {
    packages = JSON.parse(advJson.value)
    if (!Array.isArray(packages)) throw new Error('not array')
  } catch (e) {
    alert(t('imgscan.jsonInvalid'))
    return
  }
  importing.value = true
  importMsg.value = ''
  try {
    const res = await imgsafetyApi.importAdvisory(packages)
    importMsg.value = t('imgscan.importOk', { n: res.imported, total: res.total })
    advJson.value = ''
    await loadAdvisory()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  } finally {
    importing.value = false
  }
}

onMounted(loadAdvisory)
</script>

<style scoped>
.summary { display: flex; gap: 8px; margin-bottom: 8px; }
.badge { background: #f0f3fa; color: #0a3d7a; font-size: 11px; padding: 2px 10px; border-radius: 10px; }
.badge.ok { background: #d1fae5; color: #065f46; }
.badge.warn { background: #fde68a; color: #92400e; }
.sev { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: #eef2ff; color: #4338ca; }
.sev.critical { background: #7f1d1d; color: #fff; }
.sev.high { background: #c0392b; color: #fff; }
.sev.medium { background: #f39c12; color: #fff; }
.sev.low { background: #eef2ff; color: #4338ca; }
.mono { font-family: Consolas, monospace; }
</style>