# -*- coding: utf-8 -*-
"""
netstorage.py - 网络储存路由

管理并浏览远程网络存储：FTP / FTPS / SMB / WebDAV / 对象存储(S3兼容)。

功能：
- 连接管理（增删改查 + 测试连通性），配置持久化到 data/netstorage.json。
- 以「云盘」卡片形式呈现，点击后进入远程文件浏览器执行文件操作
  （列表 / 读取 / 写入 / 上传 / 下载 / 新建目录 / 删除 / 重命名 / 移动）。

设计说明：
- 各协议通过独立适配器（Adapter）封装统一接口，路由层面向统一抽象编程，
  便于扩展新协议。所有适配器以「云端逻辑路径 / 开头的相对路径」与前端交互，
  由适配器内部翻译为协议真实路径（如 SMB 的 //host/share/path、S3 的 bucket/key）。
- 凭据以明文存储于 data/netstorage.json（与 databases.json 一致，面板需回读使用），
  对外接口一律脱敏（password 置空 + has_password 标记），编辑时不回传明文。

安全（对齐本项目审计规范）：
- 本路由全部 require_admin（外挂在 main.py 的 ADMIN 依赖）。
- 连接字段白名单校验，拒绝控制字符 / 注入（名、主机、基础路径、端口范围）。
"""
import os
import re
import uuid
import logging
import asyncio
import threading
import queue
from io import BytesIO
from typing import Optional, Generator

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import get_current_user, get_client_ip
from app import auditlog

logger = logging.getLogger("graw.netstorage")

router = APIRouter()

# 配置持久化目录（与 data/ 其它 json 一致）
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
CONF_FILE = os.path.join(DATA_DIR, "netstorage.json")

# 允许的协议
ALLOWED_TYPES = ("ftp", "ftps", "smb", "webdav", "s3")
_PROTO_LABEL = {
    "ftp": "FTP",
    "ftps": "FTPS",
    "smb": "SMB",
    "webdav": "WebDAV",
    "s3": "对象存储",
}


async def _run_blocking(fn, *args, **kwargs):
    """把同步阻塞调用移出事件循环（线程池执行）。

    安全修复（第十四轮审计，Medium）：此前各 async 端点直接在事件循环内
    执行 requests/FTP/paramiko 等阻塞网络 I/O（超时 15-30s，上传为全程），
    连接 tarpit 远端或慢速大文件时整个面板对所有用户冻结（单事件循环 DoS）。
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


def _read_limited(iter_chunks, max_bytes: int = None) -> bytes:
    """流式读取并限制累计大小：超过 max_bytes 立即抛 413（防 OOM）。

    安全修复（第十四轮审计，Medium）：此前 read 先整读进内存再校验 2MB，
    恶意/超大响应可在返回 413 前把面板 OOM。改为分块累计，超限即中断。
    """
    limit = max_bytes if max_bytes is not None else MAX_READ_BYTES
    buf = BytesIO()
    total = 0
    for chunk in iter_chunks:
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "文件过大（>2MB），请下载查看")
        buf.write(chunk)
    return buf.getvalue()

# 输入白名单（对齐本项目安全规范，防注入 / 控制字符）
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff\u3400-\u4dbf ]{1,64}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,255}$")
_BUCKET_RE = re.compile(r"^[A-Za-z0-9._\-]{1,255}$")
_SHARE_RE = re.compile(r"^[^/\\\r\n:]{1,255}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

# 单文件/文件内容读取上限
MAX_READ_BYTES = 2 * 1024 * 1024
TOKEN_LIMIT = 1024  # 单次删除的对象上限（细化为分批处理）


# ---------------------------------------------------------------------------
# 连接数据模型与持久化
# ---------------------------------------------------------------------------
class ConnectionIn(BaseModel):
    """连接配置入参（password 为空串表示保持原密码）。"""

    name: str = Field(min_length=1, max_length=64)
    type: str
    host: str = Field(min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    base: Optional[str] = Field(default=None, max_length=1024)  # 共享/桶名/初始目录/WebDAV根URL
    params: dict = Field(default_factory=dict)  # 协议扩展参数

    def validate_rules(self):
        """协议相关的字段白名单校验（静默替换非法值为安全默认/抛 400）。"""
        if self.type not in ALLOWED_TYPES:
            raise HTTPException(400, "不支持的存储类型")
        if _CTRL.search(self.name) or not _NAME_RE.match(self.name):
            raise HTTPException(400, "名称包含非法字符")
        if not _HOST_RE.match(self.host):
            raise HTTPException(400, "主机地址包含非法字符")
        if _CTRL.search(self.username):
            raise HTTPException(400, "用户名包含非法字符")
        # 默认端口
        if self.port is None:
            self.port = {"ftp": 21, "ftps": 990, "webdav": 443, "s3": 9000, "smb": 445}[self.type]
        # SSRF 防护（第八轮审计修复，Medium）：FTP/FTPS/SMB/S3 的 host 为裸
        # 主机/IP，此前仅过字符白名单，169.254.169.254（云元数据）等受保护
        # 地址可直接连接。此处统一用 assert_safe_host 拒绝回环/链路本地/
        # 保留/未指定地址（内网存储允许 RFC1918/ULA，受保护地址始终拒绝）。
        if self.type in ("ftp", "ftps", "smb", "s3"):
            try:
                from app.ssrf_guard import assert_safe_host
                assert_safe_host(self.host, allow_private=True)
            except ValueError as e:
                raise HTTPException(400, str(e))
        # 基础路径校验
        if self.base:
            if _CTRL.search(self.base):
                raise HTTPException(400, "基础路径包含非法字符")
            if self.type in ("smb",):
                if not _SHARE_RE.match(self.base) or self.base in (".", ".."):
                    raise HTTPException(400, "共享名非法")
            elif self.type == "s3":
                if not _BUCKET_RE.match(self.base):
                    raise HTTPException(400, "桶名非法")
            else:
                if "://" in self.base and self.type == "webdav":
                    if not self.base.startswith(("http://", "https://")):
                        raise HTTPException(400, "WebDAV 根地址仅支持 http/https")
                    # SSRF 防护：拒绝回环/链路本地（含云 metadata）/保留地址；
                    # 内网存储（RFC1918/ULA）允许，但受保护地址始终拒绝。
                    try:
                        from app.ssrf_guard import assert_safe_http_url
                        assert_safe_http_url(self.base, allow_private=True)
                    except ValueError as e:
                        raise HTTPException(400, str(e))
        return self


def _load() -> list:
    """读取连接列表；文件缺失 / 损坏时返回空列表并告警。"""
    try:
        with open(CONF_FILE, "r", encoding="utf-8") as f:
            data = __import__("json").load(f)
        return data.get("connections", []) if isinstance(data, dict) else []
    except FileNotFoundError:
        return []
    except Exception as e:  # 损坏时不阻塞面板，记录并重置为空
        logger.warning("读取 netstorage 配置失败: %s", e)
        return []


def _save(conns: list) -> None:
    """写回连接列表（先建目录再原子写）。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CONF_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            __import__("json").dump({"connections": conns}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONF_FILE)
    except Exception as e:
        logger.error("写入 netstorage 配置失败: %s", e)
        raise HTTPException(500, "保存配置失败")


def _get_conn(cid: str) -> dict:
    """按 id 查找连接，不存在抛 404。"""
    for c in _load():
        if c.get("id") == cid:
            return c
    raise HTTPException(404, "连接不存在")


def _mask(conn: dict) -> dict:
    """对外脱敏：去掉明文密码，标记是否已设置密码。"""
    c = dict(conn)
    c["has_password"] = bool(c.get("password"))
    c["password"] = ""
    return c


def _proto_label(t: str) -> str:
    return _PROTO_LABEL.get(t, t)


# ---------------------------------------------------------------------------
# 协议适配器：统一文件接口（云端路径以 "/" 开头）
# ---------------------------------------------------------------------------
class BaseAdapter:
    def list(self, lpath: str):
        raise NotImplementedError

    def read(self, lpath: str) -> str:
        raise NotImplementedError

    def write(self, lpath: str, content: str) -> None:
        raise NotImplementedError

    def mkdir(self, lpath: str) -> None:
        raise NotImplementedError

    def delete(self, lpath: str) -> None:
        raise NotImplementedError

    def rename(self, src: str, dst: str) -> None:
        raise NotImplementedError

    def upload(self, lpath: str, file) -> None:
        raise NotImplementedError

    def download(self, lpath: str) -> Generator[bytes, None, None]:
        raise NotImplementedError


def _lu_join(base: str, lpath: str) -> str:
    """基础路径 + 逻辑路径拼接为协议路径（供 SMB/WebDAV）。"""
    p = (lpath or "/").strip("/")
    if not p:
        return base
    return base.rstrip("/") + "/" + p


class FTPAdapter(BaseAdapter):
    """FTP / FTPS：使用标准库 ftplib。"""

    def __init__(self, conn):
        self.conn = conn
        self.ftps = conn["type"] == "ftps"
        self.base = (conn.get("base") or "").strip() or "/"

    # 每个操作独立建立连接，避免一次性长连接阻塞
    def _connect(self):
        import ftplib

        host = self.conn["host"]
        port = self.conn.get("port") or (990 if self.ftps else 21)
        username = self.conn.get("username") or ""
        password = self.conn.get("password") or ""
        passive = bool(self.conn.get("params", {}).get("passive", True))
        # SSRF 防护（纵深防御，与 validate_rules 同基线）：每次实际连接前
        # 复检主机，拒绝云元数据（169.254.169.254）等受保护地址。
        try:
            from app.ssrf_guard import assert_safe_host
            assert_safe_host(host, allow_private=True)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if self.ftps:
            ftp = ftplib.FTP_TLS()
            ftp.connect(host, port, timeout=15)
            ftp.login(username, password)
            ftp.prot_p()  # 数据通道加密，区别 FTPS 与 FTP
        else:
            ftp = ftplib.FTP()
            ftp.connect(host, port, timeout=15)
            ftp.login(username, password)
        ftp.set_pasv(passive)
        return ftp

    def _full(self, lpath: str) -> str:
        p = (lpath or "/").strip("/")
        b = self.base.strip("/")
        if not b:
            return "/" + p
        return "/" + b + ("/" + p if p else "")

    def list(self, lpath: str):
        ftp = self._connect()
        try:
            cur = self._full(lpath)
            items = []
            try:
                # 优先 MLSD（带类型/大小/时间，Python3.9+ 支持）
                for name, facts in ftp.mlsd(cur, facts=["type", "size", "modify"]):
                    if name in (".", ".."):
                        continue
                    is_dir = facts.get("type") in ("dir", "cdir", "pdir")
                    size = 0
                    try:
                        size = int(facts.get("size") or 0)
                    except (TypeError, ValueError):
                        size = 0
                    mtime = _ftp_ts(facts.get("modify")) if facts.get("modify") else 0
                    items.append(
                        dict(name=name, path=_join_l(lpath, name), is_dir=is_dir,
                             size=size, modified=mtime)
                    )
            except Exception:
                # 回退：nlst 列名 + size 探测类型
                names = ftp.nlst(cur) or []
                for n in names:
                    b = n.split("/")[-1]
                    items.append(dict(name=b, path=_join_l(lpath, b),
                                      is_dir=False, size=0, modified=0))
            items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
            return dict(path=lpath or "/", parent=_lp_parent(lpath), items=items)
        finally:
            try:
                ftp.quit()
            except Exception:
                # 关闭 FTP 连接失败（连接已断开）时忽略
                pass

    def read(self, lpath: str) -> str:
        # 流式限长（第十四轮审计修复）：retrbinary 回调内累计字节数，
        # 超过 2MB 立即抛 413 中断传输，避免整读超大文件导致 OOM
        total = {"n": 0}
        buf = BytesIO()

        def _cb(data):
            total["n"] += len(data)
            if total["n"] > MAX_READ_BYTES:
                raise HTTPException(413, "文件过大（>2MB），请下载查看")
            buf.write(data)

        ftp = self._connect()
        try:
            ftp.retrbinary("RETR " + self._full(lpath), _cb)
        finally:
            try:
                ftp.quit()
            except Exception:
                # 关闭 FTP 连接失败（连接已断开）时忽略
                pass
        return buf.getvalue().decode("utf-8", errors="replace")

    def write(self, lpath: str, content: str) -> None:
        import io
        ftp = self._connect()
        try:
            ftp.storbinary("STOR " + self._full(lpath), io.BytesIO(content.encode("utf-8")))
        finally:
            try:
                ftp.quit()
            except Exception:
                # 关闭 FTP 连接失败（连接已断开）时忽略
                pass

    def mkdir(self, lpath: str) -> None:
        ftp = self._connect()
        try:
            ftp.mkd(self._full(lpath))
        finally:
            try:
                ftp.quit()
            except Exception:
                # 关闭 FTP 连接失败（连接已断开）时忽略
                pass

    def delete(self, lpath: str) -> None:
        ftp = self._connect()
        try:
            full = self._full(lpath)
            try:
                # 尝试作为目录递归删除
                self._rmtree(ftp, full)
            except Exception:
                try:
                    ftp.delete(full)
                except Exception:
                    raise HTTPException(500, "删除失败（文件或目录不存在/无权限）")
        finally:
            try:
                ftp.quit()
            except Exception:
                # 关闭 FTP 连接失败（连接已断开）时忽略
                pass

    def _rmtree(self, ftp, full: str) -> None:
        names = ftp.nlst(full) or []
        for n in names:
            sub = n if n.startswith("/") else full + "/" + n.split("/")[-1]
            try:
                self._rmtree(ftp, sub)  # 递归尝试当目录
            except Exception:
                try:
                    ftp.delete(sub)
                except Exception:
                    # 作为文件删除失败（子项可能是目录）时忽略
                    pass
        try:
            ftp.rmd(full)
        except Exception:
            # 目录移除失败（非空或无权限）时忽略
            pass

    def rename(self, src: str, dst: str) -> None:
        ftp = self._connect()
        try:
            ftp.rename(self._full(src), self._full(dst))
        finally:
            try:
                ftp.quit()
            except Exception:
                # 关闭 FTP 连接失败（连接已断开）时忽略
                pass

    def upload(self, lpath: str, file) -> None:
        ftp = self._connect()
        try:
            ftp.storbinary("STOR " + self._full(lpath), file.file)
        finally:
            try:
                ftp.quit()
            except Exception:
                # 关闭 FTP 连接失败（连接已断开）时忽略
                pass

    def download(self, lpath: str):
        """流式下载：后台线程执行 retrbinary，经队列逐块 yield。

        安全修复（第十四轮审计修复）：此前整读进 BytesIO 再一次性 yield，
        大文件会整份驻留内存（OOM）。改为队列流式，内存占用恒定。
        """
        q = queue.Queue(maxsize=8)
        done = threading.Event()
        err = []

        def _cb(data):
            q.put(data)

        def _run():
            try:
                ftp = self._connect()
                try:
                    ftp.retrbinary("RETR " + self._full(lpath), _cb)
                finally:
                    try:
                        ftp.quit()
                    except Exception:
                        # 关闭 FTP 连接失败（连接已断开）时忽略
                        pass
            except Exception as e:  # noqa: BLE001
                err.append(e)
            finally:
                done.set()

        threading.Thread(target=_run, daemon=True).start()
        while not done.is_set() or not q.empty():
            try:
                chunk = q.get(timeout=1)
                yield chunk
            except queue.Empty:
                continue
        if err:
            raise err[0]


def _ftp_ts(s: str) -> float:
    """把 FTP modify（YYYYMMDDHHMMSS）转成 epoch 秒。"""
    try:
        import datetime as _dt
        return int(_dt.datetime.strptime(s, "%Y%m%d%H%M%S").timestamp())
    except Exception:
        return 0


class SMBAdapter(BaseAdapter):
    """SMB：基于 smbprotocol 的高层便捷接口 smbclient。"""

    def __init__(self, conn):
        self.conn = conn
        self.host = conn["host"]
        self.share = (conn.get("base") or "").strip()
        self._root = "//{host}/{share}".format(host=self.host, share=self.share)

    def _register(self):
        # 同一会话重复注册是安全的；每次连接 CPU 取真实凭据。
        # SSRF 防护（第十轮审计加固，Low）：此前校验仅包在 username 非空
        # 分支内，匿名 SMB 连接（username 为空）直接跳过复检——若存储配置
        # 被其它途径（如面板备份导入）写入恶意 host，可绕过校验连接内网。
        # 现在无论是否匿名，注册会话前一律复检（拒绝云元数据等受保护地址）。
        from app.ssrf_guard import assert_safe_host
        try:
            assert_safe_host(self.host, allow_private=True)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if self.conn.get("username"):
            smbclient = __import__("smbclient")
            smbclient.register_session(
                self.host,
                username=self.conn.get("username") or "",
                password=self.conn.get("password") or "",
            )

    def _full(self, lpath: str) -> str:
        return _lu_join(self._root, lpath)

    def list(self, lpath: str):
        import stat as _stat
        self._register()
        smbclient = __import__("smbclient")
        full = self._full(lpath)
        try:
            entries = smbclient.listdir(full)
        except Exception as e:
            raise HTTPException(400, "读取目录失败: %s" % _safe_err(e))
        items = []
        for name in entries:
            if name in (".", ".."):
                continue
            try:
                st = smbclient.stat(self._full(_join_l(lpath, name)))
                is_dir = _stat.S_ISDIR(st.st_mode)
            except Exception:
                is_dir = False
                st = None
            items.append(dict(
                name=name, path=_join_l(lpath, name), is_dir=is_dir,
                size=getattr(st, "st_size", 0), modified=getattr(st, "st_mtime", 0),
            ))
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return dict(path=lpath or "/", parent=_lp_parent(lpath), items=items)

    def read(self, lpath: str) -> str:
        # 流式限长（第十四轮审计修复）：分块累计字节数，超 2MB 即中断
        self._register()
        smbclient = __import__("smbclient")
        total = 0
        parts = []
        with smbclient.open_file(self._full(lpath), "rb") as f:
            while True:
                chunk = f.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_READ_BYTES:
                    raise HTTPException(413, "文件过大（>2MB），请下载查看")
                parts.append(chunk)
        return b"".join(parts).decode("utf-8", errors="replace")

    def write(self, lpath: str, content: str) -> None:
        self._register()
        smbclient = __import__("smbclient")
        with smbclient.open_file(self._full(lpath), "w") as f:
            f.write(content)

    def mkdir(self, lpath: str) -> None:
        self._register()
        smbclient = __import__("smbclient")
        smbclient.mkdir(self._full(lpath))

    def delete(self, lpath: str) -> None:
        import stat as _stat
        self._register()
        smbclient = __import__("smbclient")
        full = self._full(lpath)
        st = smbclient.stat(full)
        if _stat.S_ISDIR(st.st_mode):
            # 递归删除目录
            for sub in smbclient.walk(full, topdown=False):
                root, dirs, files = sub
                for f in files:
                    smbclient.remove(root + "/" + f)
                for d in dirs:
                    try:
                        smbclient.rmdir(root + "/" + d)
                    except Exception:
                        # 子目录删除失败（非空或被占用）时忽略
                        pass
            try:
                smbclient.rmdir(full)
            except Exception:
                # 根目录删除失败（非空或被占用）时忽略
                pass
        else:
            smbclient.remove(full)

    def rename(self, src: str, dst: str) -> None:
        self._register()
        smbclient = __import__("smbclient")
        smbclient.rename(self._full(src), self._full(dst))

    def upload(self, lpath: str, file) -> None:
        self._register()
        smbclient = __import__("smbclient")
        with smbclient.open_file(self._full(lpath), "wb") as f:
            while True:
                chunk = file.file.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)

    def download(self, lpath: str):
        self._register()
        smbclient = __import__("smbclient")
        with smbclient.open_file(self._full(lpath), "rb") as f:
            while True:
                chunk = f.read(1 << 16)
                if not chunk:
                    break
                yield chunk


def _safe_err(e: Exception) -> str:
    """把异常转为脱敏的简短文本，避免泄露内部路径/凭据。"""
    return (str(e)[:160] or e.__class__.__name__).replace("\n", " ")


def _url_authority(url: str) -> str:
    """URL 的权威部分（scheme://host:port）：用于判断「目标服务器」是否变更。

    第十四轮审计（凭据转发防护）：仅 authority 变化（换服务器）才要求显式
    提供新密码；同服务器路径变化不影响凭据语义。
    """
    from urllib.parse import urlparse
    try:
        p = urlparse(url or "")
        if not p.hostname:
            return ""
        port = p.port or (443 if p.scheme == "https" else 80 if p.scheme == "http" else "")
        return f"{p.scheme.lower()}://{p.hostname.lower()}:{port}"
    except Exception:
        return ""


class WebDAVAdapter(BaseAdapter):
    """WebDAV：直接基于 requests 的 HTTP 方法（无需额外库）。"""

    def __init__(self, conn):
        self.conn = conn
        base = (conn.get("base") or "").strip() or ""
        self.base = base.rstrip("/")  # 已是完整 URL（如 https://host/dav/user/）
        # SSRF 防护（请求级兜底，缓解 DNS rebinding 的 TOCTOU 窗口）：
        # 每次实际操作前重新校验基础 URL 的主机位置，拒绝回环/链路本地/
        # 保留地址（含云 metadata 169.254.169.254）。内网存储允许，但受
        # 保护地址始终拒绝。
        if base:
            eff = self.base if self.base.startswith("http") else "http://" + self.base
            try:
                from app.ssrf_guard import assert_safe_http_url
                assert_safe_http_url(eff, allow_private=True)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

    def _headers(self) -> dict:
        if self.conn.get("username"):
            import base64
            token = base64.b64encode(
                ("{}:{}".format(self.conn.get("username") or "", self.conn.get("password") or "")).encode()
            ).decode()
            return {"Authorization": "Basic " + token}
        return {}

    def _url(self, lpath: str) -> str:
        base = self.base if self.base.startswith("http") else "http://" + self.base
        return _lu_join(base, lpath)

    def _pin(self, url: str) -> tuple:
        """SSRF 主机固定：http URL 的 host 替换为已校验的解析 IP。

        第十四轮审计修复（Medium）：校验与实际连接共用同一 DNS 解析结果，
        消除 DNS rebinding TOCTOU 窗口。返回 (pinned_url, host_header)。
        """
        from app.ssrf_guard import pin_http_url

        return pin_http_url(url, allow_private=True)

    def _req(self, method, url, **kw):
        import requests
        # SSRF 防护（第八轮审计修复，High）：禁止跟随 30x 重定向。
        # ssrf_guard 只校验初始 URL 解析出的地址，重定向后的 Location 目标
        # 不再经过任何校验——攻击者可配置一个公网 WebDAV 地址，其服务器 302
        # 跳转到 http://169.254.169.254/（云元数据 IAM 凭据）或任意内网服务。
        # WebDAV 操作本就不应依赖重定向，遇 3xx 一律按异常拒绝。
        # 第十四轮审计：主机固定（DNS rebinding TOCTOU 缓解）。
        url, host_hdr = self._pin(url)
        headers = self._headers()
        if host_hdr:
            headers.setdefault("Host", host_hdr)
        r = requests.request(
            method, url, headers=headers, timeout=30,
            allow_redirects=False, **kw,
        )
        if r.status_code in (301, 302, 303, 307, 308):
            raise HTTPException(400, "检测到重定向跳转，已拒绝（SSRF 防护）")
        if r.status_code in (401, 403):
            raise HTTPException(r.status_code, "认证失败或无权限")
        if r.status_code >= 400:
            raise HTTPException(400, "WebDAV 操作失败: HTTP %s" % r.status_code)
        return r

    def _propfind(self, url):
        import requests
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
                '<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>'
                '</d:prop></d:propfind>')
        url, host_hdr = self._pin(url)  # DNS rebinding 缓解（第十四轮审计）
        headers = {**self._headers(), "Depth": "1", "Content-Type": "application/xml"}
        if host_hdr:
            headers.setdefault("Host", host_hdr)
        r = requests.request(
            "PROPFIND", url, headers=headers,
            data=body, timeout=30, allow_redirects=False,
        )
        if r.status_code in (301, 302, 303, 307, 308):
            raise HTTPException(400, "检测到重定向跳转，已拒绝（SSRF 防护）")
        if r.status_code in (401, 403):
            raise HTTPException(r.status_code, "认证失败或无权限")
        if r.status_code >= 400:
            raise HTTPException(400, "读取目录失败: HTTP %s" % r.status_code)
        return r.content

    def list(self, lpath: str):
        url = self._url(lpath)
        content = self._propfind(url)
        entries = _dav_parse(content, url)
        items = [
            dict(name=e["name"], path=_join_l(lpath, e["name"]), is_dir=e["is_dir"],
                 size=e["size"], modified=e["modified"])
            for e in entries
        ]
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return dict(path=lpath or "/", parent=_lp_parent(lpath), items=items)

    def read(self, lpath: str) -> str:
        # 流式限长（第十四轮审计修复）：iter_content 分块累计，超 2MB 即中断
        r = self._req("GET", self._url(lpath), stream=True)
        try:
            data = _read_limited(r.iter_content(1 << 16))
        finally:
            r.close()
        return data.decode("utf-8", errors="replace")

    def write(self, lpath: str, content: str) -> None:
        self._req("PUT", self._url(lpath), data=content.encode("utf-8"))

    def mkdir(self, lpath: str) -> None:
        import requests
        url, host_hdr = self._pin(self._url(lpath))  # DNS rebinding 缓解（第十四轮审计）
        headers = self._headers()
        if host_hdr:
            headers.setdefault("Host", host_hdr)
        r = requests.request("MKCOL", url, headers=headers, timeout=30,
                             allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            raise HTTPException(400, "检测到重定向跳转，已拒绝（SSRF 防护）")
        if r.status_code in (401, 403):
            raise HTTPException(r.status_code, "认证失败或无权限")
        if r.status_code not in (200, 201, 204, 301, 405):
            raise HTTPException(400, "新建目录失败: HTTP %s" % r.status_code)

    def delete(self, lpath: str) -> None:
        import requests
        url, host_hdr = self._pin(self._url(lpath))  # DNS rebinding 缓解（第十四轮审计）
        headers = self._headers()
        if host_hdr:
            headers.setdefault("Host", host_hdr)
        r = requests.request("DELETE", url, headers=headers, timeout=30,
                             allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            raise HTTPException(400, "检测到重定向跳转，已拒绝（SSRF 防护）")
        if r.status_code in (401, 403):
            raise HTTPException(r.status_code, "认证失败或无权限")
        if r.status_code not in (200, 202, 204, 404):
            raise HTTPException(400, "删除失败: HTTP %s" % r.status_code)

    def rename(self, src: str, dst: str) -> None:
        import requests
        # DNS rebinding 缓解（第十四轮审计）：请求 URL 固定 IP。
        # Destination 头为目标路径的绝对 URL（host 与请求 host 相同，
        # 均为已通过 SSRF 校验的 base 主机），保持原样即可。
        src_url, src_hdr = self._pin(self._url(src))
        headers = self._headers()
        if src_hdr:
            headers.setdefault("Host", src_hdr)
        headers["Destination"] = self._url(dst)
        r = requests.request(
            "MOVE", src_url,
            headers=headers, timeout=30,
            allow_redirects=False,
        )
        if r.status_code in (301, 302, 303, 307, 308):
            raise HTTPException(400, "检测到重定向跳转，已拒绝（SSRF 防护）")
        if r.status_code in (401, 403):
            raise HTTPException(r.status_code, "认证失败或无权限")
        if r.status_code not in (200, 201, 204, 301):
            raise HTTPException(400, "重命名失败: HTTP %s" % r.status_code)

    def upload(self, lpath: str, file) -> None:
        self._req("PUT", self._url(lpath), data=file.file)  # requests 流式上传

    def download(self, lpath: str):
        r = self._req("GET", self._url(lpath), stream=True)
        for chunk in r.iter_content(1 << 16):
            if chunk:
                yield chunk


def _dav_parse(xml_bytes: bytes, base_url: str) -> list:
    """解析 PROPFIND 返回的 multistatus XML 为条目列表。

    从 base_url 计算当前目录自身的 href（仅路径部分），对其跳过，
    避免把当前文件夹当作自身条目返回。空目录时返回空列表。
    """
    import xml.etree.ElementTree as ET
    from urllib.parse import unquote, urlsplit

    ns = {"d": "DAV:"}
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return out
    resp_list = root.findall("d:response", ns)
    try:
        # 自身目录的规范化路径（仅 path 段，去尾斜杠）
        self_path = unquote(urlsplit(base_url).path).rstrip("/") or "/"
    except Exception:
        self_path = None
    for resp in resp_list:
        href_el = resp.find("d:href", ns)
        if href_el is None or not href_el.text:
            continue
        href = unquote(href_el.text)
        # 取 href 的路径段，过滤当前目录自身（href 可能是形如 /dav/user/ 的
        # 绝对路径，也可能是带域名的完整 URL，统一用路径段比对）
        try:
            href_path = urlsplit(href).path.rstrip("/") or "/"
        except Exception:
            continue
        if self_path is not None and href_path == self_path:
            continue
        prop = resp.find("d:propstat/d:prop", ns)
        is_dir = prop is not None and prop.find("d:resourcetype/d:collection", ns) is not None
        size = 0
        size_el = prop.find("d:getcontentlength", ns) if prop is not None else None
        if size_el is not None and size_el.text:
            try:
                size = int(size_el.text)
            except (TypeError, ValueError):
                size = 0
        mtime = 0
        mtime_el = prop.find("d:getlastmodified", ns) if prop is not None else None
        if mtime_el is not None and mtime_el.text:
            mtime = _dav_ts(mtime_el.text)
        name = href.rstrip("/").split("/")[-1]
        if not name:
            continue
        out.append(dict(name=name, is_dir=is_dir, size=size, modified=mtime))
    return out


def _dav_ts(s: str) -> float:
    """解析 RFC 1123 时间格式为 epoch 秒。"""
    try:
        from email.utils import parsedate_to_datetime
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        return 0


class S3Adapter(BaseAdapter):
    """对象存储（S3 兼容）：基于 minio。对象键以 / 分割建立「文件夹」视图。"""

    def __init__(self, conn):
        self.conn = conn
        self.bucket = (conn.get("base") or "").strip()
        params = conn.get("params", {}) or {}
        self.region = params.get("region") or None
        self.secure = bool(params.get("secure", conn.get("port", 9000) == 443))

    def _client(self):
        from minio import Minio
        endpoint = self.conn["host"]
        port = self.conn.get("port")
        if port and port not in (80, 443) and ":" not in endpoint:
            endpoint = endpoint + ":" + str(port)
        # SSRF 防护（纵深防御，与 validate_rules 同基线）：每次创建客户端前
        # 复检主机，拒绝云元数据（169.254.169.254）等受保护地址。
        try:
            from app.ssrf_guard import assert_safe_host
            assert_safe_host(endpoint, allow_private=True)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return Minio(
            endpoint,
            access_key=self.conn.get("username") or "",
            secret_key=self.conn.get("password") or "",
            secure=self.secure,
            region=self.region,
        )

    def _prefix(self, lpath: str) -> str:
        p = (lpath or "/").strip("/")
        return (p + "/") if p else ""

    def _key(self, lpath: str) -> str:
        if lpath in ("", "/"):
            return self.bucket
        return (lpath or "/").strip("/")

    def _obj_key(self, lpath: str) -> str:
        return (lpath or "/").strip("/")

    def list(self, lpath: str):
        client = self._client()
        prefix = self._prefix(lpath)
        try:
            objs = list(client.list_objects(self.bucket, prefix=prefix, recursive=False))
        except Exception as e:
            raise HTTPException(400, "读取对象列表失败: %s" % _safe_err(e))
        items = []
        for o in objs:
            key = (o.object_name or "")
            if not key:
                continue
            # 过滤掉当前目录自身的 key（prefix 本身以 / 结尾时的目录对象）
            if key == prefix.rstrip("/"):
                continue
            # minio 以 recursive=False + delimiter 返回子目录占位对象
            is_dir = key.endswith("/")
            base = key[len(prefix):] if key.startswith(prefix) else key
            name = base.rstrip("/").split("/")[-1]
            if not name:
                continue
            items.append(dict(name=name, path=_join_l(lpath, name), is_dir=is_dir,
                              size=0 if is_dir else (o.size or 0),
                              modified=int(o.last_modified.timestamp()) if getattr(o, "last_modified", None) else 0))
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return dict(path=lpath or "/", parent=_lp_parent(lpath), items=items)

    def read(self, lpath: str) -> str:
        client = self._client()
        try:
            # 流式限长（第十四轮审计修复）：resp.stream 分块累计，超 2MB 即中断
            resp = client.get_object(self.bucket, self._obj_key(lpath))
            try:
                data = _read_limited(resp.stream(1 << 16))
            finally:
                resp.close()
                resp.release_conn()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, "读取对象失败: %s" % _safe_err(e))
        return data.decode("utf-8", errors="replace")

    def write(self, lpath: str, content: str) -> None:
        client = self._client()
        data = content.encode("utf-8")
        try:
            client.put_object(self.bucket, self._obj_key(lpath), BytesIO(data), len(data))
        except Exception as e:
            raise HTTPException(400, "写入对象失败: %s" % _safe_err(e))

    def mkdir(self, lpath: str) -> None:
        client = self._client()
        try:
            client.put_object(self.bucket, self._obj_key(lpath) + "/", BytesIO(b""), 0)
        except Exception as e:
            raise HTTPException(400, "新建目录失败: %s" % _safe_err(e))

    def _rm_prefix(self, client, prefix: str) -> None:
        names = [o.object_name for o in client.list_objects(self.bucket, prefix=prefix, recursive=True)]
        # 分批 remove_objects，避免单次请求对象过多
        for i in range(0, len(names), TOKEN_LIMIT):
            batch = names[i:i + TOKEN_LIMIT]
            for err in client.remove_objects(self.bucket, batch):
                logger.warning("删除对象失败 %s: %s", err.object_name, err)

    def delete(self, lpath: str) -> None:
        client = self._client()
        key = self._obj_key(lpath)
        try:
            st = client.stat_object(self.bucket, key)
            if st and ((st.size or 0) >= 0 and (key.endswith("/") or True)):
                pass  # 占位检测对象本身存在即是空目录标记，一并删除下面前缀即可
        except Exception:
            pass
        try:
            self._rm_prefix(client, key.rstrip("/") + "/")
        except Exception as e:
            raise HTTPException(400, "删除失败: %s" % _safe_err(e))
        # 一并删除占位目录对象（若存在）
        try:
            client.remove_object(self.bucket, key.rstrip("/") + "/")
        except Exception:
            pass
        try:
            client.remove_object(self.bucket, key)
        except Exception:
            pass

    def rename(self, src: str, dst: str) -> None:
        client = self._client()
        sk = self._obj_key(src)
        dk = self._obj_key(dst)
        try:
            self._rm_prefix(client, dk + "/")  # 目标已存在则先清理
        except Exception:
            pass
        st = None
        try:
            st = client.stat_object(self.bucket, sk)
        except Exception:
            st = None
        if st and sk.endswith("/"):
            # 目录：逐对象重命名
            prefix = sk
            names = [o.object_name for o in client.list_objects(self.bucket, prefix=prefix, recursive=True)]
            for n in names:
                rel = n[len(prefix):]
                try:
                    client.copy_object(self.bucket, dk + "/" + rel, self.bucket, n)
                except Exception as e:
                    raise HTTPException(400, "重命名失败: %s" % _safe_err(e))
            for n in names:
                try:
                    client.remove_object(self.bucket, n)
                except Exception:
                    # 旧对象删除失败（已被清理）时忽略
                    pass
            try:
                client.remove_object(self.bucket, sk)
            except Exception:
                # 目录占位对象删除失败时忽略
                pass
            if rel == "":
                client.put_object(self.bucket, dk + "/", BytesIO(b""), 0)
        else:
            try:
                client.copy_object(self.bucket, dk, self.bucket, sk)
                client.remove_object(self.bucket, sk)
            except Exception as e:
                raise HTTPException(400, "重命名失败: %s" % _safe_err(e))

    def upload(self, lpath: str, file) -> None:
        client = self._client()
        key = self._obj_key(lpath)
        try:
            client.put_object(self.bucket, key, file.file, file.size or -1)
        except Exception:
            # 长度未知时改为缓冲写入（minio 需要显式长度）
            data = file.file.read()
            client.put_object(self.bucket, key, BytesIO(data), len(data))

    def download(self, lpath: str):
        client = self._client()
        resp = client.get_object(self.bucket, self._obj_key(lpath))
        try:
            for chunk in resp.stream(1 << 16):
                yield chunk
        finally:
            resp.close()
            resp.release_conn()


def _lp_parent(lpath: str) -> Optional[str]:
    """计算逻辑路径的父目录；已是根则 None。"""
    p = (lpath or "/").strip("/")
    if not p:
        return None
    parts = p.split("/")
    parts.pop()
    if not parts:
        return "/"
    return "/" + "/".join(parts)


def _join_l(base: str, name: str) -> str:
    """逻辑路径 + 文件名拼接。"""
    b = (base or "/").strip("/")
    if not b:
        return "/" + name
    return "/" + b + "/" + name


def _make_adapter(conn: dict) -> BaseAdapter:
    t = conn.get("type")
    if t in ("ftp", "ftps"):
        return FTPAdapter(conn)
    if t == "smb":
        return SMBAdapter(conn)
    if t == "webdav":
        return WebDAVAdapter(conn)
    if t == "s3":
        return S3Adapter(conn)
    raise HTTPException(400, "不支持的存储类型")


def _validate_lpath(lpath: Optional[str]) -> str:
    """云端逻辑路径规范化：必须以 / 开头，拒绝控制字符与穿越。

    安全修复（第十四轮审计，High）：此前只拦截明文 ".." 段，攻击者可
    用百分号编码（%2e%2e / %2e. / .%2e）绕过——RFC 3986 中 %2E == '.',
    真实 WebDAV/FTP/S3 服务器收到 URL 后会先解码再规范化路径，面板
    拼出的 base + "/%2e%2e/x" 实际指向 base 之外（越权读写远端文件）。
    现改为：先对每个路径段做 URL 解码，再判定是否为 ".."（或解码后
    残留斜杠/反斜杠/空字节等试图拆分或逃逸路径的编码）。
    """
    from urllib.parse import unquote

    p = (lpath or "/").strip() or "/"
    if _CTRL.search(p):
        raise HTTPException(400, "路径包含非法字符")
    if not p.startswith("/"):
        raise HTTPException(400, "路径必须以 / 开头")
    # 归一化并拦截 .. 穿越（含百分号编码变体）
    parts = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        # URL 解码后再判定：%2e%2e / %2e. / .%2e 均为 ".." 语义。
        # 迭代解码（最多 3 层）以覆盖双重编码（%252e%252e）——WAF / 反代 /
        # 应用层存在多层解码场景时，单次 unquote 拦不住二次编码的 ".."。
        dseg = seg
        for _ in range(3):
            nd = unquote(dseg)
            if nd == dseg:
                break
            dseg = nd
        if dseg == "..":
            raise HTTPException(400, "路径非法（不允许 .. 或编码变体）")
        # 解码后的斜杠/反斜杠/空字节会拆分段或逃逸路径，一律拒绝
        if "/" in dseg or "\\" in dseg or "\x00" in dseg:
            raise HTTPException(400, "路径包含非法编码字符")
        parts.append(seg)
    return "/" + "/".join(parts) if parts else "/"


# ---------------------------------------------------------------------------
# 路由：连接管理
# ---------------------------------------------------------------------------
def _require_admin_user(user: dict) -> None:
    # 兜底：即使前缀未挂依赖，也强制校验管理员（纵深防御）
    if user.get("role") != "admin" and user.get("is_admin") is not True:
        raise HTTPException(403, "需要管理员权限")


@router.get("/connections")
async def list_connections(user: dict = Depends(get_current_user)):
    """返回全部连接（脱敏）。"""
    _require_admin_user(user)
    return {"connections": [_mask(c) for c in _load()], "types": list(ALLOWED_TYPES)}


@router.post("/connections")
async def create_connection(
    payload: ConnectionIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """新增连接；username/password 允许为空串（匿名）。"""
    _require_admin_user(user)
    payload.validate_rules()
    conns = _load()
    cid = uuid.uuid4().hex[:12]
    conn = {
        "id": cid,
        "name": payload.name.strip(),
        "type": payload.type,
        "host": payload.host.strip(),
        "port": payload.port,
        "username": payload.username.strip(),
        "password": payload.password,
        "base": (payload.base or "").strip() or None,
        "params": payload.params or {},
    }
    conns.append(conn)
    _save(conns)
    auditlog.record("新增网络储存", user["username"], get_client_ip(request), conn["name"])
    return _mask(conn)


@router.put("/connections/{cid}")
async def update_connection(
    cid: str,
    payload: ConnectionIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """更新连接；password 为空串表示保持原密码。"""
    _require_admin_user(user)
    payload.validate_rules()
    conns = _load()
    target = next((c for c in conns if c.get("id") == cid), None)
    if not target:
        raise HTTPException(404, "连接不存在")
    old = target.get("password") or ""
    new_host = payload.host.strip()
    new_base = (payload.base or "").strip() or None
    # 安全修复（第十四轮审计，Medium）：目标服务器（host/port/base 的
    # authority）变化而密码留空时，旧密码会被静默发往新服务器（凭据转发
    # 泄露）。端点变更必须显式提供新密码；仅名称/路径变化时留空保持原密码。
    endpoint_changed = (
        new_host != (target.get("host") or "")
        or payload.port != target.get("port")
        or _url_authority(new_base or "") != _url_authority(target.get("base") or "")
    )
    if endpoint_changed and not payload.password:
        raise HTTPException(
            status_code=400,
            detail="连接地址已变更，请显式提供新密码（密码留空不再沿用旧密码，避免凭据泄露到新服务器）",
        )
    target.update({
        "name": payload.name.strip(),
        "type": payload.type,
        "host": new_host,
        "port": payload.port,
        "username": payload.username.strip(),
        "base": new_base,
        "params": payload.params or {},
    })
    # 留空则保留原密码，否则覆盖
    if payload.password:
        target["password"] = payload.password
    elif payload.password == "":
        target["password"] = old
    _save(conns)
    auditlog.record("更新网络储存", user["username"], get_client_ip(request), target["name"])
    return _mask(target)


@router.delete("/connections/{cid}")
async def delete_connection(
    cid: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    _require_admin_user(user)
    conns = _load()
    target = next((c for c in conns if c.get("id") == cid), None)
    if not target:
        raise HTTPException(404, "连接不存在")
    conns = [c for c in conns if c.get("id") != cid]
    _save(conns)
    auditlog.record("删除网络储存", user["username"], get_client_ip(request), target["name"])
    return {"ok": True}


@router.post("/connections/{cid}/test")
async def test_connection(cid: str, user: dict = Depends(get_current_user)):
    """测试连通性：尝试列出根目录。"""
    _require_admin_user(user)
    conn = _get_conn(cid)
    adapter = _make_adapter(conn)
    try:
        await _run_blocking(adapter.list, "/")
        return {"ok": True, "message": "连接成功"}
    except HTTPException as e:
        return {"ok": False, "message": e.detail}
    except Exception as e:
        logger.warning("连接测试失败 %s: %s", conn.get("name"), _safe_err(e))
        # 安全（code-scanning py/stack-trace-exposure）：详情已记日志，不回传
        return {"ok": False, "message": "连接失败"}


# ---------------------------------------------------------------------------
# 路由：远程文件操作
# ---------------------------------------------------------------------------
def _get_adapter_for(cid: str):
    conn = _get_conn(cid)
    return conn, _make_adapter(conn)


@router.get("/connections/{cid}/list")
async def ns_list(cid: str, path: Optional[str] = None, user: dict = Depends(get_current_user)):
    """列出远程目录内容。"""
    _require_admin_user(user)
    conn, adapter = _get_adapter_for(cid)
    lp = _validate_lpath(path)
    try:
        result = await _run_blocking(adapter.list, lp)
        result["protocol"] = conn.get("type")
        result["label"] = _proto_label(conn.get("type"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("列出 %s 失败: %s", conn.get("name"), _safe_err(e))
        raise HTTPException(500, "列出目录失败: %s" % _safe_err(e))


@router.get("/connections/{cid}/read")
async def ns_read(cid: str, path: str, user: dict = Depends(get_current_user)):
    """读取远程文件文本内容（不超过 2MB）。"""
    _require_admin_user(user)
    conn, adapter = _get_adapter_for(cid)
    lp = _validate_lpath(path)
    try:
        return {"path": lp, "content": await _run_blocking(adapter.read, lp)}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.warning("读取 %s 失败: %s", conn.get("name"), _safe_err(e))
        raise HTTPException(500, "读取文件失败: %s" % _safe_err(e))


class ContentReq(BaseModel):
    path: str
    content: str


@router.post("/connections/{cid}/write")
async def ns_write(cid: str, req: ContentReq, user: dict = Depends(get_current_user)):
    """写远程文件文本内容。"""
    _require_admin_user(user)
    conn, adapter = _get_adapter_for(cid)
    lp = _validate_lpath(req.path)
    try:
        await _run_blocking(adapter.write, lp, req.content)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("写入 %s 失败: %s", conn.get("name"), _safe_err(e))
        raise HTTPException(500, "写入文件失败: %s" % _safe_err(e))


class PathReq(BaseModel):
    path: str


@router.post("/connections/{cid}/mkdir")
async def ns_mkdir(cid: str, req: PathReq, user: dict = Depends(get_current_user)):
    _require_admin_user(user)
    conn, adapter = _get_adapter_for(cid)
    lp = _validate_lpath(req.path)
    try:
        await _run_blocking(adapter.mkdir, lp)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("新建目录 %s 失败: %s", conn.get("name"), _safe_err(e))
        raise HTTPException(500, "新建目录失败: %s" % _safe_err(e))


@router.post("/connections/{cid}/delete")
async def ns_delete(cid: str, req: PathReq, user: dict = Depends(get_current_user)):
    _require_admin_user(user)
    conn, adapter = _get_adapter_for(cid)
    lp = _validate_lpath(req.path)
    if lp == "/":
        raise HTTPException(400, "不能删除根目录")
    try:
        await _run_blocking(adapter.delete, lp)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("删除 %s 失败: %s", conn.get("name"), _safe_err(e))
        raise HTTPException(500, "删除失败: %s" % _safe_err(e))


class RenameReq(BaseModel):
    src: str
    dst: str


@router.post("/connections/{cid}/rename")
async def ns_rename(cid: str, req: RenameReq, user: dict = Depends(get_current_user)):
    _require_admin_user(user)
    conn, adapter = _get_adapter_for(cid)
    src = _validate_lpath(req.src)
    dst = _validate_lpath(req.dst)
    try:
        await _run_blocking(adapter.rename, src, dst)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("重命名 %s 失败: %s", conn.get("name"), _safe_err(e))
        raise HTTPException(500, "重命名失败: %s" % _safe_err(e))


@router.post("/connections/{cid}/upload")
async def ns_upload(
    cid: str,
    request: Request,
    user: dict = Depends(get_current_user),
    path: str = Form(...),
    file: UploadFile = File(...),
):
    """上传：path 为目标云端目录，file 为二进制。"""
    _require_admin_user(user)
    conn, adapter = _get_adapter_for(cid)
    dirp = _validate_lpath(path)
    raw_name = (file.filename or "").replace("\\", "/").split("/")[-1].strip()
    if not raw_name or raw_name in (".", ".."):
        raise HTTPException(400, "非法文件名")
    if _CTRL.search(raw_name):
        raise HTTPException(400, "文件名包含非法字符")
    target = _join_l(dirp, raw_name)
    try:
        await _run_blocking(adapter.upload, target, file)
        auditlog.record("网络储存上传", user["username"], get_client_ip(request),
                        "%s:%s" % (conn.get("name"), target))
        return {"ok": True, "path": target}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("上传到 %s 失败: %s", conn.get("name"), _safe_err(e))
        raise HTTPException(500, "上传失败: %s" % _safe_err(e))


@router.get("/connections/{cid}/download")
async def ns_download(cid: str, path: str, user: dict = Depends(get_current_user)):
    """下载远程文件（流式）。"""
    _require_admin_user(user)
    conn, adapter = _get_adapter_for(cid)
    lp = _validate_lpath(path)
    name = lp.split("/")[-1] or "download"
    try:
        gen = adapter.download(lp)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("下载 %s 失败: %s", conn.get("name"), _safe_err(e))
        raise HTTPException(500, "下载失败: %s" % _safe_err(e))
    # 文件名用 URL 编码避免注入响应头
    import urllib.parse
    quoted = urllib.parse.quote(name)
    return StreamingResponse(
        gen,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename*=UTF-8\'\'%s' % quoted},
    )