/* desktop.js — 桌面「窗口管理器」：所有已打开窗口的唯一状态源。

  业务背景：Graw 是类桌面系统，每个功能（网站/数据库/Docker/终端…）打开时
  就是一个独立的「窗口」。App.vue 通过 desktop.open() 打开窗口，
  Desktop.vue / Taskbar.vue / WinWindow.vue 读取 desktop.windows 渲染；
  窗口的移动/缩放是组件内部 UI 状态，这里只管「有哪些窗口、谁的层级最高、
  谁最小化/最大化」这类桌面级事实。

  关键状态：
    windows  —— 当前所有打开的窗口数组（含各自的类型、标题、位置、尺寸、层级）
    idCounter —— 自增窗口 ID 分配器（会话内唯一，供关闭/激活时定位窗口）
    zBase     —— 层叠基准值：新开/激活窗口时 +1，实现「后开/后点的窗口浮在上层」

  用法：App.vue 或桌面组件调 desktop.open(type, title, data) 打开功能窗口；
  窗口自身或任务栏调 close/activate/minimize/toggleMaximize 管理生命周期。
*/
import { reactive } from 'vue'   // 借 Vue 响应式：窗口增删/层级变化时桌面与任务栏自动重渲染

// --- 窗口系统的基础变量：ID 分配与层叠基准（模块级，全站共享） ---
let idCounter = 1  // 自增 ID 计数器：每个新窗口分到一个唯一 ID，用于关闭/聚焦定位
let zBase = 100    // 当前层叠高度基准：每开/激活一个窗口就 +1，保证新窗口永远在最上层

// --- 对外暴露：全站唯一的桌面窗口状态单例 ---
export const desktop = reactive({
  windows: [],   // 已打开的窗口集合（桌面渲染与任务栏都遍历它）
  // --- 动作说明：打开一个新功能窗口 ---
  open(type, title, data = {}) {
    const id = idCounter++   // 分配唯一窗口 ID（关闭/激活时靠它找到窗口）
    zBase += 1               // 层叠 +1：新窗口默认排在最上层
    const w = {
      id,
      type,
      title,
      x: 60 + (this.windows.length * 24) % 300,   // 按已开窗口数错位排布，避免新窗口与旧窗口完全重叠
      y: 40 + (this.windows.length * 24) % 200,   // 超出范围时取模回绕，让窗口始终落在桌面可视区内
      width: data.width || 800,                    // 打开者可在 data 里指定尺寸，缺省用默认窗口大小
      height: data.height || 520,
      active: true,                               // 新打开的窗口即为当前聚焦窗口
      minimized: false,
      maximized: false,
      zIndex: zBase,
      data,
    }
    this.windows.forEach(x => x.active = false)   // 同一时刻只允许一个窗口聚焦，先把旧的都取消
    this.windows.push(w)                          // 入栈后桌面立即渲染出新窗口
    return w                                      // 返回窗口对象，调用方可以拿到它的 id 做后续控制
  },
  // --- 动作说明：关闭窗口（从桌面移除） ---
  close(id) {
    const idx = this.windows.findIndex(w => w.id === id)
    if (idx >= 0) this.windows.splice(idx, 1)   // 从数组移除即销毁；窗口已不存在时静默忽略
  },
  // --- 动作说明：把指定窗口设为当前聚焦窗口并浮到最上层 ---
  activate(id) {
    zBase += 1                  // 点谁谁最上层：先抬高基准，再让被点窗口占住它
    this.windows.forEach(w => {
      w.active = w.id === id    // 只有被点的窗口保持聚焦，其余取消
      if (w.id === id) {
        w.zIndex = zBase        // 被点窗口的层级抬到当前最高
        w.minimized = false     // 从任务栏点击时顺带把最小化的窗口恢复显示
      }
    })
  },
  // --- 动作说明：最小化窗口（保留在任务栏，可从任务栏恢复） ---
  minimize(id) {
    const w = this.windows.find(w => w.id === id)
    if (w) w.minimized = true   // 只标记状态、不销毁窗口对象；窗口不存在时静默忽略
  },
  // --- 动作说明：在最大化 / 还原之间切换 ---
  toggleMaximize(id) {
    const w = this.windows.find(w => w.id === id)
    if (w) w.maximized = !w.maximized   // 纯取反即可，桌面依据该标记撑满或还原窗口
  },
})
