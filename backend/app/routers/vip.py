# -*- coding: utf-8 -*-
"""
vip.py - Graw 付费功能（VIP/月卡/年卡）路由

提供：
  - GET    /status      返回 VIP 状态（登录即可）
  - POST   /activate    用授权码激活 VIP（登录即可）

说明：
  VIP 状态为**面板级共享**：任意账号激活成功后，所有账号同时生效
  （见 app/vip.py 的 _shared_active_record）。授权码服务地址固定在后端，
  前端不可修改，因此不再提供 /config 接口。

鉴权：
  本路由不挂全局依赖，由各端点内部自行校验（均需登录），方式与 ui/tamper
  路由保持一致，便于登录态复查。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import vip as vip_mod
from app.auth import get_current_user

router = APIRouter()


class ActivateBody(BaseModel):
    """激活请求体：携带授权码。"""
    code: str = Field(..., min_length=1, max_length=64)


@router.get("/status")
def vip_status(user: dict = Depends(get_current_user)):
    """返回当前登录用户的 VIP 状态。"""
    return vip_mod.get_vip((user or {}).get("username", ""))


@router.post("/activate")
def vip_activate(body: ActivateBody, user: dict = Depends(get_current_user)):
    """用授权码激活当前用户的 VIP；失败按原因返回 400/502。"""
    try:
        status = vip_mod.activate_vip((user or {}).get("username", ""), body.code)
    except ValueError as e:
        # 授权码无效 / 已使用 / 服务不可达 → 400（前端可读的 detail）
        raise HTTPException(status_code=400, detail=str(e))
    return status