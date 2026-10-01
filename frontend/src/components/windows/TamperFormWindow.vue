<!--
  TamperFormWindow.vue — 网页防篡改 添加/编辑 站点防护表单（独立窗口）
  ==========================================================
  业务作用：
    原内嵌于 TamperWindow 的「添加/编辑防篡改站点」modal 弹窗独立为
    桌面窗口，避免误触灰色遮罩丢失已填写的常用文件 / 忽略规则等长文本。
    支持站点选择（新增时）、站点名/根目录、受保护文件列表、忽略规则、
    备份快照间隔与扫描间隔配置。
  后端模块：
    /api/tamper 的 create / update。
  关键状态：
    form              表单对象（多字段，含文本域）
    error             必填校验 / 后端错误信息回显
  打开方式：
    由 App.vue 的 openTamperForm(payload) 打开，props 传入
    { task: 编辑对象或 null, candidates: 可防护的候选站点列表 }。
    保存成功后 emit('close')，并经 formBus 通知 TamperWindow 刷新。
-->
<template>
  <div class="tamper-form-window">
    <div v-if="error" class="error-box">{{ error }}</div>

    <!-- 站点选择（添加时） -->
    <label v-if="!task" class="ui-field">
      <span class="ui-label">{{ $t('tamper.selectSite') }}</span>
      <select class="ui-select" v-model="form.site_id" @change="onSiteChange">
        <option value="">{{ $t('tamper.selectSiteHint') }}</option>
        <option v-for="c in candidates" :key="c.site_id" :value="c.site_id">
          {{ c.name }}（{{ c.root || $t('tamper.noRoot') }}）
        </option>
      </select>
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('tamper.siteNameLabel') }}</span>
      <input class="ui-input" v-model.trim="form.site_name" maxlength="128" />
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('tamper.rootLabel') }}</span>
      <input class="ui-input" v-model.trim="form.root" placeholder="/var/www/html" spellcheck="false" />
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('tamper.protectedFilesLabel') }}</span>
      <textarea class="ui-textarea mono-area" v-model="form.protected_files" rows="4" :placeholder="$t('tamper.protectedFilesPlaceholder')" spellcheck="false"></textarea>
      <span class="ui-hint">{{ $t('tamper.protectedFilesHint') }}</span>
    </label>

    <label class="ui-field">
      <span class="ui-label">{{ $t('tamper.ignoreLabel') }}</span>
      <textarea class="ui-textarea mono-area" v-model="form.ignore_patterns" rows="3" :placeholder="$t('tamper.ignorePlaceholder')" spellcheck="false"></textarea>
      <span class="ui-hint">{{ $t('tamper.ignoreHint') }}</span>
    </label>

    <!-- 内置默认忽略规则（始终生效，无需配置） -->
    <div class="ui-field">
      <span class="ui-label">{{ $t('tamper.defaultIgnoreTitle') }}</span>
      <div class="default-ignore">
        <code v-for="p in defaultIgnorePatterns" :key="p" class="pat">{{ p }}</code>
      </div>
      <span class="ui-hint">{{ $t('tamper.defaultIgnoreHint') }}</span>
    </div>

    <div class="ui-field-row">
      <label class="ui-field">
        <span class="ui-label">{{ $t('tamper.backupIntervalLabel') }}</span>
        <input class="ui-input" type="number" min="1" max="10080" v-model.number="form.backup_interval_minutes" />
      </label>
      <label class="ui-field">
        <span class="ui-label">{{ $t('tamper.scanIntervalLabel') }}</span>
        <input class="ui-input" type="number" min="5" max="3600" v-model.number="form.scan_interval_seconds" />
      </label>
    </div>

    <div class="ui-actions">
      <button class="ui-btn" :disabled="saving" @click="emit('close')">{{ $t('common.cancel') }}</button>
      <button class="ui-btn primary" :disabled="saving" @click="save">
        {{ saving ? $t('common.saving') : $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
// 响应式状态与 props
import { ref, reactive } from 'vue'
// 国际化
import { useI18n } from 'vue-i18n'
// 防篡改 API：create / update
import { tamperApi } from '../../api'
// 表单保存信号：通知 TamperWindow 刷新防护站点列表
import { bumpForm } from '../../store/formBus'

const { t } = useI18n()

// task: 编辑对象（null = 新增）；candidates: 可选防护的候选站点
const props = defineProps({
  task: { type: Object, default: null },
  candidates: { type: Array, default: () => [] }
})
const emit = defineEmits(['close'])

const saving = ref(false)   // 保存中（禁用按钮防重复提交）
const error = ref('')       // 必填校验 / 后端错误信息

// 内置默认忽略规则兜底（后端可用时以返回为准）
const DEFAULT_IGNORE_PATTERNS = [
  '**/*.log', '**/*.db', '**/*.sqlite', '**/*.sqlite3', '**/*.sqlitedb',
  '**/*.db3', '**/*.sqlite-wal', '**/*.sqlite-shm', '**/*.wal',
  '**/*.shm', '**/*.tmp', '**/*.swp', '**/*.lock',
]
const defaultIgnorePatterns = ref([...DEFAULT_IGNORE_PATTERNS])

// 表单初值：编辑时回填（数组字段用换行符拼接展示），新增用默认值
const form = reactive(props.task
  ? {
      site_id: props.task.site_id,
      site_name: props.task.site_name || props.task.site_id,
      root: props.task.root || '',
      protected_files: (props.task.protected_files || []).join('\n'),
      ignore_patterns: (props.task.ignore_patterns || []).join('\n'),
      backup_interval_minutes: props.task.backup_interval_minutes,
      scan_interval_seconds: props.task.scan_interval_seconds
    }
  : {
      site_id: '',
      site_name: '',
      root: '',
      protected_files: '',
      ignore_patterns: '',
      backup_interval_minutes: 60,   // 默认 60 分钟做一次快照
      scan_interval_seconds: 15      // 默认 15 秒扫一次哈希
    })

// --- 选了候选站点后自动带出站点名与根目录，省去手填 ---
function onSiteChange() {
  const c = props.candidates.find((x) => x.site_id === form.site_id)
  if (!c) return
  form.site_name = c.name
  form.root = c.root || ''
}

// --- 保存：新增走 create，编辑走 update；成功后通知父窗口刷新并自关 ---
async function save() {
  if (saving.value) return   // 提交进行中直接退出，防止重复保存
  error.value = ''
  const body = {
    site_id: form.site_id,
    site_name: form.site_name || form.site_id,
    root: form.root,
    protected_files: (form.protected_files || '').split('\n').map((s) => s.trim()).filter(Boolean),   // 文本域按行拆回数组
    ignore_patterns: (form.ignore_patterns || '').split('\n').map((s) => s.trim()).filter(Boolean),
    backup_interval_minutes: form.backup_interval_minutes,
    scan_interval_seconds: form.scan_interval_seconds
  }
  // 前端必填校验（与后端规则一致，先拦截再提交）
  if (!body.root) { error.value = t('tamper.rootRequired'); return }
  if (body.protected_files.length === 0) { error.value = t('tamper.protectedRequired'); return }
  if (props.task) {
    if (!props.task.site_id) { error.value = t('tamper.siteRequired'); return }
    body.site_id = props.task.site_id   // 编辑态沿用原站点 id，站点本身不可改
  } else if (!body.site_id) {
    error.value = t('tamper.siteRequired')
    return
  }
  saving.value = true
  try {
    if (props.task) await tamperApi.update(props.task.site_id, body)
    else await tamperApi.create(body)
    bumpForm('tamper')   // 通知防篡改窗口重拉防护站点列表
    emit('close')
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.tamper-form-window { padding: 14px; overflow-y: auto; }
.error-box {
  color: #b91c1c;
  font-size: 12.5px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
  word-break: break-all;
}
.mono-area { font-family: ui-monospace, Menlo, Consolas, monospace; }
.default-ignore { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }
.pat {
  font-size: 10.5px;
  background: #f3f4f6;
  color: #374151;
  padding: 1px 6px;
  border-radius: 999px;
}
</style>