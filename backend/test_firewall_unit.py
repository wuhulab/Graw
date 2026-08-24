# -*- coding: utf-8 -*-
"""
firewall.py 防火墙规则生成逻辑单元测试（不依赖运行中的 iptables）

核心回归点（Docker 发布端口屏蔽语义）：
  - deny 必须同时在 INPUT 与 mangle PREROUTING（DNAT 之前）落 DROP；
    仅 INPUT DROP 对 Docker 发布端口无效（流量经 DNAT 后走 FORWARD）。
  - allow 同时在 INPUT 与 mangle PREROUTING 落 ACCEPT，且用 -I 插顶覆盖旧 DROP。
  - 新增规则前先 _flush 该端口，保证幂等、避免旧形态残留。

用法：
  py test_firewall_unit.py
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.routers import firewall  # noqa: E402

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


def test_deny_rules():
    """deny 必须含 mangle PREROUTING DROP（修复对 Docker 端口屏蔽失效）。"""
    print("[1] deny 规则生成")
    cmds = []
    with mock.patch.object(firewall, "_iptables_cmd", side_effect=lambda a: cmds.append(list(a))), \
         mock.patch.object(firewall, "IS_WIN", False):
        firewall._add_port_rule({"port": 3066, "protocol": "tcp", "action": "deny", "id": "x"})
    def has(cmdlist):
        return any(c == cmdlist for c in cmds)
    check("deny 有 INPUT DROP", has(["-I", "INPUT", "-p", "tcp", "--dport", "3066", "-j", "DROP"]))
    check("deny 有 mangle PREROUTING DROP",
          has(["-t", "mangle", "-I", "PREROUTING", "-p", "tcp", "--dport", "3066", "-j", "DROP"]))
    # flush 应先行（第一条命令是 mangle DROP 的 -D 清理）
    check("先 flush 再添加", I := cmds[0][0] == "-t" or cmds[0][1] == "mangle",
          f"flush? first={cmds[0]}")


def test_allow_rules():
    """allow 必须含 mangle PREROUTING ACCEPT。"""
    print("[2] allow 规则生成")
    cmds = []
    with mock.patch.object(firewall, "_iptables_cmd", side_effect=lambda a: cmds.append(list(a))), \
         mock.patch.object(firewall, "IS_WIN", False):
        firewall._add_port_rule({"port": 3001, "protocol": "tcp", "action": "allow", "id": "y"})
    def has(cmdlist):
        return any(c == cmdlist for c in cmds)
    check("allow 有 INPUT ACCEPT", has(["-I", "INPUT", "-p", "tcp", "--dport", "3001", "-j", "ACCEPT"]))
    check("allow 有 mangle PREROUTING ACCEPT",
          has(["-t", "mangle", "-I", "PREROUTING", "-p", "tcp", "--dport", "3001", "-j", "ACCEPT"]))


def test_flush_powerless():
    """_flush_port 清除 INPUT 与 mangle 两侧的 ACCEPT/DROP。"""
    print("[3] _flush_port 幂等清除")
    cmds = []
    with mock.patch.object(firewall, "_iptables_cmd", side_effect=lambda a: cmds.append(list(a))), \
         mock.patch.object(firewall, "IS_WIN", False):
        firewall._flush_port(3066, "tcp")
    count = 0
    for c in cmds:
        if c[:2] == ["-t", "mangle"] and c[-1] in ("ACCEPT", "DROP"):
            count += 1
        elif c[0] == "-D" and c[-1] in ("ACCEPT", "DROP"):
            count += 1
    check("flush 覆盖 4 条 ACCEPT/DROP", count == 4, f"matched={count}")


def test_reconcile():
    """reconcile 对所有 port_rules 重放。"""
    print("[4] reconcile 重放全部端口规则")
    data = {"port_rules": [{"port": 3066, "action": "deny", "protocol": "tcp", "id": "a"},
                           {"port": 3001, "action": "allow", "protocol": "tcp", "id": "b"}]}
    with mock.patch.object(firewall, "_add_port_rule", return_value=None) as m:
        n = firewall._apply_all_rules(data)
    check("reconcile 应用条数", n == 2 and m.call_count == 2, f"n={n} calls={m.call_count}")


def main():
    test_deny_rules()
    test_allow_rules()
    test_flush_powerless()
    test_reconcile()
    print(f"\n结果：通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()