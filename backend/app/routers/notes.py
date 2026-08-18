from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os
import json

router = APIRouter()

NOTES_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "notes.json")
os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)

# 备忘录内容上限：该接口对普通登录用户开放（非管理员），
# 若不限长，恶意用户可循环写入超大内容撑爆 data 分区（磁盘 DoS）。
MAX_CONTENT_BYTES = 256 * 1024  # 256KB


def _load():
    if not os.path.exists(NOTES_FILE):
        return {"content": ""}
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"content": ""}


def _save(data):
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class NoteUpdate(BaseModel):
    content: str


@router.get("/")
async def get_notes():
    return _load()


@router.post("/")
async def update_notes(req: NoteUpdate):
    # 内容大小限制：备忘录对普通登录用户开放，防止无限写入撑爆磁盘
    if len(req.content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise HTTPException(
            status_code=413, detail="备忘录内容过大（最大 256KB），请精简后保存"
        )
    data = {"content": req.content}
    try:
        _save(data)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))