# -*- coding: utf-8 -*-
"""
recycle.py - 回收站路由

提供回收站应用的 REST 接口（配置读写 / 列表 / 恢复 / 彻底删除 / 清空）。
所有条目操作都作用于「当前管理主机」（与文件管理 /api/files 同一节点语义），
核心逻辑见 app/trash.py。

安全：
  - 配置与撤销操作均面向管理员（路由级 ADMIN 依赖，main.py 注册）；
  - 恢复/删除的 path 必须落在回收站目录内（trash.py 内部强校验）；
  - 删除与原路径审计写入 audit log，便于追溯。
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi import Depends
from pydantic import BaseModel
import logging

from app import auditlog
from app import trash
from app.auth import get_current_user, get_client_ip

logger = logging.getLogger("graw.recycle")

router = APIRouter()


class ConfigRequest(BaseModel):
    enabled: bool = True
    auto_delete: bool = True
    auto_delete_days: int = 30


class PathRequest(BaseModel):
    path: str


def _to_http(e: Exception, fallback: str = "操作失败") -> HTTPException:
    """把 trash 层异常映射为带具体原因的 HTTPException。"""
    if isinstance(e, HTTPException):
        return e
    msg = str(e) or fallback
    if isinstance(e, FileNotFoundError):
        return HTTPException(status_code=404, detail=msg)
    if isinstance(e, FileExistsError):
        return HTTPException(status_code=409, detail=msg)
    if isinstance(e, PermissionError):
        return HTTPException(status_code=403, detail=f"权限不足：{msg}")
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=msg)
    return HTTPException(status_code=500, detail=f"{fallback}：{msg}")


@router.get("/config")
async def get_config(user: dict = Depends(get_current_user)):
    """读取回收站配置（enabled / auto_delete / auto_delete_days）。"""
    cfg = trash.load_cfg()
    return {**cfg, "trash_root": trash.trash_root()}


@router.post("/config")
async def set_config(
    req: ConfigRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """保存回收站配置；天数限制 1-365。"""
    if req.auto_delete_days < 1 or req.auto_delete_days > 365:
        raise HTTPException(status_code=400, detail="保留天数需在 1-365 之间")
    cfg = trash.save_cfg(req.enabled, req.auto_delete, req.auto_delete_days)
    auditlog.record("回收站配置", user["username"], get_client_ip(request),
                    f"enabled={cfg['enabled']} auto_delete={cfg['auto_delete']} days={cfg['auto_delete_days']}")
    return cfg


@router.get("/list")
async def list_recycle(user: dict = Depends(get_current_user)):
    """列出回收站条目（先清理当前节点已过期条目，再按删除时间倒序返回）。"""
    # 打开/刷新回收站即执行一次过期清理，保证「自动删除」即使后台任务未触发也生效
    try:
        trash.purge_expired()
    except Exception as e:
        logger.warning("回收站列表时清理过期条目失败: %s", e)
    try:
        items = trash.list_items()
    except Exception as e:
        logger.warning("读取回收站失败: %s", e)
        raise _to_http(e, "读取回收站失败")
    return {"items": items, "trash_root": trash.trash_root()}


@router.post("/restore")
async def restore(
    req: PathRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """把回收站条目恢复到原位置。"""
    try:
        res = trash.restore_item(req.path, user["username"])
    except Exception as e:
        logger.warning("恢复 %s 失败: %s", req.path, e)
        raise _to_http(e, "恢复失败")
    auditlog.record("恢复回收站", user["username"], get_client_ip(request), res.get("original", req.path))
    return res


@router.post("/delete")
async def delete_item(
    req: PathRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """彻底删除回收站中的单一条目（不可恢复）。"""
    try:
        trash.delete_item(req.path)
    except Exception as e:
        logger.warning("彻底删除回收站条目 %s 失败: %s", req.path, e)
        raise _to_http(e, "彻底删除失败")
    auditlog.record("彻底删除", user["username"], get_client_ip(request), req.path)
    return {"ok": True}


@router.post("/empty")
async def empty(request: Request, user: dict = Depends(get_current_user)):
    """清空回收站（所有条目永久删除）。"""
    try:
        n = trash.empty_trash()
    except Exception as e:
        logger.warning("清空回收站失败: %s", e)
        raise _to_http(e, "清空失败")
    auditlog.record("清空回收站", user["username"], get_client_ip(request), f"共 {n} 条")
    return {"ok": True, "count": n}