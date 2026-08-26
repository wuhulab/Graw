/* Graw 前端入口：创建 Vue 应用并挂载到 index.html 的 #app 节点。
   仅负责三件事：引入桌面根组件 App、引入 i18n 多语言插件、引入全局样式，
   随后 createApp(App).use(i18n).mount('#app') 启动「类桌面」界面。
   约定：不在本文件挂载其它全局插件（见 AGENTS.md 第 8 节）。 */

import { createApp } from 'vue'                         // Vue 核心：createApp 创建应用实例
import App from './App.vue'                             // 桌面环境根组件（登录态 + 窗口 / 任务栏 / 桌面）
import i18n from './locales'                            // vue-i18n 多语言插件（zh-TW/ja/ko/ru…）
import './assets/style.css'                             // 全局基础样式：深色背景、字体、reset

// 启动应用：装配 i18n 后渲染到页面 #app 容器
createApp(App).use(i18n).mount('#app')
