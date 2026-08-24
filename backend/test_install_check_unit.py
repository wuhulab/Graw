# -*- coding: utf-8 -*-
"""
system.py 安装完整性检测单元测试（不依赖运行中的后端服务）

覆盖：
  - 容器内判断（/.dockerenv、cgroup 关键字）
  - PID 1 进程名读取 / CapEff 读取
  - install_check_sync：本机直跑视为完整；容器模式下逐项检测缺失项

用法：
  python test_install_check_unit.py
"""
import os
import sys
from unittest import mock

# 确保可导入 app 包（与 test_protection_unit.py 同级目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.routers import system  # noqa: E402

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


def test_container_detection():
    print("[1] 容器内判断")
    # 存在 /.dockerenv -> 容器
    with mock.patch.object(system.os.path, "exists", side_effect=lambda p: p == "/.dockerenv"):
        check("/.dockerenv 判定容器", system._is_running_in_container() is True)
    # 存在 /run/.containerenv（Podman）-> 容器
    with mock.patch.object(system.os.path, "exists", side_effect=lambda p: p == "/run/.containerenv"):
        check("/run/.containerenv 判定容器", system._is_running_in_container() is True)
    # 无标记文件，cgroup 含 docker -> 容器
    with mock.patch.object(system.os.path, "exists", return_value=False), \
         mock.patch("builtins.open", mock.mock_open(read_data="12:memory:/docker/abc123")):
        check("cgroup 含 docker 判定容器", system._is_running_in_container() is True)
    # 无标记文件，cgroup 无关键字 -> 非容器
    with mock.patch.object(system.os.path, "exists", return_value=False), \
         mock.patch("builtins.open", mock.mock_open(read_data="0::/")):
        check("cgroup 无关键字判定非容器", system._is_running_in_container() is False)
    # 读取 cgroup 失败 -> 非容器
    with mock.patch.object(system.os.path, "exists", return_value=False), \
         mock.patch("builtins.open", side_effect=OSError("no")):
        check("cgroup 读取失败判定非容器", system._is_running_in_container() is False)


def test_readers():
    print("[2] PID1 进程名 / CapEff 读取")
    # _pid1_comm
    with mock.patch("builtins.open", mock.mock_open(read_data="systemd\n")):
        check("PID1 comm = systemd", system._pid1_comm() == "systemd")
    with mock.patch("builtins.open", side_effect=OSError("no")):
        check("PID1 comm 读取失败回退空串", system._pid1_comm() == "")
    # _capeff：CapEff 行为 0000001fffffffff -> 0x1fffffffff
    with mock.patch("builtins.open", mock.mock_open(read_data="CapEff:\t0000001fffffffff\nCapBnd:\t0000001fffffffff\n")):
        check("CapEff 解析", system._capeff() == 0x1fffffffff)
    with mock.patch("builtins.open", side_effect=OSError("no")):
        check("CapEff 读取失败回退 0", system._capeff() == 0)


def test_install_check_host_mode():
    print("[3] 本机直跑（非容器）视为完整")
    with mock.patch.object(system, "_is_running_in_container", return_value=False):
        res = system.install_check_sync()
        check("本机 ok", res["ok"] is True)
        check("本机 missing 为空", res["missing"] == [])
        check("本机 mode=host", res["mode"] == "host")


def _exists_in(paths):
    """构造 os.path.exists mock：Windows 下 os.path.join 会产生反斜杠，统一转成正斜杠比较。"""
    norm = {p.replace("\\", "/") for p in paths}
    return lambda p: p.replace("\\", "/") in norm


def test_install_check_missing():
    print("[4] 容器模式逐项检测缺失")
    env_base = {"HOST_ROOT": "/host", "GRAW_HOST_DATA": "/opt/graw/data"}

    # 完整安装 -> 无缺失
    with mock.patch.object(system, "_is_running_in_container", return_value=True), \
         mock.patch.dict(os.environ, env_base, clear=True), \
         mock.patch.object(system.os.path, "exists", _exists_in(["/host/etc/passwd", "/var/run/docker.sock"])), \
         mock.patch.object(system, "_pid1_comm", return_value="systemd"), \
         mock.patch.object(system, "_capeff", return_value=0x1fffffffff):
        res = system.install_check_sync()
        check("完整安装 ok", res["ok"] is True and res["missing"] == [], str(res["missing"]))

    # 缺 HOST_ROOT
    with mock.patch.object(system, "_is_running_in_container", return_value=True), \
         mock.patch.dict(os.environ, {}, clear=True), \
         mock.patch.object(system.os.path, "exists", return_value=False), \
         mock.patch.object(system, "_pid1_comm", return_value="systemd"), \
         mock.patch.object(system, "_capeff", return_value=0x1fffffffff):
        res = system.install_check_sync()
        check("缺 HOST_ROOT", "host_root" in res["missing"], str(res["missing"]))

    # 设了 HOST_ROOT 但 /host/etc/passwd 不存在（挂载不完整）
    with mock.patch.object(system, "_is_running_in_container", return_value=True), \
         mock.patch.dict(os.environ, {"HOST_ROOT": "/host"}, clear=True), \
         mock.patch.object(system.os.path, "exists", _exists_in(["/var/run/docker.sock"])), \
         mock.patch.object(system, "_pid1_comm", return_value="systemd"), \
         mock.patch.object(system, "_capeff", return_value=0x1fffffffff):
        res = system.install_check_sync()
        check("host 挂载不完整", "host_mount" in res["missing"], str(res["missing"]))

    # 缺 Docker socket
    with mock.patch.object(system, "_is_running_in_container", return_value=True), \
         mock.patch.dict(os.environ, env_base, clear=True), \
         mock.patch.object(system.os.path, "exists", _exists_in(["/host/etc/passwd"])), \
         mock.patch.object(system, "_pid1_comm", return_value="systemd"), \
         mock.patch.object(system, "_capeff", return_value=0x1fffffffff):
        res = system.install_check_sync()
        check("缺 Docker socket", "docker_sock" in res["missing"], str(res["missing"]))

    # 缺 --pid host（PID1 非 systemd）
    with mock.patch.object(system, "_is_running_in_container", return_value=True), \
         mock.patch.dict(os.environ, env_base, clear=True), \
         mock.patch.object(system.os.path, "exists", _exists_in(["/host/etc/passwd", "/var/run/docker.sock"])), \
         mock.patch.object(system, "_pid1_comm", return_value="python3"), \
         mock.patch.object(system, "_capeff", return_value=0x1fffffffff):
        res = system.install_check_sync()
        check("缺 --pid host", "pid_host" in res["missing"], str(res["missing"]))

    # 缺 --privileged（CapEff 无 CAP_SYS_ADMIN 位 21）
    with mock.patch.object(system, "_is_running_in_container", return_value=True), \
         mock.patch.dict(os.environ, env_base, clear=True), \
         mock.patch.object(system.os.path, "exists", _exists_in(["/host/etc/passwd", "/var/run/docker.sock"])), \
         mock.patch.object(system, "_pid1_comm", return_value="systemd"), \
         mock.patch.object(system, "_capeff", return_value=0x1fffff):
        res = system.install_check_sync()
        check("缺 --privileged", "privileged" in res["missing"], str(res["missing"]))

    # 缺 GRAW_HOST_DATA
    with mock.patch.object(system, "_is_running_in_container", return_value=True), \
         mock.patch.dict(os.environ, {"HOST_ROOT": "/host"}, clear=True), \
         mock.patch.object(system.os.path, "exists", _exists_in(["/host/etc/passwd", "/var/run/docker.sock"])), \
         mock.patch.object(system, "_pid1_comm", return_value="systemd"), \
         mock.patch.object(system, "_capeff", return_value=0x1fffffffff):
        res = system.install_check_sync()
        check("缺 GRAW_HOST_DATA", "host_data" in res["missing"], str(res["missing"]))

    # 全部缺失 -> ok=False（HOST_ROOT 未设置时无法判断挂载完整性，因此不含 host_mount）
    with mock.patch.object(system, "_is_running_in_container", return_value=True), \
         mock.patch.dict(os.environ, {}, clear=True), \
         mock.patch.object(system.os.path, "exists", return_value=False), \
         mock.patch.object(system, "_pid1_comm", return_value="python3"), \
         mock.patch.object(system, "_capeff", return_value=0):
        res = system.install_check_sync()
        check("全缺失 ok=False", res["ok"] is False)
        check("全缺失 mode=docker", res["mode"] == "docker")
        check("全缺失包含全部 key",
              set(res["missing"]) == {"host_root", "docker_sock", "pid_host", "privileged", "host_data"},
              str(res["missing"]))


def main():
    test_container_detection()
    test_readers()
    test_install_check_host_mode()
    test_install_check_missing()
    print(f"\n结果：通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
