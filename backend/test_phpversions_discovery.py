# -*- coding: utf-8 -*-
"""
test_phpversions_discovery.py - PHP 版本探测「容器 /host 挂载模式」单元测试
覆盖：
  - 容器模式（HOST_ROOT=/host 前缀）下扫描宿主 /usr/bin /usr/sbin 能发现 PHP
  - 返回的 path 应还原为宿主机视角（供展示与经 host_cmd 的 php -v 使用）
  - 非容器模式行为保持不变（目录不被映射）
"""
import os
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.routers import phpversions  # noqa: E402


class PhpHostRootDetectTest(unittest.TestCase):
    """容器挂载模式下 PHP 版本探测的路径适配。"""

    def test_detect_versions_under_host_root(self):
        # 宿主机 /usr/bin/php8.2 与 /usr/sbin/php-fpm8.3 在容器内位于
        # <host_root>/usr/bin、<host_root>/usr/sbin
        with tempfile.TemporaryDirectory() as root:
            for rel in ("usr/bin", "usr/sbin"):
                os.makedirs(os.path.join(root, rel))
            with open(os.path.join(root, "usr/bin/php8.2"), "w") as f:
                f.write("")
            with open(os.path.join(root, "usr/sbin/php-fpm8.3"), "w") as f:
                f.write("")
            with mock.patch("app.hostfs.HOST_ROOT", root):
                with mock.patch("platform.system", return_value="Linux"):
                    found = phpversions._detect_linux()

        versions = {c["version"] for c in found}
        # 扫描目录映射生效：能从容器内 /host 前缀下发现宿主 php 版本
        self.assertIn("8.2", versions)
        self.assertIn("8.3", versions)
        # 对外 path 必须是宿主机视角（不带 HOST_ROOT 前缀）；
        # Windows 下 unhost_path 对混用分隔符路径可能不还原，故按分隔符归一比较
        cli = next(c for c in found if c["sapi"] == "cli" and c["version"] == "8.2")
        fpm = next(c for c in found if c["sapi"] == "fpm" and c["version"] == "8.3")
        cli_path = cli["path"].replace("\\", "/")
        fpm_path = fpm["path"].replace("\\", "/")
        self.assertTrue(cli_path.endswith("usr/bin/php8.2"), cli_path)
        self.assertNotIn(root, cli_path)
        self.assertTrue(fpm_path.endswith("usr/sbin/php-fpm8.3"), fpm_path)
        self.assertNotIn(root, fpm_path)

    def test_detect_no_mapping_when_not_mounted(self):
        # 非容器模式（HOST_ROOT 为空）：host_path 原样返回，扫描逻辑不报错
        with mock.patch("app.hostfs.HOST_ROOT", ""):
            with mock.patch("platform.system", return_value="Linux"):
                found = phpversions._detect_linux()
        # 不抛异常即可（本机是否有 php 与测试环境有关，不做强断言）
        self.assertIsInstance(found, list)


if __name__ == "__main__":
    unittest.main()