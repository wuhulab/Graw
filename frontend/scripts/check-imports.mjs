// 前端白屏回归检查：App.vue 中所有 markRaw(<标识符>) 引用的组件/图标
// 必须已在文件中 import，否则编译不报错、运行时抛 ReferenceError 导致整页白屏。
//
// 背景（2026-08-20 线上事故）：
//   shortcuts 里新增桌面快捷方式时，用了 markRaw(DatabaseBackup)，但并发编辑
//   覆盖使 lucide 的 import 行丢失 DatabaseBackup —— vite build 通过（未声明
//   的标识符在打包时不报错），运行时 setup() 抛 `ReferenceError: DatabaseBackup
//   is not defined`，Vue 挂载中断，打开面板只有白屏。
//
// 运行：node scripts/check-imports.mjs
// 建议纳入每次前端构建前的检查流程。
import fs from 'node:fs'
import path from 'node:path'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
import { fileURLToPath } from 'node:url'

const root = path.resolve(__dirname, '..')
const src = path.join(root, 'src')
const appVue = path.join(src, 'App.vue')
const content = fs.readFileSync(appVue, 'utf8')

// 1. 收集所有已导入标识符（含 as 别名）
const imported = new Set()
const importRe = /import\s+(?:\{([^}]*)\}|([A-Za-z_$][\w$]*))\s*(?:,\s*\{([^}]*)\})?\s*from\s+['"][^'"]+['"]/g
let m
while ((m = importRe.exec(content)) !== null) {
  const named = (m[1] || m[3] || '').split(',').map((s) => s.trim()).filter(Boolean)
  for (const item of named) {
    const parts = item.split(/\s+as\s+/)
    imported.add(parts[parts.length - 1].trim()) // 用别名（无别名则原名）
  }
  if (m[2]) imported.add(m[2].trim())
}

// 2. 收集所有 markRaw(<标识符>) 引用
const used = new Set()
const markRawRe = /markRaw\(\s*([A-Za-z_$][\w$]*)\s*\)/g
while ((m = markRawRe.exec(content)) !== null) {
  used.add(m[1])
}

// 3. 比对
const missing = [...used].filter((name) => !imported.has(name))
console.log(`App.vue 已导入标识符：${imported.size} 个；markRaw 引用：${used.size} 个`)
if (missing.length === 0) {
  console.log('✔ 所有 markRaw 引用的标识符均已 import，无白屏风险')
  process.exit(0)
}
console.log('\n✘ 以下标识符被 markRaw 引用但未 import（会导致运行时白屏）：')
for (const name of missing) {
  console.log(`  - ${name}`)
}
process.exit(1)
