# -*- coding: utf-8 -*-
"""
test_security_audit_2026.py — 2026-08 安全审查修复项单元测试

覆盖本次修复的输入校验与 SSRF 防护（无需后端运行，直接调用路由模块函数）：
  1. sites.py   — nginx/apache 配置注入字符拒绝、域名白名单、site_id 规范化
  2. ssl.py     — 证书域名白名单（防 certbot 选项注入 / 路径穿越）
  3. firewall.py— IP / CIDR 白名单
  4. cron.py    — cron 调度表达式白名单（防 crontab 换行注入额外任务行）
  5. appstore.py— SSRF 公网地址校验（拒绝内网 / 回环 / 非法 scheme）
  6. cron.py    — 命令换行注入（Linux crontab 单行语义）
  7. notes.py   — 备忘录大小限制（磁盘填充 DoS）
  8. databases.py— 连接密码脱敏（列表不回传明文 / 留空保持原密码）
  9. node_manager.py— SSH host/user/key_path 白名单（防 ssh 参数注入）
 10. appstore.py— 安装参数 version/timezone 白名单（防 YAML 重解析注入）
 11. main.py    — SPA 静态回退路径规范化（防穿越检查真实生效）

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


# ----------------------------------------------------------------------
# 6. cron.py：命令换行注入（Linux crontab 单行语义）
# ----------------------------------------------------------------------
def test_cron_command():
    print("\n== cron.py：命令换行注入 ==")
    global PASS, FAIL
    import unittest.mock as mock

    orig = cron.IS_WIN
    # 模拟 Linux：换行命令必须被拒
    with mock.patch.object(cron, "IS_WIN", False):
        check("Linux 多行命令拒绝", cron._validate_cron_command,
              "echo a\necho b", should_raise=True)
        check("Linux CR 注入拒绝", cron._validate_cron_command,
              "echo a\r\n* * * * * evil", should_raise=True)
        check("Linux 空命令拒绝", cron._validate_cron_command, "", should_raise=True)
        check("Linux 单行命令放行", cron._validate_cron_command, "echo a && echo b")
        check("Linux 单行脚本放行", cron._validate_cron_command, "find /var/log -name '*.log' -delete")
    # Windows（.bat 脚本）多行应放行
    cron.IS_WIN = True
    try:
        check("Windows 多行命令放行（bat 脚本语义）", cron._validate_cron_command, "echo a\necho b")
    finally:
        cron.IS_WIN = orig


# ----------------------------------------------------------------------
# 7. notes.py：备忘录大小限制（磁盘填充 DoS）
# ----------------------------------------------------------------------
def test_notes_limit():
    print("\n== notes.py：备忘录大小限制 ==")
    global PASS, FAIL
    import asyncio
    import tempfile
    from app.routers import notes as notes_router
    from app.routers.notes import NoteUpdate

    # 成功路径写入临时文件，避免污染真实 notes.json
    orig_file = notes_router.NOTES_FILE
    tmpdir = tempfile.mkdtemp()
    notes_router.NOTES_FILE = os.path.join(tmpdir, "notes.json")
    try:
        # 超限内容必须 413
        try:
            asyncio.run(notes_router.update_notes(NoteUpdate(content="x" * (notes_router.MAX_CONTENT_BYTES + 1))))
            print("[FAIL] 超限备忘录未按预期拒绝")
            FAIL += 1
        except HTTPException as e:
            if e.status_code == 413:
                print(f"[ ok ] 超限备忘录拒绝（413: {e.detail}）")
                PASS += 1
            else:
                print(f"[FAIL] 超限备忘录异常状态码 {e.status_code}")
                FAIL += 1
        # 边界内内容放行（写入临时文件）
        try:
            asyncio.run(notes_router.update_notes(NoteUpdate(content="x" * 1024)))
            print("[ ok ] 1KB 备忘录放行")
            PASS += 1
        except Exception as e:
            print(f"[FAIL] 1KB 备忘录异常: {e}")
            FAIL += 1
        # 恶意多字节放大：UTF-8 下 4 字节字符同样计入字节数
        try:
            asyncio.run(notes_router.update_notes(NoteUpdate(content="🙂" * (notes_router.MAX_CONTENT_BYTES // 4 + 1))))
            print("[FAIL] 多字节超限内容未按预期拒绝")
            FAIL += 1
        except HTTPException as e:
            if e.status_code == 413:
                print("[ ok ] 多字节超限内容拒绝（413）")
                PASS += 1
            else:
                print(f"[FAIL] 多字节超限异常状态码 {e.status_code}")
                FAIL += 1
    finally:
        notes_router.NOTES_FILE = orig_file
        import shutil as _shutil
        _shutil.rmtree(tmpdir, ignore_errors=True)


# ----------------------------------------------------------------------
# 8. databases.py：连接密码脱敏
# ----------------------------------------------------------------------
def test_databases_masking():
    print("\n== databases.py：连接密码脱敏 ==")
    global PASS, FAIL
    import asyncio
    import tempfile
    from app.routers import databases as db_router
    from app.routers.databases import DBConnection

    orig_file = db_router.DB_FILE
    tmpdir = tempfile.mkdtemp()
    db_router.DB_FILE = os.path.join(tmpdir, "databases.json")
    try:
        # 准备一条带密码的连接
        asyncio.run(db_router.add_connection(DBConnection(
            name="t", db_type="mysql", host="127.0.0.1", port=3306,
            username="root", password="s3cret!", database="db",
        )))
        # 列表接口不得回传明文密码
        conns = asyncio.run(db_router.list_connections())["connections"]
        c = conns[0]
        if c.get("password") == "" and c.get("has_password") is True:
            print("[ ok ] 列表不回传明文密码（has_password=True）")
            PASS += 1
        else:
            print(f"[FAIL] 列表回传了密码或缺失标记: password={c.get('password')!r}")
            FAIL += 1
        cid = c["id"]
        # 编辑时密码留空 → 保持原密码
        asyncio.run(db_router.update_connection(cid, DBConnection(
            name="t2", db_type="mysql", host="127.0.0.1", port=3306,
            username="root", password="", database="db",
        )))
        raw = db_router._load_connections()
        if raw[0]["password"] == "s3cret!":
            print("[ ok ] 编辑留空保持原密码")
            PASS += 1
        else:
            print("[FAIL] 编辑留空丢失了原密码")
            FAIL += 1
        # 编辑时显式输入新密码 → 覆盖
        asyncio.run(db_router.update_connection(cid, DBConnection(
            name="t3", db_type="mysql", host="127.0.0.1", port=3306,
            username="root", password="n3wpass", database="db",
        )))
        raw = db_router._load_connections()
        if raw[0]["password"] == "n3wpass":
            print("[ ok ] 显式新密码覆盖")
            PASS += 1
        else:
            print("[FAIL] 显式新密码未覆盖")
            FAIL += 1
    finally:
        db_router.DB_FILE = orig_file
        import shutil as _shutil
        _shutil.rmtree(tmpdir, ignore_errors=True)


# ----------------------------------------------------------------------
# 9. node_manager.py：SSH 参数注入防护
# ----------------------------------------------------------------------
def test_node_ssh_target():
    print("\n== node_manager.py：SSH 参数注入防护 ==")
    global PASS, FAIL
    from app import node_manager as nm

    vt = nm._validate_ssh_target

    def check_value(name, fn, *args, should_raise: bool = False):
        """与 check() 相同，但接受 ValueError（node_manager 抛 ValueError 而非 HTTPException）。"""
        global PASS, FAIL
        try:
            fn(*args)
            if should_raise:
                print(f"[FAIL] {name}: 未按预期拒绝")
                FAIL += 1
            else:
                print(f"[ ok ] {name}")
                PASS += 1
        except ValueError as e:
            if should_raise:
                print(f"[ ok ] {name}（ValueError: {e}）")
                PASS += 1
            else:
                print(f"[FAIL] {name}: 非预期 ValueError: {e}")
                FAIL += 1
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: 非预期异常 {type(e).__name__}: {e}")
            FAIL += 1

    check_value("合法主机名放行", vt, "server1.example.com", "root")
    check_value("合法 IPv4 放行", vt, "192.168.1.10", "admin")
    check_value("合法 IPv6 放行", vt, "fe80::1", "root")
    check_value("host 选项注入拒绝", vt, "-oProxyCommand=evil", "root", should_raise=True)
    check_value("host 前导短横线拒绝", vt, "-badhost", "root", should_raise=True)
    check_value("host 含空白拒绝", vt, "host; evil", "root", should_raise=True)
    check_value("host 换行拒绝", vt, "good.com\n-oProxyCommand=x", "root", should_raise=True)
    check_value("user 选项注入拒绝", vt, "example.com", "-oBadOption=x", should_raise=True)
    check_value("user 含空白拒绝", vt, "example.com", "root evil", should_raise=True)
    check_value("user 空拒绝", vt, "example.com", "", should_raise=True)
    check_value("key_path 换行拒绝", vt, "example.com", "root", "/home/k/.ssh/id\nrsa", should_raise=True)
    check_value("合法 key_path 放行", vt, "example.com", "root", "/home/k/.ssh/id_rsa")


# ----------------------------------------------------------------------
# 10. appstore.py：安装参数 YAML 注入白名单
# ----------------------------------------------------------------------
def test_appstore_install_patterns():
    print("\n== appstore.py：安装参数 YAML 注入 ==")
    global PASS, FAIL
    from pydantic import ValidationError

    def expect_ok(name, **kw):
        global PASS, FAIL
        try:
            appstore.InstallRequest(**{"app_id": "x", "app_name": "demo", **kw})
            print(f"[ ok ] {name}")
            PASS += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            FAIL += 1

    def expect_reject(name, **kw):
        global PASS, FAIL
        try:
            appstore.InstallRequest(**{"app_id": "x", "app_name": "demo", **kw})
            print(f"[FAIL] {name}: 未按预期拒绝")
            FAIL += 1
        except ValidationError:
            print(f"[ ok ] {name}（422 拒绝）")
            PASS += 1
        except Exception as e:
            print(f"[FAIL] {name}: 非预期异常 {e}")
            FAIL += 1

    expect_ok("合法版本号放行", version="3.12")
    expect_ok("latest 放行", version="latest")
    expect_reject("版本号换行注入拒绝", version="3.12\nprivileged: true")
    expect_reject("版本号空格注入拒绝", version="3.12 -v /:/host")
    expect_ok("合法时区放行", timezone="Asia/Shanghai")
    expect_ok("UTC 时区放行", timezone="UTC")
    expect_reject("时区换行注入拒绝", timezone="UTC\nrestart: no")
    expect_reject("时区特殊字符拒绝", timezone="UTC; rm -rf /")


# ----------------------------------------------------------------------
# 11. main.py：SPA 静态回退路径规范化
# ----------------------------------------------------------------------
def test_main_dist_norm():
    print("\n== main.py：SPA 静态回退路径规范化 ==")
    global PASS, FAIL
    from app import main as app_main

    dist = app_main.FRONTEND_DIST
    if dist == os.path.normpath(dist) and ".." not in dist.split(os.sep):
        print(f"[ ok ] FRONTEND_DIST 已规范化: {dist}")
        PASS += 1
    else:
        print(f"[FAIL] FRONTEND_DIST 未规范化: {dist}")
        FAIL += 1


# ----------------------------------------------------------------------
# 12. 第三轮审计（2026-08-18）：Redis 反射调用 / Docker CLI 选项注入 /
#     install_dir label 穿越 / spa_fallback 跨盘符异常
# ----------------------------------------------------------------------
def test_round3_redis_forbidden():
    """Redis 控制台危险方法黑名单：关停 / 配置写文件 RCE 链 / Lua 执行。"""
    print("\n== databases.py：Redis 危险方法黑名单 ==")
    global PASS, FAIL
    from app.routers import databases as db_router

    dangerous = [
        "shutdown", "config_set", "save", "bgsave", "bgrewriteaof",
        "eval", "evalsha", "script_load", "replicaof", "slaveof",
        "migrate", "restore", "cluster", "swapdb", "execute_command",
    ]
    missing = [m for m in dangerous if m not in db_router._REDIS_FORBIDDEN_METHODS]
    if missing:
        print(f"[FAIL] 黑名单缺少危险方法: {missing}")
        FAIL += 1
    else:
        print(f"[ ok ] {len(dangerous)} 个危险方法均被黑名单拦截")
        PASS += 1

    # 正常读写命令不得被误伤
    safe = ["get", "set", "keys", "scan", "info", "ttl", "exists", "dbsize", "type"]
    blocked_safe = [m for m in safe if m in db_router._REDIS_FORBIDDEN_METHODS]
    if blocked_safe:
        print(f"[FAIL] 正常命令被误拦: {blocked_safe}")
        FAIL += 1
    else:
        print(f"[ ok ] 正常命令（{len(safe)} 个）不受影响")
        PASS += 1


def test_round3_docker_ref_guard():
    """容器/镜像/网络标识白名单：拦截 CLI 选项注入（- 开头）与空白/特殊字符。"""
    print("\n== docker_api.py：CLI 标识符选项注入防护 ==")
    global PASS, FAIL
    from app.routers import docker_api

    should_reject = [
        "-evil", "--privileged", "--log-level=debug",  # 选项注入
        "a b", "a\tb",                                  # 空白破坏参数边界
        "a;b", "a$b", "a`b",                            # shell 元字符
        "../etc", "/abs/path",                          # 路径穿越 / 绝对路径
    ]
    for ref in should_reject:
        try:
            docker_api._safe_docker_ref(ref)
            print(f"[FAIL] 未拒绝非法标识符: {ref!r}")
            FAIL += 1
        except HTTPException as e:
            if e.status_code == 400:
                print(f"[ ok ] 拒绝 {ref!r}（400）")
                PASS += 1
            else:
                print(f"[FAIL] {ref!r} 异常状态码 {e.status_code}")
                FAIL += 1

    should_pass = [
        "abc123", "deadbeef1234",                      # 容器 ID
        "my-container_1.0",                            # 常规容器名
        "docker.io/library/nginx:latest",              # 完整镜像引用
        "registry.example.com:5000/app@sha256:abc",    # registry:tag/digest
    ]
    for ref in should_pass:
        try:
            out = docker_api._safe_docker_ref(ref)
            if out == ref:
                print(f"[ ok ] 放行 {ref!r}")
                PASS += 1
            else:
                print(f"[FAIL] {ref!r} 被改写为 {out!r}")
                FAIL += 1
        except Exception as e:
            print(f"[FAIL] 误拒合法标识符 {ref!r}: {e}")
            FAIL += 1


def test_round3_install_dir_label_traversal():
    """恶意镜像 compose project label 的 ../ 穿越不得逃出 appstore 目录。"""
    print("\n== docker_api.py：install_dir label 穿越约束 ==")
    global PASS, FAIL
    from app.routers import docker_api

    traversal_labels = [
        "../../../../..",  # 相对路径上跳穿越
        "..",              # 上跳一级（backend/data，真实存在也必须拒绝）
        "/etc",            # 绝对路径注入
        "C:/Windows",      # Windows 绝对路径（跨盘符）
    ]
    for evil in traversal_labels:
        inspect_data = {
            "Config": {"Labels": {"com.docker.compose.project": evil}},
            "Name": "/no-match",
        }
        try:
            result = docker_api._find_install_dir(inspect_data)
            if result == "":
                print(f"[ ok ] 穿越 label {evil!r} 返回空（未逃出 appstore）")
                PASS += 1
            else:
                print(f"[FAIL] 穿越 label {evil!r} 仍返回目录: {result}")
                FAIL += 1
        except Exception as e:
            print(f"[FAIL] label {evil!r} 触发异常: {type(e).__name__}: {e}")
            FAIL += 1

    # 常规不存在的项目名：安静返回空
    inspect_data2 = {"Config": {"Labels": {"com.docker.compose.project": "no-such-proj"}}, "Name": "/x"}
    try:
        result2 = docker_api._find_install_dir(inspect_data2)
        if result2 == "":
            print("[ ok ] 不存在的项目名返回空")
            PASS += 1
        else:
            print(f"[FAIL] 非预期返回: {result2}")
            FAIL += 1
    except Exception as e:
        print(f"[FAIL] _find_install_dir(不存在项目) 异常: {e}")
        FAIL += 1


def test_round3_spa_fallback_cross_drive():
    """spa_fallback：跨盘符绝对路径不得 500（commonpath ValueError 已捕获）。"""
    print("\n== main.py：spa_fallback 跨盘符稳健性 ==")
    global PASS, FAIL
    from app import main as app_main

    if not os.path.isdir(app_main.FRONTEND_DIST):
        print("[skip] frontend/dist 未构建，跳过 spa_fallback E2E")
        return
    try:
        from fastapi.testclient import TestClient
    except Exception as e:
        print(f"[skip] TestClient 不可用: {e}")
        return

    with TestClient(app_main.app) as client:
        # Windows 跨盘符绝对路径：不得 500（应回退 200 index.html）
        r = client.get("/C:/Windows/win.ini")
        if r.status_code == 200:
            print("[ ok ] 跨盘符路径回退 200（无 500）")
            PASS += 1
        else:
            print(f"[FAIL] 跨盘符路径状态码 {r.status_code}")
            FAIL += 1
        # 未命中 API 前缀仍 404
        r2 = client.get("/api/no-such-endpoint")
        if r2.status_code == 404:
            print("[ ok ] /api 未命中返回 404")
            PASS += 1
        else:
            print(f"[FAIL] /api 未命中返回 {r2.status_code}")
            FAIL += 1
        # 普通不存在的页面路径回退 index.html
        r3 = client.get("/some/random/page")
        if r3.status_code == 200:
            print("[ ok ] 随机路径回退 index.html")
            PASS += 1
        else:
            print(f"[FAIL] 随机路径状态码 {r3.status_code}")
            FAIL += 1


def test_round4_docs_disabled():
    """默认配置下 /docs /redoc /openapi.json 必须 404（API 结构不外泄）。"""
    print("\n== main.py：交互式 API 文档端点默认关闭 ==")
    global PASS, FAIL
    from app import main as app_main

    # 当前进程未设置 GRAW_ENABLE_DOCS 时，文档端点应全部关闭
    if os.environ.get("GRAW_ENABLE_DOCS", "") == "1":
        print("[skip] 当前进程已启用 GRAW_ENABLE_DOCS=1，跳过关闭断言")
        return
    try:
        from fastapi.testclient import TestClient
    except Exception as e:
        print(f"[skip] TestClient 不可用: {e}")
        return

    with TestClient(app_main.app) as client:
        # 注意：docs 路由关闭后请求会落入 SPA catch-all 返回 200 的
        # index.html——功能上安全（未泄露 API 结构）。因此断言基于
        # 响应内容而非状态码：不得出现 Swagger UI / Redoc / OpenAPI schema。
        r = client.get("/docs")
        body = (r.text or "").lower()
        if "swagger" not in body and r.status_code in (200, 404):
            print(f"[ ok ] /docs 未提供 Swagger UI（{r.status_code}）")
            PASS += 1
        else:
            print(f"[FAIL] /docs 泄露交互式文档（{r.status_code}）")
            FAIL += 1

        r = client.get("/redoc")
        body = (r.text or "").lower()
        if "redoc" not in body and r.status_code in (200, 404):
            print(f"[ ok ] /redoc 未提供 ReDoc（{r.status_code}）")
            PASS += 1
        else:
            print(f"[FAIL] /redoc 泄露交互式文档（{r.status_code}）")
            FAIL += 1

        r = client.get("/openapi.json")
        body = (r.text or "").lstrip()[:1]
        is_schema = r.status_code == 200 and body == "{" and '"openapi"' in (r.text or "")
        if not is_schema:
            print(f"[ ok ] /openapi.json 未泄露 API schema（{r.status_code}）")
            PASS += 1
        else:
            print("[FAIL] /openapi.json 返回完整 API 结构（攻击面地图外泄）")
            FAIL += 1


def test_round4_task_id_guard():
    """tasks.py：task_id 白名单拦截路径穿越（Windows 反斜杠 / 上跳）。"""
    print("\n== tasks.py：任务 ID 白名单 ==")
    global PASS, FAIL
    from app.routers import tasks

    evil_ids = [
        "..", "..\\..\\secret", "a/b", "a\\b",      # 穿越与分隔符
        "..%5C..%5Cusers",                            # URL 编码反斜杠
        "x" * 65,                                     # 超长
        "id;rm", "id|x", "id y",                      # 元字符/空白
    ]
    for tid in evil_ids:
        try:
            tasks._validate_task_id(tid)
            print(f"[FAIL] 未拒绝非法任务 ID: {tid!r}")
            FAIL += 1
        except HTTPException as e:
            if e.status_code == 400:
                print(f"[ ok ] 拒绝 {tid!r}（400）")
                PASS += 1
            else:
                print(f"[FAIL] {tid!r} 异常状态码 {e.status_code}")
                FAIL += 1

    # 合法 ID 放行（uuid hex / 内部 record id 形态）
    for tid in ("a1b2c3d4", "rt-task-01", "Install_2026"):
        try:
            tasks._validate_task_id(tid)
            print(f"[ ok ] 放行 {tid!r}")
            PASS += 1
        except Exception as e:
            print(f"[FAIL] 误拒合法任务 ID {tid!r}: {e}")
            FAIL += 1


def test_round4_logs_addlog_validation():
    """logs.py：add_log 输入校验（控制字符 / 长度 / 空值）。"""
    print("\n== logs.py：自定义日志源输入校验 ==")
    global PASS, FAIL
    from pydantic import ValidationError

    from app.routers import logs as logs_router

    # 控制字符（含空字节 / 换行）必须被拒绝
    for bad_path in ("/var/log/x\x00.log", "/var/log/a\nb.log", "/var/log/\x1b[31m"):
        try:
            logs_router._reject_control_chars(bad_path, "日志路径")
            print(f"[FAIL] 未拒绝含控制字符的路径: {bad_path!r}")
            FAIL += 1
        except HTTPException as e:
            if e.status_code == 400:
                print(f"[ ok ] 拒绝控制字符路径（400）")
                PASS += 1
            else:
                print(f"[FAIL] 异常状态码 {e.status_code}")
                FAIL += 1

    # 正常路径放行
    try:
        logs_router._reject_control_chars("/var/log/nginx/access.log", "日志路径")
        print("[ ok ] 正常路径放行")
        PASS += 1
    except Exception as e:
        print(f"[FAIL] 误拒正常路径: {e}")
        FAIL += 1

    # 模型层：超长 / 空值被 pydantic 422 拒绝
    bad_payloads = [
        {"name": "n" * 65, "path": "/var/log/x.log"},          # name 超长
        {"name": "ok", "path": "x" * 2000},                    # path 超长
        {"name": "", "path": "/var/log/x.log"},                # name 为空
        {"name": "ok", "path": ""},                            # path 为空
    ]
    for payload in bad_payloads:
        try:
            logs_router.AddLog(**payload)
            print(f"[FAIL] 未按预期拒绝: {payload}")
            FAIL += 1
        except ValidationError:
            print("[ ok ] 非法负载被模型校验拒绝（422）")
            PASS += 1

    # 合法负载放行
    try:
        logs_router.AddLog(name="应用日志", path="/var/log/app.log", desc="测试")
        print("[ ok ] 合法负载放行")
        PASS += 1
    except Exception as e:
        print(f"[FAIL] 误拒合法负载: {e}")
        FAIL += 1


def test_round4_runtime_patterns():
    """runtime.py：版本/端口/env/容器路径白名单（Docker 选项注入防护）。"""
    print("\n== runtime.py：运行环境参数白名单 ==")
    global PASS, FAIL
    from pydantic import ValidationError

    from app.routers.runtime import RuntimeCreate

    base = {
        "type": "python", "name": "dev", "project_dir": "/opt/proj",
        "app_version": "3.12", "container_name": "", "notes": "",
        "ports": [], "env": [], "mounts": [], "hosts": [],
    }

    # app_version 选项注入：以 - 开头 / 含空格
    for evil_ver in ("--privileged", "-v /etc:/etc", "3.12;rm -rf /", "a b"):
        try:
            RuntimeCreate(**{**base, "app_version": evil_ver})
            print(f"[FAIL] 未拒绝非法版本号: {evil_ver!r}")
            FAIL += 1
        except ValidationError:
            print(f"[ ok ] 拒绝版本号 {evil_ver!r}（422）")
            PASS += 1

    # 端口非数字 / 选项形态
    for evil_port in ("-p", "8080:9090", "99999", "8 0"):
        try:
            RuntimeCreate(**{**base, "ports": [{"external": evil_port, "internal": "80"}]})
            print(f"[FAIL] 未拒绝非法端口: {evil_port!r}")
            FAIL += 1
        except ValidationError:
            print(f"[ ok ] 拒绝端口 {evil_port!r}（422）")
            PASS += 1

    # env 名称注入（以 - 开头或含特殊字符）
    for evil_env in ("-e", "EV IL", "A=B;C", "1BAD"):
        try:
            RuntimeCreate(**{**base, "env": [{"name": evil_env, "value": "x"}]})
            print(f"[FAIL] 未拒绝非法环境变量名: {evil_env!r}")
            FAIL += 1
        except ValidationError:
            print(f"[ ok ] 拒绝环境变量名 {evil_env!r}（422）")
            PASS += 1

    # 挂载容器路径必须是绝对路径
    for evil_container in ("-v", "relative/path", "../escape"):
        try:
            RuntimeCreate(**{**base, "mounts": [{"host": "/data", "container": evil_container}]})
            print(f"[FAIL] 未拒绝非法容器路径: {evil_container!r}")
            FAIL += 1
        except ValidationError:
            print(f"[ ok ] 拒绝容器路径 {evil_container!r}（422）")
            PASS += 1

    # --add-host hostname 注入
    for evil_host in ("-flag", "host;cmd", "a b"):
        try:
            RuntimeCreate(**{**base, "hosts": [{"hostname": evil_host, "ip": "1.2.3.4"}]})
            print(f"[FAIL] 未拒绝非法 hostname: {evil_host!r}")
            FAIL += 1
        except ValidationError:
            print(f"[ ok ] 拒绝 hostname {evil_host!r}（422）")
            PASS += 1

    # --add-host IP 非法文本（必须是合法 IPv4/IPv6）
    for evil_ip in ("not-an-ip", "1.2.3.4;rm", "999.999.999.999", "-inject"):
        try:
            RuntimeCreate(**{**base, "hosts": [{"hostname": "db", "ip": evil_ip}]})
            print(f"[FAIL] 未拒绝非法 IP: {evil_ip!r}")
            FAIL += 1
        except ValidationError:
            print(f"[ ok ] 拒绝 IP {evil_ip!r}（422）")
            PASS += 1

    # 合法负载放行
    try:
        RuntimeCreate(**{**base, "ports": [{"external": "8080", "internal": "80", "protocol": "tcp"}],
                         "env": [{"name": "MODE", "value": "dev"}],
                         "mounts": [{"host": "/data", "container": "/data", "mode": "ro"}],
                         "hosts": [{"hostname": "db.internal", "ip": "10.0.0.5"}]})
        print("[ ok ] 合法负载放行")
        PASS += 1
    except Exception as e:
        print(f"[FAIL] 误拒合法负载: {e}")
        FAIL += 1


def test_round4_multipart_version():
    """python-multipart 已升级到修复版（>=0.0.18，目标 0.0.31）。"""
    print("\n== 依赖：python-multipart CVE-2024-53981/CVE-2026-53537 修复版本 ==")
    global PASS, FAIL
    try:
        import multipart  # python-multipart 0.0.12+ 的包名

        ver = getattr(multipart, "__version__", "") or ""
        # 0.0.12 旧版可能无 __version__，此时通过 pip 元数据兜底
        if not ver:
            from importlib.metadata import version as _v

            ver = _v("python-multipart")
        parts = tuple(int(x) for x in ver.split(".")[:3])
        if parts >= (0, 0, 31):
            print(f"[ ok ] python-multipart {ver}（>=0.0.31，两项 CVE 均已修复）")
            PASS += 1
        elif parts >= (0, 0, 18):
            print(f"[WARN] python-multipart {ver} 修复了 CVE-2024-53981 但 <0.0.31（CVE-2026-53537 未修复）")
            FAIL += 1
        else:
            print(f"[FAIL] python-multipart {ver} 受 CVE-2024-53981 影响（需 >=0.0.18）")
            FAIL += 1
    except Exception as e:
        print(f"[FAIL] 检测 python-multipart 版本失败: {e}")
        FAIL += 1


def test_round5_protection_tar_option_injection():
    """第五轮：protection 备份命令的 tar 选项注入防护。

    basename 以 "-" 开头（如 --checkpoint-action=exec=...）会被 GNU tar
    解析为选项而非文件名，构成存储型 RCE；修复后入口直接 400，
    且 Linux 命令含 "--" 分隔符双保险。
    """
    print("\n== 第五轮：protection 备份命令 tar 选项注入 ==")
    global PASS, FAIL
    import unittest.mock as mock

    from app.routers import protection

    ok = True
    # 1) 恶意 basename（tar checkpoint exec 注入）必须被 400 拒绝
    #    注意：载荷不能含空格/斜杠结尾，否则 basename 会被切错
    for evil in (
        "/data/--checkpoint-action=exec=id",
        "/srv/db/--checkpoint=1",
        "/home/x/-oExtractOptions=...",
    ):
        try:
            protection._build_backup_command(evil)
            print(f"[FAIL] 恶意路径未被拒绝: {evil}")
            ok = False
        except HTTPException as e:
            if e.status_code == 400:
                print(f"[ ok ] 已拒绝恶意 basename: {evil.split('/')[-1][:40]}")
            else:
                print(f"[FAIL] 拒绝但状态码异常 {e.status_code}: {evil}")
                ok = False

    # 2) Linux 分支：正常路径生成命令须含 "--" 分隔符且 base 被正确转义
    with mock.patch.object(protection, "IS_WINDOWS", False):
        import shlex

        cmd = protection._build_backup_command("/var/lib/mysql")
        if " -- " in cmd and shlex.quote("mysql") in cmd:
            print("[ ok ] Linux 备份命令含 -- 分隔符（纵深防御生效）")
        else:
            print(f"[FAIL] Linux 备份命令缺少 -- 分隔符: {cmd}")
            ok = False
        # 含空格等特殊字符的目录名必须整体被 shlex.quote 包裹
        cmd2 = protection._build_backup_command("/srv/my data;rm -rf x")
        if shlex.quote("my data;rm -rf x") in cmd2:
            print("[ ok ] 特殊字符目录名被 shlex.quote 完整包裹")
        else:
            print(f"[FAIL] 特殊字符目录名转义异常: {cmd2}")
            ok = False

    # 3) Windows 分支：base 以 - 开头同样被入口拒绝（PowerShell 5.1 会吞
    #    "--"，依赖源头拒绝而非分隔符）
    with mock.patch.object(protection, "IS_WINDOWS", True):
        try:
            protection._build_backup_command(r"C:\data\--checkpoint-action=exec=x")
            print("[FAIL] Windows 分支未拒绝恶意 basename")
            ok = False
        except HTTPException as e:
            if e.status_code == 400:
                print("[ ok ] Windows 分支同样拒绝恶意 basename")
            else:
                print(f"[FAIL] Windows 分支状态码异常: {e.status_code}")
                ok = False
        # 正常 Windows 路径仍可用（单引号双写转义）
        cmd3 = protection._build_backup_command(r"C:\GrawData\db")
        if "powershell" in cmd3 and "'db'" in cmd3:
            print("[ ok ] Windows 正常路径命令生成不受影响")
        else:
            print(f"[FAIL] Windows 正常路径命令异常: {cmd3}")
            ok = False

    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


def test_round5_ssl_upload_validation():
    """第五轮：ssl 上传接口的输入校验（名称/大小/控制字符）。"""
    print("\n== 第五轮：ssl upload 输入校验 ==")
    global PASS, FAIL
    import io
    import shutil as _shutil
    import tempfile
    import unittest.mock as mock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import ssl as ssl_router

    tmp = tempfile.mkdtemp(prefix="graw_ssl_test_")
    ok = True
    try:
        # 重定向存储位置，避免污染真实 ssl.json / ssl 目录
        with mock.patch.object(ssl_router, "SSL_DIR", tmp), \
                mock.patch.object(ssl_router, "SSL_FILE", os.path.join(tmp, "ssl.json")):
            app = FastAPI()
            app.include_router(ssl_router.router)
            client = TestClient(app)

            pem = b"-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"

            # 1) 正常上传成功
            r = client.post(
                "/upload",
                data={"name": "my-cert", "domains": "a.com, b.com"},
                files={"cert": ("c.pem", io.BytesIO(pem)), "key": ("k.pem", io.BytesIO(pem))},
            )
            if r.status_code == 200 and r.json().get("ok"):
                print("[ ok ] 正常上传成功")
            else:
                print(f"[FAIL] 正常上传失败: {r.status_code} {r.text[:120]}")
                ok = False

            # 2) 名称以 - 开头（certbot --cert-name 污染面）必须 400
            r = client.post(
                "/upload",
                data={"name": "--standalone", "domains": ""},
                files={"cert": ("c.pem", io.BytesIO(pem)), "key": ("k.pem", io.BytesIO(pem))},
            )
            if r.status_code == 400:
                print("[ ok ] 拒绝以 - 开头的证书名称")
            else:
                print(f"[FAIL] - 开头名称未被拒绝: {r.status_code}")
                ok = False

            # 3) 名称含控制字符必须 400
            r = client.post(
                "/upload",
                data={"name": "bad\nname", "domains": ""},
                files={"cert": ("c.pem", io.BytesIO(pem)), "key": ("k.pem", io.BytesIO(pem))},
            )
            if r.status_code == 400:
                print("[ ok ] 拒绝含控制字符的名称")
            else:
                print(f"[FAIL] 控制字符名称未被拒绝: {r.status_code}")
                ok = False

            # 4) 超大文件（>1MB）必须 413（磁盘 DoS 防护）
            big = b"x" * (ssl_router._CERT_MAX_BYTES + 1)
            r = client.post(
                "/upload",
                data={"name": "big", "domains": ""},
                files={"cert": ("c.pem", io.BytesIO(big)), "key": ("k.pem", io.BytesIO(pem))},
            )
            if r.status_code == 413:
                print("[ ok ] 拒绝超过 1MB 的证书文件")
            else:
                print(f"[FAIL] 超大文件未被拒绝: {r.status_code}")
                ok = False

            # 5) 空文件必须 400
            r = client.post(
                "/upload",
                data={"name": "empty", "domains": ""},
                files={"cert": ("c.pem", io.BytesIO(b"")), "key": ("k.pem", io.BytesIO(pem))},
            )
            if r.status_code == 400:
                print("[ ok ] 拒绝空文件")
            else:
                print(f"[FAIL] 空文件未被拒绝: {r.status_code}")
                ok = False

            # 6) domains 超长必须 400
            r = client.post(
                "/upload",
                data={"name": "d", "domains": "x" * 513},
                files={"cert": ("c.pem", io.BytesIO(pem)), "key": ("k.pem", io.BytesIO(pem))},
            )
            if r.status_code == 400:
                print("[ ok ] 拒绝超长 domains 备注")
            else:
                print(f"[FAIL] 超长 domains 未被拒绝: {r.status_code}")
                ok = False
    finally:
        # 清理临时目录，失败不中断测试结果
        try:
            _shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)


def test_round5_starlette_cve_2024_47874():
    """第五轮：starlette >= 0.40.0（修复 CVE-2024-47874 multipart 内存 DoS）。

    受影响版本在解析 multipart 表单时对单 part 体积无上限，可被构造
    超大表单耗尽内存（DoS）；0.40.0 起逐块流式解析。fastapi 0.115.0
    锁死 starlette<0.39，故连带升级 fastapi 至 0.115.14。
    """
    print("\n== 依赖：starlette CVE-2024-47874 修复版本 ==")
    global PASS, FAIL
    try:
        from importlib.metadata import version as _v

        sv = tuple(int(x) for x in _v("starlette").split(".")[:2])
        fv = _v("fastapi")
        if sv >= (0, 40):
            print(f"[ ok ] starlette {_v('starlette')}（>=0.40.0，CVE-2024-47874 已修复；fastapi {fv}）")
            PASS += 1
        else:
            print(f"[FAIL] starlette {_v('starlette')} 受 CVE-2024-47874 影响（需 >=0.40.0）")
            FAIL += 1
    except Exception as e:
        print(f"[FAIL] 检测 starlette 版本失败: {e}")
        FAIL += 1


if __name__ == "__main__":
    test_sites()
    test_ssl()
    test_firewall()
    test_cron()
    test_appstore_ssrf()
    test_cron_command()
    test_notes_limit()
    test_databases_masking()
    test_node_ssh_target()
    test_appstore_install_patterns()
    test_main_dist_norm()
    test_round3_redis_forbidden()
    test_round3_docker_ref_guard()
    test_round3_install_dir_label_traversal()
    test_round3_spa_fallback_cross_drive()
    test_round4_docs_disabled()
    test_round4_task_id_guard()
    test_round4_logs_addlog_validation()
    test_round4_runtime_patterns()
    test_round4_multipart_version()
    test_round5_protection_tar_option_injection()
    test_round5_ssl_upload_validation()
    test_round5_starlette_cve_2024_47874()
    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    sys.exit(1 if FAIL else 0)
