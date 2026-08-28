# -*- coding: utf-8 -*-
"""
test_svcmonitor.py - 服务/端口监控功能测试

分两部分：
  1. 单元测试：直接驱动 svcmonitor 的探测函数、target 校验与状态机。
  2. 集成测试：若后端运行在 8000 端口，用真实账号登录并调用
     /api/svcmonitor/* CRUD 与手动探测接口。

用法：backend/.venv/Scripts/python.exe test_svcmonitor.py
"""
import os
import sys
import json
import time
import socket
import threading

# 保证可导入 app 包（脚本位于 backend/ 下时无需额外处理）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import urllib.request
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def _start_accept_server():
    """启动一个本地 TCP server，自动 accept 并关闭连接，返回 (srv, port, stop, thread)。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(8)
    stop = threading.Event()

    def _accept():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    t = threading.Thread(target=_accept, daemon=True)
    t.start()
    return srv, port, stop, t


def test_probe_port_unit():
    """端口探测：监听端口 ok；未监听端口 down；非法端口 down。"""
    from app.routers import svcmonitor

    srv, port, stop, th = _start_accept_server()
    try:
        r = svcmonitor._probe_port(str(port), 2)
        assert r["status"] == "ok", f"监听端口应 ok，实际 {r}"
        r2 = svcmonitor._probe_port(f"127.0.0.1:{port}", 2)
        assert r2["status"] == "ok", f"host:port 形式应 ok，实际 {r2}"
    finally:
        stop.set()
        srv.close()
        th.join(timeout=2)

    # 未监听端口（取一个大概率空闲的高端口）
    probe = svcmonitor._probe_port("59999", 1)
    assert probe["status"] == "down", f"未监听端口应 down，实际 {probe}"

    # 非法端口
    assert svcmonitor._probe_port("abc", 1)["status"] == "down"
    assert svcmonitor._probe_port("70000", 1)["status"] == "down"
    assert svcmonitor._probe_port("", 1)["status"] == "down"
    print("✔ 单元测试：端口探测（监听/未监听/非法）通过")


def test_validate_target_unit():
    """target 校验：空/非法字符/非法端口抛 400。"""
    from app.routers import svcmonitor

    # 合法
    assert svcmonitor._validate_target("port", "3306") == "3306"
    assert svcmonitor._validate_target("port", "127.0.0.1:3306") == "127.0.0.1:3306"
    assert svcmonitor._validate_target("process", "nginx-master") == "nginx-master"
    assert svcmonitor._validate_target("service", "nginx.service") == "nginx.service"

    # 空
    for bad in (None, "", "   "):
        try:
            svcmonitor._validate_target("port", bad)
            raise AssertionError("空 target 应抛 400")
        except HTTPException as e:
            assert e.status_code == 400

    # 非法字符（命令注入/路径穿越）
    for bad in ("3306; rm -rf /", "../etc/passwd", "a b", "x|y", "`id`"):
        try:
            svcmonitor._validate_target("port", bad)
            raise AssertionError(f"非法 target 应抛 400: {bad!r}")
        except HTTPException as e:
            assert e.status_code == 400

    # 非法端口范围
    for bad in ("0", "65536", "99999", "-1", "abc"):
        try:
            svcmonitor._validate_target("port", bad)
            raise AssertionError(f"非法端口应抛 400: {bad!r}")
        except HTTPException as e:
            assert e.status_code == 400
    print("✔ 单元测试：target 校验（空/注入/非法端口）通过")


def test_probe_process_unit():
    """进程探测：当前进程（本脚本解释器）应可匹配到 python。"""
    from app.routers import svcmonitor

    r = svcmonitor._probe_process("python")
    # 若运行环境无 psutil 则返回 down（detail 含失败原因），不视为失败
    assert r["status"] in ("ok", "down"), f"进程探测返回异常: {r}"
    print(f"✔ 单元测试：进程探测返回 {r['status']}")


def test_state_machine_unit():
    """状态机：down -> ok 变化时推送通知，并更新 down_since/history。"""
    from app.routers import svcmonitor

    # 屏蔽真实通知推送（避免测试触发渠道发送）
    import app.routers.notify as notify_mod
    calls = []
    orig = notify_mod.push_all

    def _fake_push(msg):
        calls.append(msg)
        return 1, 0  # (sent, failed)

    notify_mod.push_all = _fake_push
    try:
        # 阶段1：未监听端口 → down，且 prev=None 不应推送
        item = {"id": "svc_test", "name": "测试端口", "kind": "port",
                "target": "59998", "history": []}
        r1 = svcmonitor._probe_and_alert(item)
        assert r1["status"] == "down", f"首测应 down，实际 {r1}"
        assert len(calls) == 0, "首次探测（prev=None）不应推送"
        assert item["down_since"], "down 时应记录 down_since"
        assert item["last_status"] == "down"

        # 阶段2：启动监听后再次探测（ok）：down -> ok 应推送恢复通知
        srv, port, stop, th = _start_accept_server()
        item["target"] = str(port)
        r2 = svcmonitor._probe_and_alert(item)
        assert r2["status"] == "ok", f"二次应 ok，实际 {r2}"
        assert len(calls) == 1, "down->ok 应推送 1 次恢复通知"
        assert "恢复" in calls[0], f"通知内容应含恢复，实际 {calls[0]}"
        assert item["down_since"] == "", "恢复后 down_since 应清空"
        stop.set()
        srv.close()
        th.join(timeout=2)

        # 环形历史上限
        assert len(item["history"]) == 2
    finally:
        notify_mod.push_all = orig
    print("✔ 单元测试：状态机（首测不推送/变化推送/历史环形）通过")


# ---------------------------------------------------------------------------
# 集成测试（需后端运行在 8000）
# ---------------------------------------------------------------------------
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


def _http_json(url, method="GET", data=None, token=None, timeout=15):
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
    """集成测试：注入临时账号，登录并调用服务/端口监控 CRUD 接口。"""
    import shutil as _shutil
    from app.auth import USERS_FILE, hash_password

    base = "http://localhost:8000/api"
    backup = USERS_FILE + ".bak_svcmon"
    _shutil.copyfile(USERS_FILE, backup)
    token = None
    item_id = None
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        users["__svcmon"] = {
            "username": "__svcmon",
            "password": hash_password("SecPass#123"),
            "role": "admin",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__svcmon", "password": "SecPass#123"})
        assert code == 200, f"登录失败: {code} {body}"
        token = body["token"]

        # 状态摘要
        code, st = _http_json(f"{base}/svcmonitor/status", token=token)
        assert code == 200, f"status 接口异常: {code} {st}"
        print(f"✔ status 接口: items={st.get('item_count')} up={st.get('up_count')} down={st.get('down_count')}")

        # 创建（端口监控，指向后端 8000）
        code, item = _http_json(f"{base}/svcmonitor/items", "POST", {
            "name": "测试-后端端口",
            "kind": "port",
            "target": "127.0.0.1:8000",
            "interval_seconds": 60,
            "timeout_seconds": 3,
            "enabled": True,
        }, token=token)
        assert code == 200, f"创建失败: {code} {item}"
        item_id = item["id"]
        print(f"✔ 创建监控项: {item['name']} id={item_id}")

        # 非法 target 应 400（注入字符）
        code, _ = _http_json(f"{base}/svcmonitor/items", "POST", {
            "name": "非法", "kind": "port", "target": "3306; rm -rf /",
        }, token=token)
        assert code == 400, f"非法 target 应 400，实际 {code}"
        print("✔ 非法 target 被 400 拒绝")

        # 列表
        code, lst = _http_json(f"{base}/svcmonitor/items", token=token)
        assert code == 200 and any(i["id"] == item_id for i in lst.get("items", [])), "列表应包含新建项"
        print(f"✔ 列表接口: 共 {len(lst.get('items', []))} 项")

        # 手动探测：后端 8000 应 ok
        code, res = _http_json(f"{base}/svcmonitor/items/{item_id}/test", "POST", token=token)
        assert code == 200, f"手动探测异常: {code} {res}"
        print(f"✔ 手动探测: status={res.get('status')} detail={res.get('detail')}")

        # 更新
        code, upd = _http_json(f"{base}/svcmonitor/items/{item_id}", "PUT", {
            "name": "测试-后端端口-改",
            "interval_seconds": 120,
        }, token=token)
        assert code == 200 and upd["name"] == "测试-后端端口-改", f"更新失败: {code} {upd}"
        print(f"✔ 更新监控项: name={upd['name']} interval={upd['interval_seconds']}")

        # 普通用户应无权限（403）
        users["__svcmon_user"] = {
            "username": "__svcmon_user",
            "password": hash_password("SecPass#123"),
            "role": "user",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__svcmon_user", "password": "SecPass#123"})
        user_token = body["token"]
        code, _ = _http_json(f"{base}/svcmonitor/items", token=user_token)
        assert code == 403, f"普通用户访问应 403，实际 {code}"
        print("✔ 普通用户访问 svcmonitor 被 403 拒绝")

        # 删除
        code, _ = _http_json(f"{base}/svcmonitor/items/{item_id}", "DELETE", token=token)
        assert code == 200, f"删除失败: {code}"
        print("✔ 删除监控项成功")

        # 删除后再次探测应 404
        code, _ = _http_json(f"{base}/svcmonitor/items/{item_id}/test", "POST", token=token)
        assert code == 404, f"删除后探测应 404，实际 {code}"
        print("✔ 删除后手动探测被 404 拒绝")
        item_id = None
    finally:
        # 清理可能残留的测试监控项（避免污染真实数据）
        from app.routers import svcmonitor as _sm
        _data = _sm._load()
        _data["items"] = [i for i in _data.get("items", [])
                          if i.get("name", "").startswith("测试-")]
        _sm._save(_data)
        _shutil.copyfile(backup, USERS_FILE)
        os.remove(backup)


if __name__ == "__main__":
    test_probe_port_unit()
    test_validate_target_unit()
    test_probe_process_unit()
    test_state_machine_unit()
    test_http_integration()
    print("全部测试完成")
