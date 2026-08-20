# -*- coding: utf-8 -*-
"""
protection.py 数据库保护机制核心逻辑单元测试（不依赖运行中的后端服务）

覆盖：
  - 独立数据库镜像 / 内置数据库（SQLite 等）镜像识别
  - 建议数据挂载目录（内置数据库镜像的 data_dir）
  - 数据持久化评估：无挂载 danger、匿名卷 warning、bind/命名卷安全
  - 内置数据库（embedded）与独立数据库（db）告警文案区分
  - 容器内 SQLite 文件探测（覆盖任意自定义镜像）
  - 自动备份目录（Windows / Linux）

用法：
  python test_protection_unit.py
"""
import os
import sys
from unittest import mock

# 确保可导入 app 包（与 test_tamper_unit.py 同级目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.routers import protection  # noqa: E402

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


def test_image_detection():
    print("[1] 镜像识别（独立数据库 + 内置数据库）")
    # 独立数据库镜像
    check("mysql 识别", protection._is_db_image("mysql:8.0"))
    check("postgres 识别", protection._is_db_image("postgres:16"))
    check("redis 识别", protection._is_db_image("redis:7"))
    check("mongo 识别", protection._is_db_image("mongo:7"))
    # 内置数据库（SQLite 等）镜像
    check("sqlite 镜像识别", protection._is_embedded_db_image("user/sqlite-app:1"))
    check("grafana 识别", protection._is_embedded_db_image("grafana/grafana:11"))
    check("gitea 识别", protection._is_embedded_db_image("gitea/gitea:1.22"))
    check("vaultwarden 识别", protection._is_embedded_db_image("vaultwarden/server:latest"))
    check("homeassistant 识别", protection._is_embedded_db_image("ghcr.io/home-assistant/home-assistant:stable"))
    check("jellyfin 识别", protection._is_embedded_db_image("lscr.io/linuxserver/jellyfin:latest"))
    check("nextcloud 识别", protection._is_embedded_db_image("nextcloud:stable"))
    # 类别互斥：mysql 不是内置数据库、grafana 不是独立数据库
    check("mysql 不是内置数据库", not protection._is_embedded_db_image("mysql:8.0"))
    check("grafana 不是独立数据库", not protection._is_db_image("grafana/grafana:11"))
    # 普通应用镜像不触发
    check("nginx 不触发", not (protection._is_db_image("nginx:latest") or protection._is_embedded_db_image("nginx:latest")))


def test_data_dir():
    print("[2] 建议数据挂载目录")
    check("mysql 数据目录", protection._db_data_dir("mysql:8") == "/var/lib/mysql")
    check("grafana 数据目录", protection._db_data_dir("grafana/grafana:11") == "/var/lib/grafana")
    check("homeassistant 数据目录", protection._db_data_dir("homeassistant/home-assistant:stable") == "/config")
    check("gitea 数据目录", protection._db_data_dir("gitea/gitea:1.22") == "/data")
    check("未知镜像回退 /data", protection._db_data_dir("nginx:latest") == "/data")


def test_evaluate_mounts():
    print("[3] 数据持久化评估")
    cid, name, image, status = "abc123", "my-db", "mysql:8", "Up 2 hours"
    # 1) 无任何挂载 -> danger
    w = protection._evaluate_mounts(cid, name, image, status, [])
    check("无挂载 danger", w is not None and w["level"] == "danger", f"level={w and w['level']}")
    check("无挂载 data_dir", w is not None and w["data_dir"] == "/var/lib/mysql")
    # 2) 仅有匿名卷（长十六进制哈希）-> warning
    w2 = protection._evaluate_mounts(cid, name, image, status, [
        {"Type": "volume", "Name": "a" * 64, "Destination": "/var/lib/mysql"},
    ])
    check("匿名卷 warning", w2 is not None and w2["level"] == "warning", f"level={w2 and w2['level']}")
    # 3) bind 挂载 -> 安全 None
    w3 = protection._evaluate_mounts(cid, name, image, status, [
        {"Type": "bind", "Source": "/srv/data", "Destination": "/var/lib/mysql"},
    ])
    check("bind 挂载安全", w3 is None)
    # 4) 命名卷 -> 安全 None
    w4 = protection._evaluate_mounts(cid, name, image, status, [
        {"Type": "volume", "Name": "graw-mysql-data", "Destination": "/var/lib/mysql"},
    ])
    check("命名卷安全", w4 is None)


def test_embedded_message():
    print("[4] 内置数据库告警文案")
    cid, name, status = "abc", "my-app", "Up 1 hours"
    emb = protection._evaluate_mounts(cid, name, "grafana/grafana:11", status, [], category="embedded")
    check("embedded 无挂载 danger", emb is not None and emb["level"] == "danger")
    check("embedded 文案含 SQLite", "SQLite" in (emb or {}).get("message", ""))
    db = protection._evaluate_mounts(cid, name, "mysql:8", status, [], category="db")
    check("db 文案不含内置描述", "SQLite" not in (db or {}).get("message", ""))
    # 嵌入式容器已有命名卷 -> 安全
    safe = protection._evaluate_mounts(
        cid, name, "grafana/grafana:11", status,
        [{"Type": "volume", "Name": "grafana-data", "Destination": "/var/lib/grafana"}],
        category="embedded",
    )
    check("embedded 命名卷安全", safe is None)


def _mount(type_, source, dest):
    return {"Type": type_, "Source" if type_ == "bind" else "Name": source, "Destination": dest}


def test_container_internal_detection():
    print("[5] 容器内 SQLite 文件探测")
    cid, name, status = "abc123", "my-app", "Up 1 hours"
    # 1) 探测命令：仅在容器可写层内查找，排除系统目录（避免误报镜像自带库文件）
    cmd = protection.SQLITE_FIND_CMD
    check("探测命令含 -xdev", "-xdev" in cmd)
    check("探测命令排除 /proc", "-not -path '/proc/*'" in cmd)
    check("探测命令排除 /usr/lib", "-not -path '/usr/lib/*'" in cmd)
    check("探测命令包含 sqlite 扩展", "*.sqlite3" in cmd)

    # 2) CLI 探测成功 -> 返回文件列表
    with mock.patch.object(protection, "_run_engine", return_value=(0, "/data/app.db\n/root/x.sqlite\n", "")):
        files = protection._detect_sqlite_in_container("abc123", "cli", None)
        check("CLI 探测命中文件", files == ["/data/app.db", "/root/x.sqlite"], str(files))
    # 3) CLI 探测失败（容器无 shell）-> None（保守跳过，不误报）
    with mock.patch.object(protection, "_run_engine", return_value=(127, "", "no such file")):
        check("CLI 探测失败返回 None", protection._detect_sqlite_in_container("abc123", "cli", None) is None)
    # 4) SDK 探测：bytes 输出解码
    fake_cont = mock.Mock()
    fake_cont.exec_run.return_value = (0, b"/app/app.db\n")
    fake_client = mock.Mock()
    fake_client.containers.get.return_value = fake_cont
    files = protection._detect_sqlite_in_container("abc123", "docker", fake_client)
    check("SDK 探测 bytes 解码", files == ["/app/app.db"], str(files))
    # 5) SDK 探测无 shell
    fake_cont2 = mock.Mock()
    fake_cont2.exec_run.return_value = (126, b"")
    fake_client2 = mock.Mock()
    fake_client2.containers.get.return_value = fake_cont2
    check("SDK 探测失败返回 None", protection._detect_sqlite_in_container("abc123", "docker", fake_client2) is None)


def test_evaluate_container_dispatch():
    print("[6] 单容器分派（持久化跳过 / 探测命中才告警）")
    cid, name, status = "abc123", "my-app", "Up 1 hours"

    # 1) 已持久化 + 未命中关键字 -> 跳过（即使探测可能命中也不打扰）
    with mock.patch.object(protection, "_detect_sqlite_in_container", return_value=["/data/app.db"]) as m:
        w = protection._evaluate_container(cid, name, "nginx:latest", status, [_mount("volume", "myvol", "/data")], "cli", None)
        check("已持久化容器跳过", w is None)
        check("已持久化不触发探测", m.call_count == 0)

    # 2) 未持久化 + 独立数据库镜像 -> db 告警（不探测）
    with mock.patch.object(protection, "_detect_sqlite_in_container", return_value=None) as m:
        w = protection._evaluate_container(cid, name, "mysql:8", status, [], "cli", None)
        check("db 镜像告警", w is not None and w["level"] == "danger")
        check("db 镜像不探测", m.call_count == 0)

    # 3) 未持久化 + 内置数据库镜像 -> embedded 告警（不探测）
    with mock.patch.object(protection, "_detect_sqlite_in_container", return_value=None) as m:
        w = protection._evaluate_container(cid, name, "grafana/grafana:11", status, [], "cli", None)
        check("embedded 镜像告警", w is not None and w["level"] == "danger")

    # 4) 未持久化 + 自定义镜像 + 探测命中 -> detected 告警
    with mock.patch.object(protection, "_detect_sqlite_in_container", return_value=["/data/app.db", "/root/x.sqlite"]):
        w = protection._evaluate_container(cid, name, "myapp:v1", status, [], "cli", None)
        check("自定义镜像探测命中告警", w is not None and w["level"] == "danger")
        check("detected 文案含路径", "/data/app.db" in (w or {}).get("message", ""))
        check("sqlite_files 字段", (w or {}).get("sqlite_files") == ["/data/app.db", "/root/x.sqlite"])

    # 5) 未持久化 + 自定义镜像 + 探测失败（无 shell）-> 跳过
    with mock.patch.object(protection, "_detect_sqlite_in_container", return_value=None):
        w = protection._evaluate_container(cid, name, "myapp:v1", status, [], "cli", None)
        check("自定义镜像探测失败跳过", w is None)

    # 6) 未持久化 + 自定义镜像 + 探测空结果 -> 跳过
    with mock.patch.object(protection, "_detect_sqlite_in_container", return_value=[]):
        w = protection._evaluate_container(cid, name, "myapp:v1", status, [], "cli", None)
        check("自定义镜像探测无结果跳过", w is None)


def test_backup_dir():
    print("[7] 自动备份目录")
    d = protection._backup_dir()
    if protection.IS_WINDOWS:
        check("Windows 备份目录", d == r"C:\GrawBackups", d)
    else:
        check("Linux 备份目录", d == "/data/graw-backups", d)


def main():
    test_image_detection()
    test_data_dir()
    test_evaluate_mounts()
    test_embedded_message()
    test_container_internal_detection()
    test_evaluate_container_dispatch()
    test_backup_dir()
    print(f"\n结果：通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
