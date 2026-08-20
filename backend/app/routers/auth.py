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
    verify_totp,
    generate_otp_secret,
    otpauth_uri,
    get_current_user,
    require_admin,
    require_non_default_password,
    _load_users,
    _save_users,
    _get_user,
    _public_user,
    is_default_password,
    bump_token_version,
    get_client_ip,
    DEFAULT_PASSWORD,
)
from .shunx import verify_entry
from .. import auditlog

logger = logging.getLogger("graw.auth")

router = APIRouter()

# ---------------------------------------------------------------------------
# 密码策略
#   - 最小长度 8 位（登录入口安全底线，防止弱口令被字典爆破）
#   - 禁止把密码设置为用户名本身
#   - 后台管理员改密/重置时额外要求包含字母与数字（前台普通改密不强制，
#     避免过严策略导致用户频繁忘记密码而流失）
# ---------------------------------------------------------------------------
MIN_PASSWORD_LEN = 8
VALID_ROLES = ("admin", "user")


def _validate_password_strength(password: str, username: str, strict: bool) -> None:
    """校验密码策略；strict=True 时（管理员重置/后台创建）要求字母+数字。"""
    if len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400, detail=f"密码至少 {MIN_PASSWORD_LEN} 位"
        )
    if len(password) > 128:
        raise HTTPException(status_code=400, detail="密码不能超过 128 位")
    # bcrypt 仅使用前 72 字节，超长密码既无意义又可能触发 bcrypt 异常，
    # 在入口处按 UTF-8 字节数直接拒绝。
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="密码过长（超过 72 字节），请缩短")
    if password.lower() == (username or "").lower():
        raise HTTPException(status_code=400, detail="密码不能与用户名相同")
    if strict:
        if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
            raise HTTPException(
                status_code=400, detail="密码必须同时包含字母和数字"
            )

# ---------------------------------------------------------------------------
# ShunX 入口枚举防护（IP 级，独立于用户名）
#   漏洞背景（第七轮审计）：verify_entry 失败的响应（403「请通过安全入口访问」）
#   与入口正确时的响应（401「用户名或密码错误」）存在差异，构成入口枚举预言机；
#   而逐 IP|username 限流在攻击者每次更换随机用户名时永不触发。
#   修复：对「入口校验失败」按 IP 维度独立滑动窗口计数，超阈值后
#   锁定期内一律返回与「用户不存在/密码错误」完全一致的 401（抹平差异，
#   使锁定期本身不再成为新的预言机），切断无限速爆破链路。
# ---------------------------------------------------------------------------
_ENTRY_MAX_FAILURES = 10    # 每个 IP 每窗口期内入口校验最大失败次数
_ENTRY_WINDOW = 10 * 60     # 窗口期（秒）
_entry_failures: dict = {}  # ip -> [失败时间戳列表]


def _check_entry_throttle(request: Request) -> None:
    """IP 级入口失败限流检查：超限时抛出与「登录失败」完全一致的 401。

    注意：此处不能抛 403/429——否则「错误入口→锁定响应、正确入口→401」
    依然是可区分的差异响应，预言机并未消除。
    """
    ip = get_client_ip(request)
    now = time.time()
    with _lock:
        hits = _entry_failures.setdefault(ip, [])
        while hits and hits[0] <= now - _ENTRY_WINDOW:
            hits.pop(0)
        # 键空间防膨胀：顺带清理长期无活动的 IP
        if len(_entry_failures) > 10000:
            stale = [k for k, v in _entry_failures.items() if not v or v[-1] <= now - _ENTRY_WINDOW * 10]
            for k in stale:
                _entry_failures.pop(k, None)
        if len(hits) >= _ENTRY_MAX_FAILURES:
            exceeded = True
        else:
            exceeded = False
    if exceeded:
        logger.warning("IP %s 入口校验失败次数过多，锁定期内登录响应已抹平", ip)
        # 抹平时序：与「用户不存在」分支一致地执行一次哑哈希比对
        verify_password("graw-entry-throttle", _DUMMY_HASH)
        raise HTTPException(status_code=401, detail="用户名或密码错误")


def _record_entry_failure(request: Request) -> None:
    """记录一次入口校验失败（IP 维度，与用户名无关）。"""
    ip = get_client_ip(request)
    now = time.time()
    with _lock:
        hits = _entry_failures.setdefault(ip, [])
        hits.append(now)


# ---------------------------------------------------------------------------
# 登录暴力破解防护（进程内限流）
#   - 同一 IP + 账号连续失败 MAX_ATTEMPTS 次后，锁定 LOCK_SECONDS 秒。
#   - 仅内存记录，重启即清零；单进程部署下有效。
# ---------------------------------------------------------------------------
MAX_ATTEMPTS = 5
LOCK_SECONDS = 10 * 60  # 10 分钟
_lock = threading.Lock()
_failures = defaultdict(lambda: {"count": 0, "locked_until": 0.0})
# 限流字典上限：防止恶意构造不同 IP+username 组合撑爆内存
_MAX_FAILURES = 100000

# 分布式爆破防护（账号维度全局锁定）：
#   逐 IP 限流挡不住攻击者轮换 IP 对同一账号发起字典攻击，这里再按
#   「账号」维度做一次全局计数。为避免单一攻击者借此把合法用户锁出系统
#   （DoS 权衡），仅当失败来源 IP 达到 _GLOBAL_MIN_IPS 个不同地址后才
#   开始累计，且全局锁定期比逐 IP 更短。
_GLOBAL_MIN_IPS = 3
_GLOBAL_MAX_ATTEMPTS = 30
_GLOBAL_LOCK_SECONDS = 5 * 60  # 5 分钟
_global_failures = defaultdict(lambda: {"count": 0, "ips": set(), "locked_until": 0.0})


def _throttle_key(request: Request, username: str) -> str:
    """限流键：IP + 账号名（使用 get_client_ip 兼容反代部署）。"""
    ip = get_client_ip(request)
    return f"{ip}|{username}"


def _throttle_cleanup_if_needed():
    """限流字典超过上限时清理已过期的条目，防止内存 DoS。"""
    if len(_failures) < _MAX_FAILURES:
        return
    now = time.time()
    stale = [k for k, v in _failures.items() if v["locked_until"] <= now and v["count"] == 0]
    for k in stale:
        del _failures[k]


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
    # 账号维度全局锁定检查（分布式爆破防护）
    _check_global_lock(username)


def _record_failure(request: Request, username: str) -> None:
    """记录一次失败；达到阈值即锁定（逐 IP + 账号）。"""
    ip = get_client_ip(request)
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
            logger.warning("账号 %s 触发登录锁定（IP %s）", username, ip)
        _throttle_cleanup_if_needed()
    # 分布式爆破防护：按账号维度累计失败（多 IP 轮换时生效）
    _record_global_failure(username, ip)


def _clear_failures(request: Request, username: str) -> None:
    """登录成功/注销后清零该键的失败记录。"""
    with _lock:
        _failures.pop(_throttle_key(request, username), None)
    _clear_global_failures(username)


def _record_global_failure(username: str, ip: str) -> None:
    """账号维度失败累计：总失败次数达到阈值且来源为多个不同 IP 时锁定。

    说明：单 IP 连续失败在达到 5 次后已被逐 IP 限流拦截，不会再走到这里，
    因此这里的累计次数几乎全部来自不同 IP 的失败；count 与「来源 IP 数」
    共同判定，阈值精确为 _GLOBAL_MAX_ATTEMPTS 次。
    """
    now = time.time()
    with _lock:
        rec = _global_failures[username]
        if rec["locked_until"] > now:
            return  # 已全局锁定
        rec["count"] += 1
        if len(rec["ips"]) < _GLOBAL_MAX_ATTEMPTS * 4:  # 防止集合无限膨胀
            rec["ips"].add(ip)
        # 仅当失败来源为多个不同 IP（分布式攻击特征）时启用全局锁定
        if (
            len(rec["ips"]) >= _GLOBAL_MIN_IPS
            and rec["count"] >= _GLOBAL_MAX_ATTEMPTS
        ):
            rec["count"] = 0
            rec["locked_until"] = now + _GLOBAL_LOCK_SECONDS
            logger.warning(
                "账号 %s 触发全局登录锁定（分布式爆破防护，来源 IP 数 %d）",
                username, len(rec["ips"]),
            )


def _check_global_lock(username: str) -> None:
    """账号维度全局锁定检查：已锁定时抛 403。"""
    now = time.time()
    with _lock:
        rec = _global_failures[username]
    if rec["locked_until"] > now:
        raise HTTPException(status_code=403, detail="登录失败次数过多，请稍后再试")


def _clear_global_failures(username: str) -> None:
    """登录成功/注销后清零该账号的全局失败记录。"""
    with _lock:
        _global_failures.pop(username, None)


class LoginRequest(BaseModel):
    username: str = Field(default="", min_length=1, max_length=64)
    password: str = Field(default="", min_length=1, max_length=128)
    otp_code: Optional[str] = Field(default=None, max_length=16)


class LoginResponse(BaseModel):
    token: str = ""
    user: Optional[dict] = None
    otp_required: Optional[bool] = False
    username: Optional[str] = None


# 哑 bcrypt 哈希：用户不存在时也执行一次同代价的密码比对，
# 消除「用户存在→跑 bcrypt（约几十毫秒）/ 用户不存在→立即返回」的
# 响应时序差，防止借此枚举有效用户名。
_DUMMY_HASH = hash_password("graw-timing-equalizer")


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request):
    # 暴力破解防护：先查是否已锁定
    _check_throttle(request, req.username)
    # ShunX 保护：入口校验必须先于密码校验。
    # 若先验密码后验入口，「密码正确+入口错误」会返回 403 而密码错误
    # 返回 401，攻击者无需知道入口即可据此确认密码是否正确（预言机）；
    # 入口不符同样计入失败次数，防止配合 /status 高频枚举入口路径。
    # 入口枚举防护（第七轮审计修复）：入口失败按 IP 独立限流，
    # 超阈值后锁定期内响应统一抹平为 401，阻断「换随机用户名绕过
    # IP|username 限流 + 差异响应枚举入口」的爆破链路。
    _check_entry_throttle(request)
    try:
        verify_entry(request)
    except HTTPException:
        _record_entry_failure(request)
        _record_failure(request, req.username)
        auditlog.record(
            "登录失败", req.username, get_client_ip(request), "安全入口校验未通过"
        )
        raise
    user = _get_user(req.username)
    if user is None:
        # 用户不存在：比对哑哈希抹平时序后同样返回 401
        verify_password(req.password, _DUMMY_HASH)
        _record_failure(request, req.username)
        auditlog.record(
            "登录失败", req.username, get_client_ip(request), "用户不存在"
        )
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not verify_password(req.password, user["password"]):
        _record_failure(request, req.username)
        auditlog.record(
            "登录失败", req.username, get_client_ip(request), "密码错误"
        )
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 登录成功，清零失败记录（入口与密码均已通过校验）
    _clear_failures(request, req.username)
    public = _public_user(user)
    # 两步验证（2FA）：开启后必须先通过 TOTP 验证码才能签发令牌
    if user.get("otp_enabled"):
        otp_code = (req.otp_code or "").strip()
        if not otp_code:
            # 密码已正确，但还差验证码——返回 otp_required 标记让前端继续输入
            # （不签发 token；响应与常规登录一致，不泄露额外信息）
            auditlog.record("登录待验证", req.username, get_client_ip(request), "已通过密码，等待 2FA 验证码")
            return {"otp_required": True, "username": user["username"], "token": "", "user": None}
        if not verify_totp(user.get("otp_secret", ""), otp_code):
            _record_failure(request, req.username)
            auditlog.record("登录失败", req.username, get_client_ip(request), "2FA 验证码错误")
            raise HTTPException(status_code=401, detail="两步验证码错误")
    # ShunX 保护：检测到使用默认密码 → 强制要求修改后才能使用面板
    if is_default_password(user["password"]):
        public["must_change_password"] = True
        public["default_password"] = True
        logger.warning("用户 %s 使用默认密码登录，已强制要求改密", req.username)
    # 签发 JWT 时携带当前 token_version，改密/注销后旧令牌自动失效
    token = create_token(user["username"], token_version=int(user.get("token_version", 0)))
    logger.info("用户 %s 登录成功（IP %s）", req.username, get_client_ip(request))
    auditlog.record("登录成功", req.username, get_client_ip(request))
    return {"token": token, "user": public}


@router.post("/logout")
async def logout(request: Request, user: dict = Depends(get_current_user)):
    """注销登录：递增 token_version 使当前用户所有 JWT 立即失效。"""
    bump_token_version(user["username"])
    _clear_failures(request, user["username"])
    logger.info("用户 %s 已注销（IP %s）", user["username"], get_client_ip(request))
    auditlog.record("退出登录", user["username"], get_client_ip(request))
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return user


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(default="", min_length=1, max_length=128)
    new_password: str = Field(default="", min_length=1, max_length=128)


@router.post("/password")
async def change_password(
    req: ChangePasswordRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    # 改密接口同样受登录限流保护，防止被用于高频爆破原密码
    _check_throttle(request, user["username"])
    full = _get_user(user["username"])
    if full is None or not verify_password(req.old_password, full["password"]):
        # 原密码错误同样计数失败，达到阈值锁定
        _record_failure(request, user["username"])
        raise HTTPException(status_code=400, detail="原密码错误")
    if verify_password(req.new_password, full["password"]):
        # 新密码不能与原密码相同
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    # 密码策略校验（严格模式：字母+数字）
    _validate_password_strength(req.new_password, user["username"], strict=True)
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
    # 关键：改密后吊销该用户所有旧令牌（包括刚用于改密的这个），
    # 并签发新令牌返回给前端，保证会话不中断。
    bump_token_version(user["username"])
    _clear_failures(request, user["username"])
    # bump_token_version 已将 token_version 递增；必须读取递增后的新版本
    # 签发，否则新令牌携带旧版本号，会被 verify_token_version 吊销校验拒绝。
    users = _load_users() or {}
    new_tv = int(users.get(user["username"], {}).get("token_version", 0))
    new_token = create_token(user["username"], token_version=new_tv)
    logger.info("用户 %s 已修改密码并刷新令牌（IP %s）", user["username"], get_client_ip(request))
    auditlog.record("修改密码", user["username"], get_client_ip(request))
    return {"ok": True, "token": new_token}


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=MIN_PASSWORD_LEN, max_length=128)
    role: str = Field(default="user")


@router.get("/users")
async def list_users(_: dict = Depends(require_admin)):
    users = _load_users() or {}
    return [_public_user(u) for u in users.values()]


@router.post("/users")
async def create_user(req: CreateUserRequest, request: Request, admin: dict = Depends(require_admin)):
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="角色无效")
    # ShunX 保护：禁止使用默认密码作为账号密码
    if req.password == DEFAULT_PASSWORD:
        raise HTTPException(status_code=400, detail="不能将默认密码作为账号密码")
    # 密码策略校验（严格模式：字母+数字）
    _validate_password_strength(req.password, req.username, strict=True)
    users = _load_users() or {}
    if req.username in users:
        raise HTTPException(status_code=400, detail="用户已存在")
    users[req.username] = {
        "username": req.username,
        "password": hash_password(req.password),
        "role": req.role,
        "must_change_password": False,
        "token_version": 0,
        "created_at": time.time(),
    }
    _save_users(users)
    logger.info("管理员创建账号 %s（角色 %s）", req.username, req.role)
    auditlog.record("创建用户", admin["username"], get_client_ip(request), f"目标:{req.username} 角色:{req.role}")
    return {"ok": True}


class UpdateUserRequest(BaseModel):
    password: Optional[str] = Field(default=None, max_length=128)
    role: Optional[str] = None
    must_change_password: Optional[bool] = None


@router.put("/users/{username}")
async def update_user(
    username: str, req: UpdateUserRequest, request: Request, admin: dict = Depends(require_admin)
):
    users = _load_users() or {}
    target = users.get(username)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if req.password is not None:
        # ShunX 保护：禁止把密码重置为默认密码
        if req.password == DEFAULT_PASSWORD:
            raise HTTPException(status_code=400, detail="不能将默认密码作为账号密码")
        # 密码策略校验（严格模式：字母+数字）
        _validate_password_strength(req.password, username, strict=True)
        target["password"] = hash_password(req.password)
        # 重置密码后默认要求下次登录修改
        target["must_change_password"] = (
            req.must_change_password if req.must_change_password is not None else True
        )
        # 重置密码后吊销该用户所有现有会话，强制重新登录
        bump_token_version(username)
        logger.info("管理员重置账号 %s 的密码，其所有会话已失效", username)
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
    auditlog.record(
        "更新用户", admin["username"], get_client_ip(request),
        f"目标:{username} 改密码:{req.password is not None}",
    )
    return {"ok": True}


@router.delete("/users/{username}")
async def delete_user(username: str, request: Request, user: dict = Depends(require_admin)):
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
    auditlog.record("删除用户", user["username"], get_client_ip(request), f"目标:{username}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# 两步验证（2FA）管理：为当前登录用户开启 / 关闭 TOTP
# ---------------------------------------------------------------------------
@router.get("/2fa/status")
async def otp_status(user: dict = Depends(get_current_user)):
    """当前用户 2FA 状态。"""
    full = _get_user(user["username"])
    return {
        "otp_enabled": bool(full and full.get("otp_enabled")),
        # 已启用后不回显 secret（防泄露）；未启用时可通过 setup 重新生成
        "has_secret": bool(full and full.get("otp_secret")),
    }


class OtpSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


@router.post("/2fa/setup", response_model=OtpSetupResponse)
async def otp_setup(user: dict = Depends(get_current_user)):
    """生成新的 TOTP secret（仅未启用时可调用；启用后不可再生）。"""
    users = _load_users() or {}
    full = users.get(user["username"])
    if full and full.get("otp_enabled"):
        raise HTTPException(status_code=400, detail="两步验证已启用，如需重置请先关闭")
    secret = generate_otp_secret()
    # 启用前先落盘 secret，但标记未启用——前端在启用弹窗内展示二维码
    full["otp_secret"] = secret
    full["otp_enabled"] = False
    _save_users(users)
    return {
        "secret": secret,
        "otpauth_uri": otpauth_uri(secret, user["username"]),
    }


class OtpEnableRequest(BaseModel):
    code: str = Field(min_length=6, max_length=16)


@router.post("/2fa/enable")
async def otp_enable(req: OtpEnableRequest, request: Request, user: dict = Depends(get_current_user)):
    """用验证码确认后启用 2FA（验证码与当前 secret 匹配才算成功）。"""
    users = _load_users() or {}
    full = users.get(user["username"])
    if full is None or not full.get("otp_secret"):
        raise HTTPException(status_code=400, detail="请先生成两步验证密钥")
    if full.get("otp_enabled"):
        raise HTTPException(status_code=400, detail="两步验证已启用")
    if not verify_totp(full.get("otp_secret", ""), req.code):
        raise HTTPException(status_code=400, detail="验证码错误")
    full["otp_enabled"] = True
    _save_users(users)
    logger.info("用户 %s 已启用两步验证（IP %s）", user["username"], get_client_ip(request))
    auditlog.record("启用两步验证", user["username"], get_client_ip(request))
    return {"ok": True}


@router.post("/2fa/disable")
async def otp_disable(req: OtpEnableRequest, request: Request, user: dict = Depends(get_current_user)):
    """用验证码确认后关闭 2FA（防止误操作被他人直接关闭）。"""
    users = _load_users() or {}
    full = users.get(user["username"])
    if full is None or not full.get("otp_enabled"):
        raise HTTPException(status_code=400, detail="两步验证未启用")
    if not verify_totp(full.get("otp_secret", ""), req.code):
        raise HTTPException(status_code=400, detail="验证码错误")
    full["otp_enabled"] = False
    full["otp_secret"] = ""
    _save_users(users)
    logger.info("用户 %s 已关闭两步验证（IP %s）", user["username"], get_client_ip(request))
    auditlog.record("关闭两步验证", user["username"], get_client_ip(request))
    return {"ok": True}
