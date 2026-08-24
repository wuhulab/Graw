// 应用商店 compose 编辑器跨窗口共享状态
// 安装窗口与 compose 编辑器窗口（独立窗口）通过此响应式对象交换内容，
// 无需依赖 API 落盘。rev 在每次保存时 +1，供安装窗口监听内容变化。
import { reactive } from 'vue'

export const appStoreComposeState = reactive({
  appId: null,   // 当前编辑的应用 id
  content: null, // 编辑后的 docker-compose.yml 内容
  rev: 0         // 保存次数，用于触发安装窗口的响应式更新
})
