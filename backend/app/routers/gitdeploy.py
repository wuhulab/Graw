# -*- coding: utf-8 -*-
"""
gitdeploy.py（路由） - 站点 Git 自动部署 REST 接口

功能：
  - admin_router（挂全局 ADMIN 依赖，main.py 注册）：
      GET/POST /api/gitdeploy、PUT/DELETE /api/gitdeploy/{id}、POST /trigger
  - webhook_router（公开，端点内令牌校验，main.py 单独注册且排除 Agent 代理）：
      POST /api/gitdeploy/webhook/{deploy_id}

安全：
  - CRUD 全部 ADMIN；站点 id 校验存在、deploy_dir 默认站点 root 且路径防穿越、
    repo_url / branch 白名单（gitdeploy 库内校验）。
  - webhook 公开端点：HMAC-SHA256 或 ?secret= 校验通过才执行；分支不匹配
    返回 202 不执行；body 大小上限；执行放线程池不阻塞事件循环。
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import gitdeploy
from app.auth import get_current_user

logger = logging.getLogger("graw.gitdeploy")

admin_router = APIRouter()
webhook_router = APIRouter()


class SourceModel(BaseModel):
    """Git 仓库配置（repo_url 与 branch 的白名单校验在各端点内）。"""

    repo_url: str = Field(..., min_length=1, max_length=2048)
    branch: str = Field(default="main", min_length=1, max_length=128)
    auth: str = Field(default="none", pattern="^(none|token|ssh)$")
    token: str = Field(default="", max_length=2048)


class CreateDeployReq(BaseModel):
    """创建部署请求体。"""

    name: str = Field(default="", max_length=64)
    site_id: str = Field(..., min_length=1, max_length=64)
    source: SourceModel
    deploy_dir: str = Field(default="", max_length=1024)
    node_id: str = Field(default="local", max_length=64)
    notify: bool = True


class UpdateDeployReq(BaseModel):
    """更新部署请求体（全部可选，空值表示不变）。"""

    name: str | None = Field(default=None, max_length=64)
    repo_url: str | None = Field(default=None, min_length=1, max_length=2048)
    branch: str | None = Field(default=None, min_length=1, max_length=128)
    auth: str | None = Field(default=None, pattern="^(none|token|ssh)$")
    token: str | None = Field(default=None, max_length=4096)
    deploy_dir: str | None = Field(default=None, max_length=1024)
    node_id: str | None = Field(default=None, max_length=64)
    notify: bool | None = None
    reset_secret: bool = False


def _resolve_site_root(site_id: str) -> str:
    """校验站点存在（自建 + 外部站点合并列表）并返回文档根目录。"""
    try:
        from app.routers.sites import merged_sites
    except Exception as e:  # 站点模块不可用时拒绝创建，避免拿到错误 root
        raise HTTPException(status_code=503, detail=f"站点信息不可用: {e}")
    for s in merged_sites():
        if s.get("id") == site_id:
            return str(s.get("root") or "")
    raise HTTPException(status_code=400, detail=f"站点不存在: {site_id}")


def _validate_deploy_dir(deploy_dir: str, site_root: str) -> str:
    """deploy_dir 白名单校验：默认取站点 root，自定义必须为绝对路径且无控制字符。"""
    if not deploy_dir.strip():
        return site_root or "/var/www/html"
    d = deploy_dir.strip()
    if not gitdeploy._DIR_RE.match(d):
        raise HTTPException(status_code=400, detail="deploy_dir 必须为绝对路径且不含控制字符")
    return d


def _validate_repo_url(url: str) -> str:
    if not gitdeploy._REPO_RE.match(url):
        raise HTTPException(status_code=400, detail="repo_url 格式非法（仅支持 http/https/ssh/git@）")
    return url


def _validate_branch(branch: str) -> str:
    if not gitdeploy._BRANCH_RE.match(branch or ""):
        raise HTTPException(status_code=400, detail="branch 格式非法")
    return branch.strip()


def _validate_deploy_id(deploy_id: str) -> str:
    if not gitdeploy._ID_RE.match(deploy_id or ""):
        raise HTTPException(status_code=400, detail="非法的部署 ID")
    return deploy_id


# ---------------------------------------------------------------------------
# 管理端点（ADMIN）
# ---------------------------------------------------------------------------
@admin_router.get("")
def list_deploys():
    """部署列表（脱敏：不回传 token / secret）。"""
    return {"deploys": gitdeploy.list_deploys()}


@admin_router.post("")
def create_deploy(req: CreateDeployReq):
    """创建部署记录；deploy_dir 默认站点 root 可覆盖；返回一次性 secret。"""
    site_root = _resolve_site_root(req.site_id.strip())
    payload = {
        "name": req.name,
        "site_id": req.site_id.strip(),
        "site_name": None,  # 占位：由站点模块取实际名称
        "repo_url": _validate_repo_url(req.source.repo_url.strip()),
        "branch": _validate_branch(req.source.branch),
        "auth": req.source.auth,
        "token": req.source.token,
        "deploy_dir": _validate_deploy_dir(req.deploy_dir, site_root),
        "node_id": req.node_id.strip() or "local",
        "notify": req.notify,
    }
    # site_name：从合并站点列表补充（用于通知/日志展示）
    try:
        from app.routers.sites import merged_sites

        for s in merged_sites():
            if s.get("id") == req.site_id.strip():
                payload["site_name"] = str(s.get("name") or "")
                break
    except Exception:
        payload["site_name"] = ""
    created = gitdeploy.create_deploy(payload)
    return created


@admin_router.put("/{deploy_id}")
def update_deploy(deploy_id: str, req: UpdateDeployReq):
    """更新部署（secret 留空保持；reset_secret=true 重新生成）。"""
    deploy_id = _validate_deploy_id(deploy_id)
    patch = req.model_dump(exclude_none=True)
    if "repo_url" in patch:
        patch["repo_url"] = _validate_repo_url(patch["repo_url"].strip())
    if "branch" in patch:
        patch["branch"] = _validate_branch(patch["branch"])
    if "deploy_dir" in patch:
        existing = gitdeploy.get_deploy(deploy_id)
        if not existing:
            raise HTTPException(status_code=404, detail="部署不存在")
        patch["deploy_dir"] = _validate_deploy_dir(patch["deploy_dir"], existing["deploy_dir"])
    try:
        return gitdeploy.update_deploy(deploy_id, patch, reset_secret=bool(req.reset_secret))
    except KeyError:
        raise HTTPException(status_code=404, detail="部署不存在")


@admin_router.delete("/{deploy_id}")
def delete_deploy(deploy_id: str):
    """删除部署记录。"""
    deploy_id = _validate_deploy_id(deploy_id)
    if not gitdeploy.delete_deploy(deploy_id):
        raise HTTPException(status_code=404, detail="部署不存在")
    return {"ok": True}


@admin_router.post("/{deploy_id}/trigger")
async def trigger(deploy_id: str):
    """手动触发一次部署（同步等待结果，结果入任务中心）。"""
    deploy_id = _validate_deploy_id(deploy_id)
    if not gitdeploy.get_deploy(deploy_id):
        raise HTTPException(status_code=404, detail="部署不存在")
    try:
        # 部署涉及 git 网络操作，放线程池避免阻塞事件循环
        last_run = await asyncio.to_thread(gitdeploy.run_deploy, deploy_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        return {"ok": False, "error": str(e), "last_run": _last_run_of(deploy_id)}
    return {"ok": True, "last_run": last_run}


def _last_run_of(deploy_id: str) -> dict:
    d = gitdeploy.get_deploy(deploy_id)
    return (d or {}).get("last_run", {})


# ---------------------------------------------------------------------------
# Webhook 端点（公开，令牌校验）
# ---------------------------------------------------------------------------
@webhook_router.post("/webhook/{deploy_id}")
async def webhook(deploy_id: str, request: Request):
    """接收 Git 平台 Webhook：验签 + 分支匹配后异步部署。

    验签通过且分支匹配即触发部署并立即返回 200（执行在线程池）；
    分支不匹配返回 202 且不执行；验签失败返回 401。
    """
    deploy_id = _validate_deploy_id(deploy_id)
    deploy = gitdeploy.get_deploy(deploy_id)
    if not deploy:
        raise HTTPException(status_code=404, detail="部署不存在")
    body = await request.body()
    if len(body) > gitdeploy.MAX_BODY:
        raise HTTPException(status_code=413, detail="payload 过大")
    signature = request.headers.get("x-hub-signature-256", "") or ""
    query_secret = request.query_params.get("secret", "") or ""
    if not gitdeploy.verify_webhook(deploy, body, signature, query_secret):
        logger.warning("webhook 验签失败 deploy=%s ip=%s", deploy_id, request.client.host if request.client else "")
        raise HTTPException(status_code=401, detail="签名校验失败")
    branch = gitdeploy.webhook_branch(body)
    if branch and branch != deploy["source"].get("branch"):
        logger.info("webhook 分支 %s 与目标 %s 不匹配，跳过", branch, deploy["source"].get("branch"))
        return {"ok": True, "skipped": True, "reason": "branch 不匹配"}
    # 触发部署（后台线程），不阻塞 webhook 响应
    try:
        await asyncio.to_thread(gitdeploy.run_deploy, deploy_id)
    except Exception as e:  # 部署失败返回结果但不把 webhook 打成 5xx（Git 平台会重试）
        logger.warning("webhook 触发部署失败 deploy=%s: %s", deploy_id, e)
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": True}