# -*- coding: utf-8 -*-
"""
sshkeys.py - SSH 密钥管理路由

功能：
  1. 生成密钥对：Ed25519 / RSA（用 cryptography 生成，无需外部 ssh-keygen）。
  2. 导入已有私钥：粘贴 PEM 私钥内容（支持加密私钥 + passphrase）。
  3. 查看/复制公钥：返回 OpenSSH authorized_keys 格式公钥与 SHA256 指纹。
  4. 一键部署：把公钥追加到指定节点（现有节点管理）的 ~/.ssh/authorized_keys，
     配合节点管理的「密钥认证」即可免密登录。
  5. 删除密钥（仅删除本面板保管的密钥文件，不影响已部署的节点）。

安全：
  - 私钥以 0600 权限存储，绝不随列表/详情接口回传，仅保留在服务端磁盘。
  - 公钥内容严格限制为 OpenSSH 单行格式（白名单校验），防止部署时注入。
  - 部署走 node_manager 的 SSH argv 构造（复用 ssh 参数注入防护），目标节点
    必须存在；公钥内容经 shlex.quote 转义后拼入远程命令。
  - 导入/生成接口均限制字段长度，防止脏数据。

数据存储：
  backend/data/sshkeys.json       元数据（不含私钥）
  backend/data/sshkeys/<id>/id    私钥（PEM，0600）
  backend/data/sshkeys/<id>/pub   公钥（OpenSSH 格式）
"""
import base64
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import node_manager

logger = logging.getLogger("graw.sshkeys")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
KEYS_META = os.path.join(DATA_DIR, "sshkeys.json")
KEYS_DIR = os.path.join(DATA_DIR, "sshkeys")

# 公钥白名单：OpenSSH 格式 "ssh-ed25519 AAAA... comment" 或 "ssh-rsa AAAA... comment"
# （base64 段只允许 URL-safe 字符集；注释段只允许常规主机标识字符，排除反引号/
#   分号/$/括号等 shell 危险字符，阻断命令注入）
_PUB_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256)\s+"
    r"[A-Za-z0-9+/=]+\s*([A-Za-z0-9@._\-/ ]*)$"
)

# 私钥长度上限（防止超大粘贴体拖垮内存）
_MAX_PRIVATE_KEY_LEN = 64 * 1024

_lock = threading.Lock()


def _default() -> dict:
    return {"version": 1, "keys": []}


def _load_meta() -> dict:
    if not os.path.exists(KEYS_META):
        return _default()
    try:
        with open(KEYS_META, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 sshkeys.json 失败，按默认处理: %s", e)
        return _default()
    if not isinstance(data, dict):
        return _default()
    data.setdefault("keys", [])
    return data


def _save_meta(data: dict):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = KEYS_META + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, KEYS_META)


def _key_dir(key_id: str) -> str:
    """返回单个密钥的存放目录。

    安全（code-scanning py/path-injection）：key_id 来自 URL 路径参数，
    必须为服务器生成的 `key_<hex>` 形态，严格白名单防止 `/`、`..` 等
    字符拼出越界路径。
    """
    if not re.fullmatch(r"key_[0-9a-fA-F]{6,32}", key_id or ""):
        raise HTTPException(status_code=400, detail="非法密钥 ID")
    return os.path.join(KEYS_DIR, key_id)


def _find_key(keys: list, key_id: str) -> dict:
    key = next((k for k in keys if k.get("id") == key_id), None)
    if not key:
        raise HTTPException(status_code=404, detail="密钥不存在")
    return key


# ---------------------------------------------------------------------------
# 密钥生成 / 解析
# ---------------------------------------------------------------------------
def _load_private_key(pem: bytes, passphrase: Optional[str] = None):
    """加载 PEM 私钥（支持加密），返回私钥对象。"""
    from cryptography.hazmat.primitives import serialization

    password = passphrase.encode("utf-8") if passphrase else None
    try:
        return serialization.load_pem_private_key(pem, password=password)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"私钥解析失败：{e}")


def _public_openssh(private_key) -> str:
    """从私钥对象导出 OpenSSH 公钥字符串（authorized_keys 格式，含注释）。"""
    from cryptography.hazmat.primitives import serialization

    pub = private_key.public_key()
    raw = pub.public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode("ascii")
    return raw


def _fingerprint(pub_line: str) -> str:
    """计算 OpenSSH SHA256 指纹（RFC4253 公钥 blob -> sha256 -> base64 无填充）。"""
    try:
        parts = pub_line.split()
        if len(parts) < 2:
            return ""
        digest = hashlib.sha256(base64.b64decode(parts[1])).digest()
        return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
    except Exception:
        return ""


def _detect_key_type(private_key) -> str:
    """识别私钥算法类型，用于展示。"""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519, rsa, ec

        if isinstance(private_key, ed25519.Ed25519PrivateKey):
            return "ed25519"
        if isinstance(private_key, rsa.RSAPrivateKey):
            return "rsa"
        if isinstance(private_key, ec.EllipticCurvePrivateKey):
            return "ecdsa"
    except Exception:
        # 私钥解析失败时按未知类型处理
        pass
    return "unknown"


def _write_private_key(key_id: str, private_key, pub_line: str, comment: str) -> None:
    """把私钥/公钥落盘（私钥 0600）。"""
    from cryptography.hazmat.primitives import serialization

    d = _key_dir(key_id)
    os.makedirs(d, exist_ok=True)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    priv_path = os.path.join(d, "id")
    with open(priv_path, "wb") as f:
        f.write(pem)
    os.chmod(priv_path, 0o600)
    with open(os.path.join(d, "pub"), "w", encoding="utf-8") as f:
        f.write(pub_line + "\n")


def _generate_key(key_type: str, comment: str) -> dict:
    """生成密钥对，返回 (private_key, pub_line, fingerprint)。"""
    from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

    try:
        if key_type == "rsa":
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        else:
            # Ed25519 使用 generate() 工厂方法（不能直接实例化抽象类）
            private_key = ed25519.Ed25519PrivateKey.generate()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"密钥生成失败：{e}")
    pub_line = _public_openssh(private_key)
    # 追加注释（主机标识），便于在远端 authorized_keys 里识别来源
    pub_line = f"{pub_line} {comment}" if comment else pub_line
    return private_key, pub_line, _fingerprint(pub_line)


def _read_pub(key_id: str) -> str:
    """读取指定密钥的公钥内容（首行）。"""
    path = os.path.join(_key_dir(key_id), "pub")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        raise HTTPException(status_code=404, detail="公钥文件缺失")


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class KeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    key_type: str = Field("ed25519", pattern="^(ed25519|rsa)$")
    comment: Optional[str] = Field(None, max_length=128)


class KeyImportRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    private_key: str = Field(..., min_length=10, max_length=_MAX_PRIVATE_KEY_LEN)
    passphrase: Optional[str] = Field(None, max_length=128)


class KeyDeployRequest(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@router.get("")
async def list_keys():
    """返回密钥列表（仅元数据 + 公钥指纹，绝不返回私钥）。"""
    data = _load_meta()
    out = []
    for k in data.get("keys", []):
        out.append({
            "id": k.get("id"),
            "name": k.get("name"),
            "key_type": k.get("key_type"),
            "comment": k.get("comment", ""),
            "fingerprint": k.get("fingerprint", ""),
            "created_at": k.get("created_at"),
        })
    return {"keys": out}


@router.get("/nodes")
async def list_deploy_nodes():
    """返回可部署公钥的 SSH 节点列表（复用节点管理，脱敏）。"""
    return {"nodes": node_manager.list_nodes()}


@router.post("")
async def create_key(req: KeyCreateRequest):
    """生成一对新密钥。"""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="密钥名称不能为空")
    comment = (req.comment or "").strip() or f"graw-{name}"
    try:
        private_key, pub_line, fingerprint = _generate_key(req.key_type, comment)
    except HTTPException:
        raise

    key_id = "key_" + uuid.uuid4().hex[:10]
    try:
        _write_private_key(key_id, private_key, pub_line, comment)
    except OSError as e:
        logger.error("写入密钥文件失败: %s", e)
        raise HTTPException(status_code=500, detail="密钥文件写入失败")

    meta = {
        "id": key_id,
        "name": name,
        "key_type": _detect_key_type(private_key),
        "comment": comment,
        "fingerprint": fingerprint,
        "created_at": datetime.now().isoformat(),
    }
    data = _load_meta()
    data.setdefault("keys", []).append(meta)
    _save_meta(data)
    logger.info("生成 SSH 密钥：%s（%s）", name, meta["key_type"])
    return meta


@router.post("/import")
async def import_key(req: KeyImportRequest):
    """导入已有私钥（PEM），自动解析出公钥与指纹。"""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="密钥名称不能为空")
    pem = req.private_key.strip().encode("utf-8")
    private_key = _load_private_key(pem, req.passphrase)
    pub_line = _public_openssh(private_key)
    fingerprint = _fingerprint(pub_line)

    key_id = "key_" + uuid.uuid4().hex[:10]
    try:
        _write_private_key(key_id, private_key, pub_line, "")
    except OSError as e:
        logger.error("写入密钥文件失败: %s", e)
        raise HTTPException(status_code=500, detail="密钥文件写入失败")

    meta = {
        "id": key_id,
        "name": name,
        "key_type": _detect_key_type(private_key),
        "comment": "",
        "fingerprint": fingerprint,
        "created_at": datetime.now().isoformat(),
    }
    data = _load_meta()
    data.setdefault("keys", []).append(meta)
    _save_meta(data)
    logger.info("导入 SSH 密钥：%s（%s）", name, meta["key_type"])
    return meta


@router.get("/{key_id}/public")
async def get_public_key(key_id: str):
    """返回公钥内容（authorized_keys 格式），用于展示与部署确认。"""
    data = _load_meta()
    _find_key(data.get("keys", []), key_id)
    pub = _read_pub(key_id)
    if not _PUB_RE.match(pub):
        raise HTTPException(status_code=500, detail="公钥内容异常")
    return {"id": key_id, "public_key": pub, "fingerprint": _fingerprint(pub)}


@router.post("/{key_id}/deploy")
async def deploy_key(key_id: str, req: KeyDeployRequest):
    """把公钥一键部署到指定节点（追加到 ~/.ssh/authorized_keys）。"""
    data = _load_meta()
    _find_key(data.get("keys", []), key_id)
    node = node_manager.get_node(req.node_id)
    if node is None or node.get("type") != "ssh":
        raise HTTPException(status_code=400, detail="目标节点不存在或非 SSH 节点")

    pub = _read_pub(key_id)
    if not _PUB_RE.match(pub):
        raise HTTPException(status_code=500, detail="公钥内容异常")

    # 先做连通性测试，避免部署到不可达节点静默失败
    test = node_manager.connect_test(node)
    if not test.get("ok"):
        raise HTTPException(status_code=400, detail=f"目标节点不可达：{test.get('message')}")

    # 远程命令：创建 ~/.ssh，公钥已存在则跳过，否则追加（幂等）
    quoted = shlex.quote(pub)
    remote_cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"grep -qxF {quoted} ~/.ssh/authorized_keys 2>/dev/null || "
        f"printf '%s\\n' {quoted} >> ~/.ssh/authorized_keys; "
        "chmod 600 ~/.ssh/authorized_keys"
    )
    try:
        argv, env_extra = node_manager._ssh_argv(node, remote_cmd)
        env = dict(os.environ) if env_extra else None
        if env_extra:
            env.update(env_extra)
        r = subprocess.run(
            argv, env=env, capture_output=True, timeout=node_manager._SSH_CONNECT_TIMEOUT + 15
        )
    except FileNotFoundError as e:
        logger.error("部署密钥失败（ssh 缺失）: %s", e)
        raise HTTPException(status_code=500, detail="控制器缺少 ssh 客户端")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="部署超时")

    if r.returncode != 0:
        err = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
        logger.error("部署密钥到节点 %s 失败: %s", req.node_id, err)
        raise HTTPException(status_code=500, detail=f"部署失败：{err[:200]}")

    logger.info("公钥 %s 已部署到节点 %s", key_id, req.node_id)
    return {
        "ok": True,
        "node_id": req.node_id,
        "node_name": node.get("name") or req.node_id,
        "fingerprint": _fingerprint(pub),
    }


@router.delete("/{key_id}")
async def delete_key(key_id: str):
    """删除密钥（仅删除面板保管的密钥文件与元数据）。"""
    data = _load_meta()
    _find_key(data.get("keys", []), key_id)
    data["keys"] = [k for k in data.get("keys", []) if k.get("id") != key_id]
    _save_meta(data)
    # 清理密钥目录（含私钥/公钥文件）
    d = _key_dir(key_id)
    if os.path.isdir(d):
        import shutil

        shutil.rmtree(d, ignore_errors=True)
    logger.info("删除 SSH 密钥：%s", repr(key_id))
    return {"ok": True}
