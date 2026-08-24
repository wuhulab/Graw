import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

# 数据库名白名单：库名会拼入 SQL 标识符（CREATE/DROP DATABASE），
# 必须禁止反引号 / 引号 / 分号等字符，防止标识符逃逸注入多条 SQL。
# MySQL/PostgreSQL 均适用（PostgreSQL 使用双引号包裹标识符，白名单同样安全）。
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Redis 控制台危险方法黑名单：run_query 通过反射调用 redis.Redis 的方法，
# 以下方法可关停服务 / 清空数据 / 改配置写任意文件（RCE 链）/ 执行 Lua /
# 外带数据，必须与 MySQL/PG 的 SQL 黑名单（drop database / shutdown / grant all）
# 同级拦截。
#
# 安全修复（第八轮审计，Medium）：补齐此前遗漏的高危命令——
#   flushall/flushdb       清空全部/当前库（数据销毁）
#   debug_segfault         使 Redis 进程崩溃（DoS）
#   config_get             泄露 requirepass / dir / dbfilename 等敏感配置
#   function_*/fcall*      Redis 7 Lua 函数加载与执行
#   failover/replconf      主从拓扑破坏
#   monitor/psync/sync     命令流与数据流外带
#   memory_purge           强制内存清理（DoS）
#   client_pause/kill      连接级 DoS
#   module_*               加载原生模块（任意代码执行）
#   acl_*                  权限篡改
_REDIS_FORBIDDEN_METHODS = {
    "shutdown",            # 关停远程 Redis（DoS）
    "shutdown_nosave",
    "config_set",          # 改 dir/dbfilename + save 即任意文件写（RCE 链）
    "config_rewrite",
    "config_get",          # 泄露 requirepass / dir / dbfilename 等配置
    "config_resetstat",
    "save", "bgsave",      # 落盘触发上述文件写
    "bgrewriteaof",
    "eval", "evalsha",     # Lua 脚本任意执行
    "script_load", "script",
    "register_script",     # 加载任意 Lua
    "function_load", "function_load_code",  # Redis 7 Lua 函数
    "function_delete", "function_flush", "function_restore",
    "function_dump", "function_kill", "function_stats",
    "fcall", "fcall_ro",   # 调用已加载的 Lua 函数
    "replicaof", "slaveof",  # 主从复制外带数据
    "failover",            # 强制主从切换（拓扑破坏）
    "replconf",
    "migrate", "restore",  # 跨实例搬数据
    "cluster",             # 集群拓扑破坏
    "swapdb",
    "flushall", "flushdb",  # 清空全部/当前库（数据销毁）
    "monitor",             # 监控所有命令流（数据外带）
    "psync", "sync",       # 复制数据流外带
    "memory_purge",        # 强制内存清理（DoS）
    "client_pause", "client_unpause", "client_kill",  # 连接级 DoS
    "module_load", "module_loadex", "module_unload",  # 模块加载（原生代码）
    "acl", "acl_setuser", "acl_deluser", "acl_save",  # 权限篡改
    "execute_command",     # 原始命令入口，绕过一切上层过滤
    "pubsub",
}

# 按前缀整体封禁的反射方法族（纵深防御：拦截 debug_/config_ 等家族尚未
# 枚举到的新增变体，避免再次出现"枚举不全"的漏洞模式）
_REDIS_FORBIDDEN_PREFIX = ("debug_", "config_", "function_", "module_", "acl_", "client_")


def _is_forbidden_redis(method: str) -> bool:
    """判断 Redis 反射方法是否应被禁止（精确名 + 前缀族双保险）。"""
    if not method or method.startswith("_"):
        return True
    if method in _REDIS_FORBIDDEN_METHODS:
        return True
    return any(method.startswith(p) for p in _REDIS_FORBIDDEN_PREFIX)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_FILE = os.path.join(DATA_DIR, "databases.json")

# 各数据库驱动是否可用（缺驱动时接口返回 503 提示，避免崩溃）
MYSQL_LIBS = False
REDIS_LIBS = False
POSTGRES_LIBS = False
MONGO_LIBS = False

try:
    import pymysql

    MYSQL_LIBS = True
except Exception:
    pass

try:
    import redis

    REDIS_LIBS = True
except Exception:
    pass

try:
    import psycopg2

    POSTGRES_LIBS = True
except Exception:
    pass

try:
    import pymongo
    from bson import ObjectId

    MONGO_LIBS = True
except Exception:
    pass


def _load_connections() -> list:
    """读取数据库连接配置列表（文件不存在或损坏时返回空列表）。"""
    if not os.path.exists(DB_FILE):
        return []
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("读取数据库连接配置失败: %s", e)
        return []


def _save_connections(data: list):
    """持久化数据库连接配置列表。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _auto_detect_mysql() -> Optional[dict]:
    """探测本机是否运行 MySQL/MariaDB 服务进程。"""
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            name = proc.info.get("name", "") or ""
            if "mysqld" in name.lower() or "mariadbd" in name.lower():
                return {
                    "type": "mysql",
                    "host": "127.0.0.1",
                    "port": 3306,
                    "name": "Local MySQL",
                }
    except Exception:
        pass
    return None


def _auto_detect_redis() -> Optional[dict]:
    """探测本机是否运行 Redis 服务进程。"""
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            name = proc.info.get("name", "") or ""
            if "redis-server" in name.lower():
                return {
                    "type": "redis",
                    "host": "127.0.0.1",
                    "port": 6379,
                    "name": "Local Redis",
                }
    except Exception:
        pass
    return None


def _auto_detect_postgresql() -> Optional[dict]:
    """探测本机是否运行 PostgreSQL 服务进程（Windows 下进程名为 postgres.exe）。"""
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            name = proc.info.get("name", "") or ""
            if name.lower().startswith("postgres") and name.lower().endswith(".exe"):
                return {
                    "type": "postgresql",
                    "host": "127.0.0.1",
                    "port": 5432,
                    "name": "Local PostgreSQL",
                }
    except Exception:
        pass
    return None


def _auto_detect_mongodb() -> Optional[dict]:
    """探测本机是否运行 MongoDB 服务进程（Windows 下进程名为 mongod.exe）。"""
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            name = proc.info.get("name", "") or ""
            if name.lower().startswith("mongod") and name.lower().endswith(".exe"):
                return {
                    "type": "mongodb",
                    "host": "127.0.0.1",
                    "port": 27017,
                    "name": "Local MongoDB",
                }
    except Exception:
        pass
    return None


class DBConnection(BaseModel):
    """数据库连接配置模型（支持 mysql / redis / postgresql / mongodb / sqlite）。

    - sqlite 类型使用 `database` 字段存本地文件路径（相对路径基于数据目录，或绝对路径），
      忽略 host / port / username / password。
    """

    name: str = Field(..., min_length=1)
    db_type: str = Field(..., pattern="^(mysql|redis|postgresql|mongodb|sqlite)$")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=3306)
    username: Optional[str] = ""
    password: Optional[str] = ""
    database: Optional[str] = ""


class DBQuery(BaseModel):
    """数据库查询请求模型。

    - mysql / postgresql: 使用 sql 字段执行 SQL；
    - redis: 使用 command 字段执行命令（db_index 选择库号）；
    - mongodb: 使用 collection + filter(JSON) + limit 执行 find。
    """

    sql: Optional[str] = None
    command: Optional[str] = None
    db_index: Optional[int] = 0
    collection: Optional[str] = None
    filter: Optional[str] = None
    limit: Optional[int] = 100


def _pg_connect(conn: dict):
    """建立 PostgreSQL 连接（psycopg2），统一超时与参数处理。"""
    return psycopg2.connect(
        host=conn["host"],
        port=conn.get("port", 5432),
        user=conn.get("username") or None,
        password=conn.get("password") or None,
        dbname=conn.get("database") or None,
        connect_timeout=5,
    )


def _mongo_client(conn: dict):
    """建立 MongoDB 客户端（pymongo），database 字段作为认证库(authSource)。"""
    return pymongo.MongoClient(
        host=conn["host"],
        port=conn.get("port", 27017),
        username=conn.get("username") or None,
        password=conn.get("password") or None,
        authSource=conn.get("database") or "admin",
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )


def _mongo_jsonable(doc):
    """将 MongoDB 文档（含 ObjectId/datetime）转换为可 JSON 序列化的结构。"""
    if isinstance(doc, dict):
        return {k: _mongo_jsonable(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_mongo_jsonable(v) for v in doc]
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


def _find_connection(conn_id: str) -> dict:
    """按 id 查找连接配置，不存在则抛 404。"""
    conn = next((c for c in _load_connections() if c["id"] == conn_id), None)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


def _sqlite_file(conn: dict) -> str:
    """解析 SQLite 数据库文件路径。

    - `database` 字段存文件路径（相对路径基于数据目录 DATA_DIR，绝对路径原样使用）；
    - 拒绝空字节 / CR / LF 等控制字符，防止字符注入到文件路径与后续元数据写入；
    - 相对路径归一化后必须仍位于 DATA_DIR 之内，拒绝经由 `..` 逃逸出数据目录
      （否则可在任意可写位置建库或探测任意文件存在性）。
    """
    raw = (conn.get("database") or "").strip()
    if not raw:
        # SQLite 必须指定一个本地文件，否则无法说清要管理哪个库
        raise HTTPException(status_code=400, detail="SQLite 文件路径不能为空")
    if "\x00" in raw or "\r" in raw or "\n" in raw:
        raise HTTPException(status_code=400, detail="SQLite 文件路径包含非法字符")
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    resolved = os.path.normpath(os.path.join(DATA_DIR, raw))
    # 相对路径逃逸防护：归一化后必须仍落在 DATA_DIR 之内
    base = os.path.abspath(DATA_DIR)
    abs_resolved = os.path.abspath(resolved)
    try:
        if os.path.commonpath([abs_resolved, base]) != base:
            raise HTTPException(status_code=400, detail="SQLite 文件路径超出数据目录")
    except ValueError:
        # 跨盘符/UNC：不可能位于 DATA_DIR 内，直接拒绝
        raise HTTPException(status_code=400, detail="SQLite 文件路径超出数据目录")
    return resolved


@router.get("/status")
async def db_status():
    """返回各数据库驱动安装状态与本机服务探测结果。"""
    return {
        "mysql_detected": bool(_auto_detect_mysql()),
        "redis_detected": bool(_auto_detect_redis()),
        "postgresql_detected": bool(_auto_detect_postgresql()),
        "mongodb_detected": bool(_auto_detect_mongodb()),
        "mysql_libs": MYSQL_LIBS,
        "redis_libs": REDIS_LIBS,
        "postgresql_libs": POSTGRES_LIBS,
        "mongodb_libs": MONGO_LIBS,
        # SQLite 为 Python 标准库内置驱动，本机始终可用，无需安装 / 探测
        "sqlite_libs": True,
    }


@router.get("/connections")
async def list_connections():
    """返回全部数据库连接配置（密码脱敏，不回传明文）。

    密码只返回 has_password 布尔标记；编辑时前端留空表示保持原密码。
    减少凭据暴露面：即使管理员会话被 XSS/抓包，也无法读回已保存的密码。
    """
    conns = _load_connections()
    for c in conns:
        c["has_password"] = bool(c.get("password"))
        c["password"] = ""
    return {"connections": conns}


@router.post("/connections")
async def add_connection(req: DBConnection):
    """新增数据库连接配置。"""
    data = _load_connections()
    conn = {
        "id": str(uuid.uuid4())[:8],
        "name": req.name,
        "db_type": req.db_type,
        "host": req.host,
        "port": req.port,
        "username": req.username or "",
        "password": req.password or "",
        "database": req.database or "",
        "created_at": datetime.now().isoformat(),
    }
    data.append(conn)
    _save_connections(data)
    logger.info("新增数据库连接: %s (%s@%s:%s)", conn["name"], conn["db_type"], conn["host"], conn["port"])
    # 响应同样脱敏（调用方本来就知道刚输入的密码）
    return {**conn, "has_password": bool(conn.get("password")), "password": ""}


@router.put("/connections/{conn_id}")
async def update_connection(conn_id: str, req: DBConnection):
    """编辑数据库连接配置（密码留空表示保持原密码）。"""
    data = _load_connections()
    conn = next((c for c in data if c["id"] == conn_id), None)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    conn["name"] = req.name
    conn["db_type"] = req.db_type
    conn["host"] = req.host
    conn["port"] = req.port
    conn["username"] = req.username or ""
    # 密码脱敏配套：列表不再回传明文，编辑时留空即保持已保存的密码
    conn["password"] = req.password or "" if (req.password or "").strip() else conn.get("password", "")
    conn["database"] = req.database or ""
    conn["updated_at"] = datetime.now().isoformat()
    _save_connections(data)
    logger.info("编辑数据库连接: %s (%s)", conn["name"], conn["id"])
    return {
        **conn,
        "has_password": bool(conn.get("password")),
        "password": "",
    }


@router.delete("/connections/{conn_id}")
async def delete_connection(conn_id: str):
    """删除数据库连接配置。"""
    data = _load_connections()
    data = [c for c in data if c["id"] != conn_id]
    _save_connections(data)
    logger.info("删除数据库连接: %s", conn_id)
    return {"ok": True}


@router.post("/connections/{conn_id}/test")
async def test_connection(conn_id: str):
    """测试连接可用性，按类型分别执行最小验证。"""
    conn = _find_connection(conn_id)
    if conn["db_type"] == "mysql":
        if not MYSQL_LIBS:
            raise HTTPException(status_code=503, detail="PyMySQL not installed")
        try:
            db = pymysql.connect(
                host=conn["host"],
                port=conn.get("port", 3306),
                user=conn.get("username", "root"),
                password=conn.get("password", ""),
                database=conn.get("database", "") or None,
                connect_timeout=5,
            )
            with db.cursor() as cur:
                cur.execute("SELECT 1")
            db.close()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "redis":
        if not REDIS_LIBS:
            raise HTTPException(status_code=503, detail="Redis client not installed")
        try:
            r = redis.Redis(
                host=conn["host"],
                port=conn.get("port", 6379),
                password=conn.get("password") or None,
                socket_connect_timeout=5,
            )
            r.ping()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "postgresql":
        if not POSTGRES_LIBS:
            raise HTTPException(status_code=503, detail="psycopg2 not installed")
        try:
            db = _pg_connect(conn)
            with db.cursor() as cur:
                cur.execute("SELECT 1")
            db.close()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "mongodb":
        if not MONGO_LIBS:
            raise HTTPException(status_code=503, detail="pymongo not installed")
        try:
            client = _mongo_client(conn)
            client.admin.command("ping")
            client.close()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "sqlite":
        # SQLite：检查目标文件可打开（不存在时尝试创建新库文件）
        try:
            path = _sqlite_file(conn)
            parent = os.path.dirname(path) or "."
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            db = sqlite3.connect(path, timeout=5)
            db.execute("SELECT 1")
            db.close()
            return {"ok": True, "file": path}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    raise HTTPException(status_code=400, detail="Unsupported db_type")


@router.get("/connections/{conn_id}/databases")
async def list_databases(conn_id: str):
    """列出数据库（MySQL/PG 返回库列表，Redis 返回 info，MongoDB 返回库列表）。"""
    conn = _find_connection(conn_id)
    if conn["db_type"] == "mysql":
        if not MYSQL_LIBS:
            raise HTTPException(status_code=503, detail="PyMySQL not installed")
        try:
            db = pymysql.connect(
                host=conn["host"],
                port=conn.get("port", 3306),
                user=conn.get("username", "root"),
                password=conn.get("password", ""),
                connect_timeout=5,
            )
            with db.cursor() as cur:
                cur.execute("SHOW DATABASES")
                rows = cur.fetchall()
            with db.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM information_schema.processlist")
                proc_count = cur.fetchone()[0]
            db.close()
            return {"databases": [r[0] for r in rows], "process_count": proc_count}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "redis":
        if not REDIS_LIBS:
            raise HTTPException(status_code=503, detail="Redis client not installed")
        try:
            r = redis.Redis(
                host=conn["host"],
                port=conn.get("port", 6379),
                password=conn.get("password") or None,
                socket_connect_timeout=5,
            )
            info = r.info()
            dbs = []
            for key in sorted(info.keys()):
                if key.startswith("db"):
                    dbs.append({"db": key, "keys": info[key].get("keys", 0)})
            return {"info": info, "keyspaces": dbs}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "postgresql":
        if not POSTGRES_LIBS:
            raise HTTPException(status_code=503, detail="psycopg2 not installed")
        try:
            db = _pg_connect(conn)
            with db.cursor() as cur:
                cur.execute(
                    "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname"
                )
                rows = cur.fetchall()
            with db.cursor() as cur:
                cur.execute("SHOW server_version")
                version = cur.fetchone()[0]
            db.close()
            return {"databases": [r[0] for r in rows], "version": version}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "mongodb":
        if not MONGO_LIBS:
            raise HTTPException(status_code=503, detail="pymongo not installed")
        try:
            client = _mongo_client(conn)
            databases = client.list_database_names()
            version = client.server_info().get("version", "")
            client.close()
            return {"databases": databases, "version": version}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "sqlite":
        # SQLite：单文件库，列出库内数据表（排除 sqlite_* 系统表）+ 文件信息
        try:
            path = _sqlite_file(conn)
            if not os.path.exists(path):
                # 文件尚未创建（新连接）时返回空表列表，前端可提示先建库/执行建表 DDL
                return {"tables": [], "file": path, "exists": False, "size": 0}
            db = sqlite3.connect(path, timeout=5)
            cur = db.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            tables = [r[0] for r in cur.fetchall()]
            db.close()
            size = os.path.getsize(path)
            return {"tables": tables, "file": path, "exists": True, "size": size}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    raise HTTPException(status_code=400, detail="Unsupported db_type")


# ---------------------------------------------------------------------------
# SQL 安全过滤（替代原先仅含 4 个弱子串的黑名单，并修复其绕过缺陷）。
#
# 原有逻辑：forbidden = ["drop database","drop user","shutdown","grant all"]
# 仅做子串匹配且未清洗注释，可被轻易绕过（见上版）。
#
# 上一版修复改为 _DANGER_FRAGMENTS 子串黑名单，但仍存在绕过：
#   - 仅覆盖 into outfile/load_file/drop/create 等少量词，遗漏各数据库专有
#     的「服务端文件读写原语」，例如 PostgreSQL 的 pg_read_file / pg_ls_dir /
#     pg_write_file 等，在特权（superuser/root）账号下可造成服务端任意文件
#     读 / 写，进而升级为宿主机 RCE；
#   - 子串匹配会误伤（列名含 drop）且无法覆盖同义原语。
#
# 本版加固（纵深防御）：
#   1. 引号感知地清洗注释并把字符串字面量内容置空（_normalize_sql），避免
#      字符串内 '--'/'#' 被误删、也避免字面量里的关键词造成误判/混淆；
#   2. 首动词白名单：仅允许 SELECT/WITH/SHOW/DESC/EXPLAIN/PRAGMA/USE 及受限
#      DML（INSERT INTO/UPDATE/DELETE FROM/REPLACE INTO），其余动词一律拒绝；
#   3. 多语句拒绝：清洗后按 ';' 拆分，存在多于一个非空语句即拒绝；
#   4. 危险原语拦截：即便以查询动词开头，也禁止文件读写（pg_read_file /
#      pg_ls_dir / pg_write_file / load_file / into outfile / into dumpfile /
#      into file / load data）、SELECT ... INTO（建表/写变量/写文件）及提权
#      执行类原语（采用单词边界正则，避免误伤正常标识符）。
# ---------------------------------------------------------------------------
def _normalize_sql(sql: str) -> str:
    """清洗注释并把「字符串字面量」内容置空，便于安全地做危险原语匹配。

    危险原语（pg_read_file / into outfile / drop ...）永远不会出现在字符串
    字面量内部，因此把字面量内容置空可避免「列数据/注释含关键词」造成误判，
    同时保留引号外壳以便多语句拆分时正确跳过字面量内的分号。

    安全修复（第八轮审计，High）：此前把单引号与双引号一律当作字符串字面量
    处理，进入引号后每个字符都被替换为引号外壳（内容整体置空）——但
    PostgreSQL 中双引号是「标识符引用」（quoted identifier）而非字符串，
    SELECT "pg_read_file"('/etc/passwd') 经清洗后危险函数名完全消失，
    全部危险正则不命中，过滤器被彻底绕过（数据库服务端任意文件读写）。

    修复策略：
      - 单引号 '...'        -> 字符串字面量，内容置空（保留外壳）
      - 双引号 "..." / 反引号 `...` -> 标识符，内容保留原文，使被引号包裹的
        危险函数名仍能被单词边界正则命中（过滤器宁可误拦，不可漏拦）
      - 转义引号（'' / "" / ``）按 SQL 语义处理，避免提前退出引用状态
    """
    out = []
    i, n = 0, len(sql)
    quote = None  # 当前引用类型: ' 字符串字面量 / " 标识符 / ` 标识符
    while i < n:
        ch = sql[i]
        if quote:
            # 转义引号：连续两个同种引号表示一个字面引号，不结束引用状态
            if ch == quote and i + 1 < n and sql[i + 1] == quote:
                if quote == "'":
                    out.append(quote)  # 字符串内转义引号：保留外壳即可
                else:
                    out.append(ch)     # 标识符内转义引号：保留原文
                i += 2
                continue
            if ch == quote:
                out.append(quote)
                quote = None
                i += 1
                continue
            if quote == "'":
                # 字符串字面量：内容置空（只保留外壳），避免误判与混淆
                out.append(quote)
            else:
                # 标识符（"..." / `...`）：保留原文，防止过滤绕过
                out.append(ch)
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if sql[i:i + 2] == "--":
            while i < n and sql[i] not in ("\r", "\n"):
                i += 1
            continue
        if ch == "#":
            while i < n and sql[i] not in ("\r", "\n"):
                i += 1
            continue
        if sql[i:i + 2] == "/*":
            end = sql.find("*/", i + 2)
            i = end + 2 if end != -1 else n
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# 允许作为语句首动词的「查询 / 受限 DML」白名单（忽略前导空白、不区分大小写）。
# 不满足此白名单的语句（DROP/CREATE/ALTER/TRUNCATE/GRANT/REVOKE/RENAME/
# ATTACH/COPY/SHUTDOWN/CALL/DO/PREPARE/EXECUTE/IMPORT 等）一律拒绝。
_ALLOWED_LEAD_RE = re.compile(
    r"^\s*("
    r"select|with|show|desc|describe|explain|pragma|use"
    r"|insert\s+into|update|delete\s+from|replace\s+into"
    r")\b",
    re.IGNORECASE,
)

# 即便语句以允许的查询动词开头，也禁止以下「文件读写 / 提权 / 执行」原语
# （按单词边界匹配，避免误伤正常列名/别名）。覆盖各数据库专有函数：
#   PostgreSQL   : pg_read_file / pg_read_binary_file / pg_ls_dir /
#                  pg_write_file / pg_ls_waldir / pg_logdir_ls / pg_stat_file
#   MySQL/MariaDB: load_file / into outfile / into dumpfile / into file / load data
#   通用执行      : call / do / prepare / execute / copy / import / attach
_DANGER_PATTERNS = [
    re.compile(r"\bpg_read_file\b", re.IGNORECASE),
    re.compile(r"\bpg_read_binary_file\b", re.IGNORECASE),
    re.compile(r"\bpg_ls_dir\b", re.IGNORECASE),
    re.compile(r"\bpg_write_file\b", re.IGNORECASE),
    re.compile(r"\bpg_ls_waldir\b", re.IGNORECASE),
    re.compile(r"\bpg_logdir_ls\b", re.IGNORECASE),
    re.compile(r"\bpg_stat_file\b", re.IGNORECASE),
    re.compile(r"\bload_file\b", re.IGNORECASE),
    re.compile(r"\binto\s+outfile\b", re.IGNORECASE),
    re.compile(r"\binto\s+dumpfile\b", re.IGNORECASE),
    re.compile(r"\binto\s+file\b", re.IGNORECASE),
    re.compile(r"\bload\s+data\b", re.IGNORECASE),
    re.compile(r"\bcall\b", re.IGNORECASE),
    re.compile(r"\bdo\b", re.IGNORECASE),
    re.compile(r"\bprepare\b", re.IGNORECASE),
    re.compile(r"\bexecute\b", re.IGNORECASE),
    re.compile(r"\bcopy\b", re.IGNORECASE),
    re.compile(r"\bimport\b", re.IGNORECASE),
    re.compile(r"\battach\b", re.IGNORECASE),
]


def _reject_dangerous_sql(sql: str) -> bool:
    """返回 True 表示该 SQL 应被拒绝（危险）。

    加固策略（替换原脆弱的子串黑名单）：
      1. 引号感知地清洗注释并把字符串字面量内容置空，避免误判/混淆；
      2. 首动词白名单：仅允许 SELECT/WITH/SHOW/DESC/EXPLAIN/PRAGMA/USE 及
         受限 DML（INSERT INTO / UPDATE / DELETE FROM / REPLACE INTO），
         其余动词（DROP/CREATE/ALTER/...）直接拒绝；
      3. 多语句拒绝：清洗后按 ';' 拆分，存在多于一个非空语句即拒绝；
      4. 危险原语拦截：即便以查询动词开头，也禁止文件读写（pg_read_file /
         pg_ls_dir / pg_write_file / load_file / into outfile ...）、
         SELECT ... INTO（建表/写变量/写文件）及提权执行类原语。
    """
    cleaned = _normalize_sql(sql).strip()
    if not cleaned:
        return True
    # 多语句：以分号拆分后存在多于一个非空语句即视为高危（防止附加恶意语句）
    if len([p for p in cleaned.split(";") if p.strip()]) > 1:
        return True
    # 首动词白名单：不满足则拒绝（覆盖所有 DDL / 提权 / 执行类动词）
    m = _ALLOWED_LEAD_RE.match(cleaned)
    if not m:
        return True
    # SELECT ... INTO 一律禁止（建表 / 写变量 / 写文件）
    if m.group(1).strip().lower() == "select" and re.search(r"\binto\b", cleaned, re.IGNORECASE):
        return True
    # 危险原语拦截（单词边界，避免误伤列名/别名）
    if any(pat.search(cleaned) for pat in _DANGER_PATTERNS):
        return True
    return False


@router.post("/connections/{conn_id}/query")
async def run_query(conn_id: str, req: DBQuery):
    """执行查询：SQL 类返回列+行，Redis 返回命令结果，MongoDB 返回文档列表。"""
    conn = _find_connection(conn_id)
    if conn["db_type"] == "mysql":
        if not MYSQL_LIBS:
            raise HTTPException(status_code=503, detail="PyMySQL not installed")
        sql = (req.sql or "").strip()
        if not sql:
            raise HTTPException(status_code=400, detail="Empty SQL")
        if _reject_dangerous_sql(sql):
            raise HTTPException(status_code=403, detail="该 SQL 包含危险语句，已被安全策略拒绝")
        try:
            db = pymysql.connect(
                host=conn["host"],
                port=conn.get("port", 3306),
                user=conn.get("username", "root"),
                password=conn.get("password", ""),
                database=conn.get("database", "") or None,
                connect_timeout=5,
            )
            with db.cursor() as cur:
                cur.execute(sql)
                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    db.close()
                    return {"columns": columns, "rows": rows}
                else:
                    affected = cur.rowcount
                    db.commit()
                    db.close()
                    return {"affected": affected}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "redis":
        if not REDIS_LIBS:
            raise HTTPException(status_code=503, detail="Redis client not installed")
        try:
            r = redis.Redis(
                host=conn["host"],
                port=conn.get("port", 6379),
                password=conn.get("password") or None,
                db=req.db_index or 0,
                socket_connect_timeout=5,
            )
            cmd = (req.command or "").strip()
            if not cmd:
                raise HTTPException(status_code=400, detail="Empty command")
            parts = cmd.split()
            method = parts[0].lower()
            args = parts[1:]
            # 安全防护：禁止反射调用危险方法（与 MySQL/PG 的 forbidden 黑名单对齐）。
            # hasattr+getattr 可触达 redis.Redis 的任意方法，包括：
            #   shutdown（关停远程 Redis）、config_set/save/bgrewriteaof
            #   （配合 dir/dbfilename 可写任意文件形成 RCE 链）、
            #   eval/evalsha/script_load（Lua 任意执行）、
            #   replicaof/slaveof/migrate/restore/cluster（数据外带与拓扑破坏）、
            #   execute_command（绕过一切上层过滤的原始命令入口）。
            if _is_forbidden_redis(method):
                raise HTTPException(
                    status_code=403, detail=f"Forbidden redis command: {method}"
                )
            if hasattr(r, method):
                result = getattr(r, method)(*args)
                if isinstance(result, bytes):
                    result = result.decode("utf-8", errors="replace")
                return {"result": result}
            else:
                raise HTTPException(
                    status_code=400, detail=f"Unknown redis command: {method}"
                )
        except HTTPException:
            # 400/403 等业务校验异常原样放行，不被下方 502 兜底改写
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "postgresql":
        if not POSTGRES_LIBS:
            raise HTTPException(status_code=503, detail="psycopg2 not installed")
        sql = (req.sql or "").strip()
        if not sql:
            raise HTTPException(status_code=400, detail="Empty SQL")
        if _reject_dangerous_sql(sql):
            raise HTTPException(status_code=403, detail="该 SQL 包含危险语句，已被安全策略拒绝")
        try:
            db = _pg_connect(conn)
            db.autocommit = True
            with db.cursor() as cur:
                cur.execute(sql)
                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchall()
                    db.close()
                    return {"columns": columns, "rows": rows}
                else:
                    affected = cur.rowcount
                    db.close()
                    return {"affected": affected}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "mongodb":
        if not MONGO_LIBS:
            raise HTTPException(status_code=503, detail="pymongo not installed")
        collection_name = (req.collection or "").strip()
        if not collection_name:
            raise HTTPException(status_code=400, detail="Collection name required")
        # 解析 JSON 过滤条件，非法 JSON 返回 400
        try:
            filter_dict = json.loads(req.filter) if (req.filter or "").strip() else {}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid filter JSON: {e}")
        if not isinstance(filter_dict, dict):
            raise HTTPException(status_code=400, detail="Filter must be a JSON object")
        limit = max(1, min(req.limit or 100, 500))
        try:
            client = _mongo_client(conn)
            db = client[conn.get("database") or "test"]
            col = db[collection_name]
            docs = list(col.find(filter_dict).limit(limit))
            client.close()
            return {"result": [_mongo_jsonable(d) for d in docs]}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "sqlite":
        # SQLite：执行 SQL（只读查询或写操作），复用 SQL 类黑名单防危险片段
        sql = (req.sql or "").strip()
        if not sql:
            raise HTTPException(status_code=400, detail="Empty SQL")
        if _reject_dangerous_sql(sql):
            raise HTTPException(status_code=403, detail="该 SQL 包含危险语句，已被安全策略拒绝")
        path = _sqlite_file(conn)
        try:
            db = sqlite3.connect(path, timeout=5)
            cur = db.cursor()
            cur.execute(sql)
            if cur.description:
                # 查询语句：返回列+行
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                db.close()
                return {"columns": columns, "rows": rows}
            else:
                # 写语句：提交并返回受影响行数
                affected = cur.rowcount
                db.commit()
                db.close()
                return {"affected": affected}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    raise HTTPException(status_code=400, detail="Unsupported db_type")


@router.post("/connections/{conn_id}/create-db")
async def create_database(conn_id: str, body: dict):
    """创建数据库：MySQL/PG 执行 CREATE DATABASE，MongoDB 按需自动创建。"""
    conn = _find_connection(conn_id)
    # SQLite 是单文件库，无服务器级建库概念，可在查询页执行 CREATE TABLE 建表
    if conn["db_type"] == "sqlite":
        raise HTTPException(
            status_code=400, detail="SQLite 为单文件库，请在查询页执行 CREATE TABLE 建表"
        )
    db_name = body.get("name", "").strip()
    if not db_name:
        raise HTTPException(status_code=400, detail="Database name required")
    # 库名白名单校验，防止反引号逃逸注入 SQL
    if not _DB_NAME_RE.match(db_name):
        raise HTTPException(
            status_code=400, detail="数据库名仅允许字母、数字、下划线和中划线（≤64 字符）"
        )
    if conn["db_type"] == "mysql":
        if not MYSQL_LIBS:
            raise HTTPException(status_code=503, detail="PyMySQL not installed")
        try:
            db = pymysql.connect(
                host=conn["host"],
                port=conn.get("port", 3306),
                user=conn.get("username", "root"),
                password=conn.get("password", ""),
                connect_timeout=5,
            )
            with db.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            db.commit()
            db.close()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "postgresql":
        if not POSTGRES_LIBS:
            raise HTTPException(status_code=503, detail="psycopg2 not installed")
        try:
            db = _pg_connect(conn)
            db.autocommit = True
            with db.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{db_name}"')
            db.close()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "mongodb":
        # MongoDB 数据库在首次写入时自动创建，此处仅做名称校验
        if not MONGO_LIBS:
            raise HTTPException(status_code=503, detail="pymongo not installed")
        return {"ok": True, "note": "MongoDB 数据库将在首次写入时自动创建"}
    raise HTTPException(status_code=400, detail="Only MySQL/PostgreSQL support create-db")


@router.post("/connections/{conn_id}/delete-db")
async def delete_database(conn_id: str, body: dict):
    """删除数据库：MySQL/PG 执行 DROP DATABASE，MongoDB 调用 drop_database。"""
    conn = _find_connection(conn_id)
    # SQLite 是单文件库，删除库应删除文件本身，属文件管理范畴，不在此提供
    if conn["db_type"] == "sqlite":
        raise HTTPException(
            status_code=400, detail="SQLite 为单文件库，请直接管理数据库文件（本接口不删除文件）"
        )
    db_name = body.get("name", "").strip()
    if not db_name:
        raise HTTPException(status_code=400, detail="Database name required")
    # 库名白名单校验，防止反引号逃逸注入 SQL
    if not _DB_NAME_RE.match(db_name):
        raise HTTPException(
            status_code=400, detail="数据库名仅允许字母、数字、下划线和中划线（≤64 字符）"
        )
    if conn["db_type"] == "mysql":
        if not MYSQL_LIBS:
            raise HTTPException(status_code=503, detail="PyMySQL not installed")
        try:
            db = pymysql.connect(
                host=conn["host"],
                port=conn.get("port", 3306),
                user=conn.get("username", "root"),
                password=conn.get("password", ""),
                connect_timeout=5,
            )
            with db.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
            db.commit()
            db.close()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "postgresql":
        if not POSTGRES_LIBS:
            raise HTTPException(status_code=503, detail="psycopg2 not installed")
        try:
            db = _pg_connect(conn)
            db.autocommit = True
            with db.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
            db.close()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    elif conn["db_type"] == "mongodb":
        if not MONGO_LIBS:
            raise HTTPException(status_code=503, detail="pymongo not installed")
        try:
            client = _mongo_client(conn)
            client.drop_database(db_name)
            client.close()
            return {"ok": True}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    raise HTTPException(status_code=400, detail="Only MySQL/PostgreSQL/MongoDB support delete-db")
