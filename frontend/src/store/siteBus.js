import { ref } from 'vue'

// 站点数据版本号：独立「站点编辑」窗口保存成功后将版本号 +1，
// 「网站」列表窗口监听到变化即重新 load()，从而与独立窗口保持同步。
export const siteRevision = ref(0)

export function bumpSites() {
  siteRevision.value += 1
}