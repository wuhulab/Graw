# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 说明：本 CHANGELOG 自 v1.5.0 起按标准格式维护；v1.5.0 之前的更早历史
> （v1.0.0 ~ v1.4.x）为阶段里程碑摘要，逐条提交细节请参见 `git log`。

## [Unreleased]

### 待办
- 补齐 `backend/test_agent_cfg_unit.py` 与当前 `agent_cfg.set_config()` 签名（已移除 `role` 参数）的同步，并将其重新纳入 CI 测试集。

## [1.5.2] - 2026-08-27

### 修复
- 修复实时监控图表数据闪断问题。
- 若干代码注释与稳定性优化。

## [1.5.1] - 2026-08-26

### 新增
- 会话管理：在线会话列表、Token 过期与空闲（`last_seen`）双维度在线判定。
- 登录日志（`/api/loginlog`）与面板审计日志（`/api/auditlog`）。
- 双因素认证（2FA）支持。
- 回收站：文件删除改为可配置的进回收站，支持自动清理（`backend/app/trash.py` + `/api/recycle`）。
- 面板数据备份（`/api/panelbackup`）。

### 变更
- 会话在线状态改为「创建时间 + TTL」与「最后活跃时间」双重判定，空闲阈值可通过 `GRAW_SESSION_ONLINE_SECONDS` 配置。

## [1.5.0] - 2026-08-20

### 新增
- 多节点 / Agent 架构完善：子节点「收取模式」热开关（`agent_cfg`）、隧道代理客户端（`agent_client`）、请求级 `X-Graw-Node` 覆盖（`node_manager`）、远端子节点能力门控（`remote_cap`）。
- 网站模块针对 1Panel + OpenResty 的深度集成：
  - 自动发现 `/opt/1panel/www/conf.d/*.conf` 中的真实站点，并入统一站点清单（`sites_names.json` 持久化外部站点名）。
  - 外部（1Panel）站点支持直接编辑，改动写回真实 Nginx 配置。
  - 合并站点清单统一供给 WAF、站点增强（`sitesopts_external.json` 独立持久化）等特性。
- Docker 运行时的 podman 兼容（容器 ID / 名称 / 时间等字段差异统一适配）。
- 防火墙对 Docker 发布端口的完整控制：同步落 INPUT 与 mangle PREROUTING 链。

### 变更
- 站点日志自动发现支持通配符展开（如 `sites/*/log/access.log`）。
- 外部站点增强配置与自管理站点配置分离持久化。

## [1.4.x] - 2026-07 ~ 2026-08

- 服务监控 / 应用商店（`app-store` YAML 配方）/ 任务中心 / 运行时容器。
- 内网穿透（Frp）、网络储存（SMB / S3 / FTP / WebDAV）、证书到期检查、站点可用性监测。
- 工具箱、PHP 版本管理、体检、防篡改、WAF、ShunX 安全入口。
- 网站 / 数据库 / 计划任务 / 防火墙 / SSL / 日志 / 备份等核心管理模块持续迭代。
- Web 终端（xterm.js + paramiko）、文件管理、进程管理、备忘录。
- 前端多语言（zh-CN / zh-TW / en / ja / ko / ru / de / fr / es / pt / eo）。

## [1.0.0] - 2026-04

### 初始发布

- 基于 Web 的「类桌面操作系统」服务器管理面板：
  - Vue 3 + Vite 前端（窗口 / 任务栏 / 桌面快捷方式交互）。
  - FastAPI 后端，JWT 鉴权（admin / 普通用户两级角色）。
  - 实时系统监控（CPU / 内存 / 磁盘 / 网络 / 负载，WebSocket 推送）。
  - Docker 容器与镜像管理、进程管理、文件管理、Web 终端、备忘录。
- Docker 多阶段构建镜像 `shunx/graw`（amd64 / arm64）。