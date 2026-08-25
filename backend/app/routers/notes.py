from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import os
import json

from app.auth import require_admin, get_current_user, get_client_ip
from app import auditlog

router = APIRouter()

NOTES_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "notes.json")
os.makedirs(os.path.dirname(NOTES_FILE), exist_ok=True)

# 备忘录内容上限：该接口对普通登录用户开放（只读），若不限长，
# 恶意用户可循环写入超大内容撑爆 data 分区（磁盘 DoS）。
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
async def update_notes(
    req: NoteUpdate,
    request: Request,
    _: dict = Depends(require_admin),
):
    """更新共享备忘录（桌面备忘录卡片展示给所有登录用户）。

    安全修复（第十二轮审计，Medium）：此接口此前仅挂 PROTECTED（任意登录
    用户可写）。备忘录是 data/notes.json 的「全局单例」，任何低权限用户都
    可覆盖/清空管理员桌面看到的同一份内容 —— 构成跨用户数据篡改与内容
    投毒，且无审计记录。现改为写操作仅限管理员（读保持登录即可），
    与面板「写操作一律管理员」的权限模型对齐，并补充审计日志。
    """
    # 内容大小限制：防无限写入撑爆磁盘
    if len(req.content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise HTTPException(
            status_code=413, detail="备忘录内容过大（最大 256KB），请精简后保存"
        )
    data = {"content": req.content}
    try:
        _save(data)
        auditlog.record("更新备忘录", _["username"], get_client_ip(request))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
