"""Docker 数据卷与网络管理路由（/api/dockervolumes，仅管理员）。

复用 docker_api.py 中统一的后端探测 / CLI 发现 / 输入校验 / 错误归一化工具，
保持与 Docker 窗口（容器/镜像/网络）一致的 CLI（podman/docker）+ Docker SDK
双后端实现：
  - GET  /                    列出数据卷
  - POST /{name}/remove       强制删除数据卷
  - GET  /{name}/inspect      查看数据卷挂载/使用信息

说明：网络（networks）管理接口位于 docker_api.py（GET /api/docker/networks、
POST /api/docker/networks/{name}/remove），此处不再重复实现；前端
DockerVolumesWindow 的「网络」标签直接调用 dockerApi.networks()。
"""

import asyncio

from fastapi import APIRouter, HTTPException

from app.routers.docker_api import (
    _run,
    _find_podman,
    _podman_json,
    _safe_docker_ref,
    _clean_reason,
    get_backend,
)

router = APIRouter()


# podman 不同版本的 volume ls --format json 字段大小写不一（Name/name），
# 统一取兼容取值，避免版本差异导致前端空字段。
def _vol_get(vol: dict, *keys, default=""):
    for k in keys:
        v = vol.get(k)
        if v:
            return v
    return default


# ------------------------------------------------------------
# 列出数据卷（路由根路径，前端 dockerApi.volumes() 请求 /api/dockervolumes）
# ------------------------------------------------------------
@router.get("")
async def volumes():
    """列出容器数据卷（CLI/SDK 双后端）。"""
    return await asyncio.to_thread(_volumes_sync)


def _volumes_sync():
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        try:
            arr = _podman_json(["volume", "ls", "--format", "json"])
            result = []
            for v in arr:
                result.append({
                    "name": _vol_get(v, "Name", "name"),
                    "driver": _vol_get(v, "Driver", "driver"),
                    "mountpoint": _vol_get(v, "Mountpoint", "mountpoint"),
                })
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))
    try:
        result = []
        for vol in client.volumes.list():
            attrs = vol.attrs or {}
            result.append({
                "name": attrs.get("Name", ""),
                "driver": attrs.get("Driver", ""),
                "mountpoint": attrs.get("Mountpoint", ""),
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))


# ------------------------------------------------------------
# 删除数据卷（强制）
# ------------------------------------------------------------
@router.post("/{name}/remove")
async def remove_volume(name: str):
    """删除数据卷（-f 强制，使用中的数据卷会报错）。"""
    return await asyncio.to_thread(_remove_volume_sync, name)


def _remove_volume_sync(name: str):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        name = _safe_docker_ref(name, "数据卷标识")
        rc, out, err = _run(_find_podman() + ["volume", "rm", "-f", name], timeout=60)
        if rc != 0:
            raise HTTPException(status_code=500, detail=err.strip() or "删除数据卷失败")
        return {"ok": True}
    try:
        vol = client.volumes.get(name)
        vol.remove(force=True)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))


# ------------------------------------------------------------
# 数据卷详细信息（挂载点 / 使用信息）
# ------------------------------------------------------------
@router.get("/{name}/inspect")
async def volume_inspect(name: str):
    """返回数据卷的完整属性（挂载点、驱动、使用状态等）。"""
    return await asyncio.to_thread(_volume_inspect_sync, name)


def _volume_inspect_sync(name: str):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        name = _safe_docker_ref(name, "数据卷标识")
        try:
            arr = _podman_json(["volume", "inspect", name])
            if not arr:
                raise HTTPException(status_code=404, detail="数据卷不存在")
            return arr[0]
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))
    try:
        vol = client.volumes.get(name)
        return vol.attrs
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))
