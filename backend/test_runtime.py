# -*- coding: utf-8 -*-
"""
test_runtime.py - 运行环境模块单元测试

覆盖不依赖真实容器引擎的纯逻辑（无需 pytest / httpx 额外依赖）：
    1. runtimes 模板数据包含全部 5 种运行时
    2. _build_image 按类型+版本生成镜像名
    3. _build_run_args 正确构造端口 / 环境变量 / 挂载 / 主机映射 / 工作目录
    4. 配置持久化读写 round-trip
    5. 输入校验（绝对路径 / 容器名合法性）

运行方式（在 backend 目录）：
    .\\.venv\\Scripts\\python -m unittest test_runtime -v
"""
import os
import tempfile
import unittest

# 允许直接 import runtime 模块（不启动 FastAPI 服务）
os.environ.setdefault("HOST_ROOT", "")

from app.routers import runtime  # noqa: E402


class TestRuntime(unittest.TestCase):
    def test_runtimes_templates_complete(self):
        """模板应包含全部 5 种运行时且每类有默认参数。"""
        self.assertEqual(
            set(runtime.RUNTIMES.keys()),
            {"python", "java", "node", "go", "dotnet"},
        )
        self.assertEqual(set(runtime.PORT_PROTOCOLS), {"tcp", "udp"})
        self.assertEqual(set(runtime.MOUNT_MODES), {"rw", "ro"})
        for key, rt in runtime.RUNTIMES.items():
            self.assertTrue(rt["default_version"])
            self.assertTrue(rt["workdir"])
            self.assertTrue(rt["suggest_cmd"])
            # 默认版本应生成非空镜像
            self.assertTrue(rt["image"](rt["default_version"]))

    def test_build_image(self):
        """按类型+版本生成完整的镜像名。"""
        self.assertEqual(runtime._build_image({"type": "python", "app_version": "3.11"}), "python:3.11")
        self.assertEqual(runtime._build_image({"type": "node", "app_version": "20"}), "node:20")
        self.assertEqual(
            runtime._build_image({"type": "dotnet", "app_version": "8.0"}),
            "mcr.microsoft.com/dotnet/sdk:8.0",
        )
        # 未填版本时回退默认版本
        go_img = runtime._build_image({"type": "go", "app_version": ""})
        self.assertEqual(go_img, "golang:" + runtime.RUNTIMES["go"]["default_version"])

    def test_build_run_args_full(self):
        """run 参数应覆盖项目挂载、端口、环境变量、挂载模式、主机映射、工作目录。"""
        cfg = {
            "project_dir": "/srv/proj",
            "workdir": "/app",
            "ports": [
                {"external": "8080", "internal": "80", "protocol": "tcp"},
                {"external": "53", "internal": "53", "protocol": "udp"},
                {"external": "", "internal": "80", "protocol": "tcp"},  # 空 external 忽略
            ],
            "env": [{"name": "A", "value": "1"}, {"name": "", "value": "x"}],  # 空 name 忽略
            "mounts": [
                {"host": "/data", "container": "/var/lib", "mode": "rw"},
                {"host": "/cfg", "container": "/etc/app", "mode": "ro"},
                {"host": "", "container": "/x", "mode": "rw"},  # 空 host 忽略
            ],
            "hosts": [
                {"hostname": "db", "ip": "10.0.0.1"},
                {"hostname": "", "ip": "9.9.9.9"},  # 空 hostname 忽略
            ],
        }
        args = runtime._build_run_args(cfg)
        text = " ".join(args)
        # 项目目录挂载 + 工作目录
        self.assertIn("-v /srv/proj:/app", text)
        self.assertIn("-w /app", text)
        # 端口：tcp / udp 各有一条
        self.assertIn("-p 8080:80/tcp", text)
        self.assertIn("-p 53:53/udp", text)
        # 环境变量：只含有效项
        self.assertIn("-e A=1", text)
        self.assertNotIn("name=", text)
        # 挂载：rw 无后缀，ro 带 :ro
        self.assertIn("-v /data:/var/lib", text)
        self.assertIn("-v /cfg:/etc/app:ro", text)
        # 主机映射
        self.assertIn("--add-host db:10.0.0.1", text)

    def test_build_run_args_default_workdir(self):
        """未显式配置工作目录时使用默认值。"""
        args = runtime._build_run_args({"project_dir": "/p", "workdir": ""})
        self.assertIn("-v /p:/app", " ".join(args))

    def test_config_persistence_roundtrip(self):
        """配置保存/读取 round-trip，以及文件缺失时返回空列表。"""
        with tempfile.TemporaryDirectory() as d:
            runtime.RUNTIME_FILE = os.path.join(d, "runtime.json")
            runtime.DATA_DIR = d
            cfg = {"id": "rt_1", "name": "测试", "type": "python"}
            runtime._save_configs([cfg])
            self.assertEqual(runtime._load_configs(), [cfg])
        with tempfile.TemporaryDirectory() as d2:
            runtime.RUNTIME_FILE = os.path.join(d2, "none.json")
            runtime.DATA_DIR = d2
            self.assertEqual(runtime._load_configs(), [])

    def test_container_name_validation(self):
        """容器名校验规则：字母数字开头，仅含字母/数字/_/./-。"""
        good = ["app", "app_1", "rt-py", "a.b-c", "A1"]
        bad = ["-bad", "has space", "s*y", ""]
        for name in good:
            self.assertTrue(runtime._CONTAINER_NAME_RE.match(name), f"应通过: {name}")
        for name in bad:
            self.assertIsNone(runtime._CONTAINER_NAME_RE.match(name), f"应拒绝: {name}")

    def test_default_command_fallback(self):
        """启动命令为空时回退到运行时建议命令。"""
        cmd = runtime._default_command({"type": "python", "start_command": ""})
        self.assertEqual(cmd, runtime.RUNTIMES["python"]["suggest_cmd"])
        cmd2 = runtime._default_command({"type": "go", "start_command": "go run /app/x.go"})
        self.assertEqual(cmd2, "go run /app/x.go")


if __name__ == "__main__":
    unittest.main(verbosity=2)