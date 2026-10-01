# -*- coding: utf-8 -*-
"""
report.py - 巡检报告 REST 路由（仅管理员）

功能：
  POST /api/report/generate - 手动生成一份巡检报告（同步等待，结果落盘并推送）
  GET  /api/report/list     - 报告文件列表（时间倒序）
  GET  /api/report/{file}   - 读取某份报告全文（文件名白名单防穿越）

说明：
  每日定时生成由 reporting.start_daily() 在 main.py lifespan 中启停（默认 08:00），
  本路由提供手动入口与历史查看。
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app import reporting

logger = logging.getLogger("graw.report")

router = APIRouter()


@router.post("/generate")
async def generate():
    """手动生成一份巡检报告。"""
    try:
        # 数据汇聚 + 写盘 + 推送放到线程池，避免阻塞事件循环
        result = await asyncio.to_thread(reporting.generate_report)
    except Exception as e:
        logger.error("生成巡检报告失败: %s", e)
        raise HTTPException(status_code=500, detail=f"生成报告失败: {e}")
    return {"ok": True, "file": result["file"], "pushed": result["pushed"], "text": result["text"]}


@router.get("/list")
async def list_reports(limit: int = 30):
    """报告文件列表（时间倒序）。"""
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 30
    return {"files": reporting.list_reports(limit)}


@router.get("/{fname}")
async def get_report(fname: str):
    """读取一份报告全文。"""
    text = reporting.read_report(fname)
    if not text:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {"file": fname, "text": text}