// 临时校验脚本：提取组件中引用的 i18n key，与 zh-CN.js 对比，报告缺失 key
// 运行：node scripts/check-i18n-keys.mjs
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const src = path.join(root, 'src')

// 1. 加载 zh-CN.js 中已定义的 key 集合
const localePath = path.join(src, 'locales', 'zh-CN.js')
const localeSrc = fs.readFileSync(localePath, 'utf8')
// 将 export default {...} 转成可执行的函数并求值
const localeModule = new Function(`${localeSrc.replace('export default', 'return')}`)()

const definedKeys = new Set()
function collect(obj, prefix) {
  for (const [k, v] of Object.entries(obj)) {
    const p = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      collect(v, p)
    } else {
      definedKeys.add(p)
    }
  }
}
collect(localeModule, '')
console.log(`zh-CN.js 已定义 key 数量：${definedKeys.size}`)

// 2. 扫描目标组件中引用的 key
const files = [
  'LogsWindow.vue', 'FilesWindow.vue', 'ProcessWindow.vue', 'DisksWindow.vue',
  'UserWindow.vue', 'TerminalWindow.vue', 'ProtectionWindow.vue',
  'RuntimeWindow.vue', 'RuntimeCreateWindow.vue',
]
const referenced = new Map() // key -> [file]
const missing = new Map() // key -> [file]
const keyRe = /(?:\$t|\bt)\(\s*['"`]([a-zA-Z]+\.[a-zA-Z]+)['"`]/g

for (const f of files) {
  const filePath = path.join(src, 'components', 'windows', f)
  const content = fs.readFileSync(filePath, 'utf8')
  let m
  while ((m = keyRe.exec(content)) !== null) {
    const key = m[1]
    if (!referenced.has(key)) referenced.set(key, [])
    if (!referenced.get(key).includes(f)) referenced.get(key).push(f)
    if (!definedKeys.has(key)) {
      if (!missing.has(key)) missing.set(key, [])
      if (!missing.get(key).includes(f)) missing.get(key).push(f)
    }
  }
}

console.log(`组件引用的唯一 key 数量：${referenced.size}`)
console.log('\n=== 缺失的 key（组件中引用但 zh-CN.js 未定义）===')
if (missing.size === 0) {
  console.log('无缺失 key ✔')
} else {
  for (const [key, filesArr] of [...missing.entries()].sort()) {
    console.log(`  ${key}  <- ${filesArr.join(', ')}`)
  }
}
