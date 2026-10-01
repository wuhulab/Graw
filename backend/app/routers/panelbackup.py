# -*- coding: utf-8 -*-
"""
panelbackup.py - Graw 面板自身备份路由

功能：
  1. 导出：把面板全部配置（backend/data/ 下所有文件，含 users.json / secret.key /
     各模块配置 json）打包成 tar.gz 归档，供下载保存或迁移到新服务器。
  2. 导入：上传归档并恢复——导入前自动把当前 data/ 备份到
     panelbackups/pre-import-*.tar.gz（可回滚），再覆盖恢复归档内容。
  3. 归档管理：列表 / 下载 / 删除。

安全说明：
  - 归档目录为 backend/data/panelbackups/，导出时自动排除自身与 *.tmp，避免嵌套。
  - 导入解压严格防 Zip Slip：逐成员校验绝对路径与 .. 越界，且全部文件
    必须落在临时目录之内；解压内容先校验再整体覆盖 data/。
  - 下载/删除按文件名白名单校验（防路径穿越）。
  - 注意：导出的归档包含 secret.key（JWT 签名密钥）等敏感信息，
    请提示用户妥善保管；导入后如需生效建议重启后端。

数据存储：
  backend/data/panelbackups/  ：导出与导入前备份的归档
"""
import io
import logging
import os
import re
import shutil
import tarfile
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

logger = logging.getLogger("graw.panelbackup")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
# 归档目录（位于 data/ 内，导出时自动排除，避免嵌套）
BACKUP_DIR = os.path.join(DATA_DIR, "panelbackups")

# 归档文件名白名单：{时间戳}_{随机}.tar.gz
_ARCHIVE_RE = re.compile(r"^\d{8}_\d{6}_[A-Za-z0-9]{8}\.tar\.gz$")
# 预导入备份命名：pre-import-{时间戳}.tar.gz
_PRE_IMPORT_RE = re.compile(r"^pre-import-\d{8}_\d{6}\.tar\.gz$")

# 上传导入的大小上限（200MB），防止撑爆磁盘
MAX_IMPORT_BYTES = 200 * 1024 * 1024

# 解压炸弹防护（第十二轮审计修复，Medium）：
#   此前仅限制压缩后体积，解压时无总量/成员数/单成员大小上限——
#   攻击者上传高压缩比 tar.gz（200MB 内可含数十 GB 重复数据），
#   导入即瞬间写满数据分区（磁盘耗尽 DoS）。
#   在 extractall 之前先对成员清单做三重复核：
#     - 单成员大小上限（tar 成员的 size 即展开后字节数，稀疏文件同口径）
#     - 成员总数上限
#     - 解压后总字节数上限（含目录项元数据开销的近似值：成员 size 之和）
MAX_IMPORT_MEMBERS = 2000
MAX_IMPORT_MEMBER_BYTES = 256 * 1024 * 1024   # 单文件 256MB
MAX_IMPORT_EXPANDED_BYTES = 1024 * 1024 * 1024  # 解压总量 1GB

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def _collect_files(root: str, skip_prefixes: tuple) -> list:
    """递归收集 root 下待打包的文件（排除 skip_prefixes 前缀与 *.tmp）。"""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("panelbackups",)]
        for fn in filenames:
            if fn.endswith(".tmp"):
                continue
            full = os.path.join(dirpath, fn)
            if any(full.startswith(p) for p in skip_prefixes):
                continue
            files.append(full)
    return files


def _export_sync() -> dict:
    """执行导出：打包 data/ 下所有配置文件到 panelbackups/，返回归档信息。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{ts}_{uuid.uuid4().hex[:8]}.tar.gz"
    dest = os.path.join(BACKUP_DIR, name)
    files = _collect_files(DATA_DIR, (BACKUP_DIR,))
    if not files:
        raise HTTPException(status_code=400, detail="没有可导出的配置")
    with tarfile.open(dest, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for f in files:
            arc = os.path.relpath(f, DATA_DIR)
            tf.add(f, arcname=arc)
    size = os.path.getsize(dest)
    logger.info("面板配置导出完成：%s（%d 个文件，%.1f KB）", name, len(files), size / 1024)
    return {"name": name, "size": size, "file_count": len(files), "created_at": datetime.now().isoformat()}


def _list_archives_sync() -> list:
    """列出归档目录中的导出/预导入备份（按时间倒序）。"""
    if not os.path.isdir(BACKUP_DIR):
        return []
    items = []
    try:
        for fn in os.listdir(BACKUP_DIR):
            if not (_ARCHIVE_RE.match(fn) or _PRE_IMPORT_RE.match(fn)):
                continue
            fp = os.path.join(BACKUP_DIR, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            items.append({
                "name": fn,
                "size": st.st_size,
                "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "is_pre_import": bool(_PRE_IMPORT_RE.match(fn)),
            })
    except OSError:
        return []
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


def _delete_archive_sync(name: str) -> None:
    """删除归档（文件名白名单校验防穿越）。"""
    if not (_ARCHIVE_RE.match(name) or _PRE_IMPORT_RE.match(name)):
        raise HTTPException(status_code=400, detail="归档文件名非法")
    # 文件名已由 _ARCHIVE_RE/_PRE_IMPORT_RE 白名单校验，拼入 BACKUP_DIR 不会越界
    fp = os.path.join(BACKUP_DIR, name)  # lgtm[py/path-injection]
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="归档不存在")
    os.remove(fp)
    logger.info("删除面板配置归档：%s", name)


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
def _sanitize_member(member: tarfile.TarInfo, root: str) -> str:
    """校验并规范化归档成员的目标路径，防 Zip Slip；越界抛异常。"""
    name = member.name.replace("/", os.sep)
    if os.path.isabs(name):
        raise HTTPException(status_code=400, detail="归档内包含绝对路径，已中止导入")
    joined = os.path.normpath(os.path.join(root, name))
    root_norm = os.path.normcase(os.path.abspath(root))
    joined_norm = os.path.normcase(joined)
    if joined_norm != root_norm and not joined_norm.startswith(root_norm + os.sep):
        raise HTTPException(status_code=400, detail="归档内包含越界路径，已中止导入")
    # 拒绝特殊文件类型（设备/套接字/链接），仅允许普通文件与目录
    if not member.isreg() and not member.isdir():
        raise HTTPException(status_code=400, detail=f"归档内包含不支持的文件类型：{member.name}")
    return joined


def _import_sync(content: bytes) -> dict:
    """执行导入：校验并解压归档，备份现有配置后覆盖恢复。

    流程：
      1. 解压到临时目录（逐成员防穿越校验）；
      2. 把当前 data/ 备份到 panelbackups/pre-import-{ts}.tar.gz（可回滚）；
      3. 清空 data/（保留 panelbackups 目录本身），把归档内容移入 data/。
    """
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="归档过大（超过 200MB）")
    # 1. 解压校验到临时目录
    tmp_root = os.path.join(BACKUP_DIR, f".import_{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp_root, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tf:
            members = tf.getmembers()
            # 解压炸弹防护：先对成员清单做总量/数量/单成员大小核验，
            # 任何一项超限立即中止（此时尚未向磁盘写入任何解压内容）。
            if len(members) > MAX_IMPORT_MEMBERS:
                raise HTTPException(
                    status_code=400,
                    detail=f"归档成员过多（{len(members)} > {MAX_IMPORT_MEMBERS}），已中止导入",
                )
            total_expanded = 0
            for m in members:
                _sanitize_member(m, tmp_root)
                if m.size > MAX_IMPORT_MEMBER_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"归档包含超大文件（{m.name} 展开 {m.size} 字节 > 上限），已中止导入",
                    )
                total_expanded += m.size
                if total_expanded > MAX_IMPORT_EXPANDED_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail="归档解压后总量超过 1GB 上限，已中止导入（疑似解压炸弹）",
                    )
            tf.extractall(tmp_root, members=members)
    except tarfile.TarError as e:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"归档解压失败：{e}")
    except HTTPException:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise

    # 统计将恢复的文件数
    file_count = 0
    for root, _dirs, files in os.walk(tmp_root):
        file_count += len(files)
    if file_count == 0:
        shutil.rmtree(tmp_root, ignore_errors=True)
        raise HTTPException(status_code=400, detail="归档为空或没有配置文件")

    # 2. 备份当前 data/（排除 panelbackups 自身）
    with _lock:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_name = f"pre-import-{ts}.tar.gz"
        pre_path = os.path.join(BACKUP_DIR, pre_name)
        cur_files = _collect_files(DATA_DIR, (BACKUP_DIR,))
        with tarfile.open(pre_path, "w:gz", format=tarfile.PAX_FORMAT) as tf:
            for f in cur_files:
                tf.add(f, arcname=os.path.relpath(f, DATA_DIR))

        # 3. 清空 data/（保留 panelbackups），把归档内容移入
        for entry in os.listdir(DATA_DIR):
            if entry == "panelbackups":
                continue
            p = os.path.join(DATA_DIR, entry)
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            except OSError as e:
                logger.warning("清理 data/ 失败 %s: %s", p, e)
        for root, _dirs, files in os.walk(tmp_root):
            rel_root = os.path.relpath(root, tmp_root)
            dest_root = DATA_DIR if rel_root == "." else os.path.join(DATA_DIR, rel_root)
            os.makedirs(dest_root, exist_ok=True)
            for fn in files:
                shutil.move(os.path.join(root, fn), os.path.join(dest_root, fn))

    shutil.rmtree(tmp_root, ignore_errors=True)
    logger.info("面板配置导入完成：恢复 %d 个文件（原配置已备份为 %s）", file_count, pre_name)
    return {"ok": True, "restored_files": file_count, "pre_backup": pre_name}


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@router.get("/list")
async def list_archives():
    """返回面板配置归档列表。"""
    import asyncio

    return {"archives": await asyncio.to_thread(_list_archives_sync)}


@router.post("/export")
async def export():
    """创建面板配置导出归档。"""
    import asyncio

    return await asyncio.to_thread(_export_sync)


@router.get("/download/{name}")
async def download(name: str):
    """下载指定归档。"""
    if not (_ARCHIVE_RE.match(name) or _PRE_IMPORT_RE.match(name)):
        raise HTTPException(status_code=400, detail="归档文件名非法")
    # 文件名已由 _ARCHIVE_RE/_PRE_IMPORT_RE 白名单校验，拼入 BACKUP_DIR 不会越界
    fp = os.path.join(BACKUP_DIR, name)  # lgtm[py/path-injection]
    if not os.path.isfile(fp):
        raise HTTPException(status_code=404, detail="归档不存在")
    return FileResponse(
        fp,
        media_type="application/gzip",
        filename=name,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.delete("/{name}")
async def delete(name: str):
    """删除指定归档。"""
    import asyncio

    await asyncio.to_thread(_delete_archive_sync, name)
    return {"ok": True}


@router.post("/import")
async def import_archive(file: UploadFile = File(...)):
    """上传并导入面板配置归档（导入前自动备份当前配置）。"""
    import asyncio

    if not file.filename or not file.filename.endswith(".tar.gz"):
        raise HTTPException(status_code=400, detail="仅支持 .tar.gz 归档")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="归档内容为空")
    return await asyncio.to_thread(_import_sync, content)
