# Pull Request 描述

> 提交 PR 前请自查 [CONTRIBUTING.md](../CONTRIBUTING.md)。
> 保持 PR 聚焦：一次 PR 解决一个问题。

## 变更类型

- [ ] 新功能 (feat)
- [ ] 修复 (fix)
- [ ] 文档 (docs)
- [ ] 样式 / 重构 (style / refactor)
- [ ] 测试 (test)
- [ ] 杂项 / 构建 (chore / build)

## 变更说明

<!-- 简述这个 PR 做了什么、为什么这样做 -->

## 关联 Issues

<!-- 关闭关联 Issue: Fixes #123 -->

## 测试情况

- [ ] 后端相关改动：`backend` 无语法错误，相关单测通过
      （`cd backend && python -m pytest test_xxx.py -q`）
- [ ] 前端相关改动：`npm run build` 通过
- [ ] 已补充/更新测试用例（如有）
- [ ] 手动验证过的场景与结果

## 自查清单

- [ ] 已通读 `AGENTS.md`，改动符合鉴权 / 持久化 / 多节点约定
- [ ] 新增后端接口已在 `main.py` 注册，且鉴权依赖（`PROTECTED`/`ADMIN`）选择正确
- [ ] 前端界面文案走 `src/locales/` i18n，无硬编码
- [ ] 未提交任何敏感信息（`backend/data/`、密码、token）

## 截图（可选）

<!-- UI 变更请附截图，便于 review -->