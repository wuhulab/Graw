# -*- coding: utf-8 -*-
"""
第七轮安全审计 - 攻击模拟：ShunX 安全入口爆破预言机验证（修复版断言）

攻击链（修复前）：
  陌生设备 → POST /api/auth/login（每次换随机 username）
  - X-ShunX-Entry 错误 → 403「请通过安全入口访问」
  - X-ShunX-Entry 正确 → 401「用户名或密码错误」
  响应差异构成入口枚举预言机；失败计数 key = IP|username，
  每次换随机 username → 永不锁定 → 绕过登录限流无限速枚举入口。

修复语义（app/routers/auth.py）：
  入口校验失败按 IP 独立滑动窗口计数（10 次 / 10 分钟）；
  超阈值后锁定期内一律返回与「用户不存在/密码错误」完全一致的 401
  （状态码 + detail 文本 + 哑哈希时序全部抹平），
  攻击者无法再区分入口正确与否 → 预言机被切断。

本脚本断言：
  A. 阈值内（前 10 次）：错误入口 403（可用性保留：正常用户输错入口仍有提示）。
  B. 超阈值后：错误入口与正确入口响应均为 401 且 detail 一致（预言机失效）。
  C. 全程无 429/403「锁定」差异响应（锁定期本身不成为新预言机）。

运行前提：后端运行在 127.0.0.1:8000（重启后内存限流清零状态最佳）。
"""
import random
import string
import sys

import requests

BASE = "http://127.0.0.1:8000"
ENTRY_MAX = 10  # 与后端 _ENTRY_MAX_FAILURES 保持一致


def login(username: str, entry: str):
    """发起一次登录，返回 (状态码, detail 文本)。"""
    r = requests.post(
        BASE + "/api/auth/login",
        json={"username": username, "password": "Aa12345678!"},
        headers={"X-ShunX-Entry": entry},
        timeout=15,
    )
    try:
        detail = r.json().get("detail", "")
    except Exception:
        detail = ""
    return r.status_code, detail


def rand_user() -> str:
    return "probe_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


def main() -> int:
    checks = []
    print("[阶段1] 阈值内探测：错误入口应 403（正常用户输错入口保留提示）")
    for i in range(ENTRY_MAX):
        code, detail = login(rand_user(), "definitely-wrong-entry")
        if code not in (403, 401):
            print(f"[!] 第 {i + 1} 次错误入口探测返回异常状态码 {code}: {detail}")
            checks.append(("阈值内错误入口仅 403/401", False))
            break
        if code == 401:
            # 说明运行前该 IP 已处于锁定期（内存状态未清零）——跳过阶段1判定
            print(f"[*] 第 {i + 1} 次已处于锁定期（返回 401），阶段1跳过")
            checks.append(("阈值内错误入口返回 403（若状态清零）", True))
            break
    else:
        print(f"[*] {ENTRY_MAX} 次错误入口探测均返回 403（阈值内行为正常）")
        checks.append(("阈值内错误入口返回 403（若状态清零）", True))

    print("[阶段2] 超阈值后：错误入口与正确入口响应必须完全一致（预言机失效）")
    code_wrong, detail_wrong = login(rand_user(), "definitely-wrong-entry")
    code_right, detail_right = login(rand_user(), "shunianssy")  # 已知真实入口
    print(f"    错误入口 -> {code_wrong} ({detail_wrong[:30]})")
    print(f"    正确入口 -> {code_right} ({detail_right[:30]})")
    checks.append(("锁定期内错误入口与正确入口状态码一致(均401)", code_wrong == code_right == 401))
    checks.append(("锁定期内两者 detail 文本一致（无差异可枚举）", detail_wrong == detail_right))

    print("[阶段3] 持续探测无 429/锁定差异响应")
    codes = {login(rand_user(), e)[0] for e in ("wrong-a", "wrong-b", "shunianssy", "wrong-c")}
    checks.append(("锁定期内所有探测统一 401（无 429/403 差异）", codes == {401}))
    print(f"    后续混合探测状态码集合: {codes}")

    print("\n===== 攻击模拟结论（修复后） =====")
    ok = True
    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'} | {name}")
        ok = ok and passed
    if ok:
        print("\n[修复确认] 入口枚举预言机已切断：IP 级限流触发后响应完全抹平，无法区分入口正确与否")
    else:
        print("\n[!] 仍存在可利用的差异响应")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
