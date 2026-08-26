/* Graw 前端构建与开发服务器配置（Vite）。
   职责：
   - 启用 @vitejs/plugin-vue 编译 .vue 单文件组件；
   - 开发服务器监听 5173（前端），并把 /api（含 WebSocket，ws:true）反向代理到
     后端 FastAPI（:8000），使开发期前端像生产一样同源调用 /api/*；
   - 生产构建输出到 dist/，由后端静态托管（见 AGENTS.md 第 6.3 / 7.6 节）。 */

import { defineConfig } from 'vite'                     // Vite 配置入口：defineConfig 提供类型提示
import vue from '@vitejs/plugin-vue'                    // 编译 Vue 单文件组件（<template>/<script>/<style>）

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,                                        // 前端开发服务器端口（npm run dev 访问地址）
    proxy: {
      '/api': {
        target: 'http://localhost:8000',               // 后端 FastAPI 地址（开发期）
        changeOrigin: true,                            // 改写 Host 头，使后端视为同源请求
        ws: true                                       // 同时代理 WebSocket（监控流 / 终端）
      }
    }
  },
  build: {
    outDir: 'dist'                                     // 构建产物目录，后端 main.py 自动挂载（SPA 回退）
  }
})
