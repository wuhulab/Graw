# Graw 文档中心

这里是 Graw 仓库全部文档的入口索引。按「想做什么」挑一条路线读，不要从第一篇硬啃到底。

| 项 | 说明 |
|----|------|
| 读者 | 所有人（首次接触本仓库请从这里开始） |
| 前置阅读 | 无 |

## 全部文档一览

下表按「定位」分区，链接均为仓库内真实文件。

| 文档 | 定位 | 面向谁 |
|------|------|--------|
| [README.md](../README.md) | 项目门面：功能特性、下载部署的三种容器启动方式、默认账号、常用环境变量、API 概览 | 所有人（第一站） |
| [AGENTS.md](../AGENTS.md) | **权威**：架构、目录结构、路由清单与鉴权分级、开发约定与陷阱 | 改代码的人（必读） |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | 贡献流程、代码风格、提交规范、贡献者许可协议（CLA） | 想提 PR 的人 |
| [SECURITY.md](../SECURITY.md) | 安全问题报告流程与披露约定 | 发现漏洞的人 |
| [CHANGELOG.md](../CHANGELOG.md) | 版本变更记录 | 升级前查阅 |
| [TODO.md](../TODO.md) | 待办与已知规划 | 想找活干的贡献者 |
| [app-store/README.md](../app-store/README.md) | 社区应用商店的**配方字段详解与提交规范**（data.yml / compose 约定 / i18n / CI 发布） | 想提交应用配方的人 |
| [plugin-examples/hello-graw/](../plugin-examples/hello-graw/) | 最小可运行插件示例（plugin.yml + docker-compose.yml + server.py） | 想写 GPOP 插件的人 |
| [docs/plugin-protocol.md](./plugin-protocol.md) | 插件开放协议（GPOP）完整规范：清单协议、管理接口、开放接口、令牌与安全模型 | 插件开发者 |
| [docs/deployment.md](./deployment.md) | 部署与运维：三种启动方式、环境变量全表、数据目录与权限、升级备份、反向代理/HTTPS、排障 | 运维 / 部署者 |
| [docs/node-agent.md](./node-agent.md) | 子节点 Agent 部署与接入**实操**：收取模式开关、成对密钥轮换、JWT 换取、纳管步骤、能力门控、排障 | 运维 / 多节点管理者 |
| [docs/app-store-recipe.md](./app-store-recipe.md) | 面板侧应用商店机制：索引拉取、安装流程、落盘位置、自定义 compose、与 GPOP 的取舍 | 运维 / 配方作者 |
| [docs/api-overview.md](./api-overview.md) | API 概览：鉴权模型、公共端点、WebSocket 鉴权、路由分组总表、错误约定、调试 | 集成开发者 |
| [GETMEREAD/README.md](../GETMEREAD/README.md) | 「大白话」运行逻辑文档索引（原理向，非操纵向） | 想理解内部原理的人 |

### GETMEREAD 运行逻辑文档（11 篇）

以下文档用大白话 + Mermaid 图讲清各模块「怎么跑起来的」，与 `AGENTS.md` 的约定互补：

| 文档 | 讲什么 |
|------|--------|
| [01-项目架构与启动.md](../GETMEREAD/01-项目架构与启动.md) | 这是啥、用什么写的、怎么跑起来、启动时做了哪些事 |
| [02-请求生命周期与中间件.md](../GETMEREAD/02-请求生命周期与中间件.md) | 一个请求一路上经过哪些关卡、最后怎么返回 |
| [03-登录与鉴权.md](../GETMEREAD/03-登录与鉴权.md) | 账号密码怎么存、登录怎么发令牌、之后怎么验身份、怎么踢人下线 |
| [04-多节点与Agent隧道.md](../GETMEREAD/04-多节点与Agent隧道.md) | 一台面板怎么管多台机器、Agent 隧道怎么不开公网口连子节点 |
| [05-数据存储与后台任务.md](../GETMEREAD/05-数据存储与后台任务.md) | 数据存在哪、权限怎么收紧、后台常驻监控任务都在干嘛 |
| [06-前端界面逻辑.md](../GETMEREAD/06-前端界面逻辑.md) | 前端长什么样、桌面模式与标准面板模式、点图标怎么开窗口、怎么调接口 |
| [07-文件管理与回收站.md](../GETMEREAD/07-文件管理与回收站.md) | 文件管理怎么复刻 Windows 剪贴板、删除的文件去哪、回收站怎么自动清 |
| [08-应用接口开放协议.md](../GETMEREAD/08-应用接口开放协议.md) | 第三方怎么做「插件式应用」装进 Graw：清单 / 令牌 / 能力白名单 / 开放接口 |
| [09-网站管理与WAF.md](../GETMEREAD/09-网站管理与WAF.md) | 网站（Nginx/OpenResty）怎么建怎么改、1Panel 外部站点怎么发现、WAF/伪静态/缓存/统计怎么挂钩 |
| [10-应用商店与Git部署.md](../GETMEREAD/10-应用商店与Git部署.md) | 应用商店怎么拉索引怎么装、Git 自动部署怎么接 webhook 拉代码 |
| [11-安全防护与运维工具.md](../GETMEREAD/11-安全防护与运维工具.md) | 防火墙容器端口防护、保护中心、SSH 端口转发、镜像扫描、巡检报告、慢查询、批量操作、配置快照 |

## 建议阅读顺序

按身份选一条路线即可，不必全读：

| 你是 | 建议顺序 |
|------|----------|
| 第一次部署 | `README.md` → `docs/deployment.md` → 需要多机时接 `docs/node-agent.md` |
| 运维多节点 | `docs/node-agent.md` → `GETMEREAD/04-多节点与Agent隧道.md`（补原理） → `docs/deployment.md`（排障） |
| 后端 / 前端开发 | `AGENTS.md` → `GETMEREAD/01` + `GETMEREAD/02` → 对应模块的 GETMEREAD 篇（网站/WAF 看 09，应用商店与 Git 部署看 10，安全与运维工具看 11） |
| 集成调用 API | `docs/api-overview.md` → `AGENTS.md` 第 3 节（路由与鉴权） |
| 写插件（GPOP） | `docs/plugin-protocol.md` → `plugin-examples/hello-graw/` → `GETMEREAD/08-应用接口开放协议.md` |
| 提交应用配方 | `docs/app-store-recipe.md`（机制） → `app-store/README.md`（字段与规范） |
| 提 PR | `CONTRIBUTING.md` → `AGENTS.md` |

> 文档与代码不一致时，以代码与 `AGENTS.md` 为准。发现文档过期，欢迎按 `CONTRIBUTING.md` 提 PR。