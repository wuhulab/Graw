"""账号与鉴权工具：密码哈希、JWT 签发/校验、FastAPI 依赖。

用户数据以 JSON 文件形式持久化在 backend/data/users.json，签名密钥
持久化在 backend/data/secret.key（首次启动自动生成）。JWT 采用 HS256，
默认有效期 7 天。WebSocket 鉴权通过查询参数 ?token=... 传递。
"""

import os
import json
import time
import secrets
import threading
from typing import Optional

import jwt
import bcrypt
from fastapi import Depends, HTTPException, Query, WebSocket
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")

os.makedirs(DATA_DIR, exist_ok=True)

ALGORITHM = "HS256"
TOKEN_TTL = 86400 * 7  # 7 天

_security = HTTPBearer(auto_error=False)
_file_lock = threading.Lock()


def _load_users() -> Optional[dict]:
    """读取用户表。文件不存在返回 None（用于首次播种判定），
    文件存在但损坏返回空 dict（避免误播种覆盖已有数据）。"""
    if not os.path.exists(USERS_FILE):
        return None
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {}
    except Exception:
        return {}


def _save_users(data: dict) -> None:
    """原子写入用户表，避免并发写入互相覆盖。"""
    with _file_lock:
        tmp = USERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, USERS_FILE)


def _get_secret() -> str:
    if os.path.exists(SECRET_FILE):
        try:
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                s = f.read().strip()
                if s:
                    return s
        except Exception:
            pass
    secret = secrets.token_urlsafe(48)
    with open(SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(secret)
    return secret


SECRET_KEY = _get_secret()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def seed_default_users() -> None:
    """首次启动时创建默认管理员账号 admin / admin123（需首次登录改密）。
    若 admin 已存在但角色不是 admin，自动修复以至少保留一个管理员。"""
    users = _load_users()
    if users is None:
        default = {
            "admin": {
                "username": "admin",
                "password": hash_password("admin123"),
                "role": "admin",
                "must_change_password": True,
                "created_at": time.time(),
            }
        }
        _save_users(default)
        return
    admin = users.get("admin")
    if admin and admin.get("role") != "admin":
        admin["role"] = "admin"
        _save_users(users)


def create_token(username: str) -> str:
    now = int(time.time())
    payload = {"sub": username, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return None


def _get_user(username: str) -> Optional[dict]:
    users = _load_users()
    if not users:
        return None
    return users.get(username)


def _public_user(user: dict) -> dict:
    return {
        "username": user["username"],
        "role": user.get("role", "user"),
        "must_change_password": user.get("must_change_password", False),
        "created_at": user.get("created_at", 0),
    }


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> dict:
    """HTTP 接口鉴权依赖：校验 Bearer 令牌并返回当前用户（脱敏）。"""
    if (
        credentials is None
        or (credentials.scheme or "").lower() != "bearer"
        or not credentials.credentials
    ):
        raise HTTPException(status_code=401, detail="未认证")
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    user = _get_user(payload.get("sub", ""))
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return _public_user(user)


async def get_current_user_ws(
    websocket: WebSocket, token: str = Query(default="")
) -> Optional[dict]:
    """WebSocket 鉴权依赖：通过 ?token= 传递令牌。失败时关闭连接。"""
    if not token:
        await websocket.close(code=4401)
        return None
    payload = decode_token(token)
    if payload is None:
        await websocket.close(code=4401)
        return None
    user = _get_user(payload.get("sub", ""))
    if user is None:
        await websocket.close(code=4401)
        return None
    return _public_user(user)


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
