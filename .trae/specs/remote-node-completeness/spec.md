# 远程节点管理完整性 Spec

## Why
切换 SSH 节点后，节点管理并不完整：设置页多机管理打开缓慢、测试连接无任何反馈、系统概览/实时监控显示本机数据而非远端数据、大部分应用切换节点后仍操作本机。目标是在「SSH 节点已可用（nodes.json 已有节点）」的前提下，让多机管理在切换节点后可靠、一致地作用于所管理的远端主机，且对无法作用到裸远端的应用给出明确禁用与提示。

## 现状与根因（已定位）
1. **设置页慢（问题1）**：`SettingsWindow.onMounted` 串行 await 多项请求，多机管理区域要等前面请求完成才显示，且无 loading 反馈。且后端 `webserver.status()`（`available()`）会做本机 `host_which/host_cmd` 探测，叠加串行拉取放大了延迟。
2. **测试连接无反馈（问题2）**：`SettingsWindow.testNode()` 把结果写进 `editorMsg`，该 div 位于 `v-if="showEditor"` 的编辑器内部；非编辑状态下编辑器隐藏，消息不显示。
3. **监控不对（问题3）**：`system.py` 已实现 `_remote_overview/_remote_network/_remote_diskio/_remote_info`，但 `_overview_sync()/_network_sync()/_diskio_sync()` 未按 `node_manager.is_remote()` 分支，后端实时指标 WebSocket（`_collect_sync()`）与 HTTP `/overview` 等接口在远程节点下仍读本机 psutil。仅 `_info_sync()` 有分支，故进程/系统信息/主机名对、CPU/内存/磁盘/网络不对。
4. **应用显示本机内容（问题4）**：大量后端模块直接 `import hostfs` 或裸调 `subprocess/psutil`/读 `backend/data/*.json`，未走 `node_manager` 的节点感知封装（`host_cmd/host_shell/host_which/host_path` 与文件原语）。而 `node_manager.host_cmd` 等本身在本地节点回落到 `hostfs`、远程节点走 SSH，因此将这些模块改为从 `node_manager` 取底层操作是安全的迁移路径。SSH 节点是「裸服务器」（未安装 Graw 代理），只有「系统态」类应用（进程/文件/Docker/磁盘/日志/终端/系统监控/防火墙/服务监控/体检）对远端有意义；sites/databases/cron/SSL 等是「Graw 面板自身管理项」，对无代理的远端无意义，应禁用并提示。

## What Changes
- **设置页性能**：`SettingsWindow` 打开时并行加载各请求，多机管理区加 loading 态与「重新加载」按钮；多机数据不再被前面的慢请求阻塞。
- **测试连接反馈**：新增位于节点列表旁的独立反馈区（不再复用编辑器的 `editorMsg`），展示测试成功/失败与错误信息，并保留行内 loading。
- **远端监控修复**：`system.py` 的 `_overview_sync/_network_sync/_diskio_sync` 增加 `node_manager.is_remote()` 分支，复用已实现的 `_remote_*` 采集；WebSocket 生产者与 HTTP 接口在远程节点下返回远端指标。
- **应用远程能力模型（问题4）**：
  - 定义每个桌面窗口的「远程能力」：`host`（作用于远端主机的系统态）或 `local`（面板自身管理项）。
  - 前端：当前主机为远程（SSH）时，`host` 应用正常使用；`local` 应用禁用快捷方式并提示「仅本机节点可用」。
  - 后端：`local` 类路由在 `node_manager.is_remote()` 时返回明确的 4xx 错误（纵深防御，避免误操作本机）。
  - 后端 `host` 类模块从直接 `import hostfs`/裸 subprocess 迁移为走 `node_manager` 节点感知封装，使切换节点后真正操作远端。
- **BREAKING**：远程节点下 `local` 类应用被禁用（行为变化，属预期；本机节点行为完全不变）。

## Impact
- Affected specs：多机管理后端封装（`node_manager`）、系统指标（`system`）、多机管理前端（`nodes` store / `SettingsWindow` / `App.vue` 窗口注册）。
- Affected code：
  - 后端：`app/routers/system.py`、`app/routers/nodes.py`（如需）、若干 `local` 类路由增加远程守卫、若干 `host` 类模块改为经 `node_manager`。
  - 前端：`src/components/windows/SettingsWindow.vue`、`src/App.vue`、`src/store/nodes.js`、各应用窗口/快捷方式的远程能力标记、`src/locales/*` 新增文案。
- 数据：`backend/data/nodes.json` 结构不变；`backend/data/*.json` 均为面板本地配置，无迁移。

## ADDED Requirements
### Requirement: 设置页多机管理快速呈现
系统 SHALL 在打开设置窗口时并行加载各区块数据，多机管理区 SHALL 提供 loading 态与重新加载能力，且不被其它慢请求阻塞。

#### Scenario: 打开设置-多机管理
- **WHEN** 管理员打开设置窗口
- **THEN** 多机管理区块出现加载中；数据就绪后显示节点列表与当前主机，无需等待其它区块完成

#### Scenario: 多机数据加载失败
- **WHEN** 加载节点列表失败
- **THEN** 区块内展示错误信息并可点击重新加载

### Requirement: 测试连接即时反馈
系统 SHALL 在节点列表旁提供测试连接结果的独立反馈区，展示成功/失败及错误详情。

#### Scenario: 测试连接远程节点成功
- **WHEN** 用户点击某节点的「测试连接」且连接成功
- **THEN** 该节点按钮短暂显示 testing 态后，列表旁显示成功消息

#### Scenario: 测试连接远程节点失败
- **WHEN** 用户点击某节点的「测试连接」且连接失败
- **THEN** 列表旁显示失败消息与后端返回的可读错误原因

### Requirement: 远端系统监控正确
系统 SHALL 在当前管理主机为 SSH 节点时，概览/网络/磁盘 IO/系统信息均读取远端主机指标。

#### Scenario: 远程节点下实时监控
- **WHEN** 当前管理主机切换为远程 SSH 节点
- **THEN** 系统概览、实时监控（网络/磁盘 IO）与系统信息显示远端主机的 CPU/内存/磁盘/负载/主机名等数据

### Requirement: 应用远程能力门控
系统 SHALL 依据各应用对远程节点的支持能力（`host`/`local`）门控其可用性。

#### Scenario: 远程节点下打开 host 类应用
- **WHEN** 当前管理主机为远程节点且用户打开 host 类应用（进程/文件/Docker/磁盘/日志/终端/监控等）
- **THEN** 应用正常打开并作用于远端主机

#### Scenario: 远程节点下打开 local 类应用
- **WHEN** 当前管理主机为远程节点且用户尝试打开 local 类应用（网站/数据库/计划任务/SSL 等）
- **THEN** 快捷方式被禁用，提示「该功能仅本机节点可用」，后端相应接口一并拒绝

#### Scenario: 本机节点下所有应用可用
- **WHEN** 当前管理主机为本机（local）
- **THEN** 所有应用正常可用，行为与改造前一致

## MODIFIED Requirements
### Requirement: 系统指标采集接口
`system.py` 的 `_overview_sync/_network_sync/_diskio_sync` 原仅调用本机 psutil；现加入 `node_manager.is_remote()` 分支，远程节点复用 `_remote_overview/_remote_network/_remote_diskio`。

## REMOVED Requirements
无（不删除现有功能；远程节点下禁用 local 类应用是新增约束，本机行为不变）。