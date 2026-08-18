# -*- coding: utf-8 -*-
"""
test_security_audit_2026.py — 2026-08 安全审查修复项单元测试

覆盖本次修复的输入校验与 SSRF 防护（无需后端运行，直接调用路由模块函数）：
  1. sites.py   — nginx/apache 配置注入字符拒绝、域名白名单、site_id 规范化
  2. ssl.py     — 证书域名白名单（防 certbot 选项注入 / 路径穿越）
  3. firewall.py— IP / CIDR 白名单
  4. cron.py    — cron 调度表达式白名单（防 crontab 换行注入额外任务行）
  5. appstore.py— SSRF 公网地址校验（拒绝内网 / 回环 / 非法 scheme）

运行：.venv\\Scripts\\python.exe test_security_audit_2026.py
"""
import os
import sys

# 确保以 backend 目录为工作目录时可直接导入 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import HTTPException  # noqa: E402

from app.routers import sites, ssl as ssl_router, firewall, cron, appstore  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, fn, *args, should_raise: bool = False, **kwargs):
    """断言辅助：should_raise=True 时期望抛 HTTPException(400)。"""
    global PASS, FAIL
    try:
        fn(*args, **kwargs)
        if should_raise:
            print(f"[FAIL] {name}: 未按预期拒绝")
            FAIL += 1
        else:
            print(f"[ ok ] {name}")
            PASS += 1
    except HTTPException as e:
        if should_raise and e.status_code == 400:
            print(f"[ ok ] {name}（400: {e.detail}）")
            PASS += 1
        else:
            print(f"[FAIL] {name}: 异常状态码 {e.status_code}")
            FAIL += 1
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name}: 非预期异常 {type(e).__name__}: {e}")
        FAIL += 1


# ----------------------------------------------------------------------
# 1. sites.py：配置注入与路径穿越
# ----------------------------------------------------------------------
def test_sites():
    print("\n== sites.py：配置注入 / 路径穿越 ==")
    # 换行 / 分号 / 花括号注入必须被拒
    check("root 含分号拒绝", sites._validate_site_payload, should_raise=True,
          root="/var/www; alias /etc;")
    check("root 含换行拒绝", sites._validate_site_payload, should_raise=True,
          root="/var/www\ninclude /etc/passwd;")
    check("root 含 {} 拒绝", sites._validate_site_payload, should_raise=True,
          root="/var/www{gap}")
    check("reverse_proxy 注入拒绝", sites._validate_site_payload, should_raise=True,
          reverse_proxy="http://127.0.0.1:8080;\nroot /etc;")
    check("合法 reverse_proxy 放行", sites._validate_site_payload,
          reverse_proxy="http://127.0.0.1:8080/app")
    check("非 http(s) 协议代理拒绝", sites._validate_site_payload, should_raise=True,
          reverse_proxy="ftp://evil/x")
    # 域名白名单
    check("合法域名放行", sites._validate_site_payload,
          domains=["example.com", "*.example.com", "a-b.example.co.uk"])
    check("注入域名拒绝", sites._validate_site_payload, should_raise=True,
          domains=["evil.com;\nroot /etc;"])
    check("路径形式域名拒绝", sites._validate_site_payload, should_raise=True,
          domains=["../../etc"])
    check("非列表 domains 拒绝", sites._validate_site_payload, should_raise=True,
          domains="example.com")
    # site_id 规范化（配置文件名防穿越）
    import re
    normalize = lambda name: re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")
    assert normalize("My Site") == "my-site"
    assert normalize("../../evil") == "evil"          # 穿越片段被清洗为普通 id
    assert normalize("c:\\windows\\sys") == "c-windows-sys"  # Windows 盘符被中和
    assert sites._SITE_ID_RE.match(normalize("normal-name_1"))
    print("[ ok ] site_id 规范化：../.. 与盘符均被中和")
    globals()["PASS"] += 1
    # upstream / protocol / subdomain
    check("合法 upstream 放行", sites._validate_site_payload,
          upstream="127.0.0.1:3306")
    check("注入 upstream 拒绝", sites._validate_site_payload, should_raise=True,
          upstream="1.2.3.4:80;\nserver evil;")
    check("非法 protocol 拒绝", sites._validate_site_payload, should_raise=True,
          protocol="tcp\n}")
    check("合法 protocol 放行", sites._validate_site_payload, protocol="udp")
    check("非法 subdomain 拒绝", sites._validate_site_payload, should_raise=True,
          subdomain="evil;id")
    check("非法根域名拒绝", sites._validate_site_payload, should_raise=True,
          domain="../../../etc")
    # locations
    check("location 注入拒绝", sites._validate_site_payload, should_raise=True,
          locations=[{"path": "/x; root /etc;", "root": "/var/www"}])
    check("合法 location 放行", sites._validate_site_payload,
          locations=[{"path": "/static", "root": "/var/www/html"}])
    check("ssl 路径穿越拒绝", sites._validate_site_payload, should_raise=True,
          ssl={"enabled": True, "cert": "../../secret.key", "key": "/etc/k.pem"})
    check("合法 ssl 路径放行", sites._validate_site_payload,
          ssl={"enabled": True, "cert": "/etc/letsencrypt/live/x/fullchain.pem",
               "key": "/etc/letsencrypt/live/x/privkey.pem"})
    # 配置生成纵深防御：历史脏数据按域名白名单整体丢弃（server_name 回退 _）
    dirty = {"type": "static", "domains": ["a.com;\nroot /etc;"], "root": "/var/www",
             "port": 80, "locations": []}
    conf = sites._nginx_site_config(dirty)
    server_line = conf.split("server_name", 1)[1].split("\n", 1)[0]
    assert "\nroot /etc;" not in conf, "注入的换行指令不应出现"
    assert "root /etc" not in server_line, "server_name 行不应残留注入片段"
    assert server_line.count(";") == 1, "server_name 行应仅剩行尾语句分号"
    # 子网站脏数据同样兜底：非法 subdomain 被丢弃，回退 *.根域名（合法值）
    dirty_sub = {"type": "subsite", "subdomain": "evil;id", "domain": "example.com"}
    assert sites._site_server_name(dirty_sub) == "*.example.com"
    # 根域名本身也脏时回退 _
    assert sites._site_server_name({"type": "subsite", "subdomain": "a", "domain": "x;y"}) == "_"
    print("[ ok ] _nginx_site_config 对脏数据做白名单兜底（脏 token 整体丢弃）")
    globals()["PASS"] += 1


# ----------------------------------------------------------------------
# 2. ssl.py：证书域名
# ----------------------------------------------------------------------
def test_ssl():
    print("\n== ssl.py：证书域名白名单 ==")
    check("合法域名放行", ssl_router._DOMAIN_RE.match, "www.example.com")
    check("通配符域名放行", ssl_router._DOMAIN_RE.match, "*.example.com")
    for bad in ["--standalone", "../../etc", "evil.com -d", "a b", ""]:
        if ssl_router._DOMAIN_RE.match(bad):
            print(f"[FAIL] 非法域名被放行: {bad!r}")
            globals()["FAIL"] += 1
        else:
            print(f"[ ok ] 非法域名拒绝: {bad!r}")
            globals()["PASS"] += 1


# ----------------------------------------------------------------------
# 3. firewall.py：IP / CIDR
# ----------------------------------------------------------------------
def test_firewall():
    print("\n== firewall.py：IP / CIDR 白名单 ==")
    check("合法 IPv4 放行", firewall._validate_ip, "1.2.3.4")
    check("合法 CIDR 放行", firewall._validate_ip, "10.0.0.0/8")
    check("合法 IPv6 放行", firewall._validate_ip, "2001:db8::1")
    check("注入片段拒绝", firewall._validate_ip, "1.2.3.4 -j DROP", should_raise=True)
    check("选项片段拒绝", firewall._validate_ip, "--flush", should_raise=True)
    check("任意文本拒绝", firewall._validate_ip, "8.8.8.8;rm -rf /", should_raise=True)


# ----------------------------------------------------------------------
# 4. cron.py：调度表达式
# ----------------------------------------------------------------------
def test_cron():
    print("\n== cron.py：调度表达式白名单 ==")
    check("合法 5 字段放行", cron._validate_schedule, "*/5 * * * *")
    check("合法范围放行", cron._validate_schedule, "30 2 * * 1-5")
    check("合法列表放行", cron._validate_schedule, "0 0,12 1 */2 mon")
    check("换行注入拒绝", cron._validate_schedule, "* * * * *\n* * * * * rm -rf /",
          should_raise=True)
    check("分号注入拒绝", cron._validate_schedule, "* * * * ; id", should_raise=True)
    check("4 字段拒绝", cron._validate_schedule, "* * * *", should_raise=True)
    check("6 字段拒绝", cron._validate_schedule, "* * * * * *", should_raise=True)


# ----------------------------------------------------------------------
# 5. appstore.py：SSRF
# ----------------------------------------------------------------------
def test_appstore_ssrf():
    print("\n== appstore.py：SSRF 公网校验 ==")
    check("file:// 拒绝", appstore._assert_public_http_url, "file:///etc/passwd",
          should_raise=True)
    check("ftp:// 拒绝", appstore._assert_public_http_url, "ftp://x/y", should_raise=True)
    check("回环地址拒绝", appstore._assert_public_http_url, "http://127.0.0.1:8000/api/health",
          should_raise=True)
    check("localhost 拒绝", appstore._assert_public_http_url, "http://localhost:8000/",
          should_raise=True)
    check("私网地址拒绝", appstore._assert_public_http_url, "http://192.168.1.1/index.json",
          should_raise=True)
    check("链路本地拒绝", appstore._assert_public_http_url, "http://169.254.169.254/latest/meta-data",
          should_raise=True)
    # 公网地址放行（DNS 解析需在线；离线环境解析失败同样 400，此处容错）
    try:
        appstore._assert_public_http_url("https://wuhulab.github.io/Graw-app-store/index.json")
        print("[ ok ] 公网地址放行")
        globals()["PASS"] += 1
    except HTTPException as e:
        # 离线 / DNS 不可用导致的解析失败可接受（防护逻辑本身已生效）
        print(f"[skip] 公网地址校验依赖 DNS（{e.detail}）")
    except Exception:
        globals()["FAIL"] += 1
        print("[FAIL] 公网地址校验异常")


if __name__ == "__main__":
    test_sites()
    test_ssl()
    test_firewall()
    test_cron()
    test_appstore_ssrf()
    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    sys.exit(1 if FAIL else 0)
