import { reactive, readonly } from 'vue'
import { nodesApi } from '../api'
import { setRequestNode } from './requestNode'

// 多节点（多机）全局状态：节点列表 + 当前管理的主机。
// 「当前主机」由后端持久化；前端切换后同步刷新本地缓存，供各处展示。
export const nodes = reactive({
  list: [],
  currentId: 'local',
  loaded: false,
  // 「统一面板兼容」：当前聚焦窗口绑定的目标节点（全局请求的默认目标节点）。
  // 为空时所有请求走全局 currentId；聚焦某窗口时更新为该窗口打开时对应的节点。
  activeWindowNode: '',
})

// 当前管理主机是否远程节点（供 UI 标记"远程/本机"）
export const isRemote = () => {
  const cur = nodes.list.find((n) => n.id === nodes.currentId)
  return cur ? cur.type === 'ssh' : false
}

// 当前管理主机的显示名
export const currentNodeName = () => {
  const cur = nodes.list.find((n) => n.id === nodes.currentId)
  return cur ? cur.name : 'local'
}

export async function refreshNodes() {
  const data = await nodesApi.list()
  nodes.list = data.nodes || []
  nodes.currentId = data.current || 'local'
  nodes.loaded = true
  return data
}

export async function setCurrentNode(nodeId) {
  const data = await nodesApi.setCurrent(nodeId)
  nodes.currentId = data.current || nodeId
  // 切换主机后立即让请求目标回到「跟随全局新主机」的默认值，
  // 避免在打开/聚焦新窗口之前，后续请求仍沿用切换前旧窗口绑定的节点
  setRequestNode('')
  return data
}

// 供只读消费（如需要 reactive 的衍生）
export function useNodes() {
  return readonly(nodes)
}