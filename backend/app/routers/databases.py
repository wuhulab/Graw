import json
import logging
import os
import re
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
# 以下方法可关停服务 / 改配置写任意文件（RCE 链）/ 执行 Lua / 外带数据，
# 必须与 MySQL/PG 的 SQL 黑名单（drop database / shutdown / grant all）同级拦截。
_REDIS_FORBIDDEN_METHODS = {
    "shutdown",            # 关停远程 Redis（DoS）
    "config_set",          # 改 dir/dbfilename + save 即任意文件写（RCE 链）
    "config_rewrite",
    "save", "bgsave",      # 落盘触发上述文件写
    "bgrewriteaof",
    "eval", "evalsha",     # Lua 脚本任意执行
    "script_load", "script",
    "replicaof", "slaveof",  # 主从复制外带数据
    "migrate", "restore",  # 跨实例搬数据
    "cluster",             # 集群拓扑破坏
    "swapdb",
    "execute_command",     # 原始命令入口，绕过一切上层过滤
    "register_script",     # 加载任意 Lua
    "pubsub",
}

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
    """数据库连接配置模型（支持 mysql / redis / postgresql / mongodb）。"""

    name: str = Field(..., min_length=1)
    db_type: str = Field(..., pattern="^(mysql|redis|postgresql|mongodb)$")
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
    raise HTTPException(status_code=400, detail="Unsupported db_type")


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
        forbidden = ["drop database", "drop user", "shutdown", "grant all"]
        lower = sql.lower()
        if any(f in lower for f in forbidden):
            raise HTTPException(status_code=403, detail="Forbidden SQL fragment")
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
            if method in _REDIS_FORBIDDEN_METHODS or method.startswith("_"):
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
        forbidden = ["drop database", "drop user", "shutdown", "grant all"]
        lower = sql.lower()
        if any(f in lower for f in forbidden):
            raise HTTPException(status_code=403, detail="Forbidden SQL fragment")
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
    raise HTTPException(status_code=400, detail="Unsupported db_type")


@router.post("/connections/{conn_id}/create-db")
async def create_database(conn_id: str, body: dict):
    """创建数据库：MySQL/PG 执行 CREATE DATABASE，MongoDB 按需自动创建。"""
    conn = _find_connection(conn_id)
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
