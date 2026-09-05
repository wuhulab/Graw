# -*- coding: utf-8 -*-
"""
面板自身更新（update.py）核心逻辑单元测试（不依赖运行中的后端/容器）

覆盖：
  - 版本 tag 解析与比较（拒绝 latest/buildcache 等非版本号）
  - Docker Hub 最新版本查询（mock 网络，含异常降级）
  - 部署模式检测（容器 / 本机）
  - compose 上下文挂载与 -f 参数构造（相对/绝对路径）
  - docker run 单容器部署：检测、重建参数构造（镜像 tag 决策 / hostname /
    network 归一化 / HostConfig 白名单 / 数据目录推导 / 非官方镜像与
    container 网络拒绝）
  - /status 与 /apply 接口逻辑（含并发防抖、本机拒绝、compose 与
    docker-run 双分支、deploy_detail 细分）
  - 后台更新执行流程（mock docker SDK，验证独立容器调用与日志落盘；
    compose 分支 + docker run 分支）
  - 执行容器内置更新脚本语法合法性

用法：
  python test_update_unit.py
"""
import ast
import asyncio
import json
import os
import sys
import tempfile
import unittest.mock as mock

# 确保可导入 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.routers import update  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    msg = f"  PASS  {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def fail(name, detail):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {name}: {detail}")


def check(name, cond, detail=""):
    if cond:
        ok(name, detail)
    else:
        fail(name, detail)


# ------------------------------------------------------------
# 1. 版本解析与比较
# ------------------------------------------------------------
def test_version_key():
    print("\n[1] 版本解析与比较")
    check("1.2.0 > 1.1.9", update._version_key("1.2.0") > update._version_key("1.1.9"))
    check("v1.2.0 == 1.2.0", update._version_key("v1.2.0") == update._version_key("1.2.0"))
    check("非版本号返回 (0,0,0)", update._version_key("latest") == (0, 0, 0))
    check("buildcache 拒绝", update._version_key("buildcache") == (0, 0, 0))
    check("非版本号恒小于真实版本", update._version_key("latest") < update._version_key("1.0.0"))
    check("空串拒绝", update._version_key("") == (0, 0, 0))


# ------------------------------------------------------------
# 2. Docker Hub 最新版本查询（mock 网络）
# ------------------------------------------------------------
def _fake_response(payload: bytes):
    """构造一个带 context manager 的假 urllib 响应。"""
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return payload

    return _Resp()


def test_fetch_latest_version():
    print("\n[2] Docker Hub 最新版本查询")
    tags = [
        {"name": "latest"},
        {"name": "buildcache"},
        {"name": "1.0.0"},
        {"name": "1.1.8"},
        {"name": "1.2.0"},
        {"name": "v1.1.9"},
    ]
    payload = json_dumps({"results": tags})

    with mock.patch("urllib.request.urlopen", return_value=_fake_response(payload.encode())):
        got = update._fetch_latest_version()
        check("取到最大语义版本 1.2.0", got == "1.2.0", f"got={got}")

    with mock.patch("urllib.request.urlopen", return_value=_fake_response(json_dumps({"results": [{"name": "latest"}, {"name": "buildcache"}]}).encode())):
        got = update._fetch_latest_version()
        check("无版本号 tag 返回 None", got is None, f"got={got}")

    with mock.patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        got = update._fetch_latest_version()
        check("网络异常返回 None（不抛出）", got is None, f"got={got}")

    with mock.patch("urllib.request.urlopen", return_value=_fake_response(b"not json")):
        got = update._fetch_latest_version()
        check("响应非 JSON 返回 None", got is None, f"got={got}")


# ------------------------------------------------------------
# 3. 部署模式检测
# ------------------------------------------------------------
def test_is_container():
    print("\n[3] 部署模式检测")
    with mock.patch.dict(os.environ, {"HOST_ROOT": "/host"}, clear=False):
        with mock.patch("os.path.exists", return_value=False):
            check("HOST_ROOT 存在视为容器", update._is_container() is True)
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch("os.path.exists", return_value=True):
            check("存在 /.dockerenv 视为容器", update._is_container() is True)
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch("os.path.exists", return_value=False):
            check("本机（无标记）视为 local", update._is_container() is False)


# ------------------------------------------------------------
# 4. compose 上下文挂载与 -f 参数
# ------------------------------------------------------------
def test_compose_mounts():
    print("\n[4] compose 挂载与 -f 参数")
    volumes, args = update._compose_mounts("/root/graw", ["/root/graw/docker-compose.yml"])
    check("docker socket 挂载", volumes.get("/var/run/docker.sock", {}).get("bind") == "/var/run/docker.sock")
    check("compose 工作目录挂载到 /work", volumes.get("/root/graw", {}).get("bind") == "/work")
    check("相对路径转 -f docker-compose.yml", args == ["-f", "docker-compose.yml"], f"args={args}")

    volumes2, args2 = update._compose_mounts("/root/graw", ["/elsewhere/compose.yml"])
    check("目录外绝对路径保留原样", args2 == ["-f", "/elsewhere/compose.yml"], f"args={args2}")
    check("目录外路径不重复挂载", "/elsewhere/compose.yml" not in volumes2)


# ------------------------------------------------------------
# 5. /status 接口逻辑
# ------------------------------------------------------------
def test_status():
    print("\n[5] /status 接口逻辑")

    async def _run():
        return await update.update_status()

    with mock.patch.object(update, "_current_version", return_value="1.0.0"):
        with mock.patch.object(update, "_fetch_latest_version", return_value="1.2.0"):
            with mock.patch.object(update, "_is_container", return_value=False):
                r = asyncio.run(_run())
                check("有新版时 update_available=True", r["update_available"] is True, f"r={r}")
                check("deploy_mode=local", r["deploy_mode"] == "local")
                check("check_error=None", r["check_error"] is None)

    with mock.patch.object(update, "_current_version", return_value="1.2.0"):
        with mock.patch.object(update, "_fetch_latest_version", return_value="1.2.0"):
            r = asyncio.run(_run())
            check("已是最新时 update_available=False", r["update_available"] is False)

    with mock.patch.object(update, "_current_version", return_value="1.0.0"):
        with mock.patch.object(update, "_fetch_latest_version", return_value=None):
            r = asyncio.run(_run())
            check("检测失败 update_available=False", r["update_available"] is False)
            check("检测失败 check_error 非空", bool(r["check_error"]))


# ------------------------------------------------------------
# 6. /apply 接口逻辑（部署模式分支 + 并发防抖）
# ------------------------------------------------------------
def test_apply():
    print("\n[6] /apply 接口逻辑")

    from fastapi import HTTPException

    async def _apply():
        return await update.apply_update()

    # 本机运行：400
    with mock.patch.object(update, "_is_container", return_value=False):
        with mock.patch.object(update, "_compose_context", return_value={"working_dir": "/x", "config_files": ["/x/dc.yml"]}):
            try:
                asyncio.run(_apply())
                fail("本机运行应 400", "未拒绝")
            except HTTPException as e:
                check("本机运行拒绝 (400)", e.status_code == 400, f"status={e.status_code}")

    # 容器但非 compose 部署：400
    with mock.patch.object(update, "_is_container", return_value=True):
        with mock.patch.object(update, "_compose_context", return_value={}):
            try:
                asyncio.run(_apply())
                fail("非 compose 应 400", "未拒绝")
            except HTTPException as e:
                check("非 compose 拒绝 (400)", e.status_code == 400, f"status={e.status_code}")

    # 容器 + compose：启动后台线程，立即返回
    update._update_state["running"] = False
    with mock.patch.object(update, "_is_container", return_value=True):
        with mock.patch.object(update, "_compose_context", return_value={"working_dir": "/root/graw", "config_files": ["/root/graw/docker-compose.yml"]}):
            with mock.patch.object(update, "threading") as fake_threading:
                r = asyncio.run(_apply())
                check("触发成功返回 started=True", r.get("started") is True, f"r={r}")
                check("已调用后台线程启动", fake_threading.Thread.called)
                check("运行状态置为 running", update._update_state["running"] is True)

    # 并发防抖：running 状态下再触发 → 409
    update._update_state["running"] = True
    with mock.patch.object(update, "_is_container", return_value=True):
        with mock.patch.object(update, "_compose_context", return_value={"working_dir": "/root/graw", "config_files": ["/root/graw/docker-compose.yml"]}):
            try:
                asyncio.run(_apply())
                fail("并发应 409", "未拒绝")
            except HTTPException as e:
                check("并发触发拒绝 (409)", e.status_code == 409, f"status={e.status_code}")
    update._update_state["running"] = False


# ------------------------------------------------------------
# 7. 后台更新执行（mock docker SDK）
# ------------------------------------------------------------
def json_dumps(obj):
    import json

    return json.dumps(obj)


class FakeDockerContainer:
    """模拟 docker SDK 的执行容器：pull 与 up 各一次。"""

    def __init__(self, calls):
        self.calls = calls

    def wait(self, timeout=None):
        return 0

    def logs(self):
        return b"compose output ok"


class FakeDockerClient:
    """模拟 docker SDK 客户端（from_env）。"""

    def __init__(self, ctx=None):
        self.ctx = ctx or {}
        self.run_calls = []

    @property
    def containers(self):
        class _C:
            def __init__(self, owner):
                self.owner = owner

            def get(self, name):
                labels = self.owner.ctx.get("labels", {})
                return _Me(labels)

            def run(self, image, command=None, working_dir=None, volumes=None, detach=None, remove=None):
                self.owner.run_calls.append({
                    "image": image, "command": command,
                    "working_dir": working_dir, "volumes": volumes,
                })
                return FakeDockerContainer(self.owner.run_calls)

        return _C(self)


class _Me:
    def __init__(self, labels):
        self.attrs = {"Config": {"Labels": labels}}


def test_run_update_bg():
    print("\n[7] 后台更新执行（独立 docker/compose 容器）")
    client = FakeDockerClient()  # 单例：from_env 始终返回同一实例，便于断言执行参数
    fake_docker = type("Docker", (), {"from_env": staticmethod(lambda: client)})()
    tmpdir = tempfile.mkdtemp(prefix="graw_update_log_")
    log_file = os.path.join(tmpdir, "update.log")
    ctx = {"working_dir": "/root/graw", "config_files": ["/root/graw/docker-compose.yml"]}

    with mock.patch.dict(sys.modules, {"docker": fake_docker}):
        with mock.patch.object(update, "_UPDATE_LOG", log_file):
            update._run_update_bg(ctx)

    # 日志已落盘且包含执行结果
    check("更新日志已写入文件", os.path.isfile(log_file))
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    check("日志包含拉取镜像阶段", "拉取镜像" in content)
    check("日志包含重建容器阶段", "重建容器" in content)
    check("日志包含更新完成", "更新完成" in content)
    check("运行状态已复位", update._update_state["running"] is False)

    # 验证独立容器调用参数
    check("执行了 2 次独立容器（pull + up）", len(client.run_calls) == 2, f"n={len(client.run_calls)}")
    if client.run_calls:
        first = client.run_calls[0]
        check("使用 docker/compose 镜像", first["image"] == "docker/compose:latest", f"img={first['image']}")
        check("working_dir=/work", first["working_dir"] == "/work")
        check("compose -f 相对路径", first["command"] == ["-f", "docker-compose.yml", "pull"], f"cmd={first['command']}")
        check("docker socket 已挂载", first["volumes"].get("/var/run/docker.sock", {}).get("bind") == "/var/run/docker.sock")
        check("compose 目录已挂载", first["volumes"].get("/root/graw", {}).get("bind") == "/work")
        if len(client.run_calls) == 2:
            check("up 命令带 --remove-orphans", client.run_calls[1]["command"][-1] == "--remove-orphans", f"cmd={client.run_calls[1]['command']}")


# ------------------------------------------------------------
# 8. docker run 单容器：挂载转换与数据目录推导
# ------------------------------------------------------------
def test_docker_run_helpers():
    print("\n[8] docker run 挂载/数据目录推导")
    mounts = [
        {"Type": "bind", "Source": "/opt/graw/data", "Destination": "/app/backend/data", "Mode": "rw"},
        {"Type": "volume", "Name": "graw-vol", "Destination": "/var/lib/graw"},
        {"Type": "tmpfs", "Destination": "/tmp/x", "Mode": "rw"},
    ]
    binds = update._mounts_to_binds(mounts)
    check("--mount bind 转 -v（保留 mode）", "/opt/graw/data:/app/backend/data:rw" in binds, f"binds={binds}")
    check("--mount volume 转 -v（Name 优先）", "graw-vol:/var/lib/graw" in binds, f"binds={binds}")
    check("tmpfs 挂载跳过", len(binds) == 2, f"binds={binds}")
    check("空输入返回空列表", update._mounts_to_binds(None) == [])

    hc1 = {"HostConfig": {
        "Binds": ["/opt/graw/data:/app/backend/data", "/:/host:rslave"],
        "Mounts": [{"Type": "bind", "Source": "/x", "Destination": "/app/backend/data"}],
    }}
    check("数据目录优先取 Binds", update._host_data_dir(hc1) == "/opt/graw/data", f"got={update._host_data_dir(hc1)}")
    hc2 = {"HostConfig": {"Binds": None, "Mounts": [
        {"Type": "bind", "Source": "/x/data", "Destination": "/app/backend/data"}]}}
    check("数据目录回退取 Mounts", update._host_data_dir(hc2) == "/x/data", f"got={update._host_data_dir(hc2)}")
    check("无数据挂载返回空串", update._host_data_dir({"HostConfig": {}}) == "")


# ------------------------------------------------------------
# 9. docker run 单容器：重建参数构造（_docker_run_context）
# ------------------------------------------------------------
def _docker_run_sample_attrs(image="shunx/graw:1.0.0", network_mode="host", extra=None):
    """构造面板自身容器的 docker inspect attrs 样例。"""
    attrs = {
        "Id": "abc123abc123abc123abc123abc123abc123",
        "Config": {
            "Image": image,
            "Env": ["HOST_ROOT=/host", "GRAW_HOST_DATA=/opt/graw/data"],
            "Cmd": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
            "OriginalCmd": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
            "Hostname": "abc123abc123",
            "Labels": {"graw": "panel"},
            "WorkingDir": "/app/backend",
            "User": "",
            "Entrypoint": None,
            "OriginalEntrypoint": None,
        },
        "HostConfig": {
            "Binds": ["/opt/graw/data:/app/backend/data", "/:/host:rslave"],
            "PortBindings": {"8041/tcp": [{"HostPort": "8041"}]},
            "NetworkMode": network_mode,
            "Privileged": True,
            "PidMode": "host",
            "RestartPolicy": {"Name": "always"},
        },
        "Mounts": [],
    }
    if extra:
        attrs["Config"].update(extra)
    return attrs


class FakeMe:
    def __init__(self, attrs, name="graw-panel"):
        self.attrs = attrs
        self.name = name
        self.id = attrs["Id"]


def run_context_with(attrs, latest="1.2.0"):
    """patch sys.modules['docker'] 中的 FakeClient 与 _fetch_latest_version，返回两个 CM。"""
    class FakeContainers:
        def __init__(self, me):
            self.me = me

        def get(self, cid):
            return self.me

    class FakeClient:
        def __init__(self, me):
            self.containers = FakeContainers(me)

    client = FakeClient(FakeMe(attrs))
    fake_docker = type("Docker", (), {"from_env": staticmethod(lambda: client)})()
    return (mock.patch.dict(sys.modules, {"docker": fake_docker}),
            mock.patch.object(update, "_fetch_latest_version", return_value=latest))


def test_docker_run_context():
    print("\n[9] docker run 重建参数构造")
    # 场景 A：host 网络 + 版本号 tag → 升到 Docker Hub 最新版本号
    patch_ctx, patch_ver = run_context_with(_docker_run_sample_attrs())
    with patch_ctx, patch_ver:
        ctx = update._docker_run_context()
    check("ok=True", ctx.get("ok") is True, f"error={ctx.get('error')}")
    check("目标镜像升到最新版本号", ctx["image"] == "shunx/graw:1.2.0", f"img={ctx['image']}")
    check("pull_tag=1.2.0", ctx["pull_tag"] == "1.2.0")
    check("容器名保留", ctx["name"] == "graw-panel")
    check("备份名以 原名-old- 开头", ctx["backup_name"].startswith("graw-panel-old-"), f"bn={ctx['backup_name']}")
    ck = ctx["create"]
    check("create 目标镜像", ck["image"] == "shunx/graw:1.2.0")
    check("create 名称", ck["name"] == "graw-panel")
    check("命令透传", ck["command"] == ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"])
    check("环境变量透传", ck["environment"] == ["HOST_ROOT=/host", "GRAW_HOST_DATA=/opt/graw/data"])
    check("工作目录透传", ck["working_dir"] == "/app/backend")
    check("labels 透传", ck["labels"] == {"graw": "panel"})
    check("默认 hostname 不传", "hostname" not in ck)
    hc = ck["host_config"]
    check("network_mode=host", hc["network_mode"] == "host", f"nm={hc['network_mode']}")
    check("privileged 透传", hc["privileged"] is True)
    check("pid_mode=host", hc["pid_mode"] == "host")
    check("restart_policy 透传", hc["restart_policy"] == {"Name": "always"})
    check("binds 透传", "/opt/graw/data:/app/backend/data" in hc["binds"] and "/:/host:rslave" in hc["binds"], f"binds={hc['binds']}")
    check("port_bindings 透传", hc["port_bindings"] == {"8041/tcp": [{"HostPort": "8041"}]})
    check("数据目录推导", ctx["data_host_dir"] == "/opt/graw/data", f"got={ctx['data_host_dir']}")

    # 场景 B：默认网络（default 归一化为 bridge）
    patch_ctx, patch_ver = run_context_with(_docker_run_sample_attrs(network_mode="default"))
    with patch_ctx, patch_ver:
        ctx = update._docker_run_context()
    check("default 网络归一化为 bridge", ctx["create"]["host_config"]["network_mode"] == "bridge",
          f"nm={ctx['create']['host_config']['network_mode']}")

    # 场景 C：latest tag 保持跟随 latest
    patch_ctx, patch_ver = run_context_with(_docker_run_sample_attrs(image="shunx/graw:latest"))
    with patch_ctx, patch_ver:
        ctx = update._docker_run_context()
    check("latest 保持 latest", ctx["image"] == "shunx/graw:latest", f"img={ctx['image']}")

    # 场景 D：显式 hostname 透传
    patch_ctx, patch_ver = run_context_with(_docker_run_sample_attrs(extra={"Hostname": "my-panel"}))
    with patch_ctx, patch_ver:
        ctx = update._docker_run_context()
    check("显式 hostname 透传", ctx["create"].get("hostname") == "my-panel", f"hn={ctx['create'].get('hostname')}")

    # 场景 E：非官方镜像拒绝
    patch_ctx, patch_ver = run_context_with(_docker_run_sample_attrs(image="evil/graw:1.0.0"))
    with patch_ctx, patch_ver:
        ctx = update._docker_run_context()
    check("非官方镜像拒绝", ctx.get("ok") is False and "非官方镜像" in ctx.get("error", ""), f"err={ctx.get('error')}")

    # 场景 F：container 网络拒绝
    patch_ctx, patch_ver = run_context_with(_docker_run_sample_attrs(network_mode="container:other"))
    with patch_ctx, patch_ver:
        ctx = update._docker_run_context()
    check("container 网络拒绝", ctx.get("ok") is False and "container" in ctx.get("error", ""), f"err={ctx.get('error')}")


# ------------------------------------------------------------
# 10. docker run 部署检测（_docker_run_detect）
# ------------------------------------------------------------
def test_docker_run_detect():
    print("\n[10] docker run 部署检测")
    patch_ctx, patch_ver = run_context_with(_docker_run_sample_attrs())
    with patch_ctx, patch_ver:
        check("官方镜像检测为 docker-run", update._docker_run_detect() is True)
    patch_ctx, patch_ver = run_context_with(_docker_run_sample_attrs(image="nope/x:1"))
    with patch_ctx, patch_ver:
        check("非官方镜像检测为 False", update._docker_run_detect() is False)


# ------------------------------------------------------------
# 11. /status 部署细分（compose / docker-run / unsupported / local）
# ------------------------------------------------------------
def test_status_deploy_detail():
    print("\n[11] /status 部署细分")

    async def _run():
        return await update.update_status()

    with mock.patch.object(update, "_current_version", return_value="1.0.0"):
        with mock.patch.object(update, "_fetch_latest_version", return_value="1.2.0"):
            with mock.patch.object(update, "_is_container", return_value=True):
                with mock.patch.object(update, "_compose_context", return_value={"working_dir": "/x", "config_files": ["/x/dc.yml"]}):
                    r = asyncio.run(_run())
                    check("compose 部署细分", r["deploy_detail"] == "compose")
                with mock.patch.object(update, "_compose_context", return_value={}):
                    with mock.patch.object(update, "_docker_run_detect", return_value=True):
                        r = asyncio.run(_run())
                        check("docker-run 部署细分", r["deploy_detail"] == "docker-run")
                    with mock.patch.object(update, "_docker_run_detect", return_value=False):
                        r = asyncio.run(_run())
                        check("自定义镜像 => unsupported", r["deploy_detail"] == "unsupported")


# ------------------------------------------------------------
# 12. /apply docker run 分支
# ------------------------------------------------------------
def test_apply_docker_run():
    print("\n[12] /apply docker run 分支")

    from fastapi import HTTPException

    async def _apply():
        return await update.apply_update()

    dctx = {
        "ok": True, "image": "shunx/graw:1.2.0", "name": "graw-panel", "container_id": "abc123",
        "pull_repo": "shunx/graw", "pull_tag": "1.2.0", "backup_name": "graw-panel-old-1",
        "create": {"image": "shunx/graw:1.2.0", "host_config": {}}, "data_host_dir": "/opt/graw/data",
    }
    update._update_state["running"] = False
    with mock.patch.object(update, "_is_container", return_value=True):
        with mock.patch.object(update, "_compose_context", return_value={}):
            # 场景 A：docker-run 上下文可用 → 启动后台线程
            with mock.patch.object(update, "_docker_run_context", return_value=dctx):
                with mock.patch.object(update, "threading") as fake_threading:
                    r = asyncio.run(_apply())
                    check("docker run 触发成功", r.get("started") is True, f"r={r}")
                    check("线程已启动", fake_threading.Thread.called)
                    check("运行状态置为 running", update._update_state["running"] is True)
            # 场景 B：docker-run 上下文失败 → 400
            update._update_state["running"] = False
            with mock.patch.object(update, "_docker_run_context", return_value={"ok": False, "error": "非官方镜像，请手动更新"}):
                try:
                    asyncio.run(_apply())
                    fail("docker-run 上下文失败应 400", "未拒绝")
                except HTTPException as e:
                    check("docker-run 失败拒绝 (400)", e.status_code == 400, f"status={e.status_code}")
    update._update_state["running"] = False


# ------------------------------------------------------------
# 13. docker run 后台更新执行（_run_update_bg_docker）
# ------------------------------------------------------------
class FakeRunContainer:
    def __init__(self, ec=0, logs=b"updater output ok"):
        self._ec = ec
        self._logs = logs

    def wait(self, timeout=None):
        return self._ec

    def logs(self):
        return self._logs


class FakeRunImages:
    def __init__(self, owner):
        self.owner = owner

    def pull(self, repo, tag=None):
        self.owner.pull_calls.append((repo, tag))


class FakeRunClients:
    def __init__(self, owner):
        self.owner = owner

    def run(self, image, command=None, working_dir=None, volumes=None, detach=None, remove=None):
        self.owner.run_calls.append({
            "image": image, "command": command, "working_dir": working_dir,
            "volumes": volumes, "detach": detach, "remove": remove,
        })
        return FakeRunContainer()


class FakeRunDockerClient:
    def __init__(self):
        self.pull_calls = []
        self.run_calls = []

    @property
    def images(self):
        return FakeRunImages(self)

    @property
    def containers(self):
        return FakeRunClients(self)


def test_run_update_bg_docker():
    print("\n[13] 后台更新执行（docker run 独立执行容器）")
    client = FakeRunDockerClient()
    fake_docker = type("Docker", (), {"from_env": staticmethod(lambda: client)})()
    tmpdir = tempfile.mkdtemp(prefix="graw_update_")
    tmp_root = os.path.join(tmpdir, "hostroot")
    os.makedirs(tmp_root)
    log_file = os.path.join(tmpdir, "update.log")
    dctx = {
        "image": "shunx/graw:1.2.0", "pull_repo": "shunx/graw", "pull_tag": "1.2.0",
        "container_id": "abc123", "name": "graw-panel", "backup_name": "graw-panel-old-1",
        "create": {"image": "shunx/graw:1.2.0", "host_config": {}},
        "data_host_dir": "/opt/graw/data",
    }
    with mock.patch.dict(sys.modules, {"docker": fake_docker}):
        with mock.patch.dict(os.environ, {"HOST_ROOT": tmp_root}):
            with mock.patch.object(update, "_UPDATE_LOG", log_file):
                update._run_update_bg_docker(dctx)

    check("运行状态已复位", update._update_state["running"] is False)
    check("拉取了目标镜像", client.pull_calls == [("shunx/graw", "1.2.0")], f"calls={client.pull_calls}")
    check("启动了一次执行容器", len(client.run_calls) == 1, f"n={len(client.run_calls)}")
    if client.run_calls:
        rc = client.run_calls[0]
        check("执行容器使用新镜像", rc["image"] == "shunx/graw:1.2.0", f"img={rc['image']}")
        check("执行容器命令为 python run.py", rc["command"] == ["python", "/work/run.py"], f"cmd={rc['command']}")
        check("working_dir=/work", rc["working_dir"] == "/work")
        check("docker socket 挂载", rc["volumes"].get("/var/run/docker.sock", {}).get("bind") == "/var/run/docker.sock")
        check("宿主临时目录挂载到 /work", any(v.get("bind") == "/work" for v in rc["volumes"].values()),
              f"vols={list(rc['volumes'].keys())}")
        check("数据目录挂载到 /data", rc["volumes"].get("/opt/graw/data", {}).get("bind") == "/data")

    # 宿主临时目录（tmp_root/tmp/graw-update-*）内的脚本与配置
    tmp_base = os.path.join(tmp_root, "tmp")
    tmp_dirs = [d for d in os.listdir(tmp_base) if d.startswith("graw-update-")] if os.path.isdir(tmp_base) else []
    check("创建了宿主临时目录", len(tmp_dirs) >= 1, f"dirs={tmp_dirs}")
    if tmp_dirs:
        base = os.path.join(tmp_base, tmp_dirs[0])
        check("run.py 已写入", os.path.isfile(os.path.join(base, "run.py")))
        check("cfg.json 已写入", os.path.isfile(os.path.join(base, "cfg.json")))
        with open(os.path.join(base, "cfg.json"), "r", encoding="utf-8") as f:
            cfg = json.load(f)
        check("cfg.json 含 create 配置", cfg.get("create", {}).get("image") == "shunx/graw:1.2.0", f"cfg={cfg}")
        check("cfg.json 含备份名", cfg.get("backup_name") == "graw-panel-old-1")

    # 面板日志落盘
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    check("日志包含拉取镜像", "拉取镜像" in content)
    check("日志包含执行容器退出码", "退出码" in content)
    check("日志包含流程结束", "流程结束" in content)
    check("日志包含执行容器输出", "updater output ok" in content)


# ------------------------------------------------------------
# 14. 执行容器内置更新脚本语法
# ------------------------------------------------------------
def test_updater_script_syntax():
    print("\n[14] 执行容器内置更新脚本语法")
    try:
        ast.parse(update._UPDATER_SCRIPT)
        check("内置脚本可编译", True)
    except SyntaxError as e:  # noqa: BLE001 - 测试用例需主动上报语法问题
        check("内置脚本可编译", False, str(e))
    check("脚本包含同名重建", "containers.create" in update._UPDATER_SCRIPT)
    check("脚本包含回滚逻辑", "回滚成功" in update._UPDATER_SCRIPT)


def main():
    global PASS, FAIL
    print("=" * 60)
    print("面板更新功能（update.py）单元测试")
    print("=" * 60)
    test_version_key()
    test_fetch_latest_version()
    test_is_container()
    test_compose_mounts()
    test_status()
    test_apply()
    test_run_update_bg()
    test_docker_run_helpers()
    test_docker_run_context()
    test_docker_run_detect()
    test_status_deploy_detail()
    test_apply_docker_run()
    test_run_update_bg_docker()
    test_updater_script_syntax()
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"结果: {PASS}/{total} 通过, {FAIL}/{total} 失败")
    if FAIL == 0:
        print("全部通过！")
    else:
        print(f"有 {FAIL} 项失败，请检查。")
    sys.exit(1 if FAIL > 0 else 0)


if __name__ == "__main__":
    main()
