# -*- coding: utf-8 -*-
"""
agent_auth.py - 子节点 Agent API 鉴权（成对访问密钥）

背景：
  架构上「子节点跑完整 Graw」给主面板提供全部应用能力。主面板不能直接复用
  子节点的人员登录口令，故设计独立的机器间鉴权：主面板持有一对访问密钥
  （访问 key + 校验 secret），向子节点换取短时 JWT，之后按现有 /api/* 鉴权
  调用——子节点现有路由接口零改动，天然支持全部应用。

鉴权协议（本文件实现 credential 校验 + JWT 签发）：
  - 子节点启动时通过环境变量注入：
      GRAW_AGENT_KEY    访问 key（主面板需持有）
      GRAW_AGENT_SECRET 校验 secret（主面板需持有）
    （未配置则 agent 端点整体关闭，不影响面板正常登录使用）
  - 主面板请求 GET /api/agent/issue 时在查询参数/头携带签名：
      X-Graw-Agent-Key  : 访问 key
      ts                : 当前 Unix 秒（防重放新鲜度）
      nonce             : 本次请求唯一随机串
      sig               = HMAC-SHA256(secret, key|ts|nonce)
    子节点用同一 secret 常量时间重算比对；超时（默认 300s）拒绝。
  - 校验通过即用与面板同源的 JWT 密钥签发一个固定 admin 角色的令牌，
    到期后主面板凭此令牌按常规 /api/* 鉴权访问全部应用接口。

安全要点：
  - hmac.compare_digest 常量时间比较，防时序侧信道
  - 角色固定为 admin（子节点始终以管理员权限被管理，由子节点侧强制，
    而非主面板自报），防越权
  - 时间戳 ±新鲜度校验防重放；nonce 防同秒重放
"""
from typing import Optional
import os
import time
import hmac
import hashlib
import secrets
import threading

from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from app import agent_cfg
from app.auth import (
    SECRET_KEY,
    create_token,
    ALGORITHM,
    hash_password,
    _load_users,
    _save_users,
    require_admin,
)

router = APIRouter()

# cache-busting / 通用小工具
token_urlsafe = secrets.token_urlsafe

# 配置已迁移到 agent_cfg（支持持久化 + 设置界面动态开关）；保留兼容别名以防外部引用
AGENT_TS_WINDOW = agent_cfg.AGENT_TS_WINDOW


class AgentIssueBody(BaseModel):
    """换取 JWT 的请求体：ts + nonce + sig。"""
    ts: int
    nonce: str
    sig: str


def _htime():
    return int(time.time())


def enabled() -> bool:
    """当前是否启用了 Agent API（成对密钥已配置，支持设置界面动态开关）。"""
    return agent_cfg.enabled()


def _compute_sig(key: str, ts: int, nonce: str) -> str:
    """计算签名：HMAC-SHA256(secret, f"{key}|{ts}|{nonce}")，hex 输出。"""
    secret = agent_cfg.get_config().get("secret") or ""
    msg = f"{key}|{ts}|{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


# Agent 专用的本地账号名：签发的 JWT 用它作为 sub，经 get_current_user 校验。
# 密码设为随机值使该账号无法通过常规登录入口登录（仅机器间 token 回流）。
def _ensure_agent_user(role: str) -> None:
    """幂等确保 agent 账号存在、角色正确，避免 JWT 校验失败。"""
    users = _load_users() or {}
    cur = users.get("agent")
    if cur is None or cur.get("role") != role:
        users["agent"] = {
            "username": "agent",
            "password": hash_password(secrets.token_urlsafe(32)),
            "role": role,
            "must_change_password": False,
            "created_at": time.time(),
        }
        _save_users(users)


def _verify_agent_credential(ts: int, nonce: str, sig: str, provided_key: str) -> bool:
    """校验成对密钥凭证：key 匹配 + 时间戳新鲜 + 签名常量时间比对 + nonce 未重放。"""
    cfg = agent_cfg.get_config()
    if not (cfg.get("enabled") and cfg.get("key") and cfg.get("secret")):
        return False
    # 访问 key 常量时间比较（防时序侧信道探测 key 前缀）
    if not hmac.compare_digest(provided_key or "", cfg.get("key") or ""):
        return False
    # 时间戳新鲜度：防重放
    now = _htime()
    if abs(now - ts) > agent_cfg.AGENT_TS_WINDOW:
        return False
    if not nonce or len(nonce) > 128:
        return False
    expected = _compute_sig(provided_key, ts, nonce)
    if not hmac.compare_digest(expected, sig or ""):
        return False
    # nonce 一次性消费：签名合法的 (key, nonce) 在时间窗内不得重复出现。
    # 此前 nonce 从未被记录，同一份被捕获的签名请求可在 ±窗口期内无限
    # 重放换取新 JWT。记录放在签名校验之后，伪造请求无法污染缓存。
    nk = f"{provided_key}|{nonce}"
    with _nonce_lock:
        _prune_used_nonces(now)
        if nk in _used_nonces:
            return False
        _used_nonces[nk] = ts
    return True


# 已消费 nonce 缓存：{f"{key}|{nonce}": ts}；容量上限防内存膨胀
_used_nonces: dict = {}
_nonce_lock = threading.Lock()
_NONCE_CACHE_MAX = 10000


def _prune_used_nonces(now: int) -> None:
    """清理时间窗之外与超容量的过期 nonce 记录（调用方持锁）。"""
    if len(_used_nonces) < _NONCE_CACHE_MAX and len(_used_nonces) < 512:
        return
    stale = [
        k for k, t in _used_nonces.items()
        if now - t > agent_cfg.AGENT_TS_WINDOW
    ]
    for k in stale:
        _used_nonces.pop(k, None)
    if len(_used_nonces) >= _NONCE_CACHE_MAX:
        # 仍超容量：按时间淘汰最旧的一半
        for k, _ in sorted(_used_nonces.items(), key=lambda kv: kv[1])[: len(_used_nonces) // 2]:
            _used_nonces.pop(k, None)


@router.get("/issue", name="agent_issue")
def agent_issue(
    ts: int = None,
    nonce: str = None,
    sig: str = None,
    x_graw_agent_key: str = Header(default=""),
):
    """成对密钥换取短时 JWT。

    主面板请求 GET /api/agent/issue?ts=..&nonce=..&sig=..，
    头 X-Graw-Agent-Key 传访问 key。校验通过返回 role 对应角色的 JWT。
    """
    if not agent_cfg.enabled():
        raise HTTPException(status_code=404, detail="Agent API 未启用")
    if ts is None or nonce is None or sig is None:
        raise HTTPException(status_code=400, detail="缺少 ts/nonce/sig")
    if not _verify_agent_credential(ts, nonce or "", sig or "", x_graw_agent_key or ""):
        raise HTTPException(status_code=401, detail="Agent 凭证校验失败")
    # 角色强制为 admin（子节点接入固定管理员权限，不可由请求方自报，防越权）
    role = "admin"
    username = "agent"
    # 确保 agent 本地账号存在且角色正确（JWT 经 get_current_user 校验时用到）
    _ensure_agent_user(role)
    users = _load_users() or {}
    cur = users.get("agent") or {}
    # 安全（第十三轮审计）：token_version 缺失/为 null 按 0 处理（防 TypeError）
    token = create_token(username, token_version=int(cur.get("token_version") or 0), sid="")
    return {
        "token": token,
        "role": role,
        "expires_in": 86400 * 7,
        "username": username,
    }


class AgentCfgBody(BaseModel):
    """设置界面「作为子节点」配置入参。子节点接入角色固定为 admin。"""
    enabled: bool
    key: str = ""
    secret: str = ""


@router.get("/cfg", name="agent_cfg_get", dependencies=[Depends(require_admin)])
def agent_cfg_get():
    """读取 Agent 收取模式配置（脱敏：不回传 secret）。仅管理员经设置界面访问。"""
    return agent_cfg.public_status()


@router.put("/cfg", name="agent_cfg_set", dependencies=[Depends(require_admin)])
def agent_cfg_set(body: AgentCfgBody):
    """写入 Agent 收取模式配置（secret 留空表示保持原值）。仅管理员。

    启用后最外层的 Agent 代理与 /api/agent/issue 立即生效，无需重启进程。
    """
    try:
        return agent_cfg.set_config(
            enabled=bool(body.enabled),
            key=body.key or "",
            secret=body.secret or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reveal-secret", name="agent_cfg_reveal", dependencies=[Depends(require_admin)])
def agent_cfg_reveal():
    """一次性返回子节点校验 secret 明文（供复制配置到其它面板）。

    仅当「启用且内有 secret 且尚未展示过」时返回明文；返回后即标记已展示，
    之后 /cfg 的 can_reveal 变为 false。重置（重新生成 secret 保存）后恢复。
    """
    return agent_cfg.reveal_secret()