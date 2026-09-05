# -*- coding: utf-8 -*-
"""
gitdeploy.py - 站点 Git 自动部署核心库（非路由模块）

背景：
  运维最常见的发布动作是「代码提交后自动上线」。本模块为站点绑定 Git 仓库：
  记录仓库 / 分支 / 目标目录（默认站点 root）与 webhook 密钥，支持手动触发与
  外部 Git 平台 Webhook 触发，在目标节点上执行 git fetch + reset --hard，
  使站点代码与远端分支强制一致。

设计要点：
  - 安全：repo_url / branch / deploy_dir / id 全部白名单校验，防注入与穿越；
    webhook 认证用 GitHub 标准 X-Hub-Signature-256（HMAC-SHA256）恒时比较，
    并兼容 ?secret= 查询参数（Gitee/Gitea 不带 HMAC、本地 curl 模拟方便）。
  - 令牌注入：auth=token 时经 git -c http.extraheader 注入 Authorization，
    不污染仓库 remote 配置，输出日志对 URL 做脱敏。
  - 幂等：reset --hard 天然幂等，失败只记录 last_run.error，无需单独回滚。
  - 记录：每次部署写入任务中心（tasks.create_task/append_log）并可选
    notify.push_all 广播；data/gitdeploy.json 原子写、凭据不回显。
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger("graw.gitdeploy")

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
DEPLOY_FILE = os.path.join(DATA_DIR, "gitdeploy.json")

# ID 白名单（同时用于路径命名/日志文件，防穿越）
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# Git 分支名（含斜杠，如 feature/foo）：字母/数字/._-/，防 shell 注入
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_.\-/]{1,128}$")
# 仓库地址白名单：https/http 或 git@ 或 ssh://（拒绝换行/分号等）
_REPO_RE = re.compile(
    r"^(https?://[^\r\n\x00]+|git@[A-Za-z0-9.\-]+:[^\r\n\x00]+|ssh://[^\r\n\x00]+)$"
)
# Linux 绝对路径 (/) 或 Windows 盘符路径；拒绝控制字符
_DIR_RE = re.compile(r"^(/[^\r\n\x00]*|[A-Za-z]:[\\/][^\r\n\x00]*)$")
# 每个仓库允许的 webhook body 上限（GitHub 大 payload 也仅数百 KB）
MAX_BODY = 2 * 1024 * 1024
# 部署超时（git 网络慢时拖住线程，避免无限等待）
DEPLOY_TIMEOUT = 120

# 数据写锁（防并发 CRUD / 并发触发 run 写坏 JSON）
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 数据存储（原子写）
# ---------------------------------------------------------------------------
def _load() -> dict:
    """读取部署配置，损坏/缺失时返回空结构。"""
    if not os.path.exists(DEPLOY_FILE):
        return {"version": 1, "deploys": []}
    try:
        with open(DEPLOY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"version": 1, "deploys": []}
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "deploys": []}


def _save(data: dict) -> None:
    """原子写：临时文件 + os.replace（防止写一半崩溃损坏数据）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DEPLOY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DEPLOY_FILE)


def _mask_url(url: str) -> str:
    """对仓库 URL 脱敏：https://user:token@host → https://***@host。"""
    try:
        import urllib.parse

        p = urllib.parse.urlsplit(url)
        if p.password:
            netloc = f"{p.username}:***@{p.hostname}" + (f":{p.port}" if p.port else "")
            return urllib.parse.urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))
    except Exception:
        pass
    return url


def _public(deploy: dict) -> dict:
    """对外脱敏：永不返回 token / secret 明文。"""
    return {
        "id": deploy.get("id", ""),
        "name": deploy.get("name", ""),
        "site_id": deploy.get("site_id", ""),
        "site_name": deploy.get("site_name", ""),
        "repo_url": _mask_url(deploy.get("source", {}).get("repo_url", "")),
        "branch": deploy.get("source", {}).get("branch", ""),
        "auth": deploy.get("source", {}).get("auth", "none"),
        "has_token": bool(deploy.get("source", {}).get("token", "")),
        "deploy_dir": deploy.get("deploy_dir", ""),
        "node_id": deploy.get("node_id", "local"),
        "has_secret": bool(deploy.get("secret", "")),
        "notify": bool(deploy.get("notify", True)),
        "status": deploy.get("status", "idle"),
        "last_run": deploy.get("last_run", {}),
    }


def list_deploys() -> list:
    """返回全部部署记录（脱敏后）。"""
    return [_public(d) for d in _load().get("deploys", [])]


def get_deploy(deploy_id: str) -> Optional[dict]:
    """按 ID 取节点内部记录（含凭据，仅内部使用）。"""
    for d in _load().get("deploys", []):
        if d.get("id") == deploy_id:
            return d
    return None


def _find_index(data: dict, deploy_id: str) -> int:
    """返回部署在数组中的索引；不存在返回 -1。"""
    for i, d in enumerate(data.get("deploys", [])):
        if d.get("id") == deploy_id:
            return i
    return -1


def create_deploy(rec: dict) -> dict:
    """创建部署记录（含内部字段），返回脱敏结果与一次性 secret。"""
    with _lock:
        data = _load()
        deploy = {
            "id": uuid.uuid4().hex[:10],
            "name": rec.get("name", "").strip()[:64] or "Git 部署",
            "site_id": rec.get("site_id", ""),
            "site_name": rec.get("site_name", ""),
            "source": {
                "repo_url": rec["repo_url"],
                "branch": rec.get("branch", "main"),
                "auth": rec.get("auth", "none"),
                "token": rec.get("token", ""),
            },
            "deploy_dir": rec["deploy_dir"],
            "node_id": rec.get("node_id", "local"),
            "secret": secrets.token_hex(16),  # 32 位十六进制随机密钥
            "notify": bool(rec.get("notify", True)),
            "status": "idle",
            "last_run": {},
        }
        data.setdefault("deploys", []).append(deploy)
        _save(data)
        pub = _public(deploy)
        return {**pub, "secret_once": deploy["secret"]}


def update_deploy(deploy_id: str, rec: dict, reset_secret: bool = False) -> dict:
    """更新部署记录；secret 传空保持原值（reset_secret=True 时重新生成）。"""
    with _lock:
        data = _load()
        idx = _find_index(data, deploy_id)
        if idx < 0:
            raise KeyError(f"部署不存在: {deploy_id}")
        d = data["deploys"][idx]
        if "name" in rec:
            d["name"] = (rec["name"] or "").strip()[:64]
        if "repo_url" in rec:
            d["source"]["repo_url"] = rec["repo_url"]
        if "branch" in rec:
            d["source"]["branch"] = rec["branch"]
        if "auth" in rec:
            d["source"]["auth"] = rec["auth"]
        if "token" in rec:
            # 允许清空令牌（传空字符串即移除）
            d["source"]["token"] = (rec.get("token") or "")
        if "deploy_dir" in rec:
            d["deploy_dir"] = rec["deploy_dir"]
        if "node_id" in rec:
            d["node_id"] = rec["node_id"]
        if "notify" in rec:
            d["notify"] = bool(rec.get("notify", True))
        if reset_secret or bool(rec.get("reset_secret")):
            d["secret"] = secrets.token_hex(16)
        _save(data)
        pub = _public(d)
        return {**pub, "secret_once": d["secret"] if reset_secret or bool(rec.get("reset_secret")) else ""}


def delete_deploy(deploy_id: str) -> bool:
    with _lock:
        data = _load()
        idx = _find_index(data, deploy_id)
        if idx < 0:
            return False
        data["deploys"].pop(idx)
        _save(data)
        return True


def _set_status(deploy_id: str, status: str, last_run: Optional[dict] = None) -> None:
    """更新部署状态（运行中 / 成功 / 失败）与最近一次运行摘要。"""
    with _lock:
        data = _load()
        idx = _find_index(data, deploy_id)
        if idx < 0:
            return
        d = data["deploys"][idx]
        d["status"] = status
        if last_run is not None:
            d["last_run"] = last_run
        _save(data)


# ---------------------------------------------------------------------------
# Webhook 校验
# ---------------------------------------------------------------------------
def verify_webhook(deploy: dict, body: bytes, signature: str, query_secret: str = "") -> bool:
    """校验 webhook 请求是否合法。

    优先 GitHub 标准 X-Hub-Signature-256（sha256=HMAC(secret, body)）恒时比较；
    无签名头时兼容 ?secret= 查询参数（Gitee/Gitea 与本地 curl 模拟）。
    """
    secret = (deploy.get("secret") or "").encode("utf-8")
    if signature:
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected):
            return True
    if query_secret and hmac.compare_digest(query_secret, (deploy.get("secret") or "")):
        return True
    return False


def webhook_branch(body: bytes) -> str:
    """从 webhook body 提取分支名（refs/heads/<branch>），解析失败返回空。"""
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return ""
    ref = payload.get("ref") or payload.get("head") or ""
    if not isinstance(ref, str):
        return ""
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    # Gitee 部分事件不带 refs/heads 前缀，直接返回末段兜底
    return ref.rsplit("/", 1)[-1] if "/" in ref else ref


# ---------------------------------------------------------------------------
# 部署执行
# ---------------------------------------------------------------------------
def _git_cmd(deploy: dict, *args: str):
    """构造 git argv（带可选 http 凭据注入），在目标节点的 git -C 语义下执行。"""
    cmd = ["git", "-C", deploy["deploy_dir"]]
    src = deploy.get("source", {})
    if src.get("auth") == "token" and src.get("token"):
        b64 = base64.b64encode(f"x-access-token:{src['token']}".encode("utf-8")).decode("ascii")
        cmd += ["-c", "http.extraheader=Authorization: basic " + b64]
    return cmd + list(args)


def _exec_on_node(deploy: dict, cmd: list) -> "subprocess.CompletedProcess":
    """在部署目标节点上下文内以 argv 形式执行 git 子命令（无 shell 拼接）。"""
    from app import node_manager

    return node_manager.run_on_node(
        deploy.get("node_id") or "local",
        lambda: node_manager.host_cmd(cmd, capture_output=True, text=True, timeout=DEPLOY_TIMEOUT),
    )


def run_deploy(deploy_id: str) -> dict:
    """执行一次部署（供手动触发 / webhook 触发共用）。

    注意：应在线程池（asyncio.to_thread）中调用，避免阻塞事件循环。
    """
    from app import tasks as taskcenter
    from app import notify

    deploy = get_deploy(deploy_id)
    if not deploy:
        raise ValueError(f"部署不存在: {deploy_id}")
    branch = deploy["source"]["branch"]
    site_name = deploy.get("site_name") or deploy.get("site_id") or deploy_id

    task = taskcenter.create_task(
        {
            "name": f"Git 部署 {site_name}@{branch}",
            "type": "gitdeploy",
            "target": deploy.get("deploy_dir"),
        }
    )
    task_id = task["id"]

    def _log(text: str):
        taskcenter.append_log(task_id, {"type": "log", "line": text})

    last_run = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ok": False, "rev": "", "error": ""}
    error = ""
    result = {}
    _log(f"开始部署：{deploy.get('name')}（{site_name} @ {branch}）")
    _log(f"目标目录：{deploy.get('deploy_dir')}（节点 {deploy.get('node_id')}）")
    _set_status(deploy_id, "running")
    try:
        # 1) fetch（远端最新提交；auth=token 时带凭据）
        r = _exec_on_node(deploy, _git_cmd(deploy, "fetch", "origin", branch))
        _log(f"git fetch: exit={r.returncode}")
        if r.returncode != 0:
            err_txt = (r.stderr or r.stdout or "").strip()
            raise RuntimeError(f"git fetch 失败: {err_txt[:500]}")
        # 2) 强制对齐远端分支（幂等）
        r = _exec_on_node(deploy, _git_cmd(deploy, "reset", "--hard", f"origin/{branch}"))
        _log(f"git reset: exit={r.returncode}")
        if r.returncode != 0:
            err_txt = (r.stderr or r.stdout or "").strip()
            raise RuntimeError(f"git reset 失败: {err_txt[:500]}")
        # 3) 子模块（失败容忍，不阻塞主流程）
        try:
            r = _exec_on_node(deploy, _git_cmd(deploy, "submodule", "update", "--init", "--recursive"))
            if r.returncode != 0:
                _log(f"submodule 警告: {(r.stderr or '').strip()[:300]}")
        except Exception as e:
            _log(f"submodule 警告: {e}")
        # 4) 取当前 HEAD 摘要
        try:
            r = _exec_on_node(deploy, ["git", "-C", deploy["deploy_dir"], "rev-parse", "--short", "HEAD"])
            result["rev"] = (r.stdout or "").strip()[:16] if r.returncode == 0 else ""
        except Exception:
            result["rev"] = ""

        taskcenter.update_task(task_id, status="success", finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), result=result)
        _log("部署成功")
        last_run = {
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ok": True,
            "rev": result.get("rev", ""),
            "error": "",
        }
        if deploy.get("notify"):
            try:
                notify.push_all(f"站点《{site_name}》Git 部署成功（{branch}@{result.get('rev', '?')}）")
            except Exception:
                logger.warning("Git 部署成功但通知失败", exc_info=True)
    except Exception as e:  # 部署失败：记录任务错误与 last_run
        error = str(e)
        taskcenter.update_task(task_id, status="failed", finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), error=error)
        taskcenter.append_log(task_id, {"type": "error", "message": error})
        last_run = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ok": False, "rev": "", "error": error[:500]}
        if deploy.get("notify"):
            try:
                notify.push_all(f"站点《{site_name}》Git 部署失败：{error[:200]}")
            except Exception:
                logger.warning("Git 部署失败通知异常", exc_info=True)
    finally:
        _set_status(deploy_id, "success" if not error else "failed", last_run)
    if error:
        raise RuntimeError(error)
    return last_run