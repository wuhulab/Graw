"""账号系统路由：登录、当前用户、改密、用户管理（管理员）。"""

import logging
import threading
import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import (
    create_token,
    hash_password,
    verify_password,
    get_current_user,
    require_admin,
    require_non_default_password,
    _load_users,
    _save_users,
    _get_user,
    _public_user,
    is_default_password,
    DEFAULT_PASSWORD,
)
from .shunx import verify_entry

logger = logging.getLogger("graw.auth")

router = APIRouter()

MIN_PASSWORD_LEN = 6
VALID_ROLES = ("admin", "user")

# ---------------------------------------------------------------------------
# 登录暴力破解防护（进程内限流）
#   - 同一 IP + 账号连续失败 MAX_ATTEMPTS 次后，锁定 LOCK_SECONDS 秒。
#   - 仅内存记录，重启即清零；单进程部署下有效。
# ---------------------------------------------------------------------------
MAX_ATTEMPTS = 5
LOCK_SECONDS = 10 * 60  # 10 分钟
_lock = threading.Lock()
_failures = defaultdict(lambda: {"count": 0, "locked_until": 0.0})


def _throttle_key(request: Request, username: str) -> str:
    """限流键：IP + 账号名。"""
    ip = request.client.host if request.client else "unknown"
    return f"{ip}|{username}"


def _check_throttle(request: Request, username: str) -> None:
    """若已锁定则抛 403；否则正常放行。"""
    now = time.time()
    with _lock:
        rec = _failures[_throttle_key(request, username)]
    if rec["locked_until"] > now:
        raise HTTPException(
            status_code=403,
            detail=f"登录失败次数过多，请 {int((rec['locked_until'] - now) // 60 + 1)} 分钟后再试",
        )


def _record_failure(request: Request, username: str) -> None:
    """记录一次失败；达到阈值即锁定。"""
    key = _throttle_key(request, username)
    now = time.time()
    with _lock:
        rec = _failures[key]
        if rec["locked_until"] > now:
            return  # 已锁定
        rec["count"] += 1
        if rec["count"] >= MAX_ATTEMPTS:
            rec["count"] = 0
            rec["locked_until"] = now + LOCK_SECONDS
            logger.warning("账号 %s 触发登录锁定（IP %s）", username, request.client.host if request.client else "?")


def _clear_failures(request: Request, username: str) -> None:
    """登录成功后清零该键的失败记录。"""
    with _lock:
        _failures.pop(_throttle_key(request, username), None)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: dict


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    # 暴力破解防护：先查是否已锁定
    _check_throttle(request, req.username)
    user = _get_user(req.username)
    if user is None or not verify_password(req.password, user["password"]):
        _record_failure(request, req.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 登录成功，清零失败记录
    _clear_failures(request, req.username)
    # ShunX 保护：已配置安全入口时，必须通过安全入口才能登录
    verify_entry(request)
    public = _public_user(user)
    # ShunX 保护：检测到使用默认密码 → 强制要求修改后才能使用面板
    if is_default_password(user["password"]):
        public["must_change_password"] = True
        public["default_password"] = True
        logger.warning("用户 %s 使用默认密码登录，已强制要求改密", req.username)
    token = create_token(user["username"])
    return {"token": token, "user": public}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return user


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/password")
async def change_password(
    req: ChangePasswordRequest, user: dict = Depends(get_current_user)
):
    full = _get_user(user["username"])
    if full is None or not verify_password(req.old_password, full["password"]):
        raise HTTPException(status_code=400, detail="原密码错误")
    if len(req.new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"新密码至少 {MIN_PASSWORD_LEN} 位")
    # ShunX 保护：禁止再次使用默认密码
    if req.new_password == DEFAULT_PASSWORD:
        raise HTTPException(status_code=400, detail="新密码不能使用默认密码，请更换")
    users = _load_users() or {}
    target = users.get(user["username"])
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    target["password"] = hash_password(req.new_password)
    target["must_change_password"] = False
    _save_users(users)
    return {"ok": True}


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=MIN_PASSWORD_LEN)
    role: str = Field(default="user")


@router.get("/users")
async def list_users(_: dict = Depends(require_admin)):
    users = _load_users() or {}
    return [_public_user(u) for u in users.values()]


@router.post("/users")
async def create_user(req: CreateUserRequest, _: dict = Depends(require_admin)):
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="角色无效")
    # ShunX 保护：禁止使用默认密码作为账号密码
    if req.password == DEFAULT_PASSWORD:
        raise HTTPException(status_code=400, detail="不能将默认密码作为账号密码")
    users = _load_users() or {}
    if req.username in users:
        raise HTTPException(status_code=400, detail="用户已存在")
    users[req.username] = {
        "username": req.username,
        "password": hash_password(req.password),
        "role": req.role,
        "must_change_password": False,
        "created_at": time.time(),
    }
    _save_users(users)
    return {"ok": True}


class UpdateUserRequest(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    must_change_password: Optional[bool] = None


@router.put("/users/{username}")
async def update_user(
    username: str, req: UpdateUserRequest, _: dict = Depends(require_admin)
):
    users = _load_users() or {}
    target = users.get(username)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.password is not None:
        if len(req.password) < MIN_PASSWORD_LEN:
            raise HTTPException(
                status_code=400, detail=f"密码至少 {MIN_PASSWORD_LEN} 位"
            )
        # ShunX 保护：禁止把密码重置为默认密码
        if req.password == DEFAULT_PASSWORD:
            raise HTTPException(status_code=400, detail="不能将默认密码作为账号密码")
        target["password"] = hash_password(req.password)
        # 重置密码后默认要求下次登录修改
        target["must_change_password"] = (
            req.must_change_password if req.must_change_password is not None else True
        )
    if req.role is not None:
        if req.role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="角色无效")
        # 阻止降级最后一个管理员
        if target.get("role") == "admin" and req.role != "admin":
            admins = [u for u in users.values() if u.get("role") == "admin"]
            if len(admins) <= 1:
                raise HTTPException(status_code=400, detail="至少保留一个管理员账号")
        target["role"] = req.role
    if req.must_change_password is not None:
        target["must_change_password"] = req.must_change_password
    _save_users(users)
    return {"ok": True}


@router.delete("/users/{username}")
async def delete_user(username: str, user: dict = Depends(require_admin)):
    if username == user["username"]:
        raise HTTPException(status_code=400, detail="不能删除当前登录账号")
    users = _load_users() or {}
    target = users.get(username)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    admins = [u for u in users.values() if u.get("role") == "admin"]
    if target.get("role") == "admin" and len(admins) <= 1:
        raise HTTPException(status_code=400, detail="至少保留一个管理员账号")
    del users[username]
    _save_users(users)
    return {"ok": True}
