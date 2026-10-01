/* Graw 前端 ESLint 配置（ESLint 9+ / 10 扁平配置 flat config）。
 *
 * 目标：在 CI 中拦截「真正的错误」（未定义变量、语法误用、Vue 模板规则等），
 * 而不是与 Prettier 争夺排版风格，因此：
 *   - 使用 @eslint/js 官方推荐规则 + eslint-plugin-vue 的 flat/essential 规则集；
 *   - 叠加 eslint-config-prettier/flat，关闭所有可能与 Prettier 冲突的格式类规则；
 *   - 通过 languageOptions.globals 声明浏览器环境全局量，避免 no-undef 误报。
 *
 * 说明：项目源码使用 2 空格缩进、单引号、不加分号（见根目录 .editorconfig），
 * 这些排版由 Prettier 统一负责，ESLint 不重复约束。 */

import js from '@eslint/js'                          // ESLint 官方推荐规则（no-undef / no-unused-vars 等）
import globals from 'globals'                        // 各运行环境预定义全局变量（browser / node）
import pluginVue from 'eslint-plugin-vue'            // Vue 单文件组件的解析器与规则集
import configPrettier from 'eslint-config-prettier/flat'   // 关闭与 Prettier 冲突的格式类规则

export default [
  // 构建产物与依赖目录无需检查（dist 由 vite build 生成，不应纳入版本控制的内容）
  {
    ignores: ['dist/**', 'node_modules/**', 'public/**']
  },

  // 基础规则集：JS 官方推荐 + Vue3 必要规则
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],

  // 业务源码：浏览器环境，ES Module
  {
    files: ['src/**/*.{js,mjs,vue}'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: {
        ...globals.browser                            // window / document / localStorage 等
      }
    },
    rules: {
      // 未使用变量视为错误（可帮助发现死代码），但放行以下常见无害场景：
      //   args: 'none'            —— 函数签名占位参数（回调/接口约定）不报错
      //   caughtErrors: 'none'    —— catch 块中为说明原因而保留的 e 不报错
      //   ignoreRestSiblings: true —— const { a, ...rest } = obj 中 a 用于剔除字段
      'no-unused-vars': ['error', {
        args: 'none',
        caughtErrors: 'none',
        ignoreRestSiblings: true
      }],
      // 空 catch 允许存在：项目多处刻意静默非关键异常（如 localStorage 写入失败、
      // 终端 reset 失败），语义是「尽力而为、失败不影响主流程」，已在代码内注释说明。
      'no-empty': ['error', { allowEmptyCatch: true }],
      // 组件命名采用项目既有的单文件名（Desktop / Taskbar / Login），
      // 强制多词命名会造成大范围重命名，收益有限，故关闭该约定类规则。
      'vue/multi-word-component-names': 'off'
    }
  },

  // 构建脚本 / Node 侧脚本：需要 node 全局量（process / console / __dirname 等）
  {
    files: ['scripts/**/*.{js,mjs}', '*.config.js', 'vite.config.js'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: {
        ...globals.node
      }
    }
  },

  // 必须放在最后：关闭上面规则集中所有与 Prettier 冲突的格式类规则
  configPrettier
]