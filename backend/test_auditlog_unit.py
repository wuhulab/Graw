# -*- coding: utf-8 -*-
"""
面板操作审计日志模块单元测试（不依赖运行中的后端服务）

覆盖：
  - 写入一行「操作/用户/IP/详情」完整格式
  - 追加写入（多次调用不覆盖）
  - 缺省字段（无用户/无 IP）容错
  - 非 UTF-8 安全的特殊字符（中文 / 超长详情）写入正常
  - 日志体积超限自动轮转（panel.log -> panel.log.1 并从空文件继续）
  - 审计失败静默降级（目录只读时不影响调用方）

用法：
  python test_auditlog_unit.py
"""
import os
import sys
import tempfile

# 确保可导入 app 包（与测试脚本同级目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unittest import mock  # noqa: E402
from app import auditlog  # noqa: E402

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


def _read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def test_basic_write():
    """写入一行完整审计日志并包含关键要素。"""
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "panel.log")
    try:
        with mock.patch.object(auditlog, "PANEL_LOG", log):
            auditlog.record("登录成功", "admin", "127.0.0.1", "detail-ok")
        lines = _read_lines(log)
        check("写入一行", len(lines) == 1)
        if lines:
            line = lines[0]
            check("包含动作", "登录成功" in line)
            check("包含用户", "用户:admin" in line)
            check("包含IP", "IP:127.0.0.1" in line)
            check("包含详情", "detail-ok" in line)
            check("包含时间戳", "[" in line and "]" in line)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_append_no_overwrite():
    """多次写入应追加而非覆盖。"""
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "panel.log")
    try:
        with mock.patch.object(auditlog, "PANEL_LOG", log):
            auditlog.record("登录成功", "admin", "1.1.1.1")
            auditlog.record("退出登录", "admin", "1.1.1.1")
        lines = _read_lines(log)
        check("共两行", len(lines) == 2)
        check("首行为登录", lines[0].find("登录成功") >= 0 if lines else False)
        check("次行为退出", lines[1].find("退出登录") >= 0 if len(lines) > 1 else False)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_missing_optional_fields():
    """无用户 / 无 IP 时其余字段仍正常写出。"""
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "panel.log")
    try:
        with mock.patch.object(auditlog, "PANEL_LOG", log):
            auditlog.record("系统任务", "", "", "后台定时备份")
        lines = _read_lines(log)
        check("仍写入一行", len(lines) == 1)
        if lines:
            line = lines[0]
            check("不含用户", "用户:" not in line)
            check("不含IP", "IP:" not in line)
            check("详情完整", "后台定时备份" in line)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_unicode_and_log_file():
    """中文与特殊字符写入无异常，且文件可正常重读。"""
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "panel.log")
    try:
        with mock.patch.object(auditlog, "PANEL_LOG", log):
            auditlog.record("写文件", "管理员", "192.168.1.10", "etc/nginx/conf.d/站点.conf -> 修改")
        raw = _read_lines(log)
        check("可读取", len(raw) == 1)
        if raw:
            check("内容编码正确", "站点.conf" in raw[0])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_rotate():
    """超过体积上限后自动归档为 .1 并从空文件继续。"""
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "panel.log")
    try:
        # 先构造一个超过阈值的大日志文件
        with open(log, "w", encoding="utf-8") as f:
            f.write("x" * (auditlog._MAX_LOG_BYTES + 10) + "\n")
        # 此时真实 getsize 已超限，状态唯一；再 mock 保持稳定后写入即触发轮转
        with mock.patch.object(auditlog, "PANEL_LOG", log):
            with mock.patch.object(
                auditlog.os.path, "getsize", return_value=auditlog._MAX_LOG_BYTES + 1
            ):
                auditlog.record("触发轮转", "admin", "1.1.1.1", "x")
        check("已生成归档文件", os.path.exists(log + ".1"))
        # 轮转后新文件应继续可写（含触发轮转那行 + 新增的一行）
        with mock.patch.object(auditlog, "PANEL_LOG", log):
            auditlog.record("轮转后", "admin", "1.1.1.1", "y")
        lines = _read_lines(log)
        check(
            "轮转后继续写入",
            len(lines) == 2 and lines[-1].find("轮转后") >= 0,
        )
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_fail_silent():
    """审计写入失败（目录不可写）时静默降级，不影响调用方。"""
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "panel.log")
    try:
        # 无论 PANEL_LOG 为何，让 open 抛异常，验证不向上传播
        with mock.patch.object(auditlog, "PANEL_LOG", log):
            with mock.patch(
                "builtins.open",
                side_effect=PermissionError("denied"),
            ):
                auditlog.record("写文件", "admin", "1.1.1.1", "should-swallow")
        check("未抛异常", True)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run():
    print("== auditlog 单元测试 ==")
    test_basic_write()
    test_append_no_overwrite()
    test_missing_optional_fields()
    test_unicode_and_log_file()
    test_rotate()
    test_fail_silent()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())