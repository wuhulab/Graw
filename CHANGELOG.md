# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 约定，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

各版本对应的完整提交记录见 git tag（`v1.4.5`、`v1.5.0`、`v1.5.2` 等）。

## [Unreleased]

### Added
- **受限管理员（RBAC 模块级权限）**：在管理员身份之上按模块收窄权限——`users.json` 新增 `perms` 白名单字段（字段缺失 / `null` = 全量权限，升级前的存量管理员行为完全不变；`[]` = 不放行；类型非法按空集合取最严）
  - 后端：`auth.MODULES`（26 个模块 key）+ `user_perms()` / `has_perm()` + `require_perm("模块")` / `require_perm_ws("模块")` 依赖工厂；40 余条业务路由由 `ADMIN` 改为 `PERM("模块")`（多节点 / SSH 密钥 / 插件 / 面板更新 / 面板备份 / 用户管理等「面板自身安全边界」仍要求完整管理员）
  - Agent 隧道：代理前用 `_proxy_perm_map()`（从路由依赖自动汇总）做等价的模块判定——子节点侧拿到的是 agent 管理员令牌，拦不住越权，必须在主面板本地先拦
  - WebSocket：终端 / Docker 实时推送改用 `require_perm_ws("模块")`；会话管理对受限管理员降级为「仅本人会话」
  - 启动期自检 `_audit_route_perms()`：扫描仍走 `ADMIN` 而未登记为面板自身边界的接口并打 warning，避免新路由漏挂
  - 兜底约束：面板必须始终保留至少一个「完整权限的超级管理员」，改角色 / 收窄 `perms` / 删除账号时校验（`routers/auth._super_admin_count()`），违规返回 400
  - 用户管理界面：新增「模块权限」列与「模块授权」弹窗（默认「完整管理员」，可切换为模块多选）；模块清单经 `GET /api/auth/modules` 下发（单一来源，避免前后端漂移）
  - 前端：`store/auth` 新增 `hasPerm()` / `isFullAdmin()`。**按产品约定不按模块隐藏入口**——受限管理员可见全部功能入口，点开无权限模块时提示「该用户无此权限」且不打开窗口（`App.vue` 的 `openWindow()` 提示条、ShunX 聚合窗口的标签级提示）；`fullAdminOnly` 的面板自身功能（账号管理 / 界面设置）仍隐藏
- 前端工程化：新增 ESLint 扁平配置（`frontend/eslint.config.js`，`@eslint/js` 推荐规则 + `eslint-plugin-vue` Vue3 essential，并用 `eslint-config-prettier` 关闭排版类冲突规则）与 Prettier 配置（`.prettierrc.json` / `.prettierignore`）
- 前端新增脚本：`lint` / `lint:fix` / `format` / `format:check`
- CI：恢复 Backend / Frontend 检查工作流（Backend 补装 httpx，修复 TestClient 收集阶段报错）
- 单元测试：新增 `test_rbac_unit.py`（35 项，覆盖 perms 兼容语义 / 依赖 403 行为 / 入参校验 / 代理映射与自检 / 超级管理员兜底约束）
- 多语言：补全英语（en）与世界语（eo）的模块权限相关文案（`users.perm.*` 模块名、模块授权界面文案与「无此权限」提示）

### Changed
- CI：前端新增 ESLint 检查步骤；后端测试集扩容为「`test_*_unit.py` 整跑 + 其余套件逐文件独立进程」（规避模块级全局状态跨文件污染），并明确排除依赖真实后端 / 会改写 `data/users.json` 的用例
- 依赖升级（已用独立虚拟环境跑通全量测试验证）：`fastapi` 0.115.14 → 0.141.1、`uvicorn[standard]` 0.30.6 → 0.52.4、`psutil` 6.0.0 → 7.2.2；并因 uvicorn 新版要求 `websockets>=13` 同步将 `websockets` 12.0 → 17.1
- 代码扫描告警收敛：修复 CodeQL 12 项告警（重复导入 / 空 except / 日志注入 / 未使用导入与变量 / 恒真条件）
- 测试活债清理：`test_tamper_unit.py` 补 pytest fixture 与临时目录隔离、`test_backup_unit.py` mock 掉真实 DNS 解析、`test_databases_sqlite.py` 与 SQL 首动词白名单对齐

### Fixed
- 计划任务「新增 → 标准」表单崩溃：`CronTaskFormWindow` 调用了未定义的 `defaultStd()`，补齐默认值工厂函数
- 防篡改告警弹窗操作报错：`TamperAlert` 使用未定义的 `t()`（漏了 `useI18n`），并修复「完全关闭」提示显示为原始 key 文本的问题
- 日志审计窗口空态误判：`AuditLogWindow` 对 computed 漏写 `.value`，导致恒显示「文件不存在」
- SSH 密钥部署窗口：prop 名 `key` 为 Vue 保留属性，重命名为 `sshKey`（调用方同步调整）
- 文件管理窗口：prop `path` 与内部状态同名造成模板命名空间冲突，prop 重命名为 `initialPath`
- 防篡改窗口：清理引用未定义变量的死代码
- 其余 ESLint 报出的死代码 / 未使用导入与变量、多余正则转义等若干处；有意静默的空 catch 通过 `no-empty: allowEmptyCatch` 显式放行（避免为 17 处「尽力而为」的容错分支逐个加注释）

## [1.7.3] - 2026-10-01

### Added
- 面板启动过渡动画：面板挂载前显示静态「Graw」文字 + 浅蓝色旋转指示，挂载完成后自动移除
- 系统概览「显示两位小数」开关：开启后 CPU / 内存 / 磁盘百分比按两位小数展示（界面设置实时生效，无需保存）
- 窗口组件懒加载拆分（`lazyWindows.js`），进一步切割组件、加快面板启动速度
- Docker 实时数据单元测试（`test_docker_realtime_unit.py`）
- Docker Hub 说明文档（DOCKERHUB.md）与演示图目录（demo-photo）；Issue 模板补充 question 模板并新增 FUNDING.yml

### Changed
- Docker：移除 `/api/docker/status`、`/api/docker/containers` 的常驻轮询，改为按需拉取，降低空闲开销
- Docker：修复部分容器日志无法获取、显示为「空」的问题
- README 与多语言 README（readme-i18n）更新；docs/deployment 补充说明

## [1.7.2] - 2026-09-26

仅版本号同步，无功能变更。

## [1.7.1] - 2026-09-25

仅版本号同步，无功能变更。

## [1.7.0] - 2026-09-25

### Added
- 界面多语言：新增 11 种语言包（阿拉伯语、埃及阿拉伯语、古埃及语、爱尔兰语、希腊语、意大利语、拉丁语、波兰语、越南语、藏语、壮语），累计支持 22 种界面语言
- 古代语言语言包：新增苏美尔语 / 阿卡德语 / 赫梯语语言包，并新增「语言括号提示」设置（控制 `本语言书写形式 (中文原文/拉丁转写)` 括号注释的展示内容）
- 标准面板模式：侧边栏分组菜单 + 顶部多标签栏（标签页可缓存窗口状态），并补充传统服务器面板主页与 Docker / 进程窗口适配
- 应用商店：README / 详情窗口增强（README 拉取增加 512KB 上限与 owner/name 路径白名单校验）
- 贡献者许可协议（CLA）：CONTRIBUTING.md 新增第 7 节「贡献者许可协议」（提交即视为同意）
- 文档体系完善：新增 docs/README、docs/api-overview、docs/deployment、docs/node-agent、docs/app-store-recipe
- Web 终端「保留持久化终端」：勾选后 shell 进程常驻后端（会话按节点隔离、支持多窗口共享），刷新面板或重开窗口会接回同一会话并回放最近 256KB 输出，长时间任务不再因刷新而中断；新增 `GET/DELETE /api/terminal/persist` 管理接口，面板退出时自动结束全部常驻会话

### Changed
- 安全审查与漏洞修复：新增全局请求体大小限制中间件（防未认证超大请求体内存耗尽 DoS）、新增 security-audit 攻击面验证脚本、收敛 code-scanning 告警（path-injection / log-injection / stack-trace / empty-except）
- README 与多语言 README（readme-i18n）全面更新

### Removed
- VIP 付费功能：移除 `/api/vip` 路由与 `vip.py`、VipWindow、store/vip.js 及相关语言包条目，相关能力改为免费开放

## [1.6.1] - 2026-09

### Added
- 界面多语言：补全 9 种语言包全文翻译（德语、世界语、西班牙语、法语、日语、韩语、葡萄牙语、俄语、繁体中文）并扩充英文语言包

## [1.6.0] - 2026-09

### Added
- 配置快照与一键回滚：站点 nginx conf、防火墙规则写前自动快照，支持列表 / 预览 / 回滚 / 删除，并按类型触发 reload
- 批量操作中心：多节点批量执行 shell 命令、按关键字批量启停 / 重启容器（并发受限，单节点失败不影响整批）
- 站点 Git 自动部署：绑定仓库 / 分支 / 目标目录，支持手动触发与 Git 平台 Webhook（HMAC-SHA256 或 secret 校验）自动拉取部署
- 巡检报告：每日 08:00 汇总资源 / 证书 / 服务监控 / 站点可用性 / 告警生成报告并推送，支持手动生成与历史查看
- SSH 端口转发：在面板主机本地监听，经 SSH 隧道直连远程 MySQL / Redis 等服务
- Docker 镜像漏洞扫描：基于本地 advisory 库比对镜像软件包版本，输出命中 CVE
- MySQL 慢查询分析：解析慢查询日志 TOP N 并给出低效模式建议
- 站点维护模式：一键将站点切换到维护页（nginx 层 503 拦截），支持自定义维护页
- 服务监控：新增 start / stop / restart / enable / disable 处置动作
- 工具箱：新增网络诊断（ping / traceroute / DNS）与运维脚本库
- 面板一键更新：新增 docker run 单容器部署检测、独立执行容器内置重建脚本与更新日志查看
- 命令面板（CommandPalette）与桌面偏好持久化（desktopPrefs）
- 前端 PWA：新增 manifest 与 Service Worker
- 前端表单窗口化：备份 / 计划任务 / 数据库 / 防火墙 / Frp / FTP / 通知 / 服务监控 / SSL / WAF 等表单拆分为独立窗口（formBus）

### Changed
- 数据库管理：新增创建 / 管理独立窗口
- 应用商店：新增应用商店配置窗口

## [1.5.9] / [1.5.8] / [1.5.7] / [1.5.6] / [1.5.5] / [1.5.4] / [1.5.3] - 2026-08

### Added
- 应用接口开放协议（GPOP）：插件协议核心库（plugin_protocol）与插件管理接口（安装 / 启停 / 卸载 / 轮换令牌），附协议文档与 hello-graw 示例插件
- 桌面偏好（desktopPrefs）：桌面布局与偏好持久化
- 开源协作规范：CONTRIBUTING.md、SECURITY.md、CHANGELOG.md、.editorconfig、GitHub Issue/PR 模板与 CodeQL 扫描工作流（AGPL 许可文件重命名为 LICENSE）
- Web 终端：新增应用层心跳（ping/pong）与空闲读超时容错，修复「挂久了 / 多终端时无法输入」；终端与文件管理窗口增强
- ShunX 安全入口：聚合系统体检、面板备份等子应用
- VIP：面板级共享（任一账号激活即整面板解锁）与无限叠加（按授权码时长增量顺延）；应用商店免责声明等文案接入多语言

### Changed
- 阻塞 IO 下沉线程池：文件管理、数据库、日志、防火墙、Frp、网站、网站统计等接口将本地文件 IO / SSH 调用移入线程池，避免阻塞事件循环
- 安全与健壮性加固：日志去除用户可控值、空异常分支补充说明、WAF / 网络储存 / Frp 等路径与错误回传收敛（规避 stack-trace 泄露）
- 多节点：节点切换与当前管理主机上下文的日志收敛

## [1.5.2] - 2026-08

### Added
- 回收站：删除文件进入回收站，支持还原与定时自动清理（跨节点）
- 文件管理：Windows 风格剪贴板（Ctrl+C/V/Delete）、拖拽文件夹上传、复制自动重命名
- 会话管理：在线会话列表按 token 有效期与最后活跃时间判定在线状态
- 页面防篡改（Tamper）WebSocket 实时告警

### Changed
- 实时监控数据流稳定性修复，减少闪断

## [1.5.0] - 2026-07

### Added
- WAF（Web 应用防火墙）与网站增强（rewrite/伪静态、构成单站点管理）
- 网站统计（webstats）、证书到期检测（certcheck）、服务监控（svcmonitor）
- 健康体检（healthcheck）与面板备份（panelbackup）
- PHP 版本管理（phpversions）、FTP 用户管理（ftpusers）、工具箱（toolbox）
- 登录日志（loginlog）、网络储存（netstorage）、内网穿透（frp）
- 服务可用性检测（uptime）、通知中心（notify）、VIP 功能（vip）
- ShunX 安全入口

### Changed
- 1Panel/OpenResty 兼容：自动发现外部真实站点（conf.d）并支持直接编辑
- 防火墙规则统一（reconcile）与 Docker 端口出入站双链管控

## [1.4.10] / [1.4.9.1] / [1.4.9] / [1.4.8] / [1.4.7.x] / [1.4.6] / [1.4.5]

多节点管理（nodes）、SSH 密钥部署（sshkeys）、子节点 Agent 隧道架构
与远端子节点能力门控（remote_cap）等功能逐步演进上线，详细变更
请查看对应 git tag 提交记录。

[Unreleased]: https://github.com/wuhulab/Graw/compare/v1.7.3...HEAD
[1.7.3]: https://github.com/wuhulab/Graw/releases/tag/v1.7.3
[1.7.2]: https://github.com/wuhulab/Graw/releases/tag/v1.7.2
[1.7.1]: https://github.com/wuhulab/Graw/releases/tag/v1.7.1
[1.7.0]: https://github.com/wuhulab/Graw/releases/tag/v1.7.0
[1.6.1]: https://github.com/wuhulab/Graw/releases/tag/v1.6.1
[1.6.0]: https://github.com/wuhulab/Graw/releases/tag/v1.6.0
[1.5.9]: https://github.com/wuhulab/Graw/releases/tag/v1.5.9
[1.5.8]: https://github.com/wuhulab/Graw/releases/tag/v1.5.8
[1.5.7]: https://github.com/wuhulab/Graw/releases/tag/v1.5.7
[1.5.6]: https://github.com/wuhulab/Graw/releases/tag/v1.5.6
[1.5.5]: https://github.com/wuhulab/Graw/releases/tag/v1.5.5
[1.5.4]: https://github.com/wuhulab/Graw/releases/tag/v1.5.4
[1.5.3]: https://github.com/wuhulab/Graw/releases/tag/v1.5.3
[1.5.2]: https://github.com/wuhulab/Graw/releases/tag/v1.5.2
[1.5.0]: https://github.com/wuhulab/Graw/releases/tag/v1.5.0
[1.4.10]: https://github.com/wuhulab/Graw/releases/tag/v1.4.10