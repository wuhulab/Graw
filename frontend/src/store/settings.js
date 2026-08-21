import { reactive, watch } from 'vue'

const STORAGE_KEY = 'graw_settings'

const defaults = {
  showTaskbarText: true,
  taskbarTextOnly: false,
  // 界面语言（locale code，与 locales/index.js 中 LANGUAGES 对应）
  locale: 'zh-CN',
  // 统一面板兼容：开启后每个应用窗口绑定打开时对应的节点，聚焦窗口即操作该节点
  unifiedPanel: false,
}

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...defaults }
    return { ...defaults, ...JSON.parse(raw) }
  } catch {
    return { ...defaults }
  }
}

export const settings = reactive(load())

watch(
  () => ({
    showTaskbarText: settings.showTaskbarText,
    taskbarTextOnly: settings.taskbarTextOnly,
    locale: settings.locale,
    unifiedPanel: settings.unifiedPanel,
  }),
  (val) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(val))
  },
  { deep: true }
)
