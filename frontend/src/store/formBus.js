/*
  这个文件是「表单类弹窗拆分独立窗口」后的消息总线——通知各列表窗口刷新。

  业务背景：原先新增/编辑某条数据（防火墙规则、备份任务、通知渠道等）以内嵌
  modal 弹窗承载，点灰色遮罩即关闭容易丢输入。现将表单拆成独立桌面窗口：
  独立窗口提交成功后无法直接刷新父窗口列表，于是这里用「作用域版本号」做信号：
  表单窗口保存成功就 bump(scope)，父窗口 watch(formBus[scope]) 后重新拉列表。

  用法：表单窗口保存成功后调用 bumpForm('firewall')；
       父（列表）窗口 watch(() => formBus.firewall, load) 重新加载。

  与 siteBus.js（站点专用）思路一致：版本号递增而非布尔取反，连续多次
  保存不会互相抵消通知。
*/
import { reactive } from 'vue'   // reactive 对象便于各窗口按 scope 读取版本号

// 各作用域的数据版本号：{ firewall: 0, backup: 0, cron: 0, notify: 0, frp: 0, ... }
// 首次 read 前不存在，均在取用时以 undefined 兜底为 0（见 bumpForm）。
export const formBus = reactive({})

// --- 通知全站：某个 scope 的数据已保存，请持有该 scope 的列表窗口重新拉取 ---
export function bumpForm(scope) {
  formBus[scope] = (formBus[scope] || 0) + 1
}