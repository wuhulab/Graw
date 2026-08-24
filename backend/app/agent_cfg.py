# -*- coding: utf-8 -*-
"""
agent_cfg.py - 「作为子节点」Agent 收取模式配置（持久化）

背景：
  Graw 允许一台面板作为子节点，供其它主面板通过 Agent 隧道 + 成对访问密钥接入。
  收取模式的开启（key/secret/role）传统上用环境变量 GRAW_AGENT_KEY / GRAW_AGENT_SECRET /
  GRAW_AGENT_ROLE 注入，进程启动时确定、无法热切换。

  本模块引入一份可持久化配置 `data/agent.json`，让管理员在「设置」里动态启用/禁用
  并配置密钥，而无需改环境变量或重启进程。读取优先级：
    1. 持久化配置（data/agent.json）——设置界面写入
    2. 环境变量（GRAW_AGENT_KEY/SECRET/ROLE）——容器/脚本部署的传统方式
  二者任一满足即可工作；持久化配置优先，便于覆盖环境变量。

安全：
  - secret 为机器间鉴权核心，允许设置时留空表示「保持原值」，读取时不回传明文。
  - role 仅允许 admin | user，其余字段按白名单收紧。
"""
import json
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger("graw.agent")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
AGENT_CFG_FILE = os.path.join(DATA_DIR, "agent.json")

# 时间戳新鲜度窗口（秒）：超过即视为重放/过时，拒绝
AGENT_TS_WINDOW = int(os.environ.get("GRAW_AGENT_TS_WINDOW", "300"))

_lock = threading.Lock()
_cache: Optional[dict] = None


def _default() -> dict:
    """默认配置：未启用。"""
    return {
        "enabled": False,
        "key": "",
        "secret": "",
        "role": "user",
        # secret 是否已被展示过（一次性展示标记）。新建/重置 secret 后置 False，
        # 前端可拉取一次明文用于复制；展示后置 True，之后不再回传。
        "secret_revealed": False,
    }


def _load() -> dict:
    """读取持久化配置（带内存缓存与锁）。"""
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        d = _default()
        if os.path.exists(AGENT_CFG_FILE):
            try:
                with open(AGENT_CFG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    d.update(data)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("读取 agent 配置失败：%s", e)
        _cache = d
        return _cache


def _save() -> None:
    """持久化当前内存配置（带锁，原子写临时文件后替换）。"""
    global _cache
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = AGENT_CFG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_cache if _cache is not None else _default(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, AGENT_CFG_FILE)


def _env_cfg() -> dict:
    """从环境变量读取的传统配置（无持久化配置时使用）。"""
    return {
        "key": os.environ.get("GRAW_AGENT_KEY", "").strip(),
        "secret": os.environ.get("GRAW_AGENT_SECRET", "").strip(),
        "role": os.environ.get("GRAW_AGENT_ROLE", "user").strip().lower(),
    }


def get_config() -> dict:
    """返回当前生效的 Agent 配置（持久化优先，回退环境变量）。

    仅返回内部口径（含 secret 明文），供签名与鉴权使用；不得直接回传前端。
    """
    d = _load()
    env = _env_cfg()
    # 若持久化未写入密钥，则启用环境变量方案（兼容传统部署，无需设置界面）
    if not d.get("key") or not d.get("secret"):
        if env["key"] and env["secret"]:
            return {
                "enabled": True,
                "key": env["key"],
                "secret": env["secret"],
                "role": env["role"] if env["role"] in ("admin", "user") else "user",
                # 环境变量注入的 secret 无持久化标记，视为已展示（不回传明文）
                "secret_revealed": True,
            }
        return {"enabled": False, "key": "", "secret": "", "role": d.get("role") or "user", "secret_revealed": True}
    return d


def enabled() -> bool:
    """Agent 收取模式是否处于可用状态（key+secret 成对齐全且 enabled）。"""
    cfg = get_config()
    return bool(cfg.get("enabled")) and bool(cfg.get("key")) and bool(cfg.get("secret"))


def public_status() -> dict:
    """供前端展示的脱敏状态：不回传 secret，不暴露 key 明文。"""
    cfg = get_config()
    return {
        "enabled": enabled(),
        "role": cfg.get("role") or "user",
        # key 是半公开标识（主面板需要它来换取 token），可回传用于展示；
        # secret 属机器间核心秘密，永不回传。
        "key": cfg.get("key") or "",
        "has_secret": bool(cfg.get("secret")),
        # 尚未被展示过（新建/重置后）→ 允许前端一次性拉取明文复制
        "can_reveal": bool(cfg.get("enabled")) and bool(cfg.get("secret")) and not cfg.get("secret_revealed"),
        # 时间戳窗口仅供认知展示，非敏感
        "ts_window": AGENT_TS_WINDOW,
    }


def reveal_secret() -> dict:
    """一次性返回 secret 明文（供复制配置用），并立即标记已展示。

    仅当「启用 + 有 secret + 尚未展示过」时才返回明文；否则返回空。返回后
    无论是否复制都置 secret_revealed=True，避免明文常驻接口。
    """
    with _lock:
        cfg = _load()
        can = bool(cfg.get("enabled")) and bool(cfg.get("secret")) and not cfg.get("secret_revealed")
        if not can:
            return {"secret": "", "revealed": True}  # 已展示过 / 未配置，无可展示
        # 返回前先置标记并持久化，保证原子性（即使客户端不去消费也只给一次）
        cfg["secret_revealed"] = True
        _cache = cfg
        plain = cfg.get("secret") or ""
        _save()
        return {"secret": plain, "revealed": False, "role": cfg.get("role") or "user"}


def reload() -> None:
    """强制重新读取持久化配置（设置写入后调用，刷新缓存）。"""
    global _cache
    with _lock:
        _cache = None


def set_config(enabled: bool, key: str = "", secret: str = "", role: str = "user") -> dict:
    """写入持久化 Agent 配置（secret 留空表示保持原值）。返回脱敏状态。

    仅管理员经设置界面调用。切换为禁用时 secret 一并清除（失活后不再鉴权）。
    secret 被更新/重置后，secret_revealed 复位为 False，允许前端再次一次性展示。
    """
    global _cache
    cur = _load()
    effective_key = (key or "").strip() or cur.get("key")
    secret_changed = False  # secret 是否本轮回传了新值（重置）
    if enabled:
        # 启用要求 key+secret 成对齐全
        if not effective_key:
            raise ValueError("未配置访问 key，无法启用")
        if (secret or "").strip():
            effective_secret = (secret or "").strip()
            secret_changed = True
        elif cur.get("secret"):
            effective_secret = cur.get("secret")
        else:
            raise ValueError("未配置校验 secret，无法启用")
        effective_role = role if role in ("admin", "user") else "user"
    else:
        effective_key = effective_key
        effective_secret = ""  # 禁用时清除 secret，避免残留鉴权面
        # 保留用户选择的角色（仅作展示记忆）；禁用态不会签发，无鉴权风险
        effective_role = role if role in ("admin", "user") else (cur.get("role") or "user")
    cfg = {
        "enabled": bool(enabled),
        "key": effective_key,
        "secret": effective_secret,
        "role": effective_role,
    }
    if secret_changed and enabled:
        cfg["secret_revealed"] = False  # 新 secret → 允许重新展示一次
    else:
        cfg["secret_revealed"] = bool(cur.get("secret_revealed", True)) if (enabled and effective_secret) else True
    with _lock:
        _cache = cfg
    _save()
    logger.info("更新 Agent 收取模式配置：enabled=%s", bool(enabled))
    return public_status()