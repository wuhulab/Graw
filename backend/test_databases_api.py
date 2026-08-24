"""
数据库模块（app/routers/databases.py）回归测试脚本

策略：
  - 使用 FastAPI TestClient 直接挂载 databases 路由，无需启动真实服务与鉴权。
  - 将 DB_FILE 指向临时文件，避免污染真实 databases.json。
  - 覆盖：状态字段 / 连接 CRUD（含新增 PostgreSQL/MongoDB） / 编辑接口 /
    非法类型校验 / 查询参数校验 / 建删库名白名单 / 不可达连接返回 502 而非 500。

用法：
  python test_databases_api.py
"""
import os
import sys
import tempfile

# 允许以脚本方式直接运行（backend 目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import databases

# ---- 将数据文件指向临时文件，不影响真实数据 ----
_tmpdir = tempfile.mkdtemp(prefix="graw_db_test_")
databases.DB_FILE = os.path.join(_tmpdir, "databases.json")

app = FastAPI()
app.include_router(databases.router, prefix="/api/databases")
client = TestClient(app)

PASS = 0
FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    msg = f"  PASS  {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def fail(name, detail):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {name}: {detail}")


def check(name, cond, detail=""):
    if cond:
        ok(name, detail)
    else:
        fail(name, detail)


# 各类型默认端口（与前端 ConnectionFormWindow 保持一致）
PORTS = {"mysql": 3306, "redis": 6379, "postgresql": 5432, "mongodb": 27017}


def conn_body(db_type, name="test"):
    return {
        "name": name,
        "db_type": db_type,
        "host": "127.0.0.1",
        "port": PORTS[db_type],
        "username": "u",
        "password": "p",
        "database": "d",
    }


def test_status():
    r = client.get("/api/databases/status")
    check("status 返回 200", r.status_code == 200)
    data = r.json()
    check("status 包含新增驱动字段", "postgresql_libs" in data and "mongodb_libs" in data)
    check("status 含探测字段", "postgresql_detected" in data and "mongodb_detected" in data)
    check("PG 驱动已安装", data.get("postgresql_libs") is True, "psycopg2")
    check("Mongo 驱动已安装", data.get("mongodb_libs") is True, "pymongo")


def test_crud_all_types():
    ids = {}
    for t in PORTS:
        r = client.post("/api/databases/connections", json=conn_body(t))
        check(f"新增 {t} 成功", r.status_code == 200 and r.json().get("db_type") == t)
        ids[t] = r.json().get("id")

    # 编辑（PUT）：四种类型逐一验证
    for t in PORTS:
        body = conn_body(t, name=f"test-{t}-edit")
        r = client.put(f"/api/databases/connections/{ids[t]}", json=body)
        check(f"编辑 {t} 成功", r.status_code == 200 and r.json().get("name") == f"test-{t}-edit")
        check(f"编辑 {t} 保留 id", r.json().get("id") == ids[t])

    # 非法类型返回 422（pydantic pattern 校验）
    r = client.post("/api/databases/connections", json={"name": "x", "db_type": "oracle"})
    check("非法类型返回 422", r.status_code == 422)

    # 列表数量
    r = client.get("/api/databases/connections")
    check("列表包含 4 条连接", len(r.json().get("connections", [])) == 4)

    # 删除
    for t in PORTS:
        r = client.delete(f"/api/databases/connections/{ids[t]}")
        check(f"删除 {t} 成功", r.status_code == 200)
    r = client.get("/api/databases/connections")
    check("删除后列表为空", len(r.json().get("connections", [])) == 0)


def test_graceful_failure():
    """不可达连接：test / 列表 / 查询 应返回 502，而非 500 崩溃。"""
    r = client.post("/api/databases/connections", json=conn_body("postgresql", "unreachable"))
    cid = r.json()["id"]
    # 本机未监听 5432 时连接被立即拒绝，返回 502
    r = client.post(f"/api/databases/connections/{cid}/test")
    check("test 不可达连接返回 502", r.status_code == 502)
    r = client.get(f"/api/databases/connections/{cid}/databases")
    check("listDBs 不可达连接返回 502", r.status_code == 502)
    r = client.post(f"/api/databases/connections/{cid}/query", json={"sql": "SELECT 1"})
    check("query 不可达连接返回 502", r.status_code == 502)
    client.delete(f"/api/databases/connections/{cid}")


def test_validation():
    # PostgreSQL 空 SQL -> 400
    r = client.post("/api/databases/connections", json=conn_body("postgresql", "pg"))
    pg_id = r.json()["id"]
    r = client.post(f"/api/databases/connections/{pg_id}/query", json={"sql": "  "})
    check("PostgreSQL 空 SQL 返回 400", r.status_code == 400)
    # 禁用的 SQL 片段 -> 403
    r = client.post(f"/api/databases/connections/{pg_id}/query", json={"sql": "DROP DATABASE x"})
    check("PostgreSQL 危险 SQL 返回 403", r.status_code == 403)
    # 建/删库名白名单校验 -> 400（连接前校验，快速返回）
    r = client.post(f"/api/databases/connections/{pg_id}/create-db", json={"name": "bad`name"})
    check("非法库名返回 400", r.status_code == 400)
    client.delete(f"/api/databases/connections/{pg_id}")

    # MongoDB：空集合名 / 非法过滤 JSON -> 400（连接前校验，快速返回）
    r = client.post("/api/databases/connections", json=conn_body("mongodb", "mongo"))
    mongo_id = r.json()["id"]
    r = client.post(f"/api/databases/connections/{mongo_id}/query", json={"collection": "  "})
    check("MongoDB 空集合名返回 400", r.status_code == 400)
    r = client.post(
        f"/api/databases/connections/{mongo_id}/query",
        json={"collection": "users", "filter": "{bad json"},
    )
    check("MongoDB 非法过滤 JSON 返回 400", r.status_code == 400)
    # MongoDB create-db 无需真实服务即可通过（按需自动创建）
    r = client.post(f"/api/databases/connections/{mongo_id}/create-db", json={"name": "good_db"})
    check("MongoDB 建库返回 200", r.status_code == 200 and r.json().get("ok") is True)
    client.delete(f"/api/databases/connections/{mongo_id}")


def main():
    print("== databases.py 回归测试 ==")
    test_status()
    test_crud_all_types()
    test_graceful_failure()
    test_validation()
    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
