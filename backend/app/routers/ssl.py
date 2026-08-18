import json
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from app.node_manager import host_path, host_cmd, host_which

# 证书域名白名单：字母/数字/中划线/点与通配符（*.example.com）
# 域名会以 argv 形式传给 certbot -d，也会拼入 /etc/letsencrypt/live/<domain>
# 路径；白名单校验可同时阻断「选项注入」（--standalone 等被当作域名）与
# 路径穿越（../ 形式的域名）。
_DOMAIN_RE = re.compile(r"^(\*\.)?([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SSL_DIR = os.path.join(DATA_DIR, "ssl")
SSL_FILE = os.path.join(DATA_DIR, "ssl.json")
os.makedirs(SSL_DIR, exist_ok=True)

# 上传证书/私钥大小上限（正常 PEM 证书 <10KB，1MB 已极宽松）；防磁盘 DoS
_CERT_MAX_BYTES = 1024 * 1024


def _reject_control_chars(value: str, what: str, max_len: int) -> str:
    """校验自由文本字段：非空、限长、拒绝 C0 控制字符（含空字节/换行）。

    这些值会持久化到 ssl.json 并在前端展示；name 还会流入
    ``certbot delete --cert-name`` 的 argv，脏数据即潜在选项注入面。
    """
    value = (value or "").strip()
    if not value or len(value) > max_len or any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise HTTPException(status_code=400, detail=f"{what}非法（1-{max_len} 字符，不含控制字符）")
    return value


def _load_certs() -> list:
    if not os.path.exists(SSL_FILE):
        return []
    try:
        with open(SSL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_certs(certs: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SSL_FILE, "w", encoding="utf-8") as f:
        json.dump(certs, f, ensure_ascii=False, indent=2)


def _which(cmd: str) -> Optional[str]:
    # 在宿主机环境中查找命令（容器模式经 hostfs 映射）
    return host_which(cmd)


class LetsEncryptRequest(BaseModel):
    domains: list[str]
    email: str = Field(..., min_length=1)


@router.get("/list")
async def list_certs():
    certs = _load_certs()
    return {"certs": certs, "certbot": bool(_which("certbot"))}


@router.post("/upload")
async def upload_cert(
    name: str = Form(...),
    domains: str = Form(""),
    cert: UploadFile = File(...),
    key: UploadFile = File(...),
):
    # 名称校验：拒绝以 "-" 开头（防 certbot --cert-name 参数被污染）
    name = _reject_control_chars(name, "证书名称", 64)
    if name.startswith("-"):
        raise HTTPException(status_code=400, detail="证书名称不能以 - 开头")
    # 域名备注校验：限长 + 拒控制字符（自由文本，仅展示用，不强制域名格式）
    domains = (domains or "").strip()
    if len(domains) > 512 or any(ord(c) < 0x20 or ord(c) == 0x7F for c in domains):
        raise HTTPException(status_code=400, detail="domains 备注非法（≤512 字符，不含控制字符）")

    def _read_limited(f, what: str) -> bytes:
        """读取上传内容并限制大小，防止无限流写满磁盘。"""
        data = f.read(_CERT_MAX_BYTES + 1)
        if len(data) > _CERT_MAX_BYTES:
            raise HTTPException(status_code=413, detail=f"{what} 文件过大（上限 1MB）")
        if not data:
            raise HTTPException(status_code=400, detail=f"{what} 文件为空")
        return data

    cert_bytes = _read_limited(cert.file, "证书")
    key_bytes = _read_limited(key.file, "私钥")

    cid = str(uuid.uuid4())[:8]
    cert_path = os.path.join(SSL_DIR, f"{cid}.crt")
    key_path = os.path.join(SSL_DIR, f"{cid}.key")
    with open(cert_path, "wb") as f:
        f.write(cert_bytes)
    with open(key_path, "wb") as f:
        f.write(key_bytes)
    certs = _load_certs()
    certs.append(
        {
            "id": cid,
            "name": name,
            "domains": [d.strip() for d in domains.split(",") if d.strip()],
            "type": "custom",
            "cert_path": cert_path,
            "key_path": key_path,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(days=365)).isoformat(),
        }
    )
    _save_certs(certs)
    return {"ok": True}


@router.post("/letsencrypt")
async def letsencrypt(req: LetsEncryptRequest):
    if not _which("certbot"):
        raise HTTPException(status_code=503, detail="certbot not installed")
    # 安全校验：域名白名单（防 certbot 选项注入 / live 目录路径穿越）；
    # 邮箱需含 @ 且不含空白（会作为 -m 的参数值传递）
    if not req.domains or len(req.domains) > 32:
        raise HTTPException(status_code=400, detail="domains 必须是 1-32 个域名")
    for d in req.domains:
        if not isinstance(d, str) or not _DOMAIN_RE.match(d):
            raise HTTPException(status_code=400, detail=f"域名格式非法: {d!r}")
    if not req.email or "@" not in req.email or any(c.isspace() for c in req.email):
        raise HTTPException(status_code=400, detail="邮箱格式非法")
    cmd = [
        "certbot",
        "certonly",
        "--standalone",
        "--agree-tos",
        "-m",
        req.email,
        "-n",
        "--preferred-challenges",
        "http",
    ] + [d for domain in req.domains for d in ("-d", domain)]
    try:
        # 在宿主机环境执行 certbot（容器模式经 chroot 映射）
        r = host_cmd(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise HTTPException(status_code=500, detail=r.stderr or r.stdout)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    certs = _load_certs()
    # Find newly created cert under /etc/letsencrypt/live/{first_domain}
    first = req.domains[0]
    live_dir = f"/etc/letsencrypt/live/{first}"
    cert_path = os.path.join(live_dir, "fullchain.pem")
    key_path = os.path.join(live_dir, "privkey.pem")
    certs.append(
        {
            "id": "le_" + str(uuid.uuid4())[:6],
            "name": first,
            "domains": req.domains,
            "type": "letsencrypt",
            "cert_path": cert_path,
            "key_path": key_path,
            "created_at": datetime.now().isoformat(),
        }
    )
    _save_certs(certs)
    return {"ok": True}


@router.post("/{cert_id}/delete")
async def delete_cert(cert_id: str):
    certs = _load_certs()
    cert = next((c for c in certs if c["id"] == cert_id), None)
    if not cert:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if cert.get("type") == "letsencrypt" and _which("certbot"):
        host_cmd(
            ["certbot", "delete", "--cert-name", cert["name"]],
            capture_output=True,
            timeout=30,
        )
    for p in [cert.get("cert_path"), cert.get("key_path")]:
        real = host_path(p) if p else None
        if real and os.path.exists(real):
            try:
                os.remove(real)
            except Exception:
                pass
    certs = [c for c in certs if c["id"] != cert_id]
    _save_certs(certs)
    return {"ok": True}
