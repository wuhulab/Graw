<!--
  MySQL 慢查询分析窗口（SlowQueryWindow）
  业务：选择一个数据库连接，解析其慢查询日志，按执行时间降序列出 TOP N，
        并对常见低效模式给出建议。日志未开启时展示开启引导。
  后端模块：slowqueryApi（connections / scan）
  关键状态：conns（连接下拉）、items（TOP N 结果）、hint（未开启/无日志提示）
  打开方式：桌面「慢查询分析」入口（管理员）
-->
<template>
  <div class="slowq-window">
    <div class="ui-toolbar">
      <select v-model="connId" class="ui-select conn-select">
        <option value="">-- {{ $t('slowq.selectConn') }} --</option>
        <option v-for="c in conns" :key="c.id" :value="c.id">{{ c.name }}（{{ c.host }}:{{ c.port }}）</option>
      </select>
      <button class="ui-btn primary" :disabled="!connId || scanning" @click="doScan">
        {{ scanning ? $t('common.loading') : $t('slowq.scan') }}
      </button>
      <span class="ui-hint right">{{ $t('slowq.hint') }}</span>
    </div>

    <div class="tab-body">
      <!-- 未开启 / 未找到日志时的引导 -->
      <div v-if="hint" class="hint-box">{{ hint }}</div>

      <div v-if="items.length" class="ui-table-wrap">
        <table>
          <thead>
            <tr>
              <th style="width:120px;">{{ $t('slowq.time') }}</th>
              <th style="width:70px;">{{ $t('slowq.queryTime') }}</th>
              <th style="width:70px;">{{ $t('slowq.sent') }}</th>
              <th style="width:80px;">{{ $t('slowq.examined') }}</th>
              <th style="width:90px;">{{ $t('slowq.user') }}</th>
              <th>{{ $t('slowq.sql') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(it, i) in items" :key="i">
              <td class="ui-mono" style="font-size:11px;">{{ it.time }}</td>
              <td><b class="query-time">{{ it.query_time.toFixed(2) }}s</b></td>
              <td>{{ it.rows_sent }}</td>
              <td>{{ it.rows_examined }}</td>
              <td>{{ it.user }}</td>
              <td>
                <div class="sql">{{ it.sql }}</div>
                <div v-if="it.suggest" class="suggest">💡 {{ it.suggest }}</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="!hint && !scanning" class="ui-empty">{{ $t('slowq.empty') }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'   // 响应式 + 挂载
import { useI18n } from 'vue-i18n'      // 国际化
import { slowqueryApi } from '../../api' // 慢查询接口

const { t } = useI18n()
const conns = ref([])      // 连接下拉数据
const connId = ref('')     // 选中连接
const items = ref([])      // TOP N 结果
const hint = ref('')       // 引导提示
const scanning = ref(false)

async function loadConns() {
  try {
    const res = await slowqueryApi.connections()
    conns.value = res.connections || []
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  }
}

async function doScan() {
  if (!connId.value) return
  scanning.value = true
  items.value = []
  hint.value = ''
  try {
    const res = await slowqueryApi.scan(connId.value)
    items.value = res.items || []
    hint.value = res.hint || ''
  } catch (e) {
    alert(e?.response?.data?.detail || String(e))
  } finally {
    scanning.value = false
  }
}

onMounted(loadConns)
</script>

<style scoped>
.slowq-window { display: flex; flex-direction: column; height: 100%; padding: 10px; box-sizing: border-box; gap: 8px; }
.conn-select { min-width: 200px; font-size: 12px; }
.tab-body { flex: 1; overflow: auto; padding: 2px 4px 12px; }
.hint-box {
  background: #fffbeb; border: 1px solid #fcd34d; color: #92400e;
  border-radius: 8px; padding: 10px 12px; font-size: 12px; margin-bottom: 10px;
}
.query-time { color: #c0392b; }
.sql {
  font-family: Consolas, monospace; font-size: 11px; color: #2c3e50;
  word-break: break-all;
}
.suggest { font-size: 11px; color: #0a3d7a; margin-top: 2px; }
</style>