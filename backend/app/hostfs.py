# -*- coding: utf-8 -*-
"""
hostfs.py - 宿主机文件系统 / 命令访问适配层

背景：
  面板以 Docker 容器方式运行时，会把宿主机根目录 "/" 挂载到容器内的
  HOST_ROOT（默认 "/host"）。通过该适配层：

    - host_path()   把"宿主机视角"的绝对路径，映射为容器内实际可访问的
                    路径（即拼接 HOST_ROOT 前缀）。例如 /etc/nginx -> /host/etc/nginx。
    - unhost_path() 反向还原，供前端展示宿主机视角的路径。
    - host_cmd()    通过 chroot HOST_ROOT 在宿主机环境中执行系统命令
                    （nginx / certbot / iptables / crontab 等），使其直接
                    作用于宿主机，而不是容器内。

  当 HOST_ROOT 为空（未设置）时，视为面板直接在宿主机本机运行，所有路径
  与命令保持原样，行为与改造前完全一致。因此同一套代码可同时支持
  "本机直接运行" 与 "Docker /host 挂载运行" 两种部署方式。
"""
import os
import shutil
import subprocess
from typing import List, Optional

# 宿主机根目录挂载点；留空表示直接在本机运行（非容器）
HOST_ROOT = os.environ.get("HOST_ROOT", "").rstrip("/")


def get_host_root() -> str:
    """返回宿主机根目录在容器内的挂载点。"""
    return HOST_ROOT


def is_host_mounted() -> bool:
    """是否启用了容器 /host 挂载模式。"""
    return bool(HOST_ROOT)


def host_path(path: str) -> str:
    """把宿主机视角的绝对路径，映射为容器内实际可访问的路径。

    若未启用挂载模式，则原样返回；否则拼接 HOST_ROOT 前缀。
    例如 host_path("/etc/nginx") -> "/host/etc/nginx"。
    """
    if not HOST_ROOT:
        return path
    if not path or path == "/":
        return HOST_ROOT
    return HOST_ROOT + "/" + path.lstrip("/")


def unhost_path(real_path: str) -> str:
    """把容器内实际路径还原为宿主机视角的路径（供前端展示）。

    例如 unhost_path("/host/etc/nginx") -> "/etc/nginx"。
    传入的路径不在挂载点下时，原样返回。
    """
    if not HOST_ROOT:
        return real_path
    if real_path == HOST_ROOT:
        return "/"
    prefix = HOST_ROOT + "/"
    if real_path.startswith(prefix):
        return "/" + real_path[len(prefix):]
    return real_path


def host_cmd(args: List[str], **kwargs) -> "subprocess.CompletedProcess":
    """在宿主机环境中执行命令。

    容器模式下通过 chroot HOST_ROOT 切换到宿主文件系统后执行命令，
    让 nginx / certbot / iptables / crontab 等直接操作宿主机。
    非容器模式下直接执行。

    入参 kwargs 透传给 subprocess.run（capture_output / timeout 等）。
    返回 CompletedProcess；命令缺失时返回 returncode=127 的占位结果，
    避免上层接口直接抛异常。
    """
    cmd = list(args)
    if HOST_ROOT:
        # chroot 后命令在宿主文件系统中查找（nginx/certbot 等均在宿主）。
        # 注意：chroot 仅改变文件系统根，进程/网络命名空间由
        # privileged + pid:host + network_mode:host 保证与宿主机一致。
        cmd = ["chroot", HOST_ROOT] + cmd
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError as e:
        # 宿主机缺少对应命令时，返回非零退出码，便于上层给出可读提示
        return subprocess.CompletedProcess(cmd, 127, b"", str(e).encode())


def host_which(cmd: str) -> Optional[str]:
    """在宿主机环境中查找命令的绝对路径。

    容器模式下仅检查宿主机常见 bin 目录；非容器模式直接调用 shutil.which。
    返回 None 表示宿主机上不存在该命令。
    """
    if not HOST_ROOT:
        return shutil.which(cmd)
    for base in ("/usr/sbin", "/usr/bin", "/sbin", "/bin"):
        p = HOST_ROOT + base + "/" + cmd
        if os.path.isfile(p):
            return p
    return None


def host_shell(command: str, **kwargs) -> "subprocess.CompletedProcess":
    """以 shell 形式在宿主机环境执行单条命令字符串（用于 crontab 等场景）。"""
    if not HOST_ROOT:
        return subprocess.run(command, shell=True, **kwargs)
    # chroot 后通过 /bin/sh -c 执行，使命令在宿主文件系统中解析
    cmd = ["chroot", HOST_ROOT, "/bin/sh", "-c", command]
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(cmd, 127, b"", str(e).encode())
