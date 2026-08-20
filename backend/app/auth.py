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
import logging
import base64
import hashlib
import hmac
import struct
from collections import defaultdict
from typing import Optional

logger = logging.getLogger("graw.auth")

import jwt
import bcrypt
from fastapi import Depends, HTTPException, Query, Request, WebSocket
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
SECRET_FILE = os.path.join(DATA_DIR, "secret.key")

os.makedirs(DATA_DIR, exist_ok=True)

ALGORITHM = "HS256"
TOKEN_TTL = 86400 * 7  # 7 天

# 默认密码：种子管理员账号使用。ShunX 保护机制会检测并强制用户修改，
# 且禁止任何地方（创建/重置/改密）再次使用该默认密码。
DEFAULT_PASSWORD = "admin123"

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
    # 限制签名密钥文件权限（仅本进程/用户可读），Linux 上避免同机低权用户读取
    try:
        os.chmod(SECRET_FILE, 0o600)
    except Exception:
        pass  # Windows 上 chmod 无实际效果，忽略
    return secret


SECRET_KEY = _get_secret()


def hash_password(password: str) -> str:
    # bcrypt 仅使用输入的前 72 字节，超长输入在 bcrypt>=4.0 会抛 ValueError
    # 导致接口 500。正常路径上游（_validate_password_strength）已拒绝超长
    # 密码，此处显式截断仅为兜底防御，避免异常密码直接打挂接口。
    pw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        # 与 hash_password 保持一致的 72 字节截断，避免校验时序/结果不一致
        pw = password.encode("utf-8")[:72]
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except Exception:
        return False


def is_default_password(password_hash: str) -> bool:
    """判断某个密码哈希是否为默认密码（用于强制改密检测）。"""
    try:
        return bcrypt.checkpw(DEFAULT_PASSWORD.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 两步验证（TOTP，RFC 6238，无第三方依赖）
#   用户可开启两步验证：登录时除密码外还需输入 6 位动态验证码（Google
#   Authenticator / 1Password 等标准 TOTP App 均可）。
# ---------------------------------------------------------------------------
def generate_otp_secret() -> str:
    """生成 Base32 编码的 TOTP 密钥（20 字节随机数，160 位熵）。"""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_at(secret: str, counter: int) -> str:
    """计算指定计数器位置的 6 位 TOTP 码（HMAC-SHA1 动态截断）。"""
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    """校验 TOTP 验证码；允许 ±window 步（共 2*window+1 个窗口）以容忍时钟偏差。

    使用 hmac.compare_digest 做常量时间比较，避免时序侧信道。
    """
    if not secret or not code:
        return False
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return False
    counter = int(time.time()) // 30
    for i in range(-window, window + 1):
        if hmac.compare_digest(_totp_at(secret, counter + i), code):
            return True
    return False


def otpauth_uri(secret: str, username: str, issuer: str = "Graw") -> str:
    """构造 otpauth URI（供二维码/手动添加 Authenticator 使用）。"""
    from urllib.parse import quote

    return (
        f"otpauth://totp/{quote(issuer)}:{quote(username)}"
        f"?secret={secret}&issuer={quote(issuer)}"
    )


def seed_default_users() -> None:
    """首次启动时创建默认管理员账号 admin / <DEFAULT_PASSWORD>（需首次登录改密）。
    若 admin 已存在但角色不是 admin，自动修复以至少保留一个管理员。"""
    users = _load_users()
    if users is None:
        default = {
            "admin": {
                "username": "admin",
                "password": hash_password(DEFAULT_PASSWORD),
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


def _public_user(user: dict) -> dict:
    """脱敏后的用户对象（不含密码哈希）。token_version 供客户端展示/调试用。"""
    return {
        "username": user["username"],
        "role": user.get("role", "user"),
        "must_change_password": user.get("must_change_password", False),
        "created_at": user.get("created_at", 0),
        "token_version": user.get("token_version", 0),
        "otp_enabled": bool(user.get("otp_enabled")),
    }


def bump_token_version(username: str) -> None:
    """递增用户 token 版本号：使该用户此前签发的所有 JWT 立即失效。

    改密 / 重置密码 / 管理员重置 / 主动退出登录时调用，实现真正的会话撤销
    （无需维护黑名单，签名校验时比对版本号即可）。
    """
    users = _load_users() or {}
    target = users.get(username)
    if target is None:
        return
    target["token_version"] = int(target.get("token_version", 0)) + 1
    _save_users(users)
    logger.info("已吊销用户 %s 的所有登录令牌（token_version -> %d）", username, target["token_version"])


def create_token(username: str, token_version: int = 0) -> str:
    """签发 JWT：携带用户名与 token 版本号（tv）。

    改密/注销后 token_version 递增，旧令牌的 tv 与新值不一致而被拒绝。
    """
    now = int(time.time())
    payload = {
        "sub": username,
        "tv": int(token_version or 0),
        "iat": now,
        "exp": now + TOKEN_TTL,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except Exception:
        return None


def verify_token_version(token_payload: dict) -> bool:
    """校验 JWT 中的 token_version（tv）是否与用户当前版本一致。

    不一致说明令牌已被撤销，返回 False。
    """
    username = token_payload.get("sub", "")
    token_tv = int(token_payload.get("tv", -1))
    user = _get_user(username)
    if user is None:
        return False
    current_tv = int(user.get("token_version", 0))
    return token_tv == current_tv


def _get_user(username: str) -> Optional[dict]:
    users = _load_users()
    if not users:
        return None
    return users.get(username)


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
    # 会话撤销校验：改密/重置/注销后 token_version 递增，旧令牌失效
    if not verify_token_version(payload):
        raise HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
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
    # 会话撤销校验：改密/注销后旧令牌不得继续建立连接
    if not verify_token_version(payload):
        await websocket.close(code=4401)
        return None
    return _public_user(user)


async def require_non_default_password(
    user: dict = Depends(get_current_user),
) -> dict:
    """业务/管理路由依赖：使用默认密码的账号必须先修改密码才能使用面板。

    该依赖基于「当前存储的密码哈希」实时检测，即使 must_change_password
    标志被人为清除（如 reset_password.py 重置回默认密码），也能强制改密。
    """
    full = _get_user(user["username"])
    if full is not None and is_default_password(full.get("password", "")):
        raise HTTPException(status_code=403, detail="必须修改默认密码后才能使用面板")
    return user


async def require_admin(user: dict = Depends(require_non_default_password)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


async def get_current_user_ws_admin(
    websocket: WebSocket, token: str = Query(default="")
) -> Optional[dict]:
    """WebSocket 管理员鉴权依赖：仅允许「已改密的管理员」访问。

    在 get_current_user_ws 基础上追加：角色必须为 admin，且未使用默认密码
    （默认密码账号必须先改密，避免经终端绕过默认密码拦截）。
    """
    user = await get_current_user_ws(websocket, token)
    if user is None:
        return None
    if user.get("role") != "admin":
        await websocket.close(code=4403)
        return None
    full = _get_user(user["username"])
    if full is not None and is_default_password(full.get("password", "")):
        await websocket.close(code=4403)
        return None
    return user


# 可信代理链长度：直接部署（无反代）为 0；反代部署时应设置为代理层数，
# 并确保上游代理剥离/覆盖 X-Forwarded-For（由部署方保证，不可由客户端注入）。
TRUSTED_PROXY_DEPTH = int(os.environ.get("TRUSTED_PROXY_DEPTH", "0"))


def get_client_ip(request: Request) -> str:
    """获取客户端真实 IP（兼容反向代理）。

    直接部署时取 socket 对端地址；反代部署时从 X-Forwarded-For 取
    从右往左数第 TRUSTED_PROXY_DEPTH 个地址（XFF 由可信代理追加，
    右侧为最近一跳，向左回退即真实客户端地址）。

    - 未配置可信代理时完全忽略 XFF，防止客户端伪造 IP 绕过限流/审计。
    - 配置后由部署方保证代理正确覆写 XFF，避免伪造。
    """
    client = request.client.host if request.client else ""
    if TRUSTED_PROXY_DEPTH > 0:
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            hops = [h.strip() for h in xff.split(",") if h.strip()]
            if len(hops) >= TRUSTED_PROXY_DEPTH:
                # 取从右往左第 TRUSTED_PROXY_DEPTH 个（最接近真实客户端）
                candidate = hops[-TRUSTED_PROXY_DEPTH]
                if candidate:
                    return candidate
    return client or "unknown"
