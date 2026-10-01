<!--
  巡检报告窗口（ReportWindow）
  业务：生成与管理「每日/手动巡检报告」：系统资源 24h 概览、证书到期、
        服务监控异常、站点可用性、告警统计。报告自动落盘并推送通知渠道。
  后端模块：reportApi（POST /generate、GET /list、GET /{file}）
  关键状态：files（报告列表）、text（当前报告全文）
  打开方式：桌面「巡检报告」入口（管理员）
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <div class="toolbar">
      <button class="btn primary" :disabled="generating" @click="doGenerate">
        {{ generating ? $t('common.loading') : $t('report.generate') }}
      </button>
      <button class="btn" @click="loadList">{{ $t('common.refresh') }}</button>
      <span style="margin-left:auto; color:#888; font-size:11px;">{{ $t('report.dailyHint') }}</span>
    </div>

    <div style="display:flex; flex:1; overflow:hidden;">
      <!-- 左：报告列表 -->
      <div style="width:230px; overflow:auto; border-right:1px solid #dfe3ec;">
        <div
          v-for="f in files"
          :key="f"
          class="file-item"
          :class="{ selected: selected === f }"
          @click="openFile(f)"
        >{{ f }}</div>
        <div v-if="files.length === 0" style="padding:20px; color:#999; text-align:center; font-size:12px;">
          {{ $t('report.empty') }}
        </div>
      </div>
      <!-- 右：报告全文 -->
      <div style="flex:1; overflow:auto; padding:12px;">
        <pre v-if="text" class="report-text">{{ text }}</pre>
        <div v-else style="color:#999; font-size:12px; text-align:center; padding:40px;">{{ $t('report.selectHint') }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'   // 响应式 + 挂载时加载
import { reportApi } from '../../api'  // 报告接口

const files = ref([])      // 报告文件列表（时间倒序）
const selected = ref('')   // 当前选中文件名
const text = ref('')       // 当前报告全文
const generating = ref(false)

async function loadList() {
  try {
    const res = await reportApi.list()
    files.value = res.files || []
    if (!selected.value && files.value.length) await openFile(files.value[0])
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

async function openFile(fname) {
  selected.value = fname
  try {
    const res = await reportApi.get(fname)
    text.value = res.text || ''
  } catch (e) {
    text.value = ''
  }
}

async function doGenerate() {
  generating.value = true
  try {
    const res = await reportApi.generate()
    selected.value = res.file || ''
    text.value = res.text || ''
    await loadList()
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  } finally {
    generating.value = false
  }
}

onMounted(loadList)
</script>

<style scoped>
.file-item {
  padding: 8px 10px;
  font-size: 12px;
  font-family: Consolas, monospace;
  cursor: pointer;
  border-bottom: 1px solid #eef0f6;
  word-break: break-all;
}
.file-item:hover { background: #f5f7fb; }
.file-item.selected { background: #eaf1fb; color: #0a3d7a; }
.report-text {
  font-family: Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  color: #2c3e50;
}
</style>