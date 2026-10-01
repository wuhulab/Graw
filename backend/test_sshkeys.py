# -*- coding: utf-8 -*-
"""
test_sshkeys.py - SSH 密钥管理功能测试

分两部分：
  1. 单元测试：直接驱动 sshkeys 的密钥生成/解析/指纹/公钥白名单校验。
  2. 集成测试：若后端运行在 8000 端口，用真实账号登录并调用
     /api/sshkeys/* 的列表/生成/导入/公钥/删除接口。
     （部署接口依赖真实 SSH 节点，不做在线验证，仅校验接口权限与入参。）

用法：backend/.venv/Scripts/python.exe test_sshkeys.py
"""
import os
import sys
import json
import shutil

# 保证可导入 app 包（脚本位于 backend/ 下时无需额外处理）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import urllib.request
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def test_generate_key_unit():
    """Ed25519 / RSA 生成：公钥格式、指纹、类型识别。"""
    from app.routers import sshkeys

    priv, pub_line, fp = sshkeys._generate_key("ed25519", "test@graw")
    assert pub_line.startswith("ssh-ed25519"), f"Ed25519 公钥前缀异常: {pub_line[:40]}"
    assert pub_line.rstrip().endswith("test@graw"), "注释应附加到公钥尾部"
    assert fp.startswith("SHA256:"), f"指纹格式异常: {fp}"
    assert sshkeys._PUB_RE.match(pub_line), "公钥应通过白名单校验"
    assert sshkeys._detect_key_type(priv) == "ed25519"

    priv2, pub2, fp2 = sshkeys._generate_key("rsa", "")
    assert pub2.startswith("ssh-rsa"), f"RSA 公钥前缀异常: {pub2[:40]}"
    assert sshkeys._detect_key_type(priv2) == "rsa"
    assert fp2.startswith("SHA256:")
    print("✔ 单元测试：Ed25519/RSA 密钥生成、指纹、类型识别通过")


def test_import_key_unit():
    """导入私钥：解析、公钥导出、指纹一致。"""
    from app.routers import sshkeys
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    priv = ed25519.Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    loaded = sshkeys._load_private_key(pem, None)
    assert sshkeys._detect_key_type(loaded) == "ed25519"
    pub = sshkeys._public_openssh(loaded)
    assert pub.startswith("ssh-ed25519")
    assert sshkeys._fingerprint(pub) == sshkeys._fingerprint(sshkeys._public_openssh(priv))
    print("✔ 单元测试：私钥导入解析、公钥导出、指纹一致性通过")


def test_pub_whitelist_unit():
    """公钥白名单：非法格式（含换行/注入）应被拒绝。"""
    from app.routers import sshkeys

    good = "ssh-ed25519 AAAAABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/= test"
    assert sshkeys._PUB_RE.match(good), "合法公钥应通过"

    # 换行注入 / 命令注入字符
    bads = [
        "ssh-ed25519 AAAA\n; rm -rf /\ntest",
        "ssh-ed25519 AAAA test`whoami`",
        "|| rm -rf /",
        "ssh-ed25519",
        "",
    ]
    for b in bads:
        assert not sshkeys._PUB_RE.match(b), f"非法公钥应被拒绝: {b!r}"
    print("✔ 单元测试：公钥白名单（换行/命令注入拒绝）通过")


def test_import_wrong_passphrase_unit():
    """导入带 passphrase 私钥：错误 passphrase 应 400。"""
    from app.routers import sshkeys
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    priv = ed25519.Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"secret123"),
    )
    # 正确 passphrase 可解析
    ok = sshkeys._load_private_key(pem, "secret123")
    assert ok is not None
    # 错误 passphrase 应抛 HTTPException(400)
    try:
        sshkeys._load_private_key(pem, "wrong")
        assert False, "错误 passphrase 应抛异常"
    except HTTPException as e:
        assert e.status_code == 400
    print("✔ 单元测试：加密私钥 passphrase 校验通过")


# ---------------------------------------------------------------------------
# 集成测试（需后端运行在 8000）
# ---------------------------------------------------------------------------
def _entry_headers():
    """ShunX 安全入口：请求需携带 X-ShunX-Entry 头（读配置，避免硬编码）。"""
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shunx.json")
    entry = None
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    # 读取可选 shunx.json：未配置/损坏时静默忽略（有意吞异常）
    except Exception:  # lgtm[py/empty-except]
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def _http_json(url, method="GET", data=None, token=None, timeout=15):
    """HTTP 请求辅助，返回 (status, json)。"""
    headers = {"Content-Type": "application/json"}
    headers.update(_entry_headers())
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def test_http_integration():
    """集成测试：注入临时账号，登录并调用 /api/sshkeys 的 CRUD 接口。"""
    from app.auth import USERS_FILE, hash_password

    base = "http://localhost:8000/api"
    backup = USERS_FILE + ".bak_sshkeys"
    shutil.copyfile(USERS_FILE, backup)
    token = None
    created_ids = []
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        users["__sshkeys"] = {
            "username": "__sshkeys",
            "password": hash_password("SecPass#123"),
            "role": "admin",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__sshkeys", "password": "SecPass#123"})
        assert code == 200, f"登录失败: {code} {body}"
        token = body["token"]

        # 节点列表（应 200，返回数组）
        code, nodes = _http_json(f"{base}/sshkeys/nodes", token=token)
        assert code == 200, f"nodes 接口异常: {code} {nodes}"
        print(f"✔ nodes 接口: 共 {len(nodes.get('nodes', []))} 个节点")

        # 生成 Ed25519 密钥
        code, key = _http_json(f"{base}/sshkeys", "POST", {
            "name": "测试密钥", "key_type": "ed25519", "comment": "test@graw",
        }, token=token)
        assert code == 200, f"生成失败: {code} {key}"
        assert key.get("key_type") == "ed25519", f"类型异常: {key}"
        assert key.get("fingerprint", "").startswith("SHA256:"), "应返回指纹"
        created_ids.append(key["id"])
        print(f"✔ 生成密钥: {key['name']} {key['key_type']} {key['fingerprint'][:24]}…")

        # 非法类型应 422（pydantic pattern 拒绝）
        code, _ = _http_json(f"{base}/sshkeys", "POST", {
            "name": "非法", "key_type": "dsa",
        }, token=token)
        assert code == 422, f"非法 key_type 应 422，实际 {code}"
        print("✔ 非法 key_type 被 422 拒绝")

        # 空名称应 400
        code, _ = _http_json(f"{base}/sshkeys", "POST", {
            "name": "   ", "key_type": "ed25519",
        }, token=token)
        assert code == 400, f"空名称应 400，实际 {code}"
        print("✔ 空名称被 400 拒绝")

        # 公钥查看：应返回 authorized_keys 格式
        code, pub = _http_json(f"{base}/sshkeys/{key['id']}/public", token=token)
        assert code == 200, f"公钥接口异常: {code} {pub}"
        assert pub["public_key"].startswith("ssh-ed25519"), "公钥格式异常"
        print(f"✔ 公钥接口: {pub['public_key'][:48]}…")

        # 列表应包含新建项，且不含私钥字段
        code, lst = _http_json(f"{base}/sshkeys", token=token)
        assert code == 200, f"列表接口异常: {code} {lst}"
        mine = [k for k in lst.get("keys", []) if k["id"] == key["id"]]
        assert mine, "列表应包含新建项"
        assert "private_key" not in lst["keys"][0], "列表不得泄露私钥"
        print(f"✔ 列表接口: 共 {len(lst.get('keys', []))} 把密钥（无私钥字段）")

        # 导入非法内容应 400
        code, _ = _http_json(f"{base}/sshkeys/import", "POST", {
            "name": "坏密钥", "private_key": "not a pem key at all",
        }, token=token)
        assert code == 400, f"非法私钥应 400，实际 {code}"
        print("✔ 非法私钥导入被 400 拒绝")

        # 普通用户无权限（403）
        users["__sshkeys_user"] = {
            "username": "__sshkeys_user",
            "password": hash_password("SecPass#123"),
            "role": "user",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__sshkeys_user", "password": "SecPass#123"})
        user_token = body["token"]
        code, _ = _http_json(f"{base}/sshkeys", token=user_token)
        assert code == 403, f"普通用户访问应 403，实际 {code}"
        print("✔ 普通用户访问 sshkeys 被 403 拒绝")

        # 删除
        for kid in created_ids:
            code, _ = _http_json(f"{base}/sshkeys/{kid}", "DELETE", token=token)
            assert code == 200, f"删除失败: {code} {kid}"
        print(f"✔ 删除 {len(created_ids)} 把测试密钥成功")

        # 删除后列表应为空（不含已删项）
        code, lst = _http_json(f"{base}/sshkeys", token=token)
        assert not any(k["id"] in created_ids for k in lst.get("keys", [])), "删除后不应残留"
    finally:
        # 清理测试密钥文件（防残留）
        from app.routers import sshkeys as _sk
        import shutil as _shutil
        _data = _sk._load_meta()
        _data["keys"] = [k for k in _data.get("keys", [])
                         if k.get("name", "").startswith("测试") is False or True]
        # 仅删除名字含「测试」的密钥
        _data["keys"] = [k for k in _data.get("keys", [])
                         if not k.get("name", "").startswith("测试")]
        _sk._save_meta(_data)
        # 清理对应的密钥目录
        for d in os.listdir(_sk.KEYS_DIR) if os.path.isdir(_sk.KEYS_DIR) else []:
            meta = _data["keys"]
            if d not in [k["id"] for k in meta]:
                _shutil.rmtree(os.path.join(_sk.KEYS_DIR, d), ignore_errors=True)
        _shutil.copyfile(backup, USERS_FILE)
        os.remove(backup)


if __name__ == "__main__":
    test_generate_key_unit()
    test_import_key_unit()
    test_pub_whitelist_unit()
    test_import_wrong_passphrase_unit()
    test_http_integration()
    print("全部测试完成")
