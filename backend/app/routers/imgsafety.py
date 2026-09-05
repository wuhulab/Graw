# -*- coding: utf-8 -*-
"""
imgsafety.py（路由） - Docker 镜像漏洞扫描接口（仅管理员）

功能：
  POST /api/imgsafety/scan            - 扫描镜像（同步等待，放入线程池）
  GET  /api/imgsafety/advisory        - 本地 advisory 列表
  POST /api/imgsafety/advisory/import - 导入 advisory（{packages:[...]}）

说明：advisory 为本地维护库，无外部 CVE 数据源；扫描命中与否取决于
      管理员导入的规则（github 等渠道导出的常见高危包清单即可）。
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import imgsafety

logger = logging.getLogger("graw.imgsafety")

router = APIRouter()


class ScanReq(BaseModel):
    """扫描请求：image 为本地镜像引用（含 tag，如 nginx:latest）。"""

    image: str = Field(..., min_length=1, max_length=256)


class AdvisoryImportReq(BaseModel):
    """导入 advisory：packages 数组，每项含 name/versions/cve/severity/desc。"""

    packages: list = Field(..., min_length=1, max_length=5000)


@router.post("/scan")
async def scan(req: ScanReq):
    """扫描一个本地镜像，返回包清单与命中 CVE。"""
    try:
        result = await asyncio.to_thread(imgsafety.scan_image, req.image)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


@router.get("/advisory")
async def advisory_list():
    """本地 advisory 列表。"""
    return {"packages": imgsafety.load_advisory(), "total": len(imgsafety.load_advisory())}


@router.post("/advisory/import")
async def advisory_import(req: AdvisoryImportReq):
    """导入 advisory（按 名称+CVE 去重，非法条目跳过）。"""
    res = imgsafety.import_advisory(req.packages)
    return {"ok": True, **res}