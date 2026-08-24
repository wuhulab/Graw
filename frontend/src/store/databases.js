import { ref } from 'vue'

// 共享信号：数据库连接新增/修改/删除后自增版本号，
// 供数据库主窗口（DatabaseWindow.vue）watch 到后自动刷新连接列表。
// 由于添加/编辑连接已改到独立窗口，主窗口需要感知跨窗口的数据变化。
export const dbVersion = ref(0)

export function notifyDatabasesChanged() {
  dbVersion.value += 1
}
