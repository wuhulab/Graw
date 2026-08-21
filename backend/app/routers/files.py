# -*- coding: utf-8 -*-
"""
files.py - 文件管理路由

支持在"本机直接运行"与"Docker /host 挂载"两种模式下管理宿主机文件。
通过 hostfs 适配层完成宿主机路径到容器内实际路径的映射（见 app/hostfs.py）。
对外展示的路径始终是宿主机视角（如 /、/etc），实际 I/O 操作在映射后的路径上进行。
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi import Depends, Request
from pydantic import BaseModel
import os
import logging
import shutil
import platform
from typing import Optional

from app import node_manager
from app.auth import get_current_user, get_client_ip
from app import auditlog

logger = logging.getLogger("graw.files")

router = APIRouter()

# 路径映射统一走当前主机感知的适配层：本地节点与 hostfs 行为一致，
# 远程节点返回远程绝对路径（远程文件 I/O 由上层 node_manager 原语执行）。
host_path = node_manager.host_path
unhost_path = node_manager.unhost_path

# 面板数据目录：包含 secret.key / users.json 等敏感文件，禁止通过文件管理访问
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)


def _is_forbidden(host_view: str) -> bool:
    """判断宿主机视角路径是否位于面板数据目录（data/）内。

    Windows 加固（第六轮审计修复）：
    - 比较前统一 normcase：否则 S:\\GRaw\\BACKEND\\DATA 这类大小写
      变体可绕过包含性判断（isfile/open 均大小写不敏感，文件仍可读）；
    - \\?\\ 设备命名空间前缀已在 _safe_path 入口拒绝；跨盘符路径
      （commonpath 抛 ValueError）必然不在 data 目录内，安全放行。
    """
    try:
        p = os.path.normcase(os.path.normpath(host_view))
        data = os.path.normcase(DATA_DIR)
        return os.path.commonpath([p, data]) == data
    except ValueError:
        # 跨盘符 / UNC：与本地 data 目录不可能同根，必然不在其内
        return False


def _safe_path(path: str) -> str:
    """规范化宿主机视角路径（绝对化），并拦截面板数据目录。"""
    if not path:
        path = "/"
    # 拒绝 Windows 设备命名空间前缀（\\?\ 与 \\.）：此类路径绕过 Win32
    # 路径规范化，且盘符解析差异会使 commonpath 抛 ValueError，导致下方
    # data 目录拦截 fail-open（第六轮审计实测可借此读取 secret.key）
    if path.startswith("\\\\?\\") or path.startswith("\\\\.\\"):
        raise HTTPException(status_code=400, detail="非法路径（不支持设备命名空间路径）")
    sp = os.path.abspath(path)
    if _is_forbidden(sp):
        raise HTTPException(status_code=403, detail="无权访问面板数据目录")
    return sp


def _is_within(base: str, target: str) -> bool:
    """判断 target 是否位于 base 目录之内（用于防 Zip Slip 路径穿越）。"""
    try:
        return os.path.commonpath([os.path.abspath(base), os.path.abspath(target)]) == os.path.abspath(base)
    except ValueError:
        return False


@router.get("/list")
async def list_dir(path: Optional[str] = None):
    # sp: 宿主机视角路径；real: 容器内实际访问路径
    sp = _safe_path(path or "/")
    real = host_path(sp)
    if not node_manager.exists(real):
        raise HTTPException(status_code=404, detail="Path not found")
    if not node_manager.isdir(real):
        raise HTTPException(status_code=400, detail="Not a directory")
    items = []
    try:
        for name in node_manager.listdir(real):
            full = real if real.endswith("/") else real
            full = full + "/" + name
            try:
                # 本地节点额外取修改时间；远程节点仅返回字节数（远程无便捷 mtime）
                mtime = 0
                if not node_manager.is_remote():
                    try:
                        mtime = os.stat(os.path.join(real, name)).st_mtime
                    except (PermissionError, OSError):
                        mtime = 0
                items.append(
                    {
                        "name": name,
                        "path": unhost_path(full),
                        "is_dir": node_manager.isdir(full),
                        "size": node_manager.getsize(full),
                        "modified": mtime,
                    }
                )
            except Exception:
                items.append(
                    {
                        "name": name,
                        "path": unhost_path(full),
                        "is_dir": node_manager.isdir(full),
                        "size": 0,
                        "modified": 0,
                    }
                )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    # 父目录：返回宿主机视角路径（前端据此回退导航）；根目录返回 None
    rp = real.rstrip("/")
    if not rp:
        parent = None
    else:
        parent = os.path.dirname(rp) or "/"
        parent = unhost_path(parent)
    return {"path": sp, "parent": parent, "items": items}


@router.get("/roots")
async def roots():
    # 宿主机根目录始终以 "/" 展示；容器模式下映射到 HOST_ROOT 挂载点
    if platform.system() == "Windows":
        import string
        from ctypes import windll

        drives = []
        bitmask = windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drives.append(f"{letter}:\\")
            bitmask >>= 1
        return {"roots": drives}
    return {"roots": ["/"]}


@router.get("/read")
async def read_file(path: str):
    real = host_path(_safe_path(path))
    if not node_manager.isfile(real):
        raise HTTPException(status_code=404, detail="File not found")
    if node_manager.getsize(real) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (>2MB)")
    try:
        return {"path": _safe_path(path), "content": node_manager.read_text(real)}
    except Exception as e:
        logger.warning("读取文件失败: %s", e)
        raise HTTPException(status_code=500, detail="读取文件失败")


class WriteRequest(BaseModel):
    path: str
    content: str


@router.post("/write")
async def write_file(
    req: WriteRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    real = host_path(_safe_path(req.path))
    try:
        node_manager.write_text(real, req.content)
        auditlog.record(
            "写文件", user["username"], get_client_ip(request), _safe_path(req.path)
        )
        return {"ok": True}
    except Exception as e:
        logger.warning("写入文件失败: %s", e)
        raise HTTPException(status_code=500, detail="写入文件失败")


class DeleteRequest(BaseModel):
    path: str


@router.post("/delete")
async def delete_path(
    req: DeleteRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    safe = _safe_path(req.path)
    real = host_path(safe)
    if not node_manager.exists(real):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        node_manager.remove(real)
        auditlog.record("删除", user["username"], get_client_ip(request), safe)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MkdirRequest(BaseModel):
    path: str


@router.post("/mkdir")
async def mkdir(
    req: MkdirRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    safe = _safe_path(req.path)
    real = host_path(safe)
    try:
        if node_manager.is_remote():
            node_manager.host_shell(f"mkdir -p {real}", timeout=30)
        else:
            os.makedirs(real, exist_ok=True)
        auditlog.record("新建目录", user["username"], get_client_ip(request), safe)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RenameRequest(BaseModel):
    src: str
    dst: str


@router.post("/rename")
async def rename(
    req: RenameRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    src = host_path(_safe_path(req.src))
    dst = host_path(_safe_path(req.dst))
    if not node_manager.exists(src):
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        if node_manager.is_remote():
            node_manager.host_shell(f"mv {src} {dst}", timeout=30)
        else:
            os.rename(src, dst)
        auditlog.record("重命名", user["username"], get_client_ip(request), f"{req.src} -> {req.dst}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download")
async def download(path: str):
    real = host_path(_safe_path(path))
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(real, filename=os.path.basename(real))


@router.post("/upload")
async def upload(
    request: Request,
    user: dict = Depends(get_current_user),
    path: str = Form(...),
    file: UploadFile = File(...),
):
    target_dir = host_path(_safe_path(path))
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="Target directory not found")
    # 文件名消毒：去除路径分隔符仅保留基本名，防止 ../ 路径穿越写至任意目录
    raw_name = (file.filename or "").replace("\\", "/")
    safe_name = os.path.basename(raw_name).strip()
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="非法文件名")
    target = os.path.join(target_dir, safe_name)
    try:
        with open(target, "wb") as f:
            shutil.copyfileobj(file.file, f)
        auditlog.record("上传文件", user["username"], get_client_ip(request), unhost_path(target))
        return {"ok": True, "path": unhost_path(target)}
    except Exception as e:
        logger.warning("上传失败: %s", e)
        raise HTTPException(status_code=500, detail="上传失败")


class ChmodRequest(BaseModel):
    path: str
    mode: int  # e.g. 0o755 -> 493


@router.post("/chmod")
async def chmod(
    req: ChmodRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    safe = _safe_path(req.path)
    real = host_path(safe)
    if not os.path.exists(real):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        os.chmod(real, req.mode)
        auditlog.record("修改权限", user["username"], get_client_ip(request), f"{safe} -> {oct(req.mode)}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CopyRequest(BaseModel):
    src: str
    dst: str


@router.post("/copy")
async def copy_path(
    req: CopyRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    src = host_path(_safe_path(req.src))
    dst = host_path(_safe_path(req.dst))
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        auditlog.record("复制", user["username"], get_client_ip(request), f"{req.src} -> {req.dst}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CompressRequest(BaseModel):
    paths: list[str]
    archive: str
    fmt: Optional[str] = "zip"  # zip, tar, tar.gz


@router.post("/compress")
async def compress(
    req: CompressRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    archive = host_path(_safe_path(req.archive))
    paths = [host_path(_safe_path(p)) for p in req.paths]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise HTTPException(status_code=404, detail=f"Paths not found: {missing}")
    try:
        if req.fmt == "zip":
            import zipfile

            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in paths:
                    if os.path.isdir(p):
                        for root, dirs, files in os.walk(p):
                            for f in files:
                                fp = os.path.join(root, f)
                                zf.write(fp, os.path.relpath(fp, os.path.dirname(p)))
                    else:
                        zf.write(p, os.path.basename(p))
        else:
            import tarfile

            mode = "w:gz" if req.fmt == "tar.gz" else "w"
            with tarfile.open(archive, mode) as tf:
                for p in paths:
                    tf.add(p, arcname=os.path.basename(p))
        auditlog.record("压缩", user["username"], get_client_ip(request), f"{[req.archive]}: {req.paths}")
        return {"ok": True, "archive": _safe_path(req.archive)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExtractRequest(BaseModel):
    archive: str
    dest: str


@router.post("/extract")
async def extract(
    req: ExtractRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    archive = host_path(_safe_path(req.archive))
    dest = host_path(_safe_path(req.dest))
    if not os.path.isfile(archive):
        raise HTTPException(status_code=404, detail="Archive not found")
    os.makedirs(dest, exist_ok=True)
    try:
        if archive.endswith(".zip"):
            import zipfile

            with zipfile.ZipFile(archive, "r") as zf:
                # 防 Zip Slip：拒绝包含 ../ 等越界路径的压缩包
                for info in zf.infolist():
                    if not _is_within(dest, os.path.join(dest, info.filename)):
                        raise HTTPException(status_code=400, detail="压缩包包含非法路径（Zip Slip）")
                zf.extractall(dest)
        else:
            import tarfile

            with tarfile.open(archive, "r:*") as tf:
                for member in tf.getmembers():
                    if not _is_within(dest, os.path.join(dest, member.name)):
                        raise HTTPException(status_code=400, detail="压缩包包含非法路径（Zip Slip）")
                    # 防 Tar Slip：符号/硬链接成员的指向目标也必须落在解压目录
                    # 内——否则可先释放链接 evil->/etc，再经 evil/passwd 写出
                    # 任意文件，仅校验成员名拦不住这种二次穿越。
                    if member.issym() or member.islnk():
                        link = member.linkname
                        if os.path.isabs(link) or not _is_within(
                            dest, os.path.join(dest, os.path.normpath(link))
                        ):
                            raise HTTPException(
                                status_code=400, detail="压缩包包含越界链接（Tar Slip）"
                            )
                    # 拒绝设备文件 / 套接字等特殊成员
                    if member.isdev():
                        raise HTTPException(status_code=400, detail="压缩包包含特殊文件，已拒绝")
                try:
                    # Python 3.9.17+ / 3.12+ 提供 data 过滤器（拒绝绝对路径、
                    # 越界链接、设备文件），作为第二道防线；旧版本忽略该参数
                    tf.extractall(dest, filter="data")
                except TypeError:
                    tf.extractall(dest)
        auditlog.record("解压", user["username"], get_client_ip(request), f"{req.archive} -> {req.dest}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("解压失败: %s", e)
        raise HTTPException(status_code=500, detail="解压失败")
