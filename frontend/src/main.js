/* Graw 前端入口：创建 Vue 应用并挂载到 index.html 的 #app 节点。
   职责：
     1. 引入桌面根组件 App、i18n 多语言插件与全局样式；
     2. createApp(App).use(i18n).mount('#app') 启动「类桌面」界面；
     3. 挂载完成后移除 index.html 中的启动加载动画（#boot-splash）。
   约定：不在本文件挂载其它全局插件（见 AGENTS.md 第 8 节）。 */

import { createApp } from 'vue'                         // Vue 核心：createApp 创建应用实例
import App from './App.vue'                             // 桌面环境根组件（登录态 + 窗口 / 任务栏 / 桌面）
import i18n, { ensureLocale } from './locales'          // vue-i18n 插件 + 语言包按需加载
import './assets/style.css'                             // 全局基础样式：深色背景、字体、reset

/**
 * 移除 index.html 中的启动加载动画。
 *
 * 背景：面板前端 JS 体积较大（Vue + ECharts/xterm + 各功能窗口分包），
 * 下载与执行期间页面此前只有空白。index.html 内置了纯 CSS 的 #boot-splash
 * 占位动画，应用挂载完成后由本函数淡出并移除，保证「加载中」与「已就绪」
 * 之间平滑过渡，不出现白屏或动画残留。
 *
 * @returns {void}
 */
function removeBootSplash() {
  const el = document.getElementById('boot-splash')
  if (!el) return                                     // 不存在（如已被移除 / 非标准外壳）直接返回
  el.classList.add('bs-hide')                         // 触发 CSS 淡出过渡（0.3s）
  // 过渡结束后再移除 DOM，避免动画中途被切断产生闪烁；
  // 用 setTimeout 兜底即可，无需监听 transitionend（元素被提前移除时同样安全）
  window.setTimeout(() => {
    if (el.parentNode) el.parentNode.removeChild(el)
  }, 320)
}

/**
 * 启动流程。
 *
 * 先确保「当前界面语言」的文案已就绪，再挂载应用：
 *   - 语言包改为按需加载（见 locales/index.js），中文为内置源语言，立即完成；
 *   - 其它语言首次启动需要下载对应语言包，先 await 可避免界面先显示中文再切换；
 *   - 加载失败不阻断启动（ensureLocale 内部已吞异常并回退中文）。
 * 挂载完成后撤下 index.html 的启动动画。
 *
 * @returns {Promise<void>}
 */
async function bootstrap() {
  await ensureLocale(i18n.global.locale.value).catch(() => false)
  try {
    createApp(App).use(i18n).mount('#app')
    removeBootSplash()
  } catch (e) {
    // 挂载失败（例如上游 chunk 加载异常）：保留启动动画而不是白屏，
    // 并把错误打到控制台，便于现场排查；用户刷新即可重试。
    console.error('[graw] 应用挂载失败：', e)
  }
}

bootstrap()
