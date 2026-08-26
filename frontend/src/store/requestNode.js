// requestNode.js - 请求级目标节点的轻量共享（统一面板兼容）
// api.js 与 App.vue/nodes store 都读取/写入它。独立模块避免 api.js ↔ nodes.js 循环依赖。
// 值由 App.vue 在聚焦窗口时写入（来自 settings.unifiedPanel），api.js 拦截器读取后
// 附加 X-Graw-Node 请求头，后端据此把业务请求路由到对应子节点。
let activeNode = ''   // 当前请求级目标节点 id；空字符串 = 跟随全局「当前管理主机」

// --- 动作说明：设置请求级目标节点（App.vue 聚焦窗口时写入） ---
export function setRequestNode(nodeId = '') {
  activeNode = nodeId || ''   // 空值统一归并为 ''，保证「未绑定」只有一个表示
}

// --- 动作说明：读取当前请求级目标节点（api.js 拦截器取它拼 X-Graw-Node 头） ---
export function getRequestNode() {
  return activeNode
}