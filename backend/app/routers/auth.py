"""账号系统路由：登录、当前用户、改密、用户管理（管理员）。"""

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import (
    create_token,
    hash_password,
    verify_password,
    get_current_user,
    require_admin,
    _load_users,
    _save_users,
    _get_user,
    _public_user,
)

router = APIRouter()

MIN_PASSWORD_LEN = 6
VALID_ROLES = ("admin", "user")


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: dict


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    user = _get_user(req.username)
    if user is None or not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(user["username"])
    return {"token": token, "user": _public_user(user)}


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
