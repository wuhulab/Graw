// requestNode.js - 请求级目标节点的轻量共享（统一面板兼容）
// api.js 与 App.vue/nodes store 都读取/写入它。独立模块避免 api.js ↔ nodes.js 循环依赖。
// 值由 App.vue 在聚焦窗口时写入（来自 settings.unifiedPanel），api.js 拦截器读取后
// 附加 X-Graw-Node 请求头，后端据此把业务请求路由到对应子节点。
let activeNode = ''

export function setRequestNode(nodeId = '') {
  activeNode = nodeId || ''
}

export function getRequestNode() {
  return activeNode
}