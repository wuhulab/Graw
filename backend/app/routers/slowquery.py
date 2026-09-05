# -*- coding: utf-8 -*-
"""
slowquery.py - MySQL 慢查询分析路由（仅管理员）

功能：
  GET  /api/slowquery/connections - MySQL 系连接列表（供前端下拉）
  POST /api/slowquery/scan        - 对某连接解析慢查询日志，返回 TOP N

实现：
  - 用 pymysql 查 SHOW VARIABLES 确认 slow_query_log 开关与日志路径；
    未开启时不改服务器配置，只返回开启引导（安全：不做 SET GLOBAL 强改）。
  - 日志文件经 node_manager 在当前节点上下文读取末尾（tail -c 8MB），
    解析 mysqld 慢日志条目（# Time / # User@Host / # Query_time / SQL 多行），
    按 Query_time 降序输出 TOP 50，并对常见低效模式做启发式建议。

安全/性能：
  - 连接读取走 databases 既有鉴权（ADMIN）；SQL 截断 500 字符；
    文件读取限 8MB 尾部 + 解析上限，避免大日志拖垮请求。
"""

import asyncio
import logging
import re
import shlex
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import auditlog
from app import node_manager
from app.routers import databases

logger = logging.getLogger("graw.slowquery")

router = APIRouter()

TAIL_BYTES = 8 * 1024 * 1024   # 只读慢日志尾部 8MB
MAX_ITEMS = 50                 # TOP N
SQL_MAX = 500                  # SQL 展示截断
_PARSE_LOCK = threading.Lock()  # 并发扫描限流（解析较重）

# 慢日志条目头部匹配（mysqld/mariadb 通用格式）
_QUERY_TIME_RE = re.compile(
    r"#\s*Query_time:\s*([0-9.]+)\s+Lock_time:\s*([0-9.]+)\s+"
    r"Rows_sent:\s*([0-9]+)\s+Rows_examined:\s*([0-9]+)"
)
_USER_HOST_RE = re.compile(r"#\s*User@Host:\s*(\S+)\s*\[([^\]]*)\]")
_TIME_RE = re.compile(r"#\s*Time:\s*(\S+\s+\S+|\S+)")


class ScanReq(BaseModel):
    """扫描请求：connection_id 指向 databases 里的 MySQL 连接。"""

    connection_id: str = Field(..., min_length=1, max_length=64)


def _norm_sql(sql: str) -> str:
    """把多行 SQL 折叠为单行并截断（防超长日志刷屏）。"""
    one = " ".join(line.strip() for line in sql.splitlines() if line.strip())
    return one[:SQL_MAX] + ("…" if len(one) > SQL_MAX else "")


def _suggest(sql: str, rows_examined: int, rows_sent: int) -> str:
    """对慢 SQL 做简单启发式建议（常见低效模式的快速提示）。"""
    low = sql.lower()
    hints = []
    if rows_sent > 0 and rows_examined > rows_sent * 100:
        hints.append("扫描行数为返回行数的百倍以上，疑似全表扫描，建议检查索引")
    if "select *" in low:
        hints.append("SELECT * 拉取了全部列，建议只选所需字段")
    if "select" in low and not re.search(r"\bwhere\b", low):
        hints.append("SELECT 无 WHERE 条件，请确认查询范围")
    if re.search(r"select.*(?:\(|\s)select\b", low):
        hints.append("存在嵌套子查询，评估可否改写为 JOIN")
    if "order by" in low and "limit" not in low:
        hints.append("ORDER BY 未配合 LIMIT，可能全量排序，建议限制条数")
    return "；".join(hints) if hints else ""


def _parse_slowlog(text: str) -> list:
    """解析慢日志文本，返回 [{time,user,host,query_time,rows_examined,rows_sent,sql,suggest}]。"""
    items = []
    cur = None  # 正在收集的条目（等待吸收 SQL 行）
    pend_sql_head = None  # 收集中的连续 SQL 行（以非 # 且非 SET 的语句开头）
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("#"):
            if pend_sql_head:
                if cur is not None:
                    cur["sql"] = _norm_sql(pend_sql_head)
                    cur["suggest"] = _suggest(cur["sql"], cur["rows_examined"], cur["rows_sent"])
                    items.append(cur)
                cur = None
                pend_sql_head = None
            m = _TIME_RE.search(line)
            if m and cur is None:
                cur = {"time": m.group(1), "user": "", "host": "", "query_time": 0.0,
                       "rows_examined": 0, "rows_sent": 0, "sql": "", "suggest": ""}
                continue
            m = _QUERY_TIME_RE.search(line)
            if m and cur is not None:
                cur["query_time"] = float(m.group(1))
                cur["rows_sent"] = int(m.group(3))
                cur["rows_examined"] = int(m.group(4))
                continue
            m = _USER_HOST_RE.search(line)
            if m and cur is not None:
                cur["user"] = m.group(1)
                cur["host"] = m.group(2)
            continue
        # 非 # 行：SQL 主体（跳过 SET timestamp 元数据行）
        if cur is not None:
            s = line.strip()
            if not s:
                continue
            if s.startswith("SET "):
                continue
            pend_sql_head = (pend_sql_head + "\n" + s) if pend_sql_head else s
    # 收尾
    if cur is not None and pend_sql_head:
        cur["sql"] = _norm_sql(pend_sql_head)
        cur["suggest"] = _suggest(cur["sql"], cur["rows_examined"], cur["rows_sent"])
        items.append(cur)
    return items


@router.get("/connections")
async def list_connections():
    """MySQL/MariaDB 系连接列表（前端扫描下拉用）。"""
    conns = databases._load_connections()
    mysql = [
        {
            "id": c.get("id"),
            "name": c.get("name") or c.get("id"),
            "db_type": c.get("db_type", "mysql"),
            "host": c.get("host", ""),
            "port": c.get("port", 3306),
        }
        for c in conns
        if (c.get("db_type") or "mysql") in ("mysql", "mariadb", "MySQL", "MariaDB")
    ]
    return {"connections": mysql}


@router.post("/scan")
async def scan(req: ScanReq):
    """扫描某 MySQL 连接的慢查询日志。"""
    conn = next((c for c in databases._load_connections() if c["id"] == req.connection_id), None)
    if not conn:
        raise HTTPException(status_code=404, detail="连接不存在")
    if (conn.get("db_type") or "mysql") not in ("mysql", "mariadb", "MySQL", "MariaDB"):
        raise HTTPException(status_code=400, detail="仅支持 MySQL / MariaDB 连接")
    host = str(conn.get("host") or "127.0.0.1")
    port = int(conn.get("port") or 3306)
    user = conn.get("username") or ""
    password = conn.get("password") or ""

    # 1) 在「数据库连接对应主机」执行变量查询：连接 host 必须是当前节点可达的
    #    （本机/回环），远端库暂不支持直接读其磁盘日志，返回引导
    if host not in ("127.0.0.1", "localhost", "::1", ""):
        return {
            "enabled": False,
            "hint": f"目标 MySQL 位于 {host}:{port}，不是当前管理节点的本机服务；请将「当前节点」切换到该数据库所在主机后重试",
            "items": [],
        }
    try:
        # pymysql 连接查询慢日志开关与文件路径
        result = await asyncio.to_thread(_query_mysql_vars, host, port, user, password)
    except Exception as e:
        logger.warning("慢查询扫描连接失败 %s: %s", req.connection_id, e)
        raise HTTPException(status_code=502, detail=f"连接 MySQL 失败: {e}")

    auditlog.record("慢查询分析", "", "", f"conn={req.connection_id}")
    if not result["slow_query_log"]:
        return {
            "enabled": False,
            "hint": "慢查询日志未开启。开启方式：SET GLOBAL slow_query_log='ON'; SET GLOBAL long_query_time=1;（需 MySQL 管理员权限，面板不做强改）",
            "items": [],
        }
    log_file = result["slow_query_log_file"]
    return await asyncio.to_thread(_scan_slowlog, log_file)


def _query_mysql_vars(host: str, port: int, user: str, password: str) -> dict:
    """用 pymysql 查慢日志相关变量（密码/私网仅本机使用，无注入面）。"""
    kw = {
        "host": host, "port": port, "user": user, "password": password,
        "connect_timeout": 6, "read_timeout": 10, "charset": "utf8mb4",
    }
    out = {"slow_query_log": False, "slow_query_log_file": "", "long_query_time": 0}
    import pymysql  # 懒加载：仅本机缺少依赖时该功能不可用，不影响面板其它模块

    db = pymysql.connect(**kw)
    try:
        with db.cursor() as cur:
            cur.execute("SHOW VARIABLES LIKE 'slow_query_log'")
            row = cur.fetchone()
            if row and row[1] in ("ON", "1"):
                out["slow_query_log"] = True
            cur.execute("SHOW VARIABLES LIKE 'slow_query_log_file'")
            row = cur.fetchone()
            if row:
                out["slow_query_log_file"] = row[1] or ""
            cur.execute("SHOW VARIABLES LIKE 'long_query_time'")
            row = cur.fetchone()
            if row:
                try:
                    out["long_query_time"] = float(row[1])
                except (TypeError, ValueError):
                    out["long_query_time"] = 0
    finally:
        db.close()
    return out


def _scan_slowlog(log_file: str) -> dict:
    """在当前节点上下文读取慢日志尾部并解析 TOP N。"""
    if not log_file:
        return {"enabled": True, "hint": "慢查询日志路径为空，无法读取", "items": []}
    try:
        r = node_manager.host_shell(
            f"tail -c {TAIL_BYTES} {shlex.quote(log_file)} 2>/dev/null || echo '__NOFILE__'",
            capture_output=True, text=True, timeout=20,
        )
        text = (r.stdout or "")
    except Exception as e:
        logger.warning("读取慢日志 %s 失败: %s", log_file, e)
        return {"enabled": True, "hint": f"读取慢日志失败: {e}", "items": []}
    if "__NOFILE__" in text or not text.strip():
        return {
            "enabled": True,
            "hint": f"未找到慢日志文件 {log_file}（可能已轮转或无记录）。若刚开启，请等待慢查询产生后再试",
            "items": [],
        }
    items = _parse_slowlog(text)
    items.sort(key=lambda x: x["query_time"], reverse=True)
    return {
        "enabled": True,
        "hint": "",
        "long_query_time": 0,  # 由前端展示引导文案
        "items": items[:MAX_ITEMS],
    }