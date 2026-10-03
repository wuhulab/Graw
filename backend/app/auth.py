"""账号与鉴权工具：密码哈希、JWT 签发/校验、FastAPI 依赖。

用户数据以 JSON 文件形式持久化在 backend/data/users.json，签名密钥
持久化在 backend/data/secret.key（首次启动自动生成）。JWT 采用 HS256，
默认有效期 7 天。WebSocket 鉴权通过查询参数 ?token=... 传递。
"""

import os
import copy
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
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")

os.makedirs(DATA_DIR, exist_ok=True)

ALGORITHM = "HS256"
TOKEN_TTL = 86400 * 7  # 7 天

# 默认密码：种子管理员账号使用。ShunX 保护机制会检测并强制用户修改，
# 且禁止任何地方（创建/重置/改密）再次使用该默认密码。
DEFAULT_PASSWORD = "admin123"

_security = HTTPBearer(auto_error=False)
_file_lock = threading.Lock()


# ---------------------------------------------------------------------------
# JSON 热文件缓存（users.json / sessions.json）
#
# 鉴权依赖链（get_current_user → require_non_default_password → require_perm）
# 每个请求要读 3-4 次用户表/会话表。实测单次「open + json.load」约 130-150µs，
# 在小内存 VPS（容器 overlayfs / 网络盘）上更慢，是请求路径上除密码校验外
# 最大的一笔固定开销。这里缓存解析结果，按「mtime_ns + size」失效：
#   - 本进程保存（_save_users/_save_sessions）时主动失效；
#   - 外部修改（reset_password.py、手工编辑、另一实例）靠 stat 变化感知。
# 返回时统一 deepcopy：调用方（用户管理接口）会就地修改后另存，
# 共享同一对象会让未保存的修改污染缓存。
# ---------------------------------------------------------------------------
_CACHE_INVALID = object()
_users_cache: dict = {"key": _CACHE_INVALID, "data": None}
_users_cache_lock = threading.Lock()
_sessions_cache: dict = {"key": _CACHE_INVALID, "data": {}}
_sessions_cache_lock = threading.Lock()


def _stat_key(path: str):
    """文件指纹 (mtime_ns, size)；文件不存在返回 None。"""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _read_json_cached(path: str, cache: dict, lock: threading.Lock, missing_default):
    """读取 JSON 热文件（带 stat 指纹缓存），返回深拷贝。

    missing_default 是「文件不存在」时的返回值（用户表用 None 以区分
    「未播种」，会话表用 {}）；文件损坏 / 非 dict 时统一返回 {}。
    """
    key = _stat_key(path)
    if key is None:
        # 文件不存在：不缓存，直接返回缺失语义（播种等流程可能马上创建它）
        return missing_default
    with lock:
        if cache["key"] == key:
            return copy.deepcopy(cache["data"])
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    with lock:
        cache["key"] = key
        cache["data"] = data
    return copy.deepcopy(data)


def _invalidate(cache: dict, lock: threading.Lock) -> None:
    with lock:
        cache["key"] = _CACHE_INVALID


def _load_users() -> Optional[dict]:
    """读取用户表。文件不存在返回 None（用于首次播种判定），
    文件存在但损坏返回空 dict（避免误播种覆盖已有数据）。"""
    return _read_json_cached(USERS_FILE, _users_cache, _users_cache_lock, None)


def _save_users(data: dict) -> None:
    """原子写入用户表，避免并发写入互相覆盖。"""
    with _file_lock:
        tmp = USERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, USERS_FILE)
    _invalidate(_users_cache, _users_cache_lock)


def _get_secret() -> str:
    if os.path.exists(SECRET_FILE):
        try:
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                s = f.read().strip()
                if s:
                    return s
        except Exception:  # lgtm[py/empty-except] 既有签名密钥不可读/损坏时视为不存在，走重建
            pass
    secret = secrets.token_urlsafe(48)
    # 签名密钥必须明文持久化才能跨重启校验 JWT（加密密钥无法加密存储——
    # 自举问题），已配套 0600 权限 + data 目录 0700 收紧
    with open(SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(secret)  # lgtm[py/clear-text-storage-sensitive-data]
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


# 默认密码判定缓存：password_hash -> bool。
# 该判定是「以存储的密码哈希为唯一输入」的纯函数（bcrypt 哈希含随机盐，
# 同一哈希结果恒定），因此可按哈希缓存；容量有上限，改密产生新哈希时写入
# 新条目、旧条目随容量淘汰，不会让已改密账号被误判。
_default_pw_cache: dict = {}
_default_pw_cache_lock = threading.Lock()
_DEFAULT_PW_CACHE_MAX = 64


def is_default_password(password_hash: str) -> bool:
    """判断某个密码哈希是否为默认密码（用于强制改密检测）。

    性能：bcrypt.checkpw 单次约 0.3s CPU，而本函数会挂在每个业务/管理请求
    的鉴权依赖（require_non_default_password）以及 Agent 代理前置鉴权上；
    不做缓存时每个请求都要付一次 bcrypt，面板吞吐被压到个位数 RPS。
    这里按密码哈希缓存判定结果，避免每请求重复计算。
    """
    if not isinstance(password_hash, str) or not password_hash:
        return False
    hit = _default_pw_cache.get(password_hash)
    if hit is not None:
        return hit
    try:
        result = bool(
            bcrypt.checkpw(DEFAULT_PASSWORD.encode("utf-8"), password_hash.encode("utf-8"))
        )
    except Exception:
        result = False
    with _default_pw_cache_lock:
        if len(_default_pw_cache) >= _DEFAULT_PW_CACHE_MAX:
            _default_pw_cache.clear()
        _default_pw_cache[password_hash] = result
    return result


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
    """脱敏后的用户对象（不含密码哈希）。token_version 供客户端展示/调试用。

    perms 为模块白名单（受限管理员），null / 缺失表示全量权限——前端据此做入口
    门控；这不是敏感信息（就是本人自己的权限），可以随用户对象下发。
    """
    return {
        "username": user["username"],
        "role": user.get("role", "user"),
        "must_change_password": user.get("must_change_password", False),
        "created_at": user.get("created_at", 0),
        "token_version": user.get("token_version", 0),
        "otp_enabled": bool(user.get("otp_enabled")),
        # 模块白名单（受限管理员）；null / 缺失 = 全量权限，前端 hasPerm() 依此放行
        "perms": user.get("perms"),
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
    # 安全（第十三轮审计）：token_version 缺失或为 null 时按 0 处理，
    # 避免 int(None) 抛 TypeError 打挂改密/注销接口（配置被手工损坏时）。
    target["token_version"] = int(target.get("token_version") or 0) + 1
    _save_users(users)
    # 同步吊销该用户全部会话记录（列表页不再显示）
    _revoke_user_sessions(username)
    logger.info("已吊销用户 %s 的所有登录令牌（token_version -> %d）", repr(username), target["token_version"])


# ---------------------------------------------------------------------------
# 会话管理（在线会话列表 / 踢出单设备）
#   基于 JWT 的 sid 字段：登录时生成唯一会话 ID 并持久化到 data/sessions.json，
#   踢出单设备 = 删除该 sid 记录（get_current_user 校验 sid 仍在列表中）。
#   旧版本签发的无 sid 令牌视为传统令牌，向后兼容不额外校验。
# ---------------------------------------------------------------------------
def _load_sessions() -> dict:
    """读取会话表；缺失/损坏返回空 dict。"""
    return _read_json_cached(SESSIONS_FILE, _sessions_cache, _sessions_cache_lock, {})


# 会话「在线」依据：设备 / 前端打开面板时会持续发起鉴权请求，每次有效鉴权
# 都会刷新会话的 last_seen；超过该阈值仍无任何活跃（如关闭了浏览器/终端）则视为离线。
# 默认 2 小时，可通过 GRAW_SESSION_ONLINE_SECONDS 覆盖。
_ONLINE_ACTIVE = int(os.environ.get("GRAW_SESSION_ONLINE_SECONDS", str(2 * 3600)))
# last_seen 落盘的节流缓存：sid -> 上次实际写盘的时间，避免每个请求都写会话文件
_touch_cache = {}
# 无 sid 的传统令牌：username -> 上次为其补建/刷新会话的时间（同样节流）
_ensure_cache = {}


def _touch_session_now(sid: str, username: str = "") -> None:
    """刷新/重建某会话的 last_seen（节流：同一 sid 至少间隔 60s 写一次盘）。

    若该 sid 记录缺失（例如曾被空闲裁剪，但此刻确有有效鉴权），则重建记录，
    避免「正在使用的账号」因一条中间态而被在线列表遗漏。
    """
    if not sid:
        return
    now = time.time()
    if now - _touch_cache.get(sid, 0) < 60:
        return
    try:
        sessions = _load_sessions()
        if sid in sessions:
            sessions[sid]["last_seen"] = now
            if username:
                sessions[sid]["username"] = username
        else:
            sessions[sid] = {
                "username": username or "",
                "ip": "",
                "device": "",
                "created_at": now,
                "last_seen": now,
            }
        _save_sessions(sessions)
        _touch_cache[sid] = now
    except Exception:
        pass


def _ensure_online_session(username: str, sid: str) -> None:
    """确保正在使用面板的账号在在线列表中有对应的会话记录。

    - 有 sid：刷新（缺失则重建），让活跃会话稳定在线。
    - 无 sid（旧版签发的传统令牌）：为该账号补一条稳定会话记录，使在线列表
      能看到当前正在使用的 admin 等账号（同时后续请求复用同一条并刷新）。
    """
    if sid:
        _touch_session_now(sid, username)
        return
    now = time.time()
    # 节流：同一账号至少间隔 60s 写一次盘，避免传统令牌每次鉴权都落盘
    if now - _ensure_cache.get(username, 0) < 60:
        return
    try:
        sessions = _load_sessions()
        # 该用户已有活跃会话 → 只需刷新，不重复创建
        for v in sessions.values():
            if v.get("username") == username and now - (v.get("last_seen") or v.get("created_at") or 0) <= _ONLINE_ACTIVE:
                v["last_seen"] = now
                _save_sessions(sessions)
                _ensure_cache[username] = now
                return
        # 无活跃会话 → 补建一条
        nid = secrets.token_urlsafe(16)
        newly = _pruned_sessions(sessions, now)
        newly[nid] = {
            "username": username or "",
            "ip": "",
            "device": "",
            "created_at": now,
            "last_seen": now,
        }
        _save_sessions(newly)
        _ensure_cache[username] = now
    except Exception:
        # 会话创建持久化失败时忽略，不影响登录
        pass


def _save_sessions(data: dict) -> None:
    """原子写入会话表。"""
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SESSIONS_FILE)
    _invalidate(_sessions_cache, _sessions_cache_lock)


def create_session(username: str, ip: str, device: str = "") -> str:
    """创建一条会话记录，返回会话 ID（sid）。异常静默降级，绝不阻断登录。"""
    try:
        sid = secrets.token_urlsafe(16)
        now = time.time()
        sessions = _load_sessions()
        # 惰性清理过期/离线会话后再写入，避免表无限膨胀
        sessions = _pruned_sessions(sessions, now)
        sessions[sid] = {
            "username": username,
            "ip": ip or "",
            "device": (device or "")[:120],
            "created_at": now,
            "last_seen": now,
        }
        _save_sessions(sessions)
        return sid
    except Exception:
        # 会话记录失败不阻断登录（token 仍可用，只是无法被单独踢出）
        return ""


def revoke_session(sid: str) -> bool:
    """吊销单个会话（踢出该设备）。返回是否真实删除。"""
    if not sid:
        return False
    sessions = _load_sessions()
    if sid not in sessions:
        return False
    del sessions[sid]
    _save_sessions(sessions)
    return True


def _revoke_user_sessions(username: str) -> None:
    """吊销某用户全部会话记录（改密/注销/管理员强制下线时调用）。"""
    try:
        sessions = _load_sessions()
        changed = False
        for sid, s in list(sessions.items()):
            if s.get("username") == username:
                del sessions[sid]
                changed = True
        if changed:
            _save_sessions(sessions)
    # 会话状态刷新失败不影响访问，忽略
    except Exception:
        pass


def _pruned_sessions(sessions: dict, now: float) -> dict:
    """裁剪已失效会话：token 超有效期（created_at 超 TTL）或长时间无活跃（last_seen 超阈值）。"""
    if not sessions:
        return sessions
    keep = {}
    for sid, s in sessions.items():
        last = s.get("last_seen") or s.get("created_at") or 0
        if now - (s.get("created_at") or 0) > TOKEN_TTL:
            continue
        if now - last > _ONLINE_ACTIVE:
            continue
        keep[sid] = s
    return keep


def list_sessions(username: Optional[str] = None, limit: int = 100) -> list:
    """列出在线会话（惰性清理过期与离线记录）。username=None 返回全部。

    「在线」以真实的最近活跃（last_seen）为准，而非仅看登录时间：设备关闭
    / 长时间无请求的会话会被剔除，避免已不在线却仍显示为在线。
    """
    sessions = _load_sessions()
    now = time.time()
    # 惰性清理：token 超期（created_at+TTL）或超过空闲阈值（last_seen）视为离线
    expired = _pruned_sessions(sessions, now)
    if len(expired) != len(sessions):
        try:
            _save_sessions(expired)
        except Exception:
            # 会话裁剪持久化失败时忽略
            pass
    items = []
    for sid, s in expired.items():
        if username and s.get("username") != username:
            continue
        items.append({
            "sid": sid,
            "username": s.get("username", ""),
            "ip": s.get("ip", ""),
            "device": s.get("device", ""),
            "created_at": s.get("created_at", 0),
            # 最近活跃时间（前端可据此判断会话新鲜度）
            "last_seen": s.get("last_seen", 0),
        })
    # 新会话在前
    items.sort(key=lambda x: x.get("last_seen", 0) or x.get("created_at", 0), reverse=True)
    return items[: max(1, min(limit, 500))]


def session_active(sid: str) -> bool:
    """判断 sid 是否仍是有效会话（未吊销）。"""
    if not sid:
        # 空 sid = 旧版本签发的传统令牌，向后兼容视为有效
        return True
    return sid in _load_sessions()


def create_token(username: str, token_version: int = 0, sid: str = "") -> str:
    """签发 JWT：携带用户名、token 版本号（tv）与会话 ID（sid）。

    改密/注销后 token_version 递增，旧令牌的 tv 与新值不一致而被拒绝；
    sid 用于「踢出单个设备」：吊销 sid 后该令牌也会被拒绝（向后兼容：
    旧令牌无 sid，视为传统令牌，仅按 tv 校验）。
    """
    now = int(time.time())
    payload = {
        "sub": username,
        "tv": int(token_version or 0),
        "iat": now,
        "exp": now + TOKEN_TTL,
    }
    if sid:
        payload["sid"] = sid
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
    安全（第十三轮审计）：对缺失/为 null 的 token_version 均按 0 处理，
    避免 int(None) 抛 TypeError 使鉴权链 500（攻击者可构造 tv=null 的
    令牌在密钥泄露场景下触发拒绝服务；配置损坏时同样容错）。
    """
    username = token_payload.get("sub", "")
    token_tv = int(token_payload.get("tv") or 0)
    user = _get_user(username)
    if user is None:
        return False
    current_tv = int(user.get("token_version") or 0)
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
    # 单设备踢出校验：sid 被吊销的令牌失效（旧令牌无 sid 跳过）
    if not session_active(payload.get("sid", "")):
        raise HTTPException(status_code=401, detail="该设备已被强制下线")
    # 会话在线判定依赖 last_seen：有效鉴权即刷新，并确保正在使用的账号在列表可见
    _ensure_online_session(user["username"], payload.get("sid", ""))
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
    # 单设备踢出校验：被踢出的设备不得建立连接
    if not session_active(payload.get("sid", "")):
        await websocket.close(code=4401)
        return None
    # 有效连接即刷新会话活跃时间，并确保该账号在在线列表可见
    _ensure_online_session(user["username"], payload.get("sid", ""))
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


# ---------------------------------------------------------------------------
# 模块级权限（受限管理员 / 细粒度授权）
#
# 背景：此前 role 只有 admin / user 两档，管理员权限「全有或全无」——想让运维
# 只管理「网站 + 日志」，就只能授予完整管理员（连带文件管理 / 终端 = 等同 RCE）。
#
# 模型（数据落在 data/users.json 的 perms 字段）：
#   - perms 为模块 key 数组 → 该管理员的模块白名单，仅放行列出的模块；
#   - perms 缺失或为 null   → 全量权限（向后兼容：升级前创建的管理员行为不变）；
#   - perms 为 []           → 不放行任何模块（可用于临时冻结账号）；
#   - perms 存在但类型非法  → 按空集合处理（配置被改坏时取最严，避免意外放大权限）；
#   - role != 'admin' 的用户一律无模块权限（模块授权只在管理员身份之上做「收窄」）。
#
# 粒度说明：模块 key 与路由前缀 / 前端快捷方式对齐（见 MODULES），不做接口级
# 授权——200+ 路由的接口级授权无法维护。面板自身安全边界（多节点 / SSH 密钥 /
# 插件安装 / 面板更新 / 面板备份 / 用户管理 / 子节点收取模式 / 安全入口）不参与
# 模块授权，始终要求完整管理员（见 main.py 的 _FULL_ADMIN_PREFIXES）。
#
# 安全约定：前端门控（hasPerm 隐藏入口）只是体验层，真正的边界在 require_perm
# 依赖——所有管理类路由都必须挂它，漏挂等于把该模块向受限管理员全量开放。
# ---------------------------------------------------------------------------
MODULES = (
    "sites",        # 网站 / 引擎模式 / 伪静态 / 站点增强 / WAF / 访问统计 / SSL / PHP 版本
    "database",     # 数据库连接与查询 / 慢查询分析
    "docker",       # Docker 容器 / 卷 / 容器编辑 / 镜像扫描 / 运行时容器
    "files",        # 文件管理 / 回收站
    "process",      # 进程管理
    "disks",        # 磁盘管理
    "cron",         # 计划任务
    "firewall",     # 防火墙 / 防护中心
    "logs",         # 系统日志
    "terminal",     # Web 终端
    "backup",       # 备份中心 / 配置回滚
    "notify",       # 通知中心 / 站点可用性 / 证书到期
    "appstore",     # 应用商店 / 任务中心
    "netstorage",   # 网络储存（FTP/SMB/WebDAV/S3）
    "frp",          # 内网穿透
    "batch",        # 批量操作
    "gitdeploy",    # 站点 Git 自动部署
    "report",       # 巡检报告
    "tamper",       # 网页防篡改
    "toolbox",      # 工具箱
    "ftpusers",     # FTP 用户
    "svcmonitor",   # 服务 / 端口监控
    "healthcheck",  # 一键系统体检
    "portforward",  # SSH 端口转发
    "loginlog",     # 登录日志管理（查看全部 / 清空 / 告警配置）
)


def user_perms(user: dict) -> Optional[set]:
    """返回用户的模块白名单集合；None 表示「全量权限」。

    - 非管理员一律返回空集合（无任何模块权限）；
    - 字段缺失 / 显式 null → None（全量，兼容升级前的存量管理员）；
    - 类型非法 → 空集合（取最严，配置损坏时不放大权限）；
    - 未知模块 key 会被忽略（避免脏数据被当作有效授权）。
    """
    if not isinstance(user, dict) or user.get("role") != "admin":
        return set()  # 非管理员：空集合 = 全拒绝
    if "perms" not in user:
        return None  # 字段缺失（升级前的存量管理员）→ 全量
    perms = user.get("perms")
    if perms is None:
        return None  # 显式 null → 全量
    if not isinstance(perms, (list, tuple, set)):
        # 类型非法（手工改坏 / 被篡改）：取最严——按空集合处理，需管理员修复配置
        logger.warning(
            "用户 %s 的 perms 字段类型非法（%s），按「无模块权限」处理",
            repr(user.get("username", "")),
            type(perms).__name__,
        )
        return set()
    return {str(p) for p in perms if isinstance(p, str) and p in MODULES}


def has_perm(user: dict, *modules: str) -> bool:
    """判断用户是否拥有指定模块中【任意一个】的权限。

    支持多模块共用一个路由组的场景（如 firewall 路由组接受 firewall/protection）。
    全量权限用户（user_perms 返回 None）恒为 True。
    """
    perms = user_perms(user)
    if perms is None:
        return True  # 全量权限
    return any(m in perms for m in modules)


def require_perm(*modules: str):
    """HTTP 依赖工厂：要求当前用户拥有指定模块中任意一个的权限。

    级联 require_non_default_password（内含 get_current_user），因此使用本依赖
    的路由无需再挂 PROTECTED / ADMIN；非管理员与受限管理员都由此统一 403。
    """
    async def _require_perm(user: dict = Depends(require_non_default_password)) -> dict:
        if not has_perm(user, *modules):
            raise HTTPException(
                status_code=403,
                detail="需要 " + "/".join(modules) + " 模块权限",
            )
        return user

    # 具名便于调试 / 启动期自检识别（main._audit_route_perms）
    _require_perm.__name__ = "require_perm_" + "_".join(modules or ("any",))
    # 携带模块元组：供 main.py 的启动期自检与 Agent 代理前置鉴权读取
    # （代理前需在本机做与业务路由等价的模块判定，见 main._proxy_auth_guard）。
    _require_perm.__perm_modules__ = tuple(modules)
    return _require_perm


def require_perm_ws(*modules: str):
    """WebSocket 依赖工厂：?token= 鉴权 + 默认密码拦截 + 模块权限判定。

    与 get_current_user_ws_admin 的差别：把「必须是管理员」换成「必须拥有指定
    模块权限」。鉴权失败 / 无权限时统一 close(4403)，与既有 WS 依赖语义一致。
    """
    async def _require_perm_ws(
        websocket: WebSocket, token: str = Query(default="")
    ) -> Optional[dict]:
        user = await get_current_user_ws(websocket, token)
        if user is None:
            return None
        # 默认密码账号必须先改密（与 get_current_user_ws_admin 的拦截一致）
        full = _get_user(user["username"])
        if full is not None and is_default_password(full.get("password", "")):
            await websocket.close(code=4403)
            return None
        if not has_perm(user, *modules):
            await websocket.close(code=4403)
            return None
        return user

    _require_perm_ws.__name__ = "require_perm_ws_" + "_".join(modules or ("any",))
    return _require_perm_ws


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


async def get_current_user_ws_checked(
    websocket: WebSocket, token: str = Query(default="")
) -> Optional[dict]:
    """WebSocket 鉴权依赖：登录 + 非默认密码（与 _PROTECTED HTTP 语义对齐）。

    安全修复（第十三轮审计，Medium）：/api/system/ws 此前仅用
    get_current_user_ws——默认密码账号（或尚未完成强制改密的账号）虽被
    HTTP 只读接口（require_non_default_password）全面拦截，却仍可通过
    WebSocket 订阅本机/子节点的实时监控流（CPU/内存/磁盘/网络），构成
    鉴权不一致的信息泄露。此依赖在 get_current_user_ws 基础上追加默认
    密码拦截，供订阅敏感数据流的 WS 端点（如 system/ws）使用。
    """
    user = await get_current_user_ws(websocket, token)
    if user is None:
        return None
    full = _get_user(user["username"])
    if full is not None and is_default_password(full.get("password", "")):
        await websocket.close(code=4403)
        return None
    return user


def ws_session_still_valid(websocket: WebSocket) -> bool:
    """复检已建立的 WebSocket 会话是否仍有效（第十四轮审计修复，Medium）。

    此前 WS 仅在握手时校验一次 token；改密（bump_token_version）/踢出设备
    （revoke_session）后，已建立的终端/监控长连接不会被中断——被盗令牌
    场景下管理员改密止损对终端无效。各长连接消息循环应周期性调用本函数，
    失效即主动断开。握手参数中的 token 与校验逻辑与 get_current_user_ws 一致。
    """
    try:
        token = (websocket.query_params or {}).get("token", "")
        payload = decode_token(token)
        if payload is None:
            return False
        if not verify_token_version(payload):
            return False
        if not session_active(payload.get("sid", "")):
            return False
        return True
    except Exception:
        return False


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
