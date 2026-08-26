# -*- coding: utf-8 -*-
"""
ui.py - 界面设置路由

管理面板登录页/浏览器标题的品牌化配置：
  - site_name : 自定义网站名（用于登录页大标题与浏览器标签标题）
  - welcome   : 自定义欢迎语（用于登录页副标题/欢迎语）
  - logo      : 自定义 Logo（Base64 data URL 图片），登录页顶部展示
  - background: 自定义背景（Base64 data URL 图片），登录页背景

配置以 JSON 存放在 backend/data/ui.json。
安全设计：
  - GET /api/ui/public 为公开接口，登录页无需鉴权即可读取展示；
  - GET/PUT /api/ui/config 仅管理员可用（写入时校验 Logo/背景格式与大小）。
"""
import base64
import json
import logging
import os
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_admin, get_current_user

logger = logging.getLogger("graw.ui")

router = APIRouter()

# 配置文件路径：backend/data/ui.json
UI_FILE = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "ui.json"))
)
# 用户级覆盖（「仅用于这个账号」）：{ username: { wallpaper: {...}, ring: {...} } }
UI_USER_FILE = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "ui_user.json"))
)

# 默认配置：未配置时沿用品牌默认值
DEFAULTS = {
    "site_name": "Graw",
    "welcome": "",
    "logo": "",
    "background": "",
    # 动态壁纸（桌面/登录页共用）：
    #   backgrounds      图片轮播列表（每项为 Base64 data URL）
    #   wallpaper_video  视频动态壁纸（Base64 data URL, mp4/webm）
    #   background_mode  生效模式：image(单图/轮播) | video
    #   background_interval 轮播间隔（秒）
    "backgrounds": [],
    "wallpaper_video": "",
    "background_mode": "image",
    "background_interval": 8,
    # 系统概览环形统计图配色（桌面所有用户共享）
    "ring_color": "#409eff",   # 环形图统一颜色（蓝色），管理员可在界面设置中修改
    "ring_alarm": True,        # 是否启用「使用率 >90% 变红」告警提示
}

# 允许的图片类型（对应常见的 PNG / JPEG / GIF / WebP / SVG）
_IMAGE_DATA_URL_RE = re.compile(
    r"^data:image/(?:png|jpe?g|gif|webp|svg\+xml);base64,", re.IGNORECASE
)
# Logo 最大体积（解码后字节数），约 2MB，防止超大文件堆爆 JSON / 拖慢登录页
_LOGO_MAX_BYTES = 2 * 1024 * 1024
# 背景最大体积（解码后字节数），约 8MB（背景通常较大，放宽上限）
_BACKGROUND_MAX_BYTES = 8 * 1024 * 1024
# 背景轮播列表最大张数（防止配置被滥用撑爆 JSON）
_MAX_BACKGROUNDS = 12
# 视频壁纸最大体积（解码后字节数），约 50MB（动态壁纸一般远超图片）
_VIDEO_MAX_BYTES = 50 * 1024 * 1024

# 允许的视频类型：MP4 / WebM（浏览器原生支持的动态壁纸格式）
_VIDEO_DATA_URL_RE = re.compile(
    r"^data:video/(?:mp4|webm|ogg);base64,", re.IGNORECASE
)


def _load() -> dict:
    """读取配置；文件缺失/损坏时回退默认值。"""
    if not os.path.exists(UI_FILE):
        return dict(DEFAULTS)
    try:
        with open(UI_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取界面配置失败，回退默认值: %s", e)
        return dict(DEFAULTS)
    if not isinstance(data, dict):
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    # 按字段类型合并：字符串字段只接受 str，布尔字段只接受 bool，非法值回退默认
    for k in DEFAULTS:
        v = data.get(k)
        if isinstance(v, bool) and isinstance(DEFAULTS[k], bool):
            merged[k] = v
        elif isinstance(v, str) and isinstance(DEFAULTS[k], str):
            merged[k] = v
        elif isinstance(v, list) and isinstance(DEFAULTS[k], list):
            # 背景轮播列表：仅保留字符串元素、量级限量（防止超大/脏数据）
            merged[k] = [x for x in v if isinstance(x, str)][: _MAX_BACKGROUNDS]
        elif isinstance(v, int) and isinstance(DEFAULTS[k], int):
            merged[k] = v
        elif isinstance(v, float) and isinstance(DEFAULTS[k], int):
            merged[k] = int(v)
    return merged


def _save(data: dict) -> None:
    """写入配置（含必要的日志与异常处理）。"""
    try:
        os.makedirs(os.path.dirname(UI_FILE), exist_ok=True)
        with open(UI_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存界面配置失败: %s", e)
        raise HTTPException(status_code=500, detail="保存界面配置失败")


def _validate_image(data_url: str, max_bytes: int, field: str) -> str:
    """校验并返回图片（Base64 data URL），空串表示清除该图片。

    仅接受 data:image/...;base64 前缀，且解码后体积受限，
    防止注入任意数据或超大 payload。
    """
    data_url = (data_url or "").strip()
    if not data_url:
        return ""
    if not _IMAGE_DATA_URL_RE.match(data_url):
        raise HTTPException(
            status_code=400, detail=f"{field} 需为 data:image/*;base64 图片数据"
        )
    # 提取 base64 主体并解码，校验是否为合法 base64
    try:
        body = data_url.split(",", 1)[1].split(";")[0]
        raw = base64.b64decode(body, validate=True)
    except Exception:
        raise HTTPException(
            status_code=400, detail=f"{field} 不是合法的 Base64 图片数据"
        )
    if len(raw) > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"{field} 图片过大（最大 {mb}MB）")
    return data_url


def _validate_video(data_url: str) -> str:
    """校验并返回视频壁纸（Base64 data URL），空串表示清除。

    仅接受 data:video/(mp4|webm|ogg);base64 前缀，且解码后体积受限，
    防止注入任意数据或超大 payload。
    """
    data_url = (data_url or "").strip()
    if not data_url:
        return ""
    if not _VIDEO_DATA_URL_RE.match(data_url):
        raise HTTPException(
            status_code=400, detail="视频壁纸需为 data:video/(mp4|webm|ogg);base64 数据"
        )
    # 提取 base64 主体并解码
    try:
        body = data_url.split(",", 1)[1].split(";")[0]
        raw = base64.b64decode(body, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="视频壁纸不是合法的 Base64 数据")
    if len(raw) > _VIDEO_MAX_BYTES:
        mb = _VIDEO_MAX_BYTES // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"视频壁纸过大（最大 {mb}MB）")
    return data_url


def _clamp_interval(value) -> int:
    """限制轮播间隔在合理范围（3~120 秒），非法回退默认 8 秒。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return DEFAULTS["background_interval"]
    return max(3, min(120, v))


class UIConfigUpdate(BaseModel):
    """界面配置更新请求。"""
    site_name: str = ""
    welcome: str = ""
    logo: str = ""
    background: str = ""
    # 动态壁纸：多背景轮播列表 + 视频壁纸 + 模式 + 轮播间隔
    backgrounds: list = []
    wallpaper_video: str = ""
    background_mode: str = "image"
    background_interval: int = 8
    ring_color: str = "#409eff"
    ring_alarm: bool = True
    # 「仅用于这个账号」：勾选则下列对应项写入当前账号的独立配置（对该账号生效），
    # 否则写入全局配置（对该账号也会顺带清除个人覆盖，使全局生效）。
    wallpaper_personal: bool = False
    ring_personal: bool = False


# 环形图颜色：仅接受 6 位十六进制（如 #409eff），防止注入任意样式数据
_RING_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_ring_color(value: str) -> str:
    """校验环形图颜色为合法的 6 位十六进制色值。"""
    value = (value or "").strip()
    if not _RING_COLOR_RE.match(value):
        raise HTTPException(status_code=400, detail="环形图颜色需为 #RRGGBB 格式")
    return value.lower()


# ---------------------------------------------------------------------------
# 用户级覆盖（「仅用于这个账号」）
# ---------------------------------------------------------------------------
def _load_user_overrides() -> dict:
    """读取用户级覆盖（username -> {wallpaper, ring}）；缺失/损坏返回空 dict。"""
    if not os.path.exists(UI_USER_FILE):
        return {}
    try:
        with open(UI_USER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_user_overrides(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(UI_USER_FILE), exist_ok=True)
        with open(UI_USER_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("保存用户界面覆盖失败: %s", e)


def _effective_wallpaper(username: str) -> dict:
    """当前账号生效的动态壁纸：个人覆盖优先，否则走全局，否则默认。"""
    acc = _load_user_overrides().get(username, {})
    w = acc.get("wallpaper") or {}
    g = _load()
    return {
        "backgrounds": w.get("backgrounds") or g.get("backgrounds") or DEFAULTS["backgrounds"],
        "wallpaper_video": w.get("wallpaper_video") or g.get("wallpaper_video") or "",
        "background_mode": (w.get("background_mode") if w.get("background_mode") else g.get("background_mode", "image")),
        "background_interval": int(w.get("background_interval") or g.get("background_interval") or DEFAULTS["background_interval"]),
    }


def _effective_ring(username: str) -> dict:
    """当前账号生效的环形图配置：个人覆盖优先，否则走全局，否则默认。"""
    acc = _load_user_overrides().get(username, {})
    r = acc.get("ring") or {}
    g = _load()
    return {
        "ring_color": (r.get("ring_color") or g.get("ring_color") or DEFAULTS["ring_color"]).lower(),
        "ring_alarm": bool(r.get("ring_alarm", g.get("ring_alarm", DEFAULTS["ring_alarm"]))),
    }


# 公开接口允许返回的单张背景图上限（解码后字节数，1MB）：
# 登录页展示足够；超大背景仅登录后（/api/ui/effective、config）可见，
# 保证匿名请求的响应体积有确定上界（第十三轮审计）。
_PUBLIC_BACKGROUND_MAX_BYTES = 1024 * 1024


@router.get("/public")
async def get_public_config():
    """公开接口：登录页展示所需品牌信息，无需登录。

    安全修复（第十三轮审计，Medium）：此接口此前把 wallpaper_video（最大
    50MB 的 base64 视频壁纸）与完整轮播列表 backgrounds/背景图一并回传——
    匿名攻击者每次请求即可拉取数十 MB 响应，构成无鉴权的带宽放大 / 流量
    DoS 面（实测单次匿名请求可达 28MB+）。登录页实际仅使用 site_name /
    welcome / logo / 单张 background；动态视频壁纸与轮播是「登录后桌面」
    才使用的功能，一律不再经公开接口回传。单张 background 同样设 1MB
    硬上限——超大背景图仅登录后可见，匿名请求不回传。
    """
    d = _load()
    background = d.get("background") or ""
    if background:
        try:
            body = background.split(",", 1)[1].split(";")[0]
            if len(base64.b64decode(body, validate=True)) > _PUBLIC_BACKGROUND_MAX_BYTES:
                background = ""  # 超大背景仅登录后可见，公开接口不放大流量
        except Exception:
            background = ""
    return {
        "site_name": d.get("site_name") or "Graw",
        "welcome": d.get("welcome") or "",
        "logo": d.get("logo") or "",
        "background": background,
        "ring_color": d.get("ring_color") or "#409eff",
        "ring_alarm": bool(d.get("ring_alarm", True)),
    }


@router.get("/config", dependencies=[Depends(require_admin)])
async def get_config(user: dict = Depends(get_current_user)):
    """管理员：读取完整界面配置，并附当前账号的个人覆盖（「仅用于这个账号」）。"""
    cfg = _load()
    overrides = _load_user_overrides().get(user["username"], {})
    wallpaper = overrides.get("wallpaper") or {}
    ring = overrides.get("ring") or {}
    return {
        **cfg,
        "personal": {
            "wallpaper": wallpaper or None,
            "ring": ring or None,
        },
    }


@router.get("/effective", dependencies=[Depends(get_current_user)])
async def get_effective_config(user: dict = Depends(get_current_user)):
    """当前账号生效的动态壁纸与环形图（「仅用于这个账号」优先，其次全局，最后默认）。

    供登录后的桌面（App.vue / 环形统计图）按账号取用，别的账号互不影响。
    """
    return {**_effective_wallpaper(user["username"]), **_effective_ring(user["username"])}


@router.put("/config", dependencies=[Depends(require_admin)])
async def update_config(req: UIConfigUpdate, user: dict = Depends(get_current_user)):
    """管理员：保存界面配置（网站名 / 欢迎语 / Logo / 背景 / 环形图配色 / 动态壁纸）。

    「仅用于这个账号」（wallpaper_personal / ring_personal）：
      勾选 → 对应项只写入当前账号覆盖（不影响全局）；
      不勾选 → 写入全局，并清除当前账号对应覆盖（让全局对其生效）。
    """
    site_name = (req.site_name or "").strip()
    welcome = (req.welcome or "").strip()
    logo = _validate_image(req.logo, _LOGO_MAX_BYTES, "Logo")
    background = _validate_image(req.background, _BACKGROUND_MAX_BYTES, "背景")
    # 背景轮播列表：逐项校验图片、限量张数，兼容保留单 background 字段
    backgrounds: list = []
    for bg in (req.backgrounds or []) or []:
        bg = bg if isinstance(bg, str) else ""
        if not bg.strip():
            continue
        if len(backgrounds) >= _MAX_BACKGROUNDS:
            break
        backgrounds.append(_validate_image(bg, _BACKGROUND_MAX_BYTES, "轮播背景"))
    if not backgrounds and background:
        # 兼容旧版：未提供列表但提供单背景 → 用单背景兜底
        backgrounds = [background]
    wallpaper_video = _validate_video(req.wallpaper_video)
    background_mode = req.background_mode if req.background_mode in ("image", "video") else "image"
    background_interval = _clamp_interval(req.background_interval)
    ring_color = _validate_ring_color(req.ring_color)
    # 网站名/欢迎语限制长度，避免滥用
    if len(site_name) > 60:
        raise HTTPException(status_code=400, detail="网站名过长（最多 60 个字符）")
    if len(welcome) > 200:
        raise HTTPException(status_code=400, detail="欢迎语过长（最多 200 个字符）")

    # 当前全局（用于勾选个人时保留全局对应项不变）
    cur = _load()
    wallpaper_personal = bool(req.wallpaper_personal)
    ring_personal = bool(req.ring_personal)

    data = {
        "site_name": site_name or "Graw",
        "welcome": welcome,
        "logo": logo,
        "background": background,
        # 动态壁纸：仅当非个人时才写全局；个人模式下保持全局原值
        "backgrounds": backgrounds if not wallpaper_personal else (cur.get("backgrounds") or []),
        "wallpaper_video": wallpaper_video if not wallpaper_personal else (cur.get("wallpaper_video") or ""),
        "background_mode": background_mode if not wallpaper_personal else (cur.get("background_mode") or "image"),
        "background_interval": background_interval if not wallpaper_personal else int(cur.get("background_interval") or 8),
        # 环形图：仅当非个人时才写全局；个人模式下保持全局原值
        "ring_color": ring_color if not ring_personal else (cur.get("ring_color") or "#409eff"),
        "ring_alarm": bool(req.ring_alarm) if not ring_personal else bool(cur.get("ring_alarm", True)),
    }
    _save(data)

    # 等写入当前账号的个人覆盖
    overrides = _load_user_overrides()
    acc = overrides.setdefault(user["username"], {})
    if wallpaper_personal:
        acc["wallpaper"] = {
            "backgrounds": backgrounds,
            "wallpaper_video": wallpaper_video,
            "background_mode": background_mode,
            "background_interval": background_interval,
        }
    else:
        acc.pop("wallpaper", None)
    if ring_personal:
        acc["ring"] = {
            "ring_color": ring_color,
            "ring_alarm": bool(req.ring_alarm),
        }
    else:
        acc.pop("ring", None)
    _save_user_overrides(overrides)

    return {**data, "personal": {"wallpaper": acc.get("wallpaper") or None, "ring": acc.get("ring") or None}}