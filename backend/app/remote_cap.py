# -*- coding: utf-8 -*-
"""
remote_cap.py - 应用「远程能力」分类与守卫

背景：
  SSH 节点是「裸服务器」（未安装 Graw 代理）。因此并非所有面板功能都能作用于
  远端主机：
    - host（系统态）：进程 / 文件 / Docker / 磁盘 / 日志 / 终端 / 系统监控 /
      防火墙 / 服务监控 / 体检 等，读取或操作远端主机的系统状态，切换节点后
      应真正作用于远端。
    - local（面板自身管理项）：网站 / 数据库 / 计划任务 / SSL / 备份 / 应用
      商店 / 通知 / WAF 等，数据存于面板本地 data/*.json，对「无代理的远端
      主机」没有意义，远端节点下应整体禁用并提示。

本模块提供：
  - LOCAL_PREFIX 集合：local 类 API 路径前缀。
  - is_local_path(path)：判断某请求路径是否属于 local 类。
  顶层以 HTTP 中间件统一拦截（见 main.py）：当前管理主机为 SSH 远端且请求路径
  命中 local 类时返回 403，作为纵深防御（前端已隐藏/禁用对应快捷方式）。
"""
from app import node_manager

# local 类 API 前缀（远端节点下禁用）
# host 类（进程/文件/Docker/磁盘/日志/终端/系统监控/防火墙/服务监控/体检/工具箱）
# 不在此列表，远端下仍可用。
LOCAL_PREFIX = (
    "/api/sites",
    "/api/databases",
    "/api/cron",
    "/api/ssl",
    "/api/protection",
    "/api/runtime",
    "/api/tasks",
    "/api/appstore",
    "/api/backup",
    "/api/netstorage",
    "/api/notify",
    "/api/uptime",
    "/api/webstats",
    "/api/rewrite",
    "/api/siteopts",
    "/api/waf",
    "/api/tamper",
    "/api/phpversions",
    "/api/ftpusers",
    "/api/sshkeys",
    "/api/certcheck",
    "/api/panelbackup",
    "/api/update",
    "/api/webmode",
    "/api/loginlog",
)


def is_local_path(path: str) -> bool:
    """判断请求路径是否命中 local 类应用（不含子路径的精确匹配用 startswith）。"""
    p = path or ""
    # 精确到前缀（含后续 / 或结尾），如 /api/sites、/api/sites/list 均命中
    return any(p == prefix or p.startswith(prefix + "/") for prefix in LOCAL_PREFIX)


def local_only_reject_reason() -> str:
    """返回 local 类接口在远端节点被拒时的提示文案。"""
    return "该功能仅本机节点可用"


def reject_if_local_remote(path: str) -> bool:
    """当且仅当当前为远端节点且路径命中 local 类时返回 True（应拦截）。"""
    return node_manager.is_remote() and is_local_path(path)