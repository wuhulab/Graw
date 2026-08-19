import { ref } from 'vue'

// 共享信号：网络储存连接新增/修改/删除后自增版本号，
// 供网络储存主窗口（NetStorageWindow.vue）watch 到后自动刷新卡片列表。
// 由于添加/编辑已改到独立窗口（NetStorageFormWindow.vue），
// 主窗口需感知跨窗口的数据变化。
export const nsVersion = ref(0)

export function notifyNetStorageChanged() {
  nsVersion.value += 1
}