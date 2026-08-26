/*
  这个文件是「网络储存卡片该刷新了」的一声招呼——跨窗口的变更通知铃。

  业务背景：网络储存（挂载远端对象存储/网络盘，用于备份落盘等场景）的
  连接列表显示在 NetStorageWindow.vue，而新增/编辑连接是另一个独立桌面窗口
  NetStorageFormWindow.vue。两个窗口彼此独立，子窗口保存后无法直接通知列表窗口。

  所以这里只存一个数字当信号：谁改动了网络储存配置就 +1，
  列表窗口 watch 到变化后重新向后端 /api/netstorage 拉一遍。
  真实数据（含访问密钥等敏感字段）始终以后端为准，前端不在这里另存一份。

  用法：表单窗口保存/删除成功后调 notifyNetStorageChanged()；
  列表窗口 watch(nsVersion, ...) 后重新 load()。
*/
import { ref } from 'vue'                            // 用 ref 包一个数字，才能被列表窗口 watch 到变化

// 共享信号：网络储存连接新增/修改/删除后自增版本号，
// 供网络储存主窗口（NetStorageWindow.vue）watch 到后自动刷新卡片列表。
// 由于添加/编辑已改到独立窗口（NetStorageFormWindow.vue），
// 主窗口需感知跨窗口的数据变化。
// --- 对外暴露：网络储存配置的变更版本号（只是计数，不含任何连接明细） ---
export const nsVersion = ref(0)

// --- 通知全站：网络储存配置已被改动，请重新拉取卡片列表 ---
export function notifyNetStorageChanged() {
  nsVersion.value += 1                               // 用递增而非布尔取反，连续多次改动也不会两两抵消掉通知
}