# -*- coding: utf-8 -*-
"""
ftpusers.py - Graw 虚拟 FTP 用户管理路由

功能：
  1. 纯 Python 实现虚拟 FTP 用户管理：无需在系统创建真实用户，仅维护
     data/ftp_users.json 中的用户清单，供 FTP 服务端（如 pyftpdlib /
     ProFTPD 虚拟用户等）按此文件完成认证。
  2. 用户字段：username / password(bcrypt 哈希) / directory(chroot 路径) /
     enabled / description / created_at。
  3. CRUD 接口：列表、创建、更新、删除（仅管理员）。

安全：
  - 密码仅以 bcrypt 哈希落盘（与面板账号同款哈希），任何接口均不返回
    明文或哈希，列表返回前统一剥离 password 字段。
  - 用户名/目录做长度与格式白名单校验，防止脏数据注入。
  - 写入采用「临时文件 + os.replace」原子替换，并加锁避免并发写坏。

数据存储：
  backend/data/ftp_users.json :
    { "users": [ {id/username/password/directory/enabled/description/created_at} ] }
"""
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Optional

import bcrypt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("graw.ftpusers")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
FTPUSERS_FILE = os.path.join(DATA_DIR, "ftp_users.json")

# 用户名允许字符：字母/数字/点/下划线/中划线（不含空格等空白与路径分隔符）
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# 目录须为绝对路径：POSIX 的 "/" 开头，或 Windows 的盘符/UNC
_DIRECTORY_RE = re.compile(r"^(?:/|\\\\|[A-Za-z]:[\\/])")

_lock = threading.Lock()


def _default() -> dict:
    """空数据默认结构。"""
    return {"users": []}


def _load() -> dict:
    """读取 ftp_users.json；文件缺失/损坏时回退默认结构，不抛异常。"""
    if not os.path.exists(FTPUSERS_FILE):
        return _default()
    try:
        with open(FTPUSERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 ftp_users.json 失败，按默认处理: %s", e)
        return _default()
    if not isinstance(data, dict):
        return _default()
    data.setdefault("users", [])
    return data


def _save(data: dict):
    """原子写入 ftp_users.json（tmp + os.replace），加锁防并发写坏。"""
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = FTPUSERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, FTPUSERS_FILE)


def _find_user(users: list, user_id: str) -> dict:
    """按 id 查找用户，不存在抛 404。"""
    user = next((u for u in users if u.get("id") == user_id), None)
    if not user:
        raise HTTPException(status_code=404, detail="FTP 用户不存在")
    return user


def _public(user: dict) -> dict:
    """返回给前端的用户对象：剥离密码哈希，避免凭据外泄。"""
    return {k: v for k, v in user.items() if k != "password"}


def _hash_password(password: str) -> str:
    """bcrypt 哈希（与面板账号一致；bcrypt 仅取前 72 字节，显式截断兜底）。"""
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def _validate_username(username: str) -> str:
    """用户名白名单校验：字母/数字/._-，长度 1-64。"""
    username = (username or "").strip()
    if not _USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="用户名仅允许字母/数字/._-，且长度为 1-64 字符")
    return username


def _validate_directory(directory: str) -> str:
    """目录校验：非空、限长、必须为绝对路径。"""
    directory = (directory or "").strip()
    if not directory:
        raise HTTPException(status_code=400, detail="目录不能为空")
    if len(directory) > 1024:
        raise HTTPException(status_code=400, detail="目录过长（最多 1024 字符）")
    if not _DIRECTORY_RE.match(directory):
        raise HTTPException(status_code=400, detail="目录必须为绝对路径（如 /srv/ftp 或 C:\\ftp）")
    return directory


def _check_username_dup(users: list, username: str, exclude_id: Optional[str] = None) -> None:
    """用户名唯一性校验（更新时可排除自身）。"""
    username = username.strip()
    if any(
        u.get("username", "").strip() == username and u.get("id") != exclude_id
        for u in users
    ):
        raise HTTPException(status_code=400, detail="用户名已存在")


def _validate_password(password: str) -> str:
    """密码校验：非空且至少 6 位（bcrypt 上限 72 字节由哈希函数截断兜底）。"""
    if not password or len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    return password


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class FtpUserCreate(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=128)
    directory: str = Field(..., max_length=1024)
    enabled: Optional[bool] = True
    description: Optional[str] = Field(None, max_length=255)


class FtpUserUpdate(BaseModel):
    username: Optional[str] = Field(None, max_length=64)
    password: Optional[str] = Field(None, max_length=128)
    directory: Optional[str] = Field(None, max_length=1024)
    enabled: Optional[bool] = None
    description: Optional[str] = Field(None, max_length=255)


# ---------------------------------------------------------------------------
# API 端点（main.py 以 ADMIN 依赖挂载，仅管理员可访问）
# ---------------------------------------------------------------------------
@router.get("")
async def list_users():
    """返回 FTP 用户列表（不含密码哈希）。"""
    data = _load()
    return {"users": [_public(u) for u in data.get("users", [])]}


@router.post("")
async def create_user(req: FtpUserCreate):
    """创建虚拟 FTP 用户。"""
    username = _validate_username(req.username)
    directory = _validate_directory(req.directory)
    _validate_password(req.password)
    data = _load()
    _check_username_dup(data.get("users", []), username)
    user = {
        "id": "ftp_" + uuid.uuid4().hex[:10],
        "username": username,
        "password": _hash_password(req.password),
        "directory": directory,
        "enabled": bool(req.enabled if req.enabled is not None else True),
        "description": (req.description or "").strip(),
        "created_at": datetime.now().isoformat(),
    }
    data.setdefault("users", []).append(user)
    _save(data)
    logger.info("创建 FTP 用户：%s（目录 %s）", username, directory)
    return _public(user)


@router.put("/{user_id}")
async def update_user(user_id: str, req: FtpUserUpdate):
    """更新虚拟 FTP 用户（仅更新传入的字段）。"""
    data = _load()
    users = data.get("users", [])
    user = _find_user(users, user_id)
    if req.username is not None:
        username = _validate_username(req.username)
        _check_username_dup(users, username, exclude_id=user_id)
        user["username"] = username
    if req.password is not None:
        _validate_password(req.password)
        user["password"] = _hash_password(req.password)
    if req.directory is not None:
        user["directory"] = _validate_directory(req.directory)
    if req.enabled is not None:
        user["enabled"] = bool(req.enabled)
    if req.description is not None:
        user["description"] = (req.description or "").strip()
    _save(data)
    logger.info("更新 FTP 用户：username_len=%s", len(user.get("username") or ""))
    return _public(user)


@router.delete("/{user_id}")
async def delete_user(user_id: str):
    """删除虚拟 FTP 用户。"""
    data = _load()
    users = data.get("users", [])
    before = len(users)
    data["users"] = [u for u in users if u.get("id") != user_id]
    if len(data["users"]) == before:
        raise HTTPException(status_code=404, detail="FTP 用户不存在")
    _save(data)
    logger.info("删除 FTP 用户：%s", repr(user_id))
    return {"ok": True}
