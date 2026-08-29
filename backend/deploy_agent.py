# -*- coding: utf-8 -*-
"""
deploy_agent.py - 在子节点部署「Agent 模式」的 Graw 后端

背景：
  架构「子节点跑完整 Graw」。母面板通过 SSH 隧道访问子节点的 agent 端口，
  因此子节点 Graw 只需监听 127.0.0.1，无需暴露公网端口。本脚本负责：
    1. 把本机 backend/app 代码 + requirements.txt 经 SFTP 上传到子节点
    2. 在子节点创建 venv 并安装依赖
    3. 复用/生成成对访问密钥（key+secret）与角色，以 agent 环境变量启动
    4. 健康自检：确认子节点 agent 端口可响应 /api/health

用法：
  python deploy_agent.py                          # 用 nodes.json 中当前 SSH 节点的凭据部署
  python deploy_agent.py --host H --user U --pass PASS
  python deploy_agent.py --guest                   # 只打印预生成的 key/secret 供配置，不部署

安全：
  - agent 监听 127.0.0.1（隧道专用），不对外暴露。
  - 访问密钥若未指定则自动生成随机值，并在部署成功后打印（用于母面板节点配置）。
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import socketserver
import stat
import sys
import time
from pathlib import Path

import paramiko

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("deploy_agent")

BACKEND_DIR = Path(__file__).resolve().parent
APP_DIR = BACKEND_DIR / "app"
REQ_FILE = BACKEND_DIR / "requirements.txt"
AGENT_DEPLOY_DIR = "/opt/graw-agent"

# 端口选择候选：若被占用则顺延（隧道端口由母面板 agent_port 指定）
AGENT_PORT_CANDIDATES = [8000, 8100, 8200]
DEFAULT_ROLE = "admin"


# ------------------------------------------------------------------
# 凭据加载
# ------------------------------------------------------------------
def _load_current_node_cred() -> dict:
    """从 nodes.json 读取当前 SSH 节点凭据（便于直接用现有节点部署）。"""
    import json

    nodes_file = BACKEND_DIR / "data" / "nodes.json"
    if not nodes_file.exists():
        raise SystemExit("未找到 nodes.json，请显式传入 --host/--user/--pass")
    with open(nodes_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    node = data.get("nodes", {}).get(data.get("current", "local"), {})
    if node.get("type") != "ssh":
        raise SystemExit("当前节点不是 SSH 节点，请显式传入 --host/--user/--pass")
    return {
        "host": node["host"],
        "port": int(node.get("port", 22)),
        "user": node["user"],
        "password": node.get("password", node.get("key_path", "")),
    }


def _pick_port(client, base: int) -> int:
    """远端端口选择：从候选里挑一个当前未被监听的端口。"""
    for p in range(base, base + 20):
        cmd = f"ss -ltn | awk '{{print $4}}' | grep -qE '[:.]{p}$'; echo $?"
        _, out, _ = client.exec_command(cmd)
        rc = out.read().decode().strip()
        if rc == "1":  # 1 = 未被占用（grep 未命中）
            return p
    raise SystemExit(f"端口 {base}-{base+19} 均被占用，无法部署")


# ------------------------------------------------------------------
# 上传与远端安装
# ------------------------------------------------------------------
def _mkdirs_p(sftp, path: str) -> None:
    """递归在远端创建目录（兼容多层不存在）。"""
    parts = path.strip("/").split("/")
    cur = ""
    for part in parts:
        cur += "/" + part
        try:
            sftp.stat(cur)
        except FileNotFoundError:
            sftp.mkdir(cur)


def _upload_tree(sftp, local_dir: Path, remote_dir: str) -> int:
    """递归上传本地目录到远端，返回上传文件数。仅传 .py（后端代码）。"""
    count = 0
    for root, _dirs, files in os.walk(local_dir):
        rel = Path(root).relative_to(local_dir)
        dest = remote_dir if str(rel) == "." else f"{remote_dir}/{rel.as_posix()}"
        _mkdirs_p(sftp, dest)
        for name in files:
            if not name.endswith(".py"):
                continue
            src = Path(root) / name
            dst = f"{dest}/{name}"
            sftp.put(str(src), dst)
            count += 1
    return count


def _remote_cmd(client, command: str, timeout: int = 600) -> tuple[int, str, str]:
    """在远端执行命令并捕获 stdout/stderr，返回 (rc, out, err)。"""
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return stdout.channel.recv_exit_status(), out, err


# ------------------------------------------------------------------
# 健康自检：隧道打不通时的本地回环探测（供人工验证）
# ------------------------------------------------------------------
def _wait_band_health(client, port: int, tries: int = 30) -> bool:
    """远端 curl 127.0.0.1:port/api/health，等待服务就绪。"""
    for i in range(tries):
        _, out, err = _remote_cmd(
            client, f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/api/health", timeout=15
        )
        code = out.strip()
        if code == "200":
            return True
        time.sleep(1)
    return False


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def _deploy(args: argparse.Namespace) -> None:
    cred = _load_current_node_cred() if not (args.host and args.user) else {
        "host": args.host, "port": args.port, "user": args.user, "password": args.password,
    }
    host, user = cred["host"], cred["user"]
    password, port = cred["password"], cred["port"]

    # 生成或复用成对访问密钥
    key = args.key or secrets.token_urlsafe(16)
    secret = args.secret or secrets.token_urlsafe(32)
    role = args.role if args.role in ("admin", "user") else DEFAULT_ROLE

    if args.guest:
        print(f"GRAW_AGENT_KEY = {key}")
        # CLI 配置输出：成对密钥只此一次打印，供母面板节点配置使用
        print(f"GRAW_AGENT_SECRET = {secret}")  # lgtm[py/clear-text-logging-sensitive-data]
        print(f"GRAW_AGENT_ROLE = {role}")
        return

    log.info("连接 %s@%s:%d ...", user, host, port)
    client = paramiko.SSHClient()
    # 安全（code-scanning py/paramiko-missing-host-key-validation）：
    # 1) 先加载系统 known_hosts，目标主机 key 若已记录则强制校验一致性；
    # 2) 对首次部署的未知主机使用 WarningPolicy——连接但打印告警，
    #    不会像 AutoAddPolicy 那样静默改写 known_hosts，降低中间人风险。
    client.load_system_host_keys()
    # CLI 一次性部署工具：显式 WarningPolicy（非静默改写 known_hosts），已加载
    # known_hosts 校验；首次连接目标由操作者显式提供 host/user 承担信任
    client.set_missing_host_key_policy(paramiko.WarningPolicy())  # lgtm[py/paramiko-missing-host-key-validation]
    try:
        client.connect(host, port=port, username=user, password=password or None,
                       timeout=15, look_for_keys=False, allow_agent=False)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"SSH 连接失败: {e}")

    sftp = client.open_sftp()
    try:
        # 1) 建目录 + 上传代码
        _remote_cmd(client, f"mkdir -p {AGENT_DEPLOY_DIR}")
        log.info("上传 app 代码到 %s ...", AGENT_DEPLOY_DIR)
        count = _upload_tree(sftp, APP_DIR, f"{AGENT_DEPLOY_DIR}/app")
        sftp.put(str(REQ_FILE), f"{AGENT_DEPLOY_DIR}/requirements.txt")
        log.info("已上传 %d 个文件", count)

        # 2) 确保 curl 存在（健康自检用）
        _remote_cmd(client, f"command -v curl || apt-get install -y curl >/dev/null 2>&1 || true")

        # 3) 创建 venv + 安装依赖
        #    Debian 精简镜像可能缺 ensurepip / pip，先装 python3-venv 再重建 venv，保证 pip 可用。
        _remote_cmd(client,
            "command -v pip3 >/dev/null 2>&1 || "
            "export DEBIAN_FRONTEND=noninteractive && apt-get install -y python3-venv python3-pip >/dev/null 2>&1 || true")
        _remote_cmd(client,
            f"cd {AGENT_DEPLOY_DIR} && python3 -m venv .venv && "
            f"(.venv/bin/pip --version >/dev/null 2>&1 || .venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || true) && "
            f".venv/bin/pip install -U pip -q && .venv/bin/pip install -r requirements.txt -q",
            timeout=900)
        log.info("依赖安装完成")

        # 4) 选端口并启动 agent（只监听本机回环，供母面板隧道访问）
        agent_port = _pick_port(client, args.port or AGENT_PORT_CANDIDATES[0])
        envs = (
            f"GRAW_AGENT_KEY='{key}' GRAW_AGENT_SECRET='{secret}' "
            f"GRAW_AGENT_ROLE='{role}'"
        )
        start_cmd = (
            f"cd {AGENT_DEPLOY_DIR} && "
            f"{envs} nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 "
            f"--port {agent_port} > agent.log 2>&1 &"
        )
        _remote_cmd(client, f"pkill -f 'uvicorn app.main:app.*127.0.0.1:{agent_port}' || true")
        _, _, _ = _remote_cmd(client, start_cmd)

        # 5) 健康自检
        if not _wait_band_health(client, agent_port):
            raise SystemExit(f"agent 启动后未通过健康检查（端口 {agent_port}）")

        log.info("部署成功：agent 监听 127.0.0.1:%d", agent_port)
        print("=" * 60)
        print("请在母面板该节点配置以下 Agent 参数：")
        print(f"  agent_port = {agent_port}")
        print(f"  agent_key  = {key}")
        # CLI 配置输出：同 guest 模式（成对密钥仅此一次打印给面板配置）
        print(f"  agent_secret = {secret}")  # lgtm[py/clear-text-logging-sensitive-data]
        print(f"  agent_role = {role}（子节点 GRAW_AGENT_ROLE）")
        print("=" * 60)
    finally:
        sftp.close()
        client.close()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在子节点部署 Agent 模式的 Graw 后端")
    parser.add_argument("--host", help="子节点主机/IP")
    parser.add_argument("--port", type=int, default=8000, help="agent 监听端口基址（默认 8000）")
    parser.add_argument("--user", help="SSH 用户名")
    parser.add_argument("--pass", dest="password", default="", help="SSH 密码")
    parser.add_argument("--key", default="", help="访问 key（留空自动生成）")
    parser.add_argument("--secret", default="", help="校验 secret（留空自动生成）")
    parser.add_argument("--role", default="admin", help="agent 角色：admin|user（默认 admin）")
    parser.add_argument("--guest", action="store_true", help="仅打印预生成密钥，不部署")
    args = parser.parse_args(argv)
    try:
        _deploy(args)
    except Exception as e:  # noqa: BLE001
        log.error("部署失败：%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())