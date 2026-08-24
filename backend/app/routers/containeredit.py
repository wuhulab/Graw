# -*- coding: utf-8 -*-
"""容器资源与端口编辑路由（/api/containeredit，仅管理员）。

复用 docker_api.py 的统一后端探测 / CLI 发现 / 输入校验 / 错误归一化工具，
保持与 Docker 窗口（容器/镜像/网络）一致的 CLI（podman/docker）+ Docker SDK
双后端实现。提供三类容器编辑操作：

  - GET  /{id}/info            读取容器可编辑配置（CPU / 内存 / 环境变量 / 端口 / 重启策略）
  - POST /{id}/update-limits   在线更新容器的 CPU / 内存限制（podman update / SDK update）
  - POST /{id}/rebuild         按新的环境变量 / 端口映射重建容器（高风险操作）

其中 rebuild 属于高风险操作：CLI 模式通过重放容器 Config.CreateCommand 实现
（实现可靠）；SDK 模式仅尽力而为（受 docker SDK 配置还原能力限制）。
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List

from app.routers.docker_api import (
    _run,
    _find_podman,
    _podman_json,
    _safe_docker_ref,
    _clean_reason,
    get_backend,
)

logger = logging.getLogger("containeredit")

router = APIRouter()

# 资源限制校验边界（与前端输入约束保持一致）
_CPU_MIN = 0.1
_CPU_MAX = 64.0
_MEM_MB_MIN = 32
_MEM_MB_MAX = 262144


# ------------------------------------------------------------
# 请求体模型
# ------------------------------------------------------------
class UpdateLimitsRequest(BaseModel):
    """更新资源限制请求：cpus 核数 0.1-64；memory_mb 0（不限）或 32-262144 MB。"""

    cpus: float
    memory_mb: int


class EnvItem(BaseModel):
    """环境变量条目（键值对）。"""

    key: str = ""
    value: str = ""


class PortItem(BaseModel):
    """端口映射条目。"""

    host_port: str = ""
    container_port: str = ""
    protocol: str = "tcp"
    ip: str = ""


class RebuildRequest(BaseModel):
    """重建容器请求：新的环境变量与端口映射列表。"""

    env: List[EnvItem] = Field(default_factory=list)
    ports: List[PortItem] = Field(default_factory=list)


# ------------------------------------------------------------
# 解析辅助
# ------------------------------------------------------------
def _parse_cpu_cores(host_config: dict) -> float:
    """从 HostConfig 解析容器 CPU 限额（核数），0 表示未限制。

    优先 NanoCpus（纳秒级配额 /1e9 即核数），回退 CpuQuota/CpuPeriod（相除即核数）。
    """
    nano_cpus = host_config.get("NanoCpus", 0) or 0
    cpu_quota = host_config.get("CpuQuota", 0) or 0
    cpu_period = host_config.get("CpuPeriod", 0) or 0
    if nano_cpus > 0:
        return round(nano_cpus / 1e9, 2)
    if cpu_quota > 0 and cpu_period > 0:
        return round(cpu_quota / cpu_period, 2)
    return 0.0


def _parse_env_list(env_list) -> list:
    """把 Config.Env（["KEY=value", ...]）解析为 [{key, value}]。

    兼容缺失 "=" 的畸形条目（key 为整个字符串，value 为空）。
    """
    result = []
    for item in env_list or []:
        if not item:
            continue
        if "=" in item:
            k, v = item.split("=", 1)
        else:
            k, v = item, ""
        result.append({"key": k, "value": v})
    return result


def _parse_port_bindings(port_bindings) -> list:
    """把 HostConfig.PortBindings 解析为 [{ip, host_port, container_port, protocol}]。

    兼容输入：{"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}], "9000/udp": [...]}
    """
    result = []
    if not port_bindings:
        return result
    for container_port, bindings in port_bindings.items():
        if "/" in container_port:
            cport, proto = container_port.split("/", 1)
        else:
            cport, proto = container_port, "tcp"
        if not bindings:
            continue
        if not isinstance(bindings, list):
            bindings = [bindings]
        for b in bindings:
            if not isinstance(b, dict):
                continue
            result.append({
                "ip": b.get("HostIp") or "",
                "host_port": str(b.get("HostPort") or ""),
                "container_port": str(cport),
                "protocol": proto or "tcp",
            })
    return result


# ------------------------------------------------------------
# 读取容器可编辑配置
# ------------------------------------------------------------
@router.get("/{container_id}/info")
async def container_edit_info(container_id: str):
    """返回容器的可编辑配置（CPU / 内存 / 环境变量 / 端口 / 重启策略）。"""
    return await asyncio.to_thread(_container_edit_info_sync, container_id)


def _container_edit_info_sync(container_id: str):
    container_id = _safe_docker_ref(container_id, "容器标识")
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        try:
            arr = _podman_json(["inspect", container_id])
            if not arr:
                raise HTTPException(status_code=404, detail="容器不存在")
            c = arr[0]
            host_config = c.get("HostConfig", {}) or {}
            config = c.get("Config", {}) or {}
            memory = host_config.get("Memory", 0) or 0
            return {
                "id": (c.get("Id", "") or container_id)[:12],
                "name": c.get("Name", ""),
                "state": (c.get("State", {}) or {}).get("Status", ""),
                "image": config.get("Image", "") or c.get("ImageName", ""),
                "cpu_cores": _parse_cpu_cores(host_config),
                "memory_mb": round(memory / 1024 / 1024) if memory > 0 else 0,
                "memory_unlimited": memory <= 0,
                "env": _parse_env_list(config.get("Env")),
                "ports": _parse_port_bindings(host_config.get("PortBindings")),
                "restart_policy": (host_config.get("RestartPolicy") or {}).get("Name", ""),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))
    # Docker SDK 模式
    try:
        c = client.containers.get(container_id)
        attrs = c.attrs
        host_config = attrs.get("HostConfig", {}) or {}
        config = attrs.get("Config", {}) or {}
        memory = host_config.get("Memory", 0) or 0
        image = ""
        if c.image:
            image = c.image.tags[0] if c.image.tags else c.image.short_id
        return {
            "id": c.short_id,
            "name": c.name,
            "state": (attrs.get("State", {}) or {}).get("Status", ""),
            "image": image,
            "cpu_cores": _parse_cpu_cores(host_config),
            "memory_mb": round(memory / 1024 / 1024) if memory > 0 else 0,
            "memory_unlimited": memory <= 0,
            "env": _parse_env_list(config.get("Env")),
            "ports": _parse_port_bindings(host_config.get("PortBindings")),
            "restart_policy": (host_config.get("RestartPolicy") or {}).get("Name", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))


# ------------------------------------------------------------
# 在线更新 CPU / 内存限制
# ------------------------------------------------------------
@router.post("/{container_id}/update-limits")
async def update_container_limits(container_id: str, req: UpdateLimitsRequest):
    """在线更新容器的 CPU / 内存限制（无需重建容器）。"""
    return await asyncio.to_thread(_update_limits_sync, container_id, req)


def _update_limits_sync(container_id: str, req: UpdateLimitsRequest):
    # 参数校验：CPU 0.1-64；内存 0（不限）或 32-262144 MB
    cpus = float(req.cpus)
    memory_mb = int(req.memory_mb)
    if not (_CPU_MIN <= cpus <= _CPU_MAX):
        raise HTTPException(status_code=400, detail=f"CPU 核数须在 {_CPU_MIN}-{_CPU_MAX} 之间")
    if memory_mb != 0 and not (_MEM_MB_MIN <= memory_mb <= _MEM_MB_MAX):
        raise HTTPException(
            status_code=400,
            detail=f"内存限制须为 0（不限）或 {_MEM_MB_MIN}-{_MEM_MB_MAX} MB",
        )
    container_id = _safe_docker_ref(container_id, "容器标识")
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        # podman update --cpus <n> [--memory <m>m] <id>；memory_mb=0 表示不限制
        cpus_str = f"{cpus:g}"  # 去掉多余的尾零（1.0 -> 1，0.5 -> 0.5）
        cmd = _find_podman() + ["update", "--cpus", cpus_str]
        if memory_mb > 0:
            cmd += ["--memory", f"{memory_mb}m"]
        else:
            cmd += ["--memory", "0"]
        cmd += [container_id]
        rc, out, err = _run(cmd, timeout=60)
        if rc != 0:
            raise HTTPException(status_code=500, detail=err.strip() or "更新资源限制失败")
        logger.info("已更新容器 %s 资源限制: cpus=%s memory_mb=%s", container_id, cpus, memory_mb)
        return {"ok": True}
    # Docker SDK 模式：cpu_quota = cpus * period（默认 100000）；mem_limit=0 表示不限
    try:
        c = client.containers.get(container_id)
        c.update(
            cpu_period=100000,
            cpu_quota=int(round(cpus * 100000)),
            mem_limit=(memory_mb * 1024 * 1024) if memory_mb > 0 else 0,
        )
        logger.info("已更新容器 %s 资源限制(SDK): cpus=%s memory_mb=%s", container_id, cpus, memory_mb)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))


# ------------------------------------------------------------
# 重建容器（应用环境变量 / 端口映射修改，高风险）
# ------------------------------------------------------------
def _strip_flags(args: list, flags: tuple) -> list:
    """从命令行参数中移除指定的成对 flag（--env X 与 --env=X 两种形式）。

    podman/docker 的 Config.CreateCommand 中 --env/-e 与 --publish/-p 均携带一个
    值参数（分 token 或 `=` 拼接两种形态），这里把 flag 及其值整体移除，
    便于后续追加新的环境变量 / 端口映射参数。
    """
    result = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in flags:
            # flag 与值分两个 token（--env KEY=value）；正常值不会以 - 开头
            if i + 1 < len(args) and not str(args[i + 1]).startswith("-"):
                i += 2
            else:
                i += 1
            continue
        if any(str(arg).startswith(f + "=") for f in flags):
            i += 1
            continue
        result.append(arg)
        i += 1
    return result


def _rebuild_cli(container_id: str, env: list, ports: list) -> dict:
    """CLI 模式重建：重放 Config.CreateCommand，替换环境变量与端口映射。

    步骤：inspect 取 CreateCommand -> 移除旧 --env/-e 与 --publish/-p ->
    追加新参数（插在 run/create 之后，保证位于镜像名之前）-> rm -f 旧容器 ->
    重新执行创建命令 -> create 模式手动 start。
    """
    arr = _podman_json(["inspect", container_id])
    if not arr:
        raise HTTPException(status_code=404, detail="容器不存在")
    c = arr[0]
    create_cmd = (c.get("Config", {}) or {}).get("CreateCommand", [])
    if not create_cmd:
        raise HTTPException(status_code=400, detail="无法获取容器创建参数，请手动重建")
    # 去掉开头的 "podman"（保留 run/create），再拼接引擎前缀执行
    sub = create_cmd[1:] if create_cmd[:1] == ["podman"] else create_cmd
    if not sub or sub[0] not in ("run", "create"):
        raise HTTPException(status_code=400, detail=f"无法识别的容器创建命令: {sub[:1]!r}")

    # 1) 移除旧的 --env/-e 与 --publish/-p 参数
    sub = _strip_flags(sub, ("--env", "-e", "--publish", "-p"))

    # 2) 组装新的 --env / --publish 参数（插在 run/create 之后，位于镜像名之前）
    new_args = []
    for kv in env:
        key = (kv.get("key") or "").strip()
        if not key:
            continue
        new_args += ["--env", f"{key}={kv.get('value', '')}"]
    for p in ports:
        host_port = str(p.get("host_port") or "").strip()
        cport = str(p.get("container_port") or "").strip()
        if not host_port or not cport:
            continue
        proto = (p.get("protocol") or "tcp").strip() or "tcp"
        new_args += ["--publish", f"{host_port}:{cport}/{proto}"]
    sub = sub[:1] + new_args + sub[1:]
    is_create = sub[0] == "create"

    # 3) 删除旧容器并重建（旧容器删除失败不阻塞，继续重建）
    _run(_find_podman() + ["rm", "-f", container_id], timeout=30)
    rc, create_out, create_err = _run(_find_podman() + sub, timeout=120)
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"重建容器失败: {create_err.strip() or create_out.strip()}")

    new_id = create_out.strip()
    # podman create 只创建不启动，需要手动 start
    if is_create and new_id:
        rc_s, _so, se = _run(_find_podman() + ["start", new_id], timeout=60)
        if rc_s != 0:
            raise HTTPException(status_code=500, detail=f"启动容器失败: {se.strip()}")

    logger.info("已重建容器 %s -> %s", container_id, (new_id or "")[:12])
    return {"ok": True, "new_container_id": (new_id or "")[:12]}


def _rebuild_sdk(client, container_id: str, env: list, ports: list) -> dict:
    """Docker SDK 模式重建（尽力而为）：修改 Env 与 PortBindings 后删除重建。

    说明：docker SDK 无法完整还原宿主配置（如 ipc/pid/ulimits 等），
    仅还原镜像 / 名称 / 命令 / 入口 / 卷 / 重启策略 / 网络等常用字段。
    """
    c = client.containers.get(container_id)
    attrs = c.attrs
    config = attrs.get("Config", {}) or {}
    host_config = attrs.get("HostConfig", {}) or {}
    name = c.name
    image = ""
    if c.image:
        image = c.image.tags[0] if c.image.tags else c.image.short_id
    if not image:
        raise HTTPException(status_code=400, detail="无法确定容器镜像，请手动重建")

    new_env = [f"{kv['key'].strip()}={kv['value']}" for kv in env if (kv.get("key") or "").strip()]
    port_bindings = {}
    for p in ports:
        host_port = str(p.get("host_port") or "").strip()
        cport = str(p.get("container_port") or "").strip()
        if not host_port or not cport:
            continue
        proto = (p.get("protocol") or "tcp").strip() or "tcp"
        port_bindings[f"{cport}/{proto}"] = [{"HostIp": p.get("ip") or "", "HostPort": host_port}]

    # 还原挂载（命名卷 / bind 挂载）
    volumes = {}
    for m in attrs.get("Mounts", []) or []:
        if m.get("Type") == "volume" and m.get("Name"):
            volumes[m["Name"]] = {"bind": m.get("Destination", ""), "mode": m.get("Mode", "rw")}
        elif m.get("Type") == "bind" and m.get("Source"):
            volumes[m["Source"]] = {"bind": m.get("Destination", ""), "mode": m.get("Mode", "rw")}

    kwargs = {
        "name": name,
        "detach": True,
        "environment": new_env,
        "ports": port_bindings,
        "volumes": volumes,
        "restart_policy": {"Name": (host_config.get("RestartPolicy") or {}).get("Name") or "no"},
        "network": host_config.get("NetworkMode") or "default",
        "entrypoint": config.get("Entrypoint"),
    }
    cmd = config.get("Cmd")
    if cmd:
        kwargs["command"] = cmd

    c.remove(force=True)
    new_c = client.containers.run(image, **kwargs)
    logger.info("已重建容器(SDK) %s -> %s", container_id, new_c.short_id)
    return {"ok": True, "new_container_id": new_c.short_id}


@router.post("/{container_id}/rebuild")
async def rebuild_container(container_id: str, req: RebuildRequest):
    """按新的环境变量 / 端口映射重建容器（高风险：会删除并重建原容器）。"""
    return await asyncio.to_thread(_rebuild_container_sync, container_id, req)


def _rebuild_container_sync(container_id: str, req: RebuildRequest):
    env = [{"key": e.key, "value": e.value} for e in req.env]
    ports = [
        {
            "host_port": p.host_port,
            "container_port": p.container_port,
            "protocol": p.protocol,
            "ip": p.ip,
        }
        for p in req.ports
    ]
    container_id = _safe_docker_ref(container_id, "容器标识")
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        return _rebuild_cli(container_id, env, ports)
    try:
        return _rebuild_sdk(client, container_id, env, ports)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))
