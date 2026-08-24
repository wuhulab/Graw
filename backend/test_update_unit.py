# -*- coding: utf-8 -*-
"""
面板自身更新（update.py）核心逻辑单元测试（不依赖运行中的后端/容器）

覆盖：
  - 版本 tag 解析与比较（拒绝 latest/buildcache 等非版本号）
  - Docker Hub 最新版本查询（mock 网络，含异常降级）
  - 部署模式检测（容器 / 本机）
  - compose 上下文挂载与 -f 参数构造（相对/绝对路径）
  - /status 与 /apply 接口逻辑（含并发防抖、本机拒绝、非 compose 拒绝）
  - 后台更新执行流程（mock docker SDK，验证独立容器调用与日志落盘）

用法：
  python test_update_unit.py
"""
import asyncio
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
