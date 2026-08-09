from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
import shutil
import platform
from typing import Optional

router = APIRouter()


def _safe_path(path: str) -> str:
    if not path:
        path = os.path.expanduser("~")
    path = os.path.abspath(path)
    return path


@router.get("/list")
async def list_dir(path: Optional[str] = None):
    target = _safe_path(path or "")
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Path not found")
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail="Not a directory")
    items = []
    try:
        for name in os.listdir(target):
            full = os.path.join(target, name)
            try:
                st = os.stat(full)
                items.append(
                    {
                        "name": name,
                        "path": full,
                        "is_dir": os.path.isdir(full),
                        "size": st.st_size,
                        "modified": st.st_mtime,
                    }
                )
            except (PermissionError, OSError):
                items.append(
                    {
                        "name": name,
                        "path": full,
                        "is_dir": os.path.isdir(full),
                        "size": 0,
                        "modified": 0,
                    }
                )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    parent = os.path.dirname(target)
    if parent == target:
        parent = None
    return {"path": target, "parent": parent, "items": items}


@router.get("/roots")
async def roots():
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
    target = _safe_path(path)
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="File not found")
    if os.path.getsize(target) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (>2MB)")
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            return {"path": target, "content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class WriteRequest(BaseModel):
    path: str
    content: str


@router.post("/write")
async def write_file(req: WriteRequest):
    target = _safe_path(req.path)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class DeleteRequest(BaseModel):
    path: str


@router.post("/delete")
async def delete_path(req: DeleteRequest):
    target = _safe_path(req.path)
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MkdirRequest(BaseModel):
    path: str


@router.post("/mkdir")
async def mkdir(req: MkdirRequest):
    target = _safe_path(req.path)
    try:
        os.makedirs(target, exist_ok=True)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class RenameRequest(BaseModel):
    src: str
    dst: str


@router.post("/rename")
async def rename(req: RenameRequest):
    src = _safe_path(req.src)
    dst = _safe_path(req.dst)
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        os.rename(src, dst)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download")
async def download(path: str):
    target = _safe_path(path)
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=os.path.basename(target))


@router.post("/upload")
async def upload(path: str = Form(...), file: UploadFile = File(...)):
    target_dir = _safe_path(path)
    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="Target directory not found")
    target = os.path.join(target_dir, file.filename)
    try:
        with open(target, "wb") as f:
            shutil.copyfileobj(file.file, f)
        return {"ok": True, "path": target}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ChmodRequest(BaseModel):
    path: str
    mode: int  # e.g. 0o755 -> 493


@router.post("/chmod")
async def chmod(req: ChmodRequest):
    target = _safe_path(req.path)
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        os.chmod(target, req.mode)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CopyRequest(BaseModel):
    src: str
    dst: str


@router.post("/copy")
async def copy_path(req: CopyRequest):
    src = _safe_path(req.src)
    dst = _safe_path(req.dst)
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CompressRequest(BaseModel):
    paths: list[str]
    archive: str
    fmt: Optional[str] = "zip"  # zip, tar, tar.gz


@router.post("/compress")
async def compress(req: CompressRequest):
    archive = _safe_path(req.archive)
    paths = [_safe_path(p) for p in req.paths]
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
        return {"ok": True, "archive": archive}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExtractRequest(BaseModel):
    archive: str
    dest: str


@router.post("/extract")
async def extract(req: ExtractRequest):
    archive = _safe_path(req.archive)
    dest = _safe_path(req.dest)
    if not os.path.isfile(archive):
        raise HTTPException(status_code=404, detail="Archive not found")
    os.makedirs(dest, exist_ok=True)
    try:
        if archive.endswith(".zip"):
            import zipfile

            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(dest)
        else:
            import tarfile

            with tarfile.open(archive, "r:*") as tf:
                tf.extractall(dest)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
