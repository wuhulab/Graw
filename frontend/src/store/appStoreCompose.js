/*
  这个文件是「应用商店 compose 内容」的跨窗口临时中转站。

  业务背景：在应用商店里安装一个应用，本质就是执行一份 docker-compose.yml。
  用户可能想在安装前先改一改这份编排（换端口、改挂载目录、加环境变量）。
  但「安装窗口」和「compose 编辑器」是桌面上两个各自独立的窗口，彼此不是
  父子组件，没法用 props/emit 传值；而这份草稿又还没确定要安装，不该提前
  写到后端。于是就用这一个模块级的响应式对象当「共享黑板」：
  编辑器写进去，安装窗口读出来。

  三个状态字段的业务含义：
    appId   —— 用户当前正在编辑哪一个应用（防止 A 应用的草稿串到 B 应用）
    content —— 用户改完的 docker-compose.yml 全文，安装时按这份内容执行
    rev     —— 保存计数器。安装窗口 watch 它，用户在编辑器点一次保存就 +1，
               安装窗口据此知道「内容变了，要重新读一遍」

  用法：编辑器窗口保存时写 appId/content 并把 rev 加一；安装窗口 watch rev，
  变化后从 content 取最新编排内容。
*/
// 应用商店 compose 编辑器跨窗口共享状态
// 安装窗口与 compose 编辑器窗口（独立窗口）通过此响应式对象交换内容，
// 无需依赖 API 落盘。rev 在每次保存时 +1，供安装窗口监听内容变化。
import { reactive } from 'vue'                    // 借 Vue 的响应式能力，让跨窗口写入能自动触发对方重渲染

// --- 对外暴露：应用商店 compose 草稿的唯一共享实例（模块单例，全站只有一份） ---
export const appStoreComposeState = reactive({
  appId: null,   // 当前编辑的应用 id
  content: null, // 编辑后的 docker-compose.yml 内容
  rev: 0         // 保存次数，用于触发安装窗口的响应式更新
})
