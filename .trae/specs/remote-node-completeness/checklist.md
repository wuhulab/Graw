# Checklist

## 阶段 A：设置页
- [x] 打开设置窗口时多机区块并行加载，未被其它串行请求阻塞
- [x] 多机区块有 loading 态与「重新加载」，失败时区内展示错误并可重试
- [x] 测试连接结果在节点列表旁独立反馈区展示（成功/失败/错误详情），不再依赖编辑器内消息
- [x] 测试连接点击瞬间有行内 loading 反馈

## 阶段 B：远端系统监控
- [x] `_overview_sync/_network_sync/_diskio_sync` 在 `node_manager.is_remote()` 时返回 `_remote_*` 数据
- [x] WebSocket 生产者与 HTTP `/overview`/`/network`/`/diskio` 在远程节点返回远端指标
- [x] 远端监控存在覆盖单元测试且通过（test_remote_monitoring 7 用例）

## 阶段 C：应用远程能力门控
- [x] 定义了远程能力常量与 `reject_if_local_remote` 守卫（中间件按路径前缀统一拦截）
- [x] host 类后端模块（svcmonitor/files 迁移 + process/docker/disks/logs/firewall/terminal/system 已 node-aware）使用节点感知封装
- [x] local 类后端路由在 `is_remote()` 时返回 403「该功能仅本机节点可用」
- [x] nodes 路由与 system 监控在本轮守卫中始终可用（TestClient 验证）
- [x] 前端 shortcuts 均有 `remoteCap` 标记（host 缺省 / local 显式）
- [x] `openWindow` 在远程节点打开 local 类应用时拦截并提示
- [x] 远程节点下 local 类桌面快捷方式被过滤（visibleShortcuts）
- [x] locale 文案键补齐（en/zh-CN nodes 块加 reload/loadingNodes/localOnlyOnRemote，其余 fallback 到 zh-CN）
- [x] `check-imports.mjs` 覆盖 `remoteCap` 引用完整性自查

## 阶段 D：验证与回归
- [x] 前端 `check-imports.mjs` 全绿、`pnpm run build` 通过、无白屏
- [x] 后端相关单测通过（remote_monitoring / remote_cap / webmode / ssh_auth_fallback / podman_probe）
- [x] 本机节点下所有应用仍可用、行为与改造前一致（remote_cap 中间件本机节点放行返回正常鉴权）