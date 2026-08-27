/* fileClipboard.js — 文件管理器「跨窗口共享剪贴板」。

  业务背景：文件管理支持类 Windows 的复制/粘贴。多个文件管理窗口可以同时打开
  （例如统一面板兼容模式下每个节点一个窗口），剪贴板必须跨窗口共享——在窗口 A
  里复制，切到窗口 B 粘贴。因此这里用响应式单例 store（与项目其它全局状态同款
  模式，见 store/settings.js 等），所有文件管理窗口读写同一份状态。

  用法：
    import { fileClipboard, setClipboard, clearClipboard } from '../store/fileClipboard'
    setClipboard(['/a/1.txt', '/a/2.txt'], 'copy')   // 复制选中路径
    pasteTo(目标目录) 时读取 fileClipboard.items 逐个调用 /api/files/copy
*/
// 响应式单例：items 为源路径数组，op 当前仅支持 copy（留作将来扩展 cut/move）
import { reactive } from 'vue'

export const fileClipboard = reactive({
  items: [],   // 已复制（待粘贴）的源路径列表
  op: 'copy',
})

// 复制：把一组路径写入剪贴板（覆盖旧内容）
export function setClipboard(paths, op = 'copy') {
  fileClipboard.items.splice(0, fileClipboard.items.length)
  fileClipboard.items.push(...(paths || []))
  fileClipboard.op = op
}

// 清空剪贴板
export function clearClipboard() {
  fileClipboard.items.length = 0
}