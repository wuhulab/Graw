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

from app.auth import require_admin

logger = logging.getLogger("graw.ui")

router = APIRouter()

# 配置文件路径：backend/data/ui.json
UI_FILE = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "ui.json"))
)

# 默认配置：未配置时沿用品牌默认值
DEFAULTS = {
    "site_name": "Graw",
    "welcome": "",
    "logo": "",
    "background": "",
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


class UIConfigUpdate(BaseModel):
    """界面配置更新请求。"""
    site_name: str = ""
    welcome: str = ""
    logo: str = ""
    background: str = ""
    ring_color: str = "#409eff"
    ring_alarm: bool = True


# 环形图颜色：仅接受 6 位十六进制（如 #409eff），防止注入任意样式数据
_RING_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_ring_color(value: str) -> str:
    """校验环形图颜色为合法的 6 位十六进制色值。"""
    value = (value or "").strip()
    if not _RING_COLOR_RE.match(value):
        raise HTTPException(status_code=400, detail="环形图颜色需为 #RRGGBB 格式")
    return value.lower()


@router.get("/public")
async def get_public_config():
    """公开接口：登录页展示所需品牌信息，无需登录。"""
    d = _load()
    return {
        "site_name": d.get("site_name") or "Graw",
        "welcome": d.get("welcome") or "",
        "logo": d.get("logo") or "",
        "background": d.get("background") or "",
        "ring_color": d.get("ring_color") or "#409eff",
        "ring_alarm": bool(d.get("ring_alarm", True)),
    }


@router.get("/config", dependencies=[Depends(require_admin)])
async def get_config():
    """管理员：读取完整界面配置。"""
    return _load()


@router.put("/config", dependencies=[Depends(require_admin)])
async def update_config(req: UIConfigUpdate):
    """管理员：保存界面配置（网站名 / 欢迎语 / Logo / 背景 / 环形图配色）。"""
    site_name = (req.site_name or "").strip()
    welcome = (req.welcome or "").strip()
    logo = _validate_image(req.logo, _LOGO_MAX_BYTES, "Logo")
    background = _validate_image(req.background, _BACKGROUND_MAX_BYTES, "背景")
    ring_color = _validate_ring_color(req.ring_color)
    # 网站名/欢迎语限制长度，避免滥用
    if len(site_name) > 60:
        raise HTTPException(status_code=400, detail="网站名过长（最多 60 个字符）")
    if len(welcome) > 200:
        raise HTTPException(status_code=400, detail="欢迎语过长（最多 200 个字符）")
    data = {
        "site_name": site_name or "Graw",
        "welcome": welcome,
        "logo": logo,
        "background": background,
        "ring_color": ring_color,
        "ring_alarm": bool(req.ring_alarm),
    }
    _save(data)
    return data