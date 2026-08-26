/* nodes.js — 多节点（多机）管理的全局状态。

  业务背景：Graw 不仅能管理本机，还能把通过 SSH 接入的其它主机作为「子节点」
  纳入统一面板。所有请求默认打到「当前管理主机」；开启「统一面板兼容」后，
  每个窗口还可绑定自己打开时对应的节点，聚焦哪个窗口就打哪台机器
  （通过 api.js 拦截器给请求附加 X-Graw-Node 头实现）。

  关键状态：
    list             —— 已配置的节点列表（本机 + SSH 子节点）
    currentId        —— 当前管理主机 id（'local' 表示本机），由后端持久化
    activeWindowNode —— 当前聚焦窗口绑定的目标节点（统一面板兼容下非空）

  用法：App.vue 启动时调 refreshNodes() 拉列表；「多机管理」窗口切换主机时调
  setCurrentNode()；ui 用 isRemote()/currentNodeName() 标记远程与显示名称。
*/
import { reactive, readonly } from 'vue'            // 响应式状态 + 只读包装（防止消费方意外改状态）
import { nodesApi } from '../api'                   // 节点增删改查接口（自动携带 Bearer 鉴权）
import { setRequestNode } from './requestNode'      // 请求级目标节点：切换主机后复位，避免请求打到旧窗口绑定的节点

// --- 对外暴露：全站唯一的节点状态单例 ---
export const nodes = reactive({
  list: [],               // 节点列表（[{ id, name, type: 'local'|'ssh', agent_enabled, ... }]）
  currentId: 'local',     // 当前管理主机 id：决定面板所有请求默认打到哪台机器
  loaded: false,          // 是否已从后端拉取过节点列表（避免重复拉取 / 提前渲染空态）
  // 「统一面板兼容」：当前聚焦窗口绑定的目标节点（全局请求的默认目标节点）。
  // 为空时所有请求走全局 currentId；聚焦某窗口时更新为该窗口打开时对应的节点。
  activeWindowNode: '',   // 聚焦窗口的节点绑定（空 = 跟随全局 currentId）
})

// --- 动作说明：判断当前管理主机是否为远程节点（供 UI 显示「远程/本机」角标） ---
export const isRemote = () => {
  const cur = nodes.list.find((n) => n.id === nodes.currentId)
  return cur ? cur.type === 'ssh' : false   // 找不到节点（如列表还没加载）时按本机处理
}

// --- 动作说明：获取当前管理主机的显示名 ---
export const currentNodeName = () => {
  const cur = nodes.list.find((n) => n.id === nodes.currentId)
  return cur ? cur.name : 'local'   // 列表未加载或节点已删除时回退为「local」展示
}

// --- 动作说明：拉取节点列表并回填全局状态（App.vue 启动时调用） ---
export async function refreshNodes() {
  const data = await nodesApi.list()
  nodes.list = data.nodes || []              // 后端没返回节点时给空列表，避免渲染报错
  nodes.currentId = data.current || 'local'  // 后端持久化的当前主机为准；异常时回退本机
  nodes.loaded = true                        // 标记加载完成，界面可结束「加载中」占位
  return data
}

// --- 动作说明：切换当前管理主机（后端持久化，前端同步本地状态） ---
export async function setCurrentNode(nodeId) {
  const data = await nodesApi.setCurrent(nodeId)
  nodes.currentId = data.current || nodeId
  // 切换主机后立即让请求目标回到「跟随全局新主机」的默认值，
  // 避免在打开/聚焦新窗口之前，后续请求仍沿用切换前旧窗口绑定的节点
  setRequestNode('')   // 复位窗口级节点绑定：新主机接管前不许残留旧窗口的请求目标
  return data
}

// --- 动作说明：以只读视图暴露节点状态（防消费方意外修改全局单例） ---
export function useNodes() {
  return readonly(nodes)
}