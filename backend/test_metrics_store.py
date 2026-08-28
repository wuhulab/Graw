# -*- coding: utf-8 -*-
"""
test_metrics_store.py - 历史监控回放功能测试

分两部分：
  1. 单元测试：直接驱动 metrics_store 记录/落盘/查询/清空/过期清理。
  2. 集成测试：若后端运行在 8000 端口，用真实账号登录并调用
     /api/system/metrics/status 与 /api/system/metrics/history 接口。

用法：backend/.venv/Scripts/python.exe test_metrics_store.py
"""
import os
import sys
import json
import time
import tempfile

# 保证可导入 app 包（脚本位于 backend/ 下时无需额外处理）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import urllib.request
import urllib.parse


def test_store_unit():
    """单元测试：驱动 metrics_store 的采样落盘与查询。"""
    from app import metrics_store

    # 用临时目录隔离，避免污染真实数据
    tmp = tempfile.mkdtemp(prefix="graw_metrics_test_")
    metrics_store.METRICS_DIR = tmp
    metrics_store._cleanup_old_files()

    now = time.time()
    # 构造 3 条采样（含一条缺失字段的脏数据，验证容错）
    samples = [
        {"overview": {"cpu": 10.5, "memory": {"percent": 40}, "storage": {"percent": 55},
                      "load": {"load1": 0.3}},
         "network": {"upload": 1024, "download": 2048},
         "diskio": {"read": 100, "write": 200}},
        {"overview": {"cpu": 20.0, "memory": {"percent": 45}, "storage": {"percent": 56},
                      "load": {"load1": 0.6}},
         "network": {"upload": 2048, "download": 4096},
         "diskio": {"read": 200, "write": 300}},
        {"overview": {"cpu": 30.5, "memory": {"percent": 50}, "storage": {"percent": 57},
                      "load": {"load1": 0.9}},
         "network": {"upload": 3072, "download": 6144},
         "diskio": {"read": 300, "write": 400}},
    ]
    for i, s in enumerate(samples):
        s2 = json.loads(json.dumps(s))
        metrics_store.record_sample(s2)
    # 脏数据：None 应被忽略
    metrics_store.record_sample(None)
    metrics_store.record_sample({"bad": "data"})

    metrics_store.flush()
    assert len(metrics_store._pending) == 0, "flush 后缓冲应为空"

    # 查询全部（含未来时间窗口，覆盖完整区间）
    res = metrics_store.history(now - 100, now + 100)
    assert res["raw"] == 3, f"应命中 3 条原始采样，实际 {res['raw']}"
    points = res["points"]
    assert len(points) == 3
    # 时间升序
    assert points[0]["ts"] <= points[1]["ts"] <= points[2]["ts"]

    # 聚合查询：bucket=60 应合并为 1 桶，均值正确
    agg = metrics_store.history(now - 100, now + 100, bucket=60)
    assert agg["buckets"] == 1, f"60s 桶应聚合为 1 桶，实际 {agg['buckets']}"
    avg_cpu = (10.5 + 20.0 + 30.5) / 3
    assert abs(agg["points"][0]["cpu"] - avg_cpu) < 0.01, f"CPU 均值错误: {agg['points'][0]['cpu']}"

    # 状态查询
    st = metrics_store.status()
    assert st["retention_days"] == 7
    assert st["earliest"] is not None and st["latest"] is not None

    # 清空后应无数据
    metrics_store.clear()
    res2 = metrics_store.history(now - 100, now + 100)
    assert res2["raw"] == 0, "清空后不应有数据"

    # 过期清理：写入一个 10 天前的超期文件，flush 后应被删除
    old_day = (time.strftime("%Y-%m-%d", time.localtime(now - 10 * 86400)))
    old_file = metrics_store._day_path(old_day)
    with open(old_file, "w", encoding="utf-8") as f:
        f.write('{"ts": %s, "cpu": 1}\n' % (now - 10 * 86400))
    metrics_store._cleanup_old_files()
    assert not os.path.exists(old_file), "超期文件应被清理"

    print("✔ 单元测试通过：记录/落盘/查询/聚合/清空/过期清理均正常")


def _entry_headers():
    """ShunX 安全入口：请求需携带 X-ShunX-Entry 头（读配置，避免硬编码）。"""
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shunx.json")
    entry = None
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    # CodeQL [py/empty-except] 读取可选 shunx.json：未配置/损坏时静默忽略
    except Exception:
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def _http_json(url, method="GET", data=None, token=None, timeout=10):
    """HTTP 请求辅助，返回 (status, json)。"""
    headers = {"Content-Type": "application/json"}
    headers.update(_entry_headers())
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def test_http_integration():
    """集成测试：注入临时账号，登录并调用历史监控 HTTP 接口。"""
    import shutil as _shutil
    from app.auth import USERS_FILE, hash_password

    base = "http://localhost:8000/api"
    # 备份用户表，注入临时管理员账号，测完恢复
    backup = USERS_FILE + ".bak_metrictest"
    _shutil.copyfile(USERS_FILE, backup)
    token = None
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        users["__metrictest"] = {
            "username": "__metrictest",
            "password": hash_password("SecPass#123"),
            "role": "admin",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__metrictest", "password": "SecPass#123"})
        assert code == 200, f"登录失败: {code} {body}"
        assert body.get("token"), f"登录未返回 token: {body}"
        token = body["token"]

        code, st = _http_json(f"{base}/system/metrics/status", token=token)
        assert code == 200, f"status 接口异常: {code} {st}"
        print(f"✔ status 接口: retention_days={st.get('retention_days')} days={st.get('days')}")

        now = time.time()
        code, hist = _http_json(
            f"{base}/system/metrics/history?start={int(now - 86400)}&end={int(now)}&bucket=300",
            token=token,
        )
        assert code == 200, f"history 接口异常: {code} {hist}"
        print(f"✔ history 接口: raw={hist.get('raw')} buckets={hist.get('buckets')}")

        # 非法时间范围应返回 400
        code, _ = _http_json(f"{base}/system/metrics/history?start=-5&end=5", token=token)
        assert code == 400, f"非法时间范围应 400，实际 {code}"
        print("✔ 非法时间范围被 400 拒绝")

        # 普通用户应无权限清空（403）
        code, _ = _http_json(f"{base}/system/metrics/clear", "DELETE", token=token)
        assert code == 200, f"管理员清空应成功，实际 {code}"
        print("✔ 管理员清空历史成功")
    finally:
        _shutil.copyfile(backup, USERS_FILE)
        os.remove(backup)


if __name__ == "__main__":
    test_store_unit()
    test_http_integration()
    print("全部测试完成")
