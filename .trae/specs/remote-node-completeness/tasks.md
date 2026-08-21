# Tasks

## 任务说明
SSH 节点为「裸服务器」（未安装 Graw 代理）。因此「全部应用远端化」按诚实拆分：**host 类（系统态）应用真正切换到远端，local 类（面板自身管理项）应用在远端节点下禁用并提示**。两阶段并行推进，互不阻塞。

## 阶段 A：设置页（问题 1、2）
- [x] Task A1：`SettingsWindow` 打开时并行加载各区块，多机管理区加 loading 态与「重新加载」按钮，不再被其它慢请求阻塞
  - [x] 子项 A1.1：将 `onMounted` 中的串行 await 改为并行（`Promise.all` / 不阻塞），多机数据即时加载
  - [x] 子项 A1.2：多机区块增加 `loadingNodes` 态与手动刷新入口，失败时区内展示错误并能重试
- [x] Task A2：测试连接增加独立反馈区（节点列表旁），不再依赖编辑器内的 `editorMsg`
  - [x] 子项 A2.1：在节点列表区域新增 `connMsg`/`connMsgType` 反馈行，展示成功/失败与后端错误
  - [x] 子项 A2.2：保留 `testingId` 行内 loading 态
  - [x] 子项 A2.3：`testNode()` 结果写入独立反馈区

## 阶段 B：远端系统监控（问题 3）
- [x] Task B1：`system.py` 概览/网络/磁盘 IO 接入远端分支
  - [x] 子项 B1.1：`_overview_sync()` 增加 `if node_manager.is_remote(): return _remote_overview()`
  - [x] 子项 B1.2：`_network_sync()` 增加 `is_remote()` 分支返回 `_remote_network()`
  - [x] 子项 B1.3：`_diskio_sync()` 增加 `is_remote()` 分支返回 `_remote_diskio()`
  - [x] 子项 B1.4：验证 WebSocket 生产者 `_collect_sync()` 与 HTTP `/overview`/`/network`/`/diskio` 在远程节点返回远端指标
- [x] Task B2：新增/运行远端监控单元测试（模拟 `is_remote()=True`），断言 `_remote_*` 输出结构

## 阶段 C：应用远程能力门控（问题 4）

### 后端：host 类模块迁移 + local 类模块守卫
- [x] Task C1：定义远程能力常量与守卫工具
  - [x] 子项 C1.1：在 `app/remote_cap.py` 定义 `LOCAL_PREFIX`（local 类路径前缀集合）与 `is_local_path`
  - [x] 子项 C1.2：`remote_cap.reject_if_local_remote(path)`：`node_manager.is_remote()` 时对 local 类路径拦截
  - [x] 子项 C1.3：在 `main.py` 以 HTTP 中间件统一拦截（按路径前缀），返回 `403 "该功能仅本机节点可用"`，避免逐个路由散改
- [x] Task C2：host 类模块改为 node_manager 节点感知封装（保证切换节点真正操作远端）
  - [x] 子项 C2.1：`svcmonitor.py` 改用 `node_manager.host_cmd`
  - [x] 子项 C2.4：确认 process / docker / disks / logs / firewall / terminal / system 已走 node_manager；`files.py`（原裸 hostfs 大模块，问题4的「大工程」）核心 path 映射 + list/read/write/remove/mkdir/rename 迁移到 node_manager，并新增 `isdir`/`listdir` 原语
  - [x] 子项 C2.说明：`webstats.py`/`waf.py`/`sites.py`/`webserver.py` 属 **local 类**（管理面板自身 nginx 配置/站点），不做远端化，交由 C3 守卫在远端节点禁用
- [x] Task C3：local 类路由增加远程守卫（深度防御）
  - [x] 子项 C3.1：main.py 中间件对 sites/databases/cron/ssl/protection/runtime/tasks/backup/appstore/netstorage/notify/uptime/webstats/rewrite/siteopts/waf/tamper/phpversions/ftpusers/sshkeys/certcheck/panelbackup/update/webmode/loginlog 在远端节点返回 `403`
  - [x] 子项 C3.2：`nodes` 路由与 `system` 监控不在 LOCAL_PREFIX 中，始终可用（TestClient 验证）

### 前端：远程能力标记 + 门控
- [x] Task C4：给 `App.vue` shortcuts 增加 `remoteCap: 'host' | 'local'` 标记并按角色/能力过滤
  - [x] 子项 C4.1：为全部窗口按 host/local 分类加 `remoteCap` 字段（host 类缺省，其余标记 'local'）
  - [x] 子项 C4.2：`openWindow()` 在 `isCurrentHostRemote()` 且 `remoteCap==='local'` 时拦截并 `alert(t('nodes.localOnlyOnRemote'))`
  - [x] 子项 C4.3：`visibleShortcuts` 对远端节点过滤 local 类桌面快捷方式
  - [x] 子项 C4.4：复用原生 alert 轻提示（设置窗口等常用 UI 仍可用，故保留打开入口）
  - [x] 子项 C4.5：在 App.vue 引入 `useI18n`/`t`，并基于 `nodesStore.currentId` 提供响应式 `isCurrentHostRemote`
- [x] Task C5：多语言文案补齐远程禁用提示与相关键
  - [x] 说明：与既有设计一致，仅 `en.js`/`zh-CN.js` 含 `nodes:` 块；其余 9 个 locale 依赖 `fallbackLocale: 'zh-CN'`。已在两文件 nodes 块补齐 `reload`/`loadingNodes`/`localOnlyOnRemote`
- [x] Task C6：`check-imports.mjs` 新增 `remoteCap` 引用合法性自查（仅允许 local 或缺省），运行通过

## 阶段 D：验证与回归
- [x] Task D1：`node scripts/check-imports.mjs` 全绿（含 remoteCap 自查），`pnpm run build` 前端构建通过、无白屏
- [x] Task D2：后端单测通过（test_remote_monitoring 7、test_remote_cap 6、test_webmode_unit、test_ssh_auth_fallback 6、test_podman_probe 4）
- [x] Task D3：本机节点回归：remote_cap 中间件在本机节点放行（local 类进入正常鉴权 401 而非 403），行为与改造前一致

# Task Dependencies
- Task C2、C3、C4 相互独立，可并行；均依赖 C1 先定义守卫/常量。
- Task D1、D2、D3 依赖 A/B/C 全部完成。
- Task C5 依赖 C4 确定的文案键。