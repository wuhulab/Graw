import { reactive, watch } from 'vue'

const STORAGE_KEY = 'graw_settings'

const defaults = {
  showTaskbarText: true,
  taskbarTextOnly: false,
  // 界面语言（locale code，与 locales/index.js 中 LANGUAGES 对应）
  locale: 'zh-CN',
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
  }),
  (val) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(val))
  },
  { deep: true }
)
