<!--
  镜像漏洞扫描窗口（ImageScanWindow）
  业务：扫描本地 Docker 镜像内的软件包，与本地 advisory 库比对输出命中的
        CVE 列表；支持查看/导入 advisory（无外部 CVE 数据源）。
  后端模块：imgsafetyApi（scan / advisory / importAdvisory）
  关键状态：image（镜像引用）、result（扫描结果）、tab（扫描/advisory）
  打开方式：桌面「镜像扫描」入口（管理员）
-->
<template>
  <div class="imgscan-window">
    <div class="ui-toolbar">
      <button class="ui-btn" :class="{ primary: tab === 'scan' }" @click="tab = 'scan'">{{ $t('imgscan.tabScan') }}</button>
      <button class="ui-btn" :class="{ primary: tab === 'adv' }" @click="tab = 'adv'; loadAdvisory()">{{ $t('imgscan.tabAdvisory') }}</button>
    </div>

    <!-- 扫描 tab -->
    <div v-if="tab === 'scan'" class="tab-body">
      <div class="scan-row">
        <select class="ui-select" v-model="selectedId">
          <option value="">{{ $t('imgscan.selectContainer') }}</option>
          <option v-for="c in containers" :key="c.id" :value="c.id">{{ c.name }}（{{ c.image }}）</option>
        </select>
        <button class="ui-btn primary" :disabled="scanning || !selected" @click="doScan">
          {{ scanning ? $t('common.loading') : $t('imgscan.scan') }}
        </button>
      </div>
      <div v-if="loadError" class="container-err">{{ loadError }}</div>
      <div v-else-if="containers.length === 0" class="ui-hint" style="margin-bottom:10px;">{{ $t('imgscan.noContainers') }}</div>
      <div v-if="result" class="summary">
        <span class="ui-badge off">{{ $t('imgscan.pkgCount') }}: {{ result.total_pkgs }}</span>
        <span class="ui-badge" :class="result.findings.length ? 'warn' : 'ok'">
          {{ $t('imgscan.findingCount', { n: result.findings.length }) }}
        </span>
        <span v-if="result.cached" class="ui-badge off">{{ $t('imgscan.cached') }}</span>
      </div>
      <div v-if="result && result.findings.length" class="ui-table-wrap">
        <table>
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
              <td class="ui-mono">{{ f.cve }}</td>
              <td>{{ f.pkg }}</td>
              <td class="ui-mono">{{ f.version }}</td>
              <td style="font-size:11px; color:#666;">{{ f.desc }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="result" class="ok-tip">{{ $t('imgscan.noFindings') }}</div>
    </div>

    <!-- advisory tab -->
    <div v-else class="tab-body">
      <textarea
        v-model="advJson"
        rows="6"
        class="ui-textarea adv-area"
        :placeholder="'[ { name, versions, cve, severity, desc }, ... ] 每项：name=软件包名，versions=版本约束(如 <= 3.0.14)'"
      />
      <div class="adv-row">
        <button class="ui-btn primary" :disabled="importing" @click="doImport">{{ importing ? $t('common.loading') : $t('imgscan.import') }}</button>
        <span v-if="importMsg" class="import-msg">{{ importMsg }}</span>
      </div>
      <div class="adv-count">{{ $t('imgscan.advisoryCount', { n: advPkgs.length }) }}</div>
      <div class="ui-table-wrap">
        <table>
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
              <td class="ui-mono">{{ a.versions }}</td>
              <td class="ui-mono">{{ a.cve }}</td>
              <td><span class="sev" :class="a.severity">{{ a.severity }}</span></td>
              <td style="font-size:11px; color:#666;">{{ a.desc }}</td>
            </tr>
            <tr v-if="advPkgs.length === 0">
              <td colspan="5" class="ui-empty">{{ $t('imgscan.advEmpty') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'         // 响应式 + 派生选中项 + 挂载
import { useI18n } from 'vue-i18n'            // 国际化
import { imgsafetyApi } from '../../api'      // 扫描接口

const { t } = useI18n()
const tab = ref('scan')
const containers = ref([])      // 本地容器列表（name + image）
const selectedId = ref('')      // 下拉选中的容器 id
const loadError = ref('')       // 容器列表加载失败提示
const scanning = ref(false)
const result = ref(null)

const advJson = ref('')
const advPkgs = ref([])
const importing = ref(false)
const importMsg = ref('')

// 当前选中的容器对象（未选时 null，禁用扫描按钮）
const selected = computed(() => containers.value.find((c) => c.id === selectedId.value) || null)

// --- 拉取本地容器列表（下拉数据源） ---
async function loadContainers() {
  loadError.value = ''
  try {
    const res = await imgsafetyApi.containers()
    containers.value = res.containers || []
    if (res.error && containers.value.length === 0) {
      loadError.value = t('imgscan.loadContainersFailed', { error: res.error })
    }
  } catch (e) {
    containers.value = []
    loadError.value = t('imgscan.loadContainersFailed', { error: e?.response?.data?.detail || e?.message || String(e) })
  }
}

// --- 扫描选中容器对应的镜像 ---
async function doScan() {
  const img = selected.value?.image
  if (!img) return   // 未选容器（防御性，按钮本身禁用）
  scanning.value = true
  result.value = null
  try {
    result.value = await imgsafetyApi.scan(img.trim())
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

onMounted(() => {
  loadContainers()   // 打开即拉取本地容器列表（下拉数据源）
  loadAdvisory()
})
</script>

<style scoped>
.imgscan-window { display: flex; flex-direction: column; height: 100%; padding: 10px; box-sizing: border-box; gap: 8px; }
.tab-body { flex: 1; overflow: auto; padding: 2px 4px 12px; }
.scan-row { display: flex; gap: 8px; margin-bottom: 10px; }
.scan-row .ui-select { flex: 1; font-size: 12px; }
.container-err { color: #b91c1c; font-size: 12.5px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; word-break: break-all; }
.summary { display: flex; gap: 8px; margin-bottom: 8px; }
.ok-tip { color: #27ae60; font-size: 12px; margin-top: 6px; }
.adv-area { width: 100%; font-family: Consolas, monospace; font-size: 11px; }
.adv-row { display: flex; gap: 8px; margin: 8px 0; align-items: center; }
.import-msg { font-size: 12px; color: #0a3d7a; align-self: center; }
.adv-count { font-size: 11px; color: #888; margin-bottom: 6px; }
.sev { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: #eef2ff; color: #4338ca; }
.sev.critical { background: #7f1d1d; color: #fff; }
.sev.high { background: #c0392b; color: #fff; }
.sev.medium { background: #f39c12; color: #fff; }
.sev.low { background: #eef2ff; color: #4338ca; }
</style>