<!--
  批量操作中心窗口（BatchWindow）
  业务：勾选多台节点 → 批量执行同一条 shell 命令，或在多台节点按容器名过滤
        后批量 start/stop/restart。每节点独立返回退出码与输出，单节点失败不中断。
  后端模块：batchApi（POST /api/batch/command、/api/batch/containers）
  关键状态：nodes（节点多选）、tab（命令/容器）、results（逐节点结果）
  打开方式：桌面「批量操作」入口（管理员）；跨节点操作，窗口不绑定单一节点
-->
<template>
  <div style="display:flex; flex-direction:column; height:100%;">
    <!-- 顶部：Tab 切换 + 节点多选 -->
    <div class="toolbar" style="gap:8px; flex-wrap:wrap;">
      <button class="btn" :class="{ primary: tab === 'cmd' }" @click="tab = 'cmd'">{{ $t('batch.tabCmd') }}</button>
      <button class="btn" :class="{ primary: tab === 'ctr' }" @click="tab = 'ctr'">{{ $t('batch.tabContainers') }}</button>
      <span style="margin-left:8px; color:#0a3d7a; font-size:12px; align-self:center;">{{ $t('batch.targetNodes') }}</span>
      <label
        v-for="n in nodes.list"
        :key="n.id"
        style="display:inline-flex; align-items:center; gap:4px; font-size:12px; background:#f0f3fa; padding:2px 8px; border-radius:10px; cursor:pointer;"
      >
        <input type="checkbox" :value="n.id" v-model="checkedNodes" />
        {{ n.name }}
        <span v-if="n.type === 'ssh'" style="color:#888; font-size:10px;">SSH</span>
      </label>
    </div>

    <!-- 命令 / 容器 两 Tab 输入区 -->
    <div style="padding:10px 12px; display:flex; gap:8px; flex-wrap:wrap; align-items:flex-start;">
      <template v-if="tab === 'cmd'">
        <textarea
          v-model="command"
          :placeholder="$t('batch.cmdPlaceholder')"
          rows="2"
          style="flex:1; min-width:300px; font-family:Consolas,monospace; font-size:12px;"
        />
      </template>
      <template v-else>
        <input v-model="keyword" :placeholder="$t('batch.keywordPlaceholder')" type="text" style="width:200px;" />
        <select v-model="action">
          <option value="start">{{ $t('batch.start') }}</option>
          <option value="stop">{{ $t('batch.stop') }}</option>
          <option value="restart">{{ $t('batch.restart') }}</option>
        </select>
      </template>
      <button class="btn primary" :disabled="running || checkedNodes.length === 0" @click="run">{{ $t('batch.execute') }}</button>
      <span v-if="running" style="color:#888; font-size:12px;">{{ $t('batch.running') }}…</span>
    </div>

    <!-- 结果区：每节点一块 -->
    <div style="flex:1; overflow:auto; padding: 0 12px 12px;">
      <div v-if="results.length === 0 && !running" style="text-align:center; color:#999; padding:30px; font-size:12px;">
        {{ $t('batch.noResult') }}
      </div>
      <div v-for="r in results" :key="r.node_id" class="node-result">
        <div style="display:flex; align-items:center; gap:8px;">
          <span class="dot" :class="r.ok ? 'ok' : 'fail'"></span>
          <b style="font-size:13px;">{{ r.node_name }}</b>
          <span style="font-size:11px; color:#888;">{{ r.node_id }}</span>
          <span v-if="r.returncode !== null && r.returncode !== undefined" style="font-size:11px; color:#555;">exit={{ r.returncode }}</span>
          <span style="margin-left:auto; font-size:11px; color:#888;">{{ r.duration }}s</span>
        </div>
        <!-- 批量容器模式：每容器一行 -->
        <template v-if="r.containers">
          <div v-if="r.containers.length === 0" style="font-size:12px; color:#999; padding:4px 0;">{{ r.note || $t('batch.noContainer') }}</div>
          <div v-for="c in r.containers" :key="c.id" class="ctr-row">
            <span :class="c.ok ? 'ok-text' : 'fail-text'">{{ c.ok ? '✓' : '✗' }}</span>
            <span style="font-size:12px;">{{ c.name }}</span>
            <span v-if="!c.ok" style="font-size:11px; color:#c0392b; margin-left:8px;">{{ c.detail }}</span>
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
.node-result {
  border: 1px solid #e4e7f0;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
  background: #fafbfe;
}
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
.dot.ok { background: #27ae60; }
.dot.fail { background: #c0392b; }
.ctr-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; }
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