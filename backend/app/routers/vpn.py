# -*- coding: utf-8 -*-
"""
vpn.py（路由） - VPN 转发策略 REST 接口

功能（管理面，PERM("vpn") 由 main.py 注册时挂载）：
  GET    /api/vpn            - 完整配置（含节点/转发条目/订阅令牌）+ 运行状态
  GET    /api/vpn/status     - 仅运行状态（窗口轮询）
  PUT    /api/vpn/config     - 保存策略（开关/端口转发/全局代理/低占用/节点/转发条目），
                               已运行的核心自动重启生效；关闭总开关时停核心
  POST   /api/vpn/start      - 手动启动核心
  POST   /api/vpn/stop       - 手动停止核心
  POST   /api/vpn/wake       - 唤醒（低占用休眠后由任意「初次请求」拉起）
  POST   /api/vpn/subtoken   - 重新生成订阅令牌（旧订阅链接立即失效）
  POST   /api/vpn/detect     - 立即探测核心可执行文件
  GET    /api/vpn/preview    - 预览将生成的 Clash 订阅 YAML
  GET    /api/vpn/log        - 核心运行日志尾部（排障）

公开端点（sub_router，不挂 Bearer——Clash 客户端无法携带请求头）：
  GET    /api/vpn/sub?token= - 订阅链接：凭令牌返回 Clash 兼容 YAML；
                               同时视为一次「初次请求」，会唤醒休眠中的核心

安全：
  - 管理接口依赖 main.py 的 PERM("vpn")；订阅接口仅凭 32 字节随机令牌，
    常量时间比对；未启用/令牌错误一律 404（不区分，防枚举）。
  - 本模块作用于面板所在主机（同 SSH 端口转发定位），已加入
    _AGENT_PROXY_EXCLUDE_PREFIX，不经 Agent 代理转发到子节点。
"""

import logging
import os

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app import vpn_policy

logger = logging.getLogger("graw.vpn")

router = APIRouter()
# 订阅接口：无全局鉴权依赖，凭 token 换取配置（Clash 客户端场景）
sub_router = APIRouter()

YAML_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Disposition": 'attachment; filename="graw-vpn.yaml"',
    "profile-update-interval": "24",
}


class NodeIn(BaseModel):
    """VPN 节点（Clash 兼容字段子集，按类型取用）。"""

    id: str = Field(default="", max_length=32)
    name: str = Field(..., min_length=1, max_length=64)
    type: str = Field(..., min_length=1, max_length=16)
    server: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    uuid: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=256)
    cipher: str = Field(default="", max_length=64)
    username: str = Field(default="", max_length=256)
    sni: str = Field(default="", max_length=255)
    flow: str = Field(default="", max_length=64)
    udp: bool = False
    tls: bool = False
    skipCertVerify: bool = False


class ForwardIn(BaseModel):
    """端口转发条目：本地监听端口 → 目标 host:port（可经 VPN 组）。"""

    id: str = Field(default="", max_length=32)
    name: str = Field(default="", max_length=64)
    listenPort: int = Field(..., ge=1024, le=65535)
    targetHost: str = Field(..., min_length=1, max_length=255)
    targetPort: int = Field(..., ge=1, le=65535)
    viaProxy: bool = False


class ConfigReq(BaseModel):
    """策略保存请求：全量覆盖（前端一次提交整个表单）。"""

    enabled: bool = False
    portForward: bool = True
    globalProxy: bool = False
    lowPower: bool = True
    idleMinutes: int = Field(default=15, ge=1, le=1440)
    mixedPort: int = Field(default=7890, ge=1024, le=65535)
    controllerPort: int = Field(default=9097, ge=1024, le=65535)
    binPath: str = Field(default="", max_length=512)
    nodes: list[NodeIn] = Field(default_factory=list)
    forwards: list[ForwardIn] = Field(default_factory=list)


@router.get("")
def get_config():
    """完整配置 + 运行状态（管理员配置窗口用）。"""
    store = vpn_policy.get_store()
    return {"config": store, "status": vpn_policy.get_status()}


@router.get("/status")
def get_status():
    """运行状态（轮询）。"""
    return vpn_policy.get_status()


@router.put("/config")
def save_config(req: ConfigReq):
    """保存策略：校验落盘；按开关状态同步核心生命周期并让新配置生效。"""
    patch = req.model_dump()
    try:
        store = vpn_policy.update_store(patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    vpn_policy.touch_activity()
    warning = ""
    if store["enabled"]:
        # 开启总开关立即拉起（低占用模式之后由看门狗按空闲关停）；
        # start_core 幂等：核心已在运行则先停再启，新配置随之生效。
        try:
            vpn_policy.start_core(reason="apply")
        except ValueError as e:
            logger.warning("应用 VPN 配置后启动核心失败: %s", e)
            warning = "核心启动失败，请查看面板日志"
    else:
        vpn_policy.stop_core("disabled")
    return {"ok": True, "config": store, "status": vpn_policy.get_status(), "warning": warning}


@router.post("/start")
def start():
    """手动启动核心。"""
    vpn_policy.touch_activity()
    try:
        vpn_policy.start_core(reason="manual")
    except ValueError as e:
        logger.warning("手动启动 VPN 核心失败: %s", e)
        raise HTTPException(status_code=400, detail="核心启动失败，请查看面板日志")
    return {"ok": True, "status": vpn_policy.get_status()}


@router.post("/stop")
def stop():
    """手动停止核心（低占用休眠与手动停止都以 stopReason 区分）。"""
    vpn_policy.stop_core("manual")
    return {"ok": True, "status": vpn_policy.get_status()}


@router.post("/wake")
def wake():
    """唤醒：低占用休眠后的「初次请求」入口（脚本/客户端可主动调用）。"""
    vpn_policy.touch_activity()
    started = False
    detail = ""
    if vpn_policy.get_store().get("enabled") and not vpn_policy.core_running():
        try:
            vpn_policy.start_core(reason="wake")
            started = True
        except ValueError as e:
            logger.warning("唤醒 VPN 核心失败: %s", e)
            detail = "核心启动失败，请查看面板日志"
    return {"ok": True, "started": started, "detail": detail, "status": vpn_policy.get_status()}


@router.post("/subtoken")
def rotate_token():
    """重新生成订阅令牌。"""
    token = vpn_policy.rotate_sub_token()
    return {"ok": True, "subToken": token}


@router.post("/detect")
def detect():
    """立即探测核心可执行文件路径。"""
    return {"binary": vpn_policy.detect_binary()}


@router.get("/preview")
def preview():
    """预览订阅 YAML（与 /api/vpn/sub 输出一致）。"""
    store = vpn_policy.get_store()
    return PlainTextResponse(
        vpn_policy.render_yaml(vpn_policy.build_subscription_config(store)),
        media_type="text/yaml; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/log")
def core_log():
    """核心运行日志尾部（排障）。"""
    if not os.path.exists(vpn_policy.CORE_LOG_FILE):
        return {"log": ""}
    try:
        with open(vpn_policy.CORE_LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 16384))
            data = f.read()
        return {"log": data.decode("utf-8", errors="replace")[-16000:]}
    except OSError:
        return {"log": ""}


# ---------------------------------------------------------------------------
# 公开订阅端点：凭 token 返回 Clash 兼容 YAML（不挂 Bearer 依赖）
# ---------------------------------------------------------------------------
@sub_router.get("/sub")
def subscription(token: str = ""):
    """Clash 订阅：常量时间校验令牌；拉取视为初次请求，唤醒休眠核心。"""
    if not vpn_policy.get_store().get("enabled"):
        raise HTTPException(status_code=404, detail="Not Found")
    if not vpn_policy.check_sub_token(token):
        raise HTTPException(status_code=404, detail="Not Found")
    # 初次请求唤醒：核心休眠中且总开关开启 → 自动拉起（失败不阻断订阅下发）
    vpn_policy.touch_activity()
    if vpn_policy.get_store().get("enabled") and not vpn_policy.core_running():
        try:
            vpn_policy.start_core(reason="wake")
        except ValueError as e:
            logger.info("订阅拉取唤醒核心未成功：%s", e)
    store = vpn_policy.get_store()
    yaml_text = vpn_policy.render_yaml(vpn_policy.build_subscription_config(store))
    logger.info("VPN 订阅已拉取（唤醒=%s）", vpn_policy.core_running())
    return Response(content=yaml_text, media_type="text/yaml; charset=utf-8", headers=YAML_HEADERS)
