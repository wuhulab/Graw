# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 约定，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

各版本对应的完整提交记录见 git tag（`v1.4.5`、`v1.5.0`、`v1.5.2` 等）。

## [Unreleased]

### Added
- 开源协作规范文件：CONTRIBUTING.md、SECURITY.md、CHANGELOG.md、.editorconfig、GitHub Issue/PR 模板

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

[Unreleased]: https://github.com/wuhulab/Graw/compare/v1.5.2...HEAD
[1.5.2]: https://github.com/wuhulab/Graw/releases/tag/v1.5.2
[1.5.0]: https://github.com/wuhulab/Graw/releases/tag/v1.5.0
[1.4.10]: https://github.com/wuhulab/Graw/releases/tag/v1.4.10