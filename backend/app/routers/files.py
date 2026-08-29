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
import asyncio
import logging
import time
import shutil
import shlex
import posixpath
import platform
import subprocess
from typing import Optional

from app import node_manager
from app import trash
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
    r"""规范化宿主机视角路径（绝对化），并拦截面板数据目录。

    多机：当控制器是 Windows 而当前管理主机是远端 Linux 时，`os.path.abspath`
    会把 `/` / `/etc` 规范成 `S:\` / `S:\etc`（用控制器本地盘符规则），
    这些 Windows 路径经 host_path 原样传给远端 SSH，远端查无此路径 → 404。
    因此远端节点下改用 POSIX 规范化（保持 `/` 前缀并归一 `.`/`..`）。
    """
    if not path:
        path = "/"
    # 拒绝 Windows 设备命名空间前缀（\\?\ 与 \\.）：此类路径绕过 Win32
    # 路径规范化，且盘符解析差异会使 commonpath 抛 ValueError，导致下方
    # data 目录拦截 fail-open（第六轮审计实测可借此读取 secret.key）
    if path.startswith("\\\\?\\") or path.startswith("\\\\.\\"):
        raise HTTPException(status_code=400, detail="非法路径（不支持设备命名空间路径）")

    tmp = (path or "").replace("\\", "/")
    if node_manager.is_remote():
        # 远端节点：POSIX 绝对路径规范化（/ 开头，归并 . / ..）
        if tmp.startswith("/"):
            sp = posixpath.abspath(tmp)
        else:
            sp = posixpath.abspath("/" + tmp.lstrip("/"))
        if _is_forbidden(sp):
            raise HTTPException(status_code=403, detail="无权访问面板数据目录")
        return sp

    sp = os.path.realpath(os.path.abspath(path))
    if _is_forbidden(sp):
        raise HTTPException(status_code=403, detail="无权访问面板数据目录")
    return sp


def _is_within(base: str, target: str) -> bool:
    """判断 target 是否位于 base 目录之内（用于防 Zip Slip 路径穿越）。"""
    try:
        return os.path.commonpath([os.path.abspath(base), os.path.abspath(target)]) == os.path.abspath(base)
    except ValueError:
        return False


def _files_error(e: Exception, fallback: str = "操作失败") -> HTTPException:
    """把文件操作异常转换为带具体原因、语义正确的 HTTPException。

    此前多处直接返回 str(e)（如 "PermissionError: [Errno 13] Permission denied:
    '/xxx'"）或笼统文案（如 "读取文件失败"），前者原始、后者丢失根因。
    此处统一分类：
    - PermissionError        -> 403 权限不足
    - FileNotFoundError      -> 404 不存在
    - subprocess.TimeoutExpired -> 504 操作超时（远程 SSH 节点响应超时）
    - 其余 OSError / 通用异常 -> 500，附带具体原因（面板为管理员工具，可读原因
      比隐藏细节更有助于排查；日志中仍会完整记录异常栈）
    """
    if isinstance(e, HTTPException):
        return e
    msg = (str(e) or "").strip() or fallback
    if isinstance(e, PermissionError):
        return HTTPException(status_code=403, detail=f"权限不足：{msg}")
    if isinstance(e, FileNotFoundError):
        return HTTPException(status_code=404, detail=f"文件或目录不存在：{msg}")
    if isinstance(e, subprocess.TimeoutExpired):
        return HTTPException(status_code=504, detail=f"操作超时：远程节点在限定时间内未完成响应（{msg}）")
    if isinstance(e, OSError):
        return HTTPException(status_code=500, detail=f"文件系统错误：{msg}")
    return HTTPException(status_code=500, detail=f"{fallback}：{msg}")


@router.get("/list")
async def list_dir(path: Optional[str] = None):
    # sp: 宿主机视角路径；real: 容器内实际访问路径
    sp = _safe_path(path or "/")
    real = host_path(sp)
    # 单次调用取回全部条目（含 is_dir/size）：本地走 os.scandir，远端走单次 SSH ls -l，
    # 避免旧实现对每个条目分别发一次 isdir/getsize（大目录会按条目数累加 SSH 连接）。
    # exists/isdir 在远程模式下会发 SSH 探测，可能抛 TimeoutExpired 或 I/O 错误，
    # 一并纳入统一异常分类，避免漏成 FastAPI 默认 500（前端只能看到无 detail 的报错）。
    try:
        # 传给 node_manager 的存在/枚举原语必须用"宿主机视角"路径 sp：这些原语在
        # 本地容器部署下会再自行 host_path 一次（补 /host 前缀）。若把上方已映射
        # 的 real 传进去会得到 /host/host 双前缀，根目录就 404 Path not found；
        # 远程节点因 host_path 原样返回，sp 与 real 等价，统一传 sp 两种场景皆正确。
        # 注意：本地 os.scandir / 远端 SSH 枚举均为阻塞 IO，放线程池避免卡事件循环。
        if not await asyncio.to_thread(node_manager.exists, sp):
            raise HTTPException(status_code=404, detail="Path not found")
        if not await asyncio.to_thread(node_manager.isdir, sp):
            raise HTTPException(status_code=400, detail="Not a directory")
        entries = await asyncio.to_thread(node_manager.listdir_detail, sp)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("列出目录失败 %s: %s", sp, e)
        raise _files_error(e, "列出目录失败")
    base = real if real.endswith("/") else real + "/"
    # 回收站目录对文件管理隐藏：仅过滤顶层同名条目（trash_root 实际路径归一化比较）
    trash_real = None
    try:
        trash_real = os.path.normpath(node_manager.host_path(trash.trash_root()))
    except Exception:
        trash_real = None
    items = []
    for en in entries:
        full = base + en["name"]
        # 跳过回收站目录本身（其内容也不应出现在文件管理中）
        if trash_real is not None and os.path.normpath(full) == trash_real:
            continue
        items.append({
            "name": en["name"],
            "path": unhost_path(full),
            "is_dir": en["is_dir"],
            "size": en["size"],
            "modified": en["modified"],
        })
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
    sp = _safe_path(path)
    # 原语统一传宿主视角 sp，由 node_manager 内部完成 /host 映射（见 list_dir 注释）
    # 远端 isfile/getsize/read_text 均为阻塞 SSH 调用，放线程池避免卡事件循环。
    if not await asyncio.to_thread(node_manager.isfile, sp):
        raise HTTPException(status_code=404, detail="File not found")
    if await asyncio.to_thread(node_manager.getsize, sp) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (>2MB)")
    try:
        content = await asyncio.to_thread(node_manager.read_text, sp)
        return {"path": sp, "content": content}
    except Exception as e:
        logger.warning("读取文件失败: %s", e)
        raise _files_error(e, "读取文件失败")


class WriteRequest(BaseModel):
    path: str
    content: str


@router.post("/write")
async def write_file(
    req: WriteRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    sp = _safe_path(req.path)
    try:
        # 写文件用宿主视角 sp，由 node_manager 内部完成 /host 映射（见 list_dir 注释）
        # 本地 / 远端均为阻塞文件 IO / SSH，放线程池避免卡事件循环。
        await asyncio.to_thread(node_manager.write_text, sp, req.content)
        auditlog.record(
            "写文件", user["username"], get_client_ip(request), sp
        )
        return {"ok": True}
    except Exception as e:
        logger.warning("写入文件失败: %s", e)
        raise _files_error(e, "写入文件失败")


class DeleteRequest(BaseModel):
    path: str


@router.post("/delete")
async def delete_path(
    req: DeleteRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    safe = _safe_path(req.path)
    # 原语统一传宿主视角 safe，由 node_manager 内部完成 /host 映射（见 list_dir 注释）
    # 远端 exists / remove / move_to_trash 均为阻塞 SSH，放线程池避免卡事件循环。
    if not await asyncio.to_thread(node_manager.exists, safe):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        # 回收站已启用时，删除 = 移入回收站（可恢复）；回收站目录内的文件
        # 直接物理删除（防递归回收）。移入失败时宁可报错，绝不静默物理删除。
        if trash.is_enabled() and not trash.path_in_trash(safe):
            item = await asyncio.to_thread(trash.move_to_trash, safe, user["username"])
            auditlog.record(
                "删除(回收站)", user["username"], get_client_ip(request),
                f"{safe} -> {item['trash']}",
            )
        else:
            await asyncio.to_thread(node_manager.remove, safe)
            auditlog.record("删除", user["username"], get_client_ip(request), safe)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("删除失败 %s: %s", repr(safe), e)
        raise _files_error(e, "删除失败")


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
        # 本地 os.makedirs / 远端 mkdir 均为阻塞 IO，放线程池避免卡事件循环
        if node_manager.is_remote():
            # 远程节点经 /bin/sh -c 执行：路径必须转义，防 shell 注入
            # （对比 node_manager.host_cmd 的远程 shlex.quote 语义）
            await asyncio.to_thread(
                node_manager.host_shell,
                f"mkdir -p {shlex.quote(real)}",
                timeout=30,
            )
        else:
            await asyncio.to_thread(os.makedirs, real, exist_ok=True)
        auditlog.record("新建目录", user["username"], get_client_ip(request), safe)
        return {"ok": True}
    except Exception as e:
        logger.warning("新建目录失败 %s: %s", safe, e)
        raise _files_error(e, "新建目录失败")


class RenameRequest(BaseModel):
    src: str
    dst: str


@router.post("/rename")
async def rename(
    req: RenameRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    src_sp = _safe_path(req.src)
    dst_sp = _safe_path(req.dst)
    src = host_path(src_sp)
    dst = host_path(dst_sp)
    # exists 判断传宿主视角 src_sp（本地资源内部自动 /host 映射）；
    # 而 os.rename / 远程 host_shell 需要容器真实路径 src/dst（二者保持一致）
    # 远端 exists / rename 均为阻塞 SSH，放线程池避免卡事件循环。
    if not await asyncio.to_thread(node_manager.exists, src_sp):
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        if node_manager.is_remote():
            # 远程节点经 /bin/sh -c 执行：src/dst 均须转义，防 shell 注入
            await asyncio.to_thread(
                node_manager.host_shell,
                f"mv {shlex.quote(src)} {shlex.quote(dst)}",
                timeout=30,
            )
        else:
            await asyncio.to_thread(os.rename, src, dst)
        auditlog.record("重命名", user["username"], get_client_ip(request), f"{req.src} -> {req.dst}")
        return {"ok": True}
    except Exception as e:
        logger.warning("重命名失败 %s -> %s: %s", req.src, req.dst, e)
        raise _files_error(e, "重命名失败")


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
    relpath: str = Form(""),
):
    # path 经 _safe_path：绝对化 + 面板数据目录拦截 + 设备命名空间拒绝
    target_dir = host_path(_safe_path(path))  # lgtm[py/path-injection]
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="Target directory not found")
    # 文件名消毒：去除路径分隔符仅保留基本名，防止 ../ 路径穿越写至任意目录
    raw_name = (file.filename or "").replace("\\", "/")
    safe_name = os.path.basename(raw_name).strip()
    if not safe_name or safe_name in (".", ".."):
        raise HTTPException(status_code=400, detail="非法文件名")
    # relpath：拖拽文件夹递归上传时携带相对路径（如 "子目录/文件名.txt"）。
    # 逐段校验，拒绝空段 / "." / ".."（防穿越），再在目标目录下逐层建目录。
    rel = (relpath or "").strip().replace("\\", "/").strip("/")
    rel_dirs = []
    if rel:
        # 去掉末段（文件名），只取子目录层级
        parts = rel.split("/")
        parts = parts[:-1] if len(parts) > 1 else []
        for seg in parts:
            if seg in ("", ".", ".."):
                raise HTTPException(status_code=400, detail="非法路径")
            rel_dirs.append(seg)
    target = target_dir
    try:
        if rel_dirs:
            target = os.path.join(target_dir, *rel_dirs)
            await asyncio.to_thread(os.makedirs, target, exist_ok=True)
        target = os.path.join(target, safe_name)
        # 大文件写入为阻塞磁盘 IO，放线程池避免卡事件循环
        await asyncio.to_thread(_save_uploaded_file, target, file.file)
        auditlog.record("上传文件", user["username"], get_client_ip(request), unhost_path(target))
        return {"ok": True, "path": unhost_path(target)}
    except Exception as e:
        logger.warning("上传失败: %s", e)
        raise _files_error(e, "上传失败")


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
    if not await asyncio.to_thread(os.path.exists, real):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        await asyncio.to_thread(os.chmod, real, req.mode)
        auditlog.record("修改权限", user["username"], get_client_ip(request), f"{safe} -> {oct(req.mode)}")
        return {"ok": True}
    except Exception as e:
        logger.warning("修改权限失败 %s: %s", repr(safe), e)
        raise _files_error(e, "修改权限失败")


class CopyRequest(BaseModel):
    src: str
    dst: str


def _save_uploaded_file(target: str, src) -> None:
    """把上传文件流写入目标路径（阻塞磁盘 IO，供 asyncio.to_thread 调用）。"""
    with open(target, "wb") as f:
        shutil.copyfileobj(src, f)


def _copy_sync(src: str, dst: str) -> str:
    """复制文件/目录并自动去重目标名（阻塞 IO，供 asyncio.to_thread 调用）。"""
    dst = _unique_dst(dst)
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return dst


def _compress_worker(paths: list, archive: str, fmt: str) -> None:
    """执行压缩（阻塞 CPU/IO，供 asyncio.to_thread 调用）。"""
    if fmt == "zip":
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

        mode = "w:gz" if fmt == "tar.gz" else "w"
        with tarfile.open(archive, mode) as tf:
            for p in paths:
                tf.add(p, arcname=os.path.basename(p))


def _extract_worker(archive: str, dest: str) -> None:
    """解压压缩包到目标目录（阻塞 CPU/IO，供 asyncio.to_thread 调用）。

    安全校验（Zip Slip / Tar Slip）与 _is_within 检查保持在原逻辑，仅移动
    到线程池执行；非法压缩包抛 HTTPException(400) 由端点统一处理。
    """
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
                # 老 Python 降级路径：安全性已由上方对每个 member 的显式校验保证
                tf.extractall(dest)  # lgtm[py/tarslip]


def _unique_dst(dst: str) -> str:
    """目标已存在时自动生成不冲突的新目标（Windows 式「名称 (2).ext」）。"""
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(dst)
    for i in range(2, 1000):
        cand = f"{stem} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
    return f"{stem}-{int(time.time())}{ext}"


@router.post("/copy")
async def copy_path(
    req: CopyRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    src = host_path(_safe_path(req.src))
    dst = host_path(_safe_path(req.dst))
    if not await asyncio.to_thread(os.path.exists, src):
        raise HTTPException(status_code=404, detail="Source not found")
    # 防止把目录复制进自身（Windows 同样禁止：复制到子目录会无限递归）
    if await asyncio.to_thread(os.path.isdir, src):
        src_abs = os.path.abspath(src)
        dst_abs = os.path.abspath(dst)
        if dst_abs == src_abs or dst_abs.startswith(src_abs + os.sep):
            raise HTTPException(status_code=400, detail="不能把目录复制到其自身内部")
    try:
        # 复制为阻塞磁盘 IO（大目录 copytree 可能很慢），放线程池避免卡事件循环
        dst = await asyncio.to_thread(_copy_sync, src, dst)
        auditlog.record("复制", user["username"], get_client_ip(request), f"{req.src} -> {dst}")
        return {"ok": True, "dst": _safe_path(dst)}
    except Exception as e:
        logger.warning("复制失败 %s -> %s: %s", req.src, req.dst, e)
        raise _files_error(e, "复制失败")


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
    missing = [p for p in paths if not await asyncio.to_thread(os.path.exists, p)]
    if missing:
        raise HTTPException(status_code=404, detail=f"Paths not found: {missing}")
    try:
        # 压缩为阻塞 CPU/IO（大目录遍历 + deflate），放线程池避免卡事件循环
        await asyncio.to_thread(_compress_worker, paths, archive, req.fmt)
        auditlog.record("压缩", user["username"], get_client_ip(request), f"{[req.archive]}: {req.paths}")
        return {"ok": True, "archive": _safe_path(req.archive)}
    except Exception as e:
        logger.warning("压缩失败 %s: %s", repr(req.archive), e)
        raise _files_error(e, "压缩失败")


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
    if not await asyncio.to_thread(os.path.isfile, archive):
        raise HTTPException(status_code=404, detail="Archive not found")
    await asyncio.to_thread(os.makedirs, dest, exist_ok=True)
    try:
        # 解压为阻塞 CPU/IO（遍历 + 释放文件），放线程池避免卡事件循环；
        # Zip Slip / Tar Slip 安全校验在 _extract_worker 内保持，非法即抛 400
        await asyncio.to_thread(_extract_worker, archive, dest)
        auditlog.record("解压", user["username"], get_client_ip(request), f"{req.archive} -> {req.dest}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("解压失败: %s", e)
        raise _files_error(e, "解压失败")
