# -*- coding: utf-8 -*-
"""
test_databases_sqlite.py — databases.py 的 SQLite 管理单元测试

区别于远程数据库：SQLite 是嵌入式单文件库，无需 host/port/账号，用文件路径访问。
不依赖后端运行：直接把 databases 模块的 DATA_DIR / DB_FILE 打到临时目录，避免污染真实 data/。

覆盖：
  1. SQLite 文件路径解析（相对基于数据目录 / 绝对路径原样）
  2. 空路径与控制字符路径 400 拒绝
  3. DBConnection 模型允许 sqlite 类型
  4. 端到端：连接测试 → 建表 → 插入 → 查询 → 表列表 → 危险片段 403
  5. 服务器级建/删库对 sqlite 明确 400
  6. status 声明 sqlite 始终可用

运行：.venv\\Scripts\\python.exe test_databases_sqlite.py
"""
import asyncio
import json
import os
import sys
import tempfile

# 确保以 backend 目录为工作目录时可直接导入 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import HTTPException  # noqa: E402

from app.routers import databases as dmod  # noqa: E402

PASS = 0
FAIL = 0

SQLITE_CONN = {
    "id": "sqlite1",
    "name": "本地SQLite",
    "db_type": "sqlite",
    "host": "",
    "port": 0,
    "username": "",
    "password": "",
    "database": "demo.db",
}

# 保存真实路径，测试结束恢复
_REAL_DATA = dmod.DATA_DIR
_REAL_DB = dmod.DB_FILE


def setUp(env_dir):
    """指向临时数据目录并写入一条 sqlite 连接。"""
    dmod.DATA_DIR = env_dir
    dmod.DB_FILE = os.path.join(env_dir, "databases.json")
    with open(dmod.DB_FILE, "w", encoding="utf-8") as f:
        json.dump([SQLITE_CONN], f, ensure_ascii=False)


def tearDown():
    """恢复真实路径。"""
    dmod.DATA_DIR = _REAL_DATA
    dmod.DB_FILE = _REAL_DB


def run_async(coro):
    """同步执行一个协程并捕获异常返回 (result, exc)。"""
    try:
        return (asyncio.run(coro), None)
    except HTTPException as e:
        return (None, e)
    except Exception as e:  # noqa: BLE001
        return (None, e)


def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        print(f"[ ok ] {name}")
        PASS += 1
    else:
        print(f"[FAIL] {name}: {detail}")
        FAIL += 1


def test_sqlite_file_resolution(env_dir):
    """相对路径基于数据目录，绝对路径原样保留。"""
    rel = dmod._sqlite_file({"database": "demo.db"})
    report("sqlite 相对路径基于数据目录", rel == os.path.normpath(os.path.join(env_dir, "demo.db")),
           f"got {rel!r}")
    abs_path = os.path.normpath(os.path.join(env_dir, "d1", "demo.db"))
    got = dmod._sqlite_file({"database": abs_path})
    report("sqlite 绝对路径原样保留", got == abs_path, f"got {got!r}")


def test_sqlite_file_rejects_invalid(env_dir):
    """空路径、控制字符路径应被 400 拒绝。"""
    for bad in ["", "   ", "a\x00b", "a\rb", "a\nb"]:
        try:
            dmod._sqlite_file({"database": bad})
            ok = False
            detail = "未按预期拒绝"
        except HTTPException as e:
            ok = e.status_code == 400
            detail = f"status={e.status_code}"
        except Exception as e:  # noqa: BLE001
            ok = False
            detail = f"{type(e).__name__}: {e}"
        report(f"sqlite 非法路径拒绝 {bad!r}", ok, detail)


def test_db_connection_model_accepts_sqlite(env_dir):
    """DBConnection 模型应允许 sqlite 类型。"""
    try:
        model = dmod.DBConnection(name="x", db_type="sqlite", database="demo.db")
        report("DBConnection 允许 sqlite 类型", model.db_type == "sqlite" and model.database == "demo.db", str(model))
    except Exception as e:  # noqa: BLE001
        report("DBConnection 允许 sqlite 类型", False, f"{type(e).__name__}: {e}")


def test_sqlite_end_to_end(env_dir):
    """建表 → 插入 → 查询 → 列表 → 危险片段拦截。"""

    # 在 case() 事件循环内直接 await，避免嵌套 asyncio.run
    async def chk(name, fn, pred):
        try:
            res = await fn()
            report(name, pred(res), f"got {res}")
        except HTTPException as e:
            report(name, False, f"HTTP {e.status_code}: {e.detail}")
        except Exception as e:  # noqa: BLE001
            report(name, False, f"{type(e).__name__}: {e}")

    async def case():
        # 连接测试：文件尚不存在也能连通（创建新库文件）
        await chk("sqlite 连接测试 ok", lambda: dmod.test_connection("sqlite1"),
                  lambda r: r["ok"] and r["file"].endswith("demo.db"))

        # 建表
        await chk("sqlite 建表", lambda: dmod.run_query(
            "sqlite1", dmod.DBQuery(sql="CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT)")),
            lambda r: "affected" in r)

        # 插入
        await chk("sqlite 插入影响行数", lambda: dmod.run_query(
            "sqlite1", dmod.DBQuery(sql="INSERT INTO users(name) VALUES ('alice'), ('bob')")),
            lambda r: r["affected"] == 2)

        # 查询返回列+行
        await chk("sqlite 查询返回列", lambda: dmod.run_query(
            "sqlite1", dmod.DBQuery(sql="SELECT id, name FROM users ORDER BY id")),
            lambda r: r["columns"] == ["id", "name"])
        await chk("sqlite 查询返回行数", lambda: dmod.run_query(
            "sqlite1", dmod.DBQuery(sql="SELECT id, name FROM users ORDER BY id")),
            lambda r: len(r["rows"]) == 2)

        # 表列表
        await chk("sqlite 表列表含 users", lambda: dmod.list_databases("sqlite1"),
                  lambda r: "users" in r["tables"])
        await chk("sqlite 文件信息", lambda: dmod.list_databases("sqlite1"),
                  lambda r: r["exists"] and r["size"] > 0)

        # 危险 SQL 片段 403
        try:
            await dmod.run_query("sqlite1", dmod.DBQuery(sql="DROP DATABASE foo"))
            report("sqlite 危险片段 403", False, "未按预期拒绝")
        except HTTPException as e:
            report("sqlite 危险片段 403", e.status_code == 403, f"status={e.status_code}")

    asyncio.run(case())


def test_sqlite_create_delete_db_rejected(env_dir):
    """SQLite 单文件库不支持服务器级建/删库，应返回明确 400。"""
    res, exc = run_async(dmod.create_database("sqlite1", {"name": "foo"}))
    report("sqlite 建库 400", isinstance(exc, HTTPException) and exc.status_code == 400, f"exc={exc}")
    res, exc = run_async(dmod.delete_database("sqlite1", {"name": "foo"}))
    report("sqlite 删库 400", isinstance(exc, HTTPException) and exc.status_code == 400, f"exc={exc}")


def test_sqlite_status_flags(env_dir):
    """status 应声明 SQLite 始终可用。"""
    status = asyncio.run(dmod.db_status())
    report("status.sqlite_libs 为 True", status["sqlite_libs"] is True, f"got {status}")


if __name__ == "__main__":
    # 每个用例用独立临时目录，隔离 demo.db 状态
    d1 = tempfile.mkdtemp(prefix="dsqlite_")
    setUp(d1)
    test_sqlite_file_resolution(d1)
    test_sqlite_file_rejects_invalid(d1)
    test_db_connection_model_accepts_sqlite(d1)
    test_sqlite_end_to_end(d1)
    test_sqlite_create_delete_db_rejected(d1)
    test_sqlite_status_flags(d1)
    tearDown()

    print(f"\nSQLite 数据库管理测试: {PASS} 项通过, {FAIL} 项失败")
    sys.exit(1 if FAIL else 0)