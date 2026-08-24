# -*- coding: utf-8 -*-
"""
test_sitesopts.py - 站点增强配置（防盗链 / gzip / 缓存）功能测试

放置于 backend 之外运行，避免被 uvicorn --reload 监视导致后端重启崩溃。

覆盖：
  1. 单元测试：get_nginx_extra 片段生成（防盗链/缓存/gzip、域名清洗、proxy 站点不注入）。
  2. 集成测试：登录后应用配置 / 清除配置 / 非法域名清洗 / 普通用户 403。

用法：backend/.venv/Scripts/python.exe test_sitesopts.py
"""
import json
import os
import shutil
import sys
import urllib.request

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
sys.path.insert(0, BACKEND)

from app.routers.sitesopts import (  # noqa: E402
    get_nginx_extra, _sanitize_domain, _cache_expires, SITES_FILE,
)
from app.auth import USERS_FILE, hash_password  # noqa: E402


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def test_fragment_unit():
    """片段生成：防盗链/缓存/gzip 注入；域名清洗；proxy 站点不注入。"""
    # 防盗链 + 缓存
    site = {
        "type": "static",
        "hotlink": {"enabled": True, "allowed": ["example.com", "*.good.com", "bad;gzip on;"],
                    "allow_empty_referer": True},
        "cache_expire": 86400,
    }
    frag = get_nginx_extra(site)
    assert "valid_referers" in frag, "应生成 valid_referers"
    assert "example.com" in frag, "合法域名应保留"
    assert "*.good.com" in frag, "通配域名应保留"
    assert "bad;gzip on;" not in frag, "非法域名（注入字符）应被清洗"
    assert "expires 1d;" in frag, "缓存 86400s 应映射为 1d"
    assert "return 403" in frag, "启用防盗链应生成拒绝逻辑"

    # 允许空来源关闭 → 不应出现 none
    site["hotlink"]["allow_empty_referer"] = False
    frag2 = get_nginx_extra(site)
    assert "none blocked" not in frag2, "禁止空来源时不应放行 none"

    # gzip
    site2 = {"type": "subsite", "gzip": {"enabled": True}, "cache_expire": 0}
    frag3 = get_nginx_extra(site2)
    assert "gzip on;" in frag3, "应生成 gzip 片段"
    assert "valid_referers" not in frag3, "未启用防盗链不应生成 valid_referers"

    # proxy 站点 → 无注入
    site3 = {"type": "proxy", "hotlink": {"enabled": True}, "gzip": {"enabled": True}}
    assert get_nginx_extra(site3) == "", "反向代理站点不应注入增强配置"

    # 空配置 → 空串
    assert get_nginx_extra({"type": "static"}) == ""
    print("✔ 单元测试：防盗链/缓存/gzip 片段生成与域名清洗通过")


def test_helpers_unit():
    """辅助函数：缓存时长映射、域名白名单。"""
    assert _cache_expires(0) == ""
    assert _cache_expires(60) == "1m"
    assert _cache_expires(3600) == "1h"
    assert _cache_expires(86400) == "1d"
    assert _cache_expires(-5) == ""
    assert _sanitize_domain("Example.COM") == "example.com"
    assert _sanitize_domain("*.a.cn") == "*.a.cn"
    assert _sanitize_domain("not a domain;") is None
    assert _sanitize_domain("") is None
    print("✔ 单元测试：缓存时长映射 / 域名白名单校验通过")


# ---------------------------------------------------------------------------
# 集成测试（需后端运行在 8000）
# ---------------------------------------------------------------------------
def _entry_headers():
    cfg = os.path.join(BACKEND, "data", "shunx.json")
    entry = None
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    except Exception:
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def _http_json(url, method="GET", data=None, token=None, timeout=30):
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
    """集成测试：应用/清除配置、非法域名清洗、普通用户 403。"""
    base = "http://localhost:8000/api"
    # 备份站点与用户文件
    sites_backup = SITES_FILE + ".bak_opts"
    sites_existed = os.path.exists(SITES_FILE)
    if sites_existed:
        shutil.copyfile(SITES_FILE, sites_backup)
    users_backup = USERS_FILE + ".bak_opts"
    shutil.copyfile(USERS_FILE, users_backup)
    try:
        # 构造一个测试站点（static 类型）
        test_sites = [{
            "id": "__optssite", "name": "测试站点", "type": "static",
            "enabled": False, "domains": ["opts.example.com"], "root": "/var/www/opts",
        }]
        if sites_existed:
            with open(SITES_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing.append(test_sites[0])
            test_sites = existing
        with open(SITES_FILE, "w", encoding="utf-8") as f:
            json.dump(test_sites, f, ensure_ascii=False, indent=2)

        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        users["__optsadmin"] = {
            "username": "__optsadmin", "password": hash_password("SecPass#123"),
            "role": "admin", "must_change_password": False, "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__optsadmin", "password": "SecPass#123"})
        assert code == 200, f"登录失败: {code} {body}"
        token = body["token"]

        # 1) 站点列表包含测试站点
        code, body = _http_json(f"{base}/sitesopts/sites", token=token)
        assert code == 200, f"站点列表失败: {code} {body}"
        ids = [s["id"] for s in body["sites"]]
        assert "__optssite" in ids, "应包含测试站点"
        print("✔ 站点列表：包含测试站点")

        # 2) 应用配置（含非法域名，应被清洗但不报错）
        code, body = _http_json(f"{base}/sitesopts/apply", "POST", {
            "site_id": "__optssite",
            "hotlink_enabled": True,
            "hotlink_allowed": ["cdn.good.com", "bad;evil on;", "evil.com"],
            "hotlink_allow_empty_referer": True,
            "gzip_enabled": True,
            "cache_expire": 604800,
        }, token=token)
        assert code == 200, f"应用配置失败: {code} {body}"

        # 3) 验证配置已持久化且非法域名被清洗
        with open(SITES_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        target = next(s for s in saved if s.get("id") == "__optssite")
        assert target["hotlink"]["enabled"] is True
        assert "bad;evil on;" not in target["hotlink"]["allowed"], "非法域名应被清洗"
        assert "evil.com" in target["hotlink"]["allowed"], "合法域名应保留"
        assert target["gzip"]["enabled"] is True
        assert target["cache_expire"] == 604800
        print("✔ 应用配置：持久化成功且非法域名被清洗")

        # 4) 清除配置
        code, body = _http_json(f"{base}/sitesopts/clear", "POST", {"site_id": "__optssite"}, token=token)
        assert code == 200, f"清除配置失败: {code} {body}"
        with open(SITES_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        target = next(s for s in saved if s.get("id") == "__optssite")
        assert "hotlink" not in target and "gzip" not in target and "cache_expire" not in target, "应清除全部增强配置"
        print("✔ 清除配置：增强字段已移除")

        # 5) 普通用户 403
        users["__optsuser"] = {
            "username": "__optsuser", "password": hash_password("SecPass#123"),
            "role": "user", "must_change_password": False, "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__optsuser", "password": "SecPass#123"})
        user_token = body["token"]
        code, _ = _http_json(f"{base}/sitesopts/sites", token=user_token)
        assert code == 403, f"普通用户应 403，实际 {code}"
        print("✔ 普通用户访问站点增强配置 → 403")
    finally:
        # 恢复站点与用户文件
        if sites_existed:
            shutil.copyfile(sites_backup, SITES_FILE)
            os.remove(sites_backup)
        else:
            if os.path.exists(SITES_FILE):
                os.remove(SITES_FILE)
        shutil.copyfile(users_backup, USERS_FILE)
        os.remove(users_backup)


if __name__ == "__main__":
    test_fragment_unit()
    test_helpers_unit()
    test_http_integration()
    print("全部测试完成")
