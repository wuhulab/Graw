import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SSL_DIR = os.path.join(DATA_DIR, "ssl")
SSL_FILE = os.path.join(DATA_DIR, "ssl.json")
os.makedirs(SSL_DIR, exist_ok=True)


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
    return shutil.which(cmd)


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
    cid = str(uuid.uuid4())[:8]
    cert_path = os.path.join(SSL_DIR, f"{cid}.crt")
    key_path = os.path.join(SSL_DIR, f"{cid}.key")
    with open(cert_path, "wb") as f:
        shutil.copyfileobj(cert.file, f)
    with open(key_path, "wb") as f:
        shutil.copyfileobj(key.file, f)
    certs = _load_certs()
    certs.append(
        {
            "id": cid,
            "name": name,
            "domains": domains.split(","),
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
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
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
        subprocess.run(
            ["certbot", "delete", "--cert-name", cert["name"]],
            capture_output=True,
            timeout=30,
        )
    for p in [cert.get("cert_path"), cert.get("key_path")]:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    certs = [c for c in certs if c["id"] != cert_id]
    _save_certs(certs)
    return {"ok": True}
