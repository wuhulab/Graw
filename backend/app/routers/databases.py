import json
import os
import platform
import re
import subprocess
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

# 数据库名白名单：库名会拼入反引号 SQL 标识符（CREATE/DROP DATABASE），
# 必须禁止反引号 / 引号 / 分号等字符，防止标识符逃逸注入多条 SQL。
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
DB_FILE = os.path.join(DATA_DIR, "databases.json")

MYSQL_LIBS = False
REDIS_LIBS = False

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


def _load_connections() -> list:
    if not os.path.exists(DB_FILE):
        return []
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_connections(data: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _auto_detect_mysql() -> Optional[dict]:
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


class DBConnection(BaseModel):
    name: str = Field(..., min_length=1)
    db_type: str = Field(..., pattern="^(mysql|redis)$")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=3306)
    username: Optional[str] = ""
    password: Optional[str] = ""
    database: Optional[str] = ""


class DBQuery(BaseModel):
    sql: Optional[str] = None  # for mysql
    command: Optional[str] = None  # for redis
    db_index: Optional[int] = 0  # for redis db index


@router.get("/status")
async def db_status():
    mysql = _auto_detect_mysql()
    red = _auto_detect_redis()
    return {
        "mysql_detected": bool(mysql),
        "redis_detected": bool(red),
        "mysql_libs": MYSQL_LIBS,
        "redis_libs": REDIS_LIBS,
    }


@router.get("/connections")
async def list_connections():
    return {"connections": _load_connections()}


@router.post("/connections")
async def add_connection(req: DBConnection):
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
    return conn


@router.delete("/connections/{conn_id}")
async def delete_connection(conn_id: str):
    data = _load_connections()
    data = [c for c in data if c["id"] != conn_id]
    _save_connections(data)
    return {"ok": True}


@router.post("/connections/{conn_id}/test")
async def test_connection(conn_id: str):
    conns = _load_connections()
    conn = next((c for c in conns if c["id"] == conn_id), None)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
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
    raise HTTPException(status_code=400, detail="Unsupported db_type")


@router.get("/connections/{conn_id}/databases")
async def list_databases(conn_id: str):
    conns = _load_connections()
    conn = next((c for c in conns if c["id"] == conn_id), None)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
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
    raise HTTPException(status_code=400, detail="Unsupported db_type")


@router.post("/connections/{conn_id}/query")
async def run_query(conn_id: str, req: DBQuery):
    conns = _load_connections()
    conn = next((c for c in conns if c["id"] == conn_id), None)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
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
            if hasattr(r, method):
                result = getattr(r, method)(*args)
                if isinstance(result, bytes):
                    result = result.decode("utf-8", errors="replace")
                return {"result": result}
            else:
                raise HTTPException(
                    status_code=400, detail=f"Unknown redis command: {method}"
                )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    raise HTTPException(status_code=400, detail="Unsupported db_type")


@router.post("/connections/{conn_id}/create-db")
async def create_database(conn_id: str, body: dict):
    conns = _load_connections()
    conn = next((c for c in conns if c["id"] == conn_id), None)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
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
    raise HTTPException(status_code=400, detail="Only MySQL supports create-db")


@router.post("/connections/{conn_id}/delete-db")
async def delete_database(conn_id: str, body: dict):
    conns = _load_connections()
    conn = next((c for c in conns if c["id"] == conn_id), None)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
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
    raise HTTPException(status_code=400, detail="Only MySQL supports delete-db")
