# -*- coding: utf-8 -*-
"""
webmode.py - Web 服务器引擎（NGINX / OpenResty）模式管理接口（仅管理员）

提供当前引擎模式查询与切换。切换只更新引擎选择（backend/data/webserver.json），
sites / waf 等路由在生成/写入配置时按当前模式解析路径与 reload 命令。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import webserver

router = APIRouter()


class ModeBody(BaseModel):
    """切换请求体：mode 必须为 nginx / openresty（白名单校验）。"""

    mode: str = Field(..., min_length=1, max_length=32)


@router.get("/status")
async def webmode_status():
    """返回当前引擎模式、可用性与配置目录（供设置界面展示）。"""
    return webserver.status()


@router.post("/mode")
async def webmode_set_mode(body: ModeBody):
    """设置引擎模式；非法值返回 400，成功返回新模式与信息。"""
    try:
        mode = webserver.set_mode(body.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"mode": mode, **webserver.status()}