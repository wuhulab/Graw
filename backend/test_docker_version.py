# -*- coding: utf-8 -*-
"""
docker_api.get_self_app_version 单元测试（从承载面板自身容器读取版本号）。

覆盖：
  - CLI 引擎：镜像 tag 解析（shunx/graw:1.3.5 -> 1.3.5）
  - tag=latest / 无 tag / digest 时不误判
  - 版本 label（org.opencontainers.image.version）兜底
  - v 前缀去除
  - SDK 引擎读取容器 attrs
  - 引擎不可用 / 容器缺失时返回 None（调用方回退常量）

用法：
  py test_docker_version.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.routers import docker_api  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))


def fail(name, detail):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {name}: {detail}")


def check(name, cond, detail=""):
    (ok if cond else fail)(name, detail)


def _cli_req(image_ref, labels=None):
    """构造 CLI 引擎场景：get_backend=(cli,None)，inspect 返回指定 Config。"""
    cfg = {"Image": image_ref}
    if labels:
        cfg["Labels"] = labels
    return mock.patch.object(docker_api, "get_backend", return_value=("cli", None)), \
           mock.patch.object(docker_api, "_podman_json", return_value=[{"Config": cfg}])


def test_cli_tag():
    print("[1] CLI 镜像 tag 解析")
    with _cli_req("shunx/graw:1.3.5")[0], _cli_req("shunx/graw:1.3.5")[1]:
        check("shunx/graw:1.3.5 -> 1.3.5", docker_api.get_self_app_version() == "1.3.5")
    with mock.patch.object(docker_api, "get_backend", return_value=("cli", None)), \
         mock.patch.object(docker_api, "_podman_json",
                           return_value=[{"Config": {"Image": "shunx/graw:v1.4.0"}}]):
        check("v1.4.0 去 v -> 1.4.0", docker_api.get_self_app_version() == "1.4.0")


def test_no_version():
    print("[2] 无有效版本 -> None（回退常量）")
    for ref in ("shunx/graw:latest", "shunx/graw", ""):
        with _cli_req(ref)[1]:
            check(f"镜像 {ref!r} -> None", docker_api.get_self_app_version() is None, ref)
    # digest 不误判
    with _cli_req("shunx/graw@sha256:63f6158255c41a1a8b24" + "a" * 20)[1]:
        check("digest 忽略 -> None", docker_api.get_self_app_version() is None)


def test_label_fallback():
    print("[3] 版本 label 兜底")
    with mock.patch.object(docker_api, "get_backend", return_value=("cli", None)), \
         mock.patch.object(docker_api, "_podman_json",
                           return_value=[{"Config": {"Image": "shunx/graw:latest",
                                                     "Labels": {"org.opencontainers.image.version": "1.5.0"}}}]):
        check("latest+label -> 1.5.0", docker_api.get_self_app_version() == "1.5.0")


def test_engine_failure():
    print("[4] 引擎不可用 / 容器缺失 -> None")
    with mock.patch.object(docker_api, "get_backend", side_effect=Exception("no engine")):
        check("get_backend 抛异常 -> None", docker_api.get_self_app_version() is None)
    # 容器缺失：inspect 返回空列表
    with _cli_req("shunx/graw:1.3.5")[0], mock.patch.object(docker_api, "_podman_json", return_value=[]):
        check("inspect 空结果 -> None", docker_api.get_self_app_version() is None)


def test_sdk():
    print("[5] SDK 引擎读取容器 attrs")
    fake = mock.Mock()
    fake.containers.get.return_value.attrs = {"Config": {"Image": "shunx/graw:2.0.1"}}
    with mock.patch.object(docker_api, "get_backend", return_value=("docker", fake)):
        check("SDK attrs Image -> 2.0.1", docker_api.get_self_app_version() == "2.0.1")


def main():
    test_cli_tag()
    test_no_version()
    test_label_fallback()
    test_engine_failure()
    test_sdk()
    print(f"\n结果：通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()