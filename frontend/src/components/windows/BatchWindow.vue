<!--
  批量操作中心窗口（BatchWindow）
  业务：勾选多台节点 → 批量执行同一条 shell 命令，或在多台节点按容器名过滤
        后批量 start/stop/restart。每节点独立返回退出码与输出，单节点失败不中断。
  后端模块：batchApi（POST /api/batch/command、/api/batch/containers）
  关键状态：nodes（节点多选）、tab（命令/容器）、results（逐节点结果）
  打开方式：桌面「批量操作」入口（管理员）；跨节点操作，窗口不绑定单一节点
-->
<template>
  <div class="batch-window">
    <!-- 顶部：Tab 切换 + 节点多选 -->
    <div class="ui-toolbar">
      <button class="ui-btn" :class="{ primary: tab === 'cmd' }" @click="tab = 'cmd'">{{ $t('batch.tabCmd') }}</button>
      <button class="ui-btn" :class="{ primary: tab === 'ctr' }" @click="tab = 'ctr'">{{ $t('batch.tabContainers') }}</button>
      <span class="node-label">{{ $t('batch.targetNodes') }}</span>
      <label v-for="n in nodes.list" :key="n.id" class="node-chip">
        <input type="checkbox" :value="n.id" v-model="checkedNodes" />
        {{ n.name }}
        <span v-if="n.type === 'ssh'" class="ssh-tag">SSH</span>
      </label>
    </div>

    <!-- 命令 / 容器 两 Tab 输入区 -->
    <div class="input-row">
      <template v-if="tab === 'cmd'">
        <textarea
          v-model="command"
          :placeholder="$t('batch.cmdPlaceholder')"
          rows="2"
          class="ui-textarea cmd-area"
        />
      </template>
      <template v-else>
        <input v-model="keyword" :placeholder="$t('batch.keywordPlaceholder')" type="text" class="ui-input kw-input" />
        <select v-model="action" class="ui-select">
          <option value="start">{{ $t('batch.start') }}</option>
          <option value="stop">{{ $t('batch.stop') }}</option>
          <option value="restart">{{ $t('batch.restart') }}</option>
        </select>
      </template>
      <button class="ui-btn primary" :disabled="running || checkedNodes.length === 0" @click="run">{{ $t('batch.execute') }}</button>
      <span v-if="running" class="ui-hint">{{ $t('batch.running') }}…</span>
    </div>

    <!-- 结果区：每节点一块 -->
    <div class="result-area">
      <div v-if="results.length === 0 && !running" class="ui-empty">{{ $t('batch.noResult') }}</div>
      <div v-for="r in results" :key="r.node_id" class="ui-card node-result">
        <div class="result-head">
          <span class="dot" :class="r.ok ? 'ok' : 'fail'"></span>
          <b style="font-size:13px;">{{ r.node_name }}</b>
          <span class="id-text">{{ r.node_id }}</span>
          <span v-if="r.returncode !== null && r.returncode !== undefined" class="exit-text">exit={{ r.returncode }}</span>
          <span class="duration-text">{{ r.duration }}s</span>
        </div>
        <!-- 批量容器模式：每容器一行 -->
        <template v-if="r.containers">
          <div v-if="r.containers.length === 0" class="no-ctr">{{ r.note || $t('batch.noContainer') }}</div>
          <div v-for="c in r.containers" :key="c.id" class="ctr-row">
            <span :class="c.ok ? 'ok-text' : 'fail-text'">{{ c.ok ? '✓' : '✗' }}</span>
            <span>{{ c.name }}</span>
            <span v-if="!c.ok" class="ctr-detail">{{ c.detail }}</span>
          </div>
        </template>
        <!-- 批量命令模式：stdout/stderr -->
        <template v-else>
          <pre v-if="r.stdout" class="out">{{ r.stdout }}</pre>
          <pre v-if="r.stderr" class="err">{{ r.stderr }}</pre>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'   // 响应式 + 挂载时刷新节点
import { useI18n } from 'vue-i18n'                // 国际化
import { batchApi } from '../../api'              // 批量接口
import { nodes, refreshNodes } from '../../store/nodes'  // 节点列表（全局状态）

const { t } = useI18n()
const tab = ref('cmd')                 // cmd | ctr
const command = ref('')                // 批量命令
const keyword = ref('')                // 容器名关键字
const action = ref('start')            // 容器动作
const checkedNodes = ref([])           // 勾选的节点 id 列表
const results = reactive([])           // 逐节点结果
const running = ref(false)

// 执行：按当前 Tab 调对应批量接口
async function run() {
  const node_ids = checkedNodes.value
  if (!node_ids.length) {
    alert(t('batch.needNodes'))
    return
  }
  if (tab.value === 'cmd' && !command.value.trim()) {
    alert(t('batch.needCommand'))
    return
  }
  running.value = true
  results.length = 0 // 清空旧结果
  try {
    if (tab.value === 'cmd') {
      const res = await batchApi.command({ node_ids, command: command.value.trim() })
      pushResults(res.results || [])
    } else {
      const res = await batchApi.containers({ node_ids, action: action.value, filter: { keyword: keyword.value.trim() } })
      pushResults(res.results || [])
    }
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  } finally {
    running.value = false
  }
}

// 结果回填到响应式数组（逐节点展开）
function pushResults(list) {
  for (const r of list) results.push(r)
}

onMounted(refreshNodes)
</script>

<style scoped>
.batch-window { display: flex; flex-direction: column; height: 100%; padding: 10px; box-sizing: border-box; gap: 8px; }
.node-label { margin-left: 8px; color: #0a3d7a; font-size: 12px; align-self: center; }
.node-chip {
  display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
  background: #f0f3fa; padding: 2px 8px; border-radius: 10px; cursor: pointer;
  border: 1px solid #e5e7eb;
}
.node-chip .ssh-tag { color: #888; font-size: 10px; }
.input-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: flex-start; }
.cmd-area { flex: 1; min-width: 300px; font-family: Consolas, monospace; font-size: 12px; }
.kw-input { width: 200px; }
.result-area { flex: 1; overflow: auto; padding: 0 4px 12px; }
.node-result { margin-bottom: 10px; }
.result-head { display: flex; align-items: center; gap: 8px; }
.id-text { font-size: 11px; color: #888; }
.exit-text { font-size: 11px; color: #555; }
.duration-text { margin-left: auto; font-size: 11px; color: #888; }
.no-ctr { font-size: 12px; color: #999; padding: 4px 0; }
.ctr-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; font-size: 12px; }
.ctr-detail { font-size: 11px; color: #c0392b; margin-left: 8px; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.dot.ok { background: #27ae60; }
.dot.fail { background: #c0392b; }
.ok-text { color: #27ae60; font-size: 12px; }
.fail-text { color: #c0392b; font-size: 12px; }
.out {
  background: #0f1722; color: #d7e3f4; border-radius: 4px; padding: 8px;
  font-family: Consolas, monospace; font-size: 12px; white-space: pre-wrap;
  word-break: break-all; max-height: 180px; overflow: auto; margin-top: 6px;
}
.err {
  background: #33120f; color: #ffb3a8; border-radius: 4px; padding: 8px;
  font-family: Consolas, monospace; font-size: 12px; white-space: pre-wrap;
  word-break: break-all; max-height: 120px; overflow: auto; margin-top: 6px;
}
</style>