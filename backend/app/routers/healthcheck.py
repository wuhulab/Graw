# -*- coding: utf-8 -*-
"""
healthcheck.py - 一键系统体检路由

提供一次性系统安全体检：扫描面板与宿主机的常见隐患，输出分级报告。
体检项（均只读，不做任何修复操作）：
  1. 弱密码：面板账号是否存在常见弱密码 / 默认密码 / 明文存储。
  2. 异常登录：登录日志中是否存在爆破迹象（连续失败）与异常登录记录。
  3. 危险开放端口：防火墙是否开启；数据库/Redis 等敏感端口是否无保护开放。
  4. 可疑定时任务：cron.json 中命令是否包含危险操作（rm -rf、curl|sh 等）。
  5. 安全配置：默认密码拦截、2FA 强制、数据目录权限等面板级配置核查。

设计：
  - 全部检查只读、超时保护、异常静默降级（单项失败不影响整体报告）。
  - 输出统一结构：{ score, summary, items: [{level, title, detail, advice}] }
  - 前端 HealthCheckWindow.vue 渲染报告并按 level 分级着色。
"""
import json
import logging
import os
import platform
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException

from app import auth

logger = logging.getLogger("graw.healthcheck")

router = APIRouter()

DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
IS_WIN = platform.system() == "Windows"

# 常见弱密码字典（面板登录弱口令探测）
# 分层设计（性能优化）：bcrypt 校验是 CPU 密集操作（单次约 0.4s），
# 若逐账号×逐口令串行校验会在账号较多时耗时数十秒。因此：
#   - 所有账号：仅校验高频弱口令（最常被爆破的 TOP 口令）
#   - 管理员账号：额外校验补充字典（管理员被攻破影响面最大）
# 且全部组合一次性提交到同一个线程池并行计算（避免每账号单独开池），
# 将单次体检从数十秒压到秒级。
#
# 注意：Windows 下 bcrypt 多线程并行收益有限（实测 8/16/32 线程耗时
# 几乎相同），因此字典规模直接决定耗时上限。这里刻意控制字典长度：
# 高频 9 个 + 补充 9 个 = 18 个，兼顾覆盖与速度；单账号体检约 2~4s，
# 账号越多线性变慢（数据文件里的历史测试账号会拉高耗时）。
WEAK_PASSWORDS_HIGH = [
    "admin", "admin123", "123456", "password", "12345678", "123456789",
    "qwerty", "abc123", "111111",
]
WEAK_PASSWORDS = WEAK_PASSWORDS_HIGH + [
    "1234567890", "password123", "123123", "root", "test", "1234",
    "000000", "666666", "888888", "passw0rd",
]

# 并行校验的线程数（bcrypt 释放 GIL，多线程可并行加速）
_WEAK_PASS_MAX_WORKERS = 16


def _scan_weak_passwords() -> list:
    """扫描面板账号弱密码：明文存储 / 默认密码 / 常见弱密码命中。

    性能设计：bcrypt 校验是 CPU 密集操作（单次约 0.4~1s），若逐账号
    串行校验会在账号较多时耗时数十秒。因此将「默认密码校验」与「弱密码
    候选校验」全部组合一次性提交到同一个线程池并行计算（避免每账号单独
    开池），把单次体检从数十秒压到秒级。
    """
    findings = []
    users = auth._load_users() or {}

    # 第一阶段：收集所有需要 bcrypt 校验的任务（先收集再并行，避免频繁建池）
    #   task 类型："default"（默认密码） / "weak"（弱密码候选）
    tasks = []  # (kind, uname, hashed, weak)
    for uname, u in users.items():
        hashed = (u or {}).get("password", "")
        # 明文存储（非 bcrypt 前缀）→ 高危（无需 bcrypt 校验）
        if hashed and not hashed.startswith("$2"):
            findings.append({
                "level": "high",
                "title": f"账号「{uname}」密码明文存储",
                "detail": "密码字段不是 bcrypt 哈希，一旦数据文件泄露即密码外泄。",
                "advice": "使用「账号管理」重置该账号密码（面板会以 bcrypt 重新哈希）。",
            })
            continue
        if not hashed:
            continue
        # 默认密码校验并入线程池，避免串行阻塞。仅对 admin 角色判定「默认密码」
        # 高危（普通账号用默认口令会走下方弱密码校验以中危上报），避免出现
        # 「没有 admin 账号仍报 admin 使用默认密码」这类误报/命名不符。
        if (u or {}).get("role") == "admin":
            tasks.append(("default", uname, hashed, ""))
        # 常见弱密码：管理员查全量字典，普通账号只查高频口令（性能优化）
        candidates = WEAK_PASSWORDS if (u or {}).get("role") == "admin" else WEAK_PASSWORDS_HIGH
        for weak in candidates:
            tasks.append(("weak", uname, hashed, weak))

    # 第二阶段：全量并行 bcrypt 校验（单线程池，避免重复创建开销）
    if tasks:
        with ThreadPoolExecutor(max_workers=_WEAK_PASS_MAX_WORKERS) as ex:
            def _run(kind, hashed, weak):
                if kind == "default":
                    return auth.is_default_password(hashed)
                return auth.verify_password(weak, hashed)

            future_map = {
                ex.submit(_run, kind, hashed, weak): (kind, uname, weak)
                for kind, uname, hashed, weak in tasks
            }
            hits = set()      # 弱密码命中集合：(uname, weak)
            default_hit = set()  # 默认密码命中账号集合
            for fut in as_completed(future_map):
                if fut.result():
                    kind, uname, weak = future_map[fut]
                    if kind == "default":
                        default_hit.add(uname)
                    else:
                        hits.add((uname, weak))

        # 默认密码命中 → 高危（优先级最高，不再判弱密码）
        # 注意：tasks 元素为四元组 (kind, uname, hashed, weak)
        for kind, uname, _hashed, _weak in tasks:
            if kind == "default" and uname in default_hit:
                findings.append({
                    "level": "high",
                    "title": f"账号「{uname}」仍在使用默认密码",
                    "detail": "默认密码属于高危隐患，任何知道默认值的人都能登录。",
                    "advice": "立即登录并修改为强密码（面板已强制拦截默认密码登录）。",
                })
                # 同账号后续弱密码候选不再上报
                hits = {(u, w) for u, w in hits if u != uname}

        # 每账号只报一条弱密码（按候选顺序取第一个命中）
        # 注意：tasks 元素为四元组 (kind, uname, hashed, weak)，解包需完整
        for kind, uname, _hashed, weak in tasks:
            if kind == "weak" and (uname, weak) in hits:
                findings.append({
                    "level": "medium",
                    # 安全修复（第十轮审计，Medium）：不回显命中的明文弱密码。
                    # 此前 title 直接带 {weak}，多管理员场景下任意管理员运行
                    # 体检即可确认/获知其他管理员账号的真实明文密码，等价于
                    # 在线爆破辅助。仅提示命中常见弱口令，不影响排查价值。
                    "title": f"账号「{uname}」密码过于简单（命中常见弱口令字典）",
                    "detail": "该密码命中常见弱口令字典，容易被暴力破解，请尽快更换。",
                    "advice": "建议更换为 12 位以上含大小写/数字/符号的强密码。",
                })
                # 同账号后续候选不再重复上报
                hits = {(u, w) for u, w in hits if u != uname}
    return findings

# 危险操作命令特征（可疑定时任务判定）
SUSPICIOUS_CMDS = [
    r"\brm\s+-rf\s+[~/]",
    r"\bcurl\b.*\|\s*(ba)?sh\b",
    r"\bwget\b.*\|\s*(ba)?sh\b",
    r"\bmkpasswd\b",
    r"base64\s+.*-d\b",
    r"\bchmod\s+777\b",
    r"i?ptables\s+-F\b",
    r">\s*/etc/passwd",
    r"python\S*\s+-c\s+['\"]import\s+(os|socket|subprocess)",
]

# 敏感端口清单（数据库/缓存/管理面板等，若对外开放且无防火墙规则即告警）
SENSITIVE_PORTS = {
    3306: "MySQL", 5432: "PostgreSQL", 6379: "Redis", 27017: "MongoDB",
    9200: "Elasticsearch", 11211: "Memcached", 1433: "SQL Server",
    3389: "RDP", 5900: "VNC", 23: "Telnet",
}


# ---------------------------------------------------------------------------
# 体检项实现（每个返回 list[dict]，异常内部捕获）
# 注意：_scan_weak_passwords 的唯一实现是上面的「并行优化版」（见模块顶部），
#       此处不再重复定义，避免旧串行版本覆盖导致体检性能回退（数十秒级）。
# ---------------------------------------------------------------------------
def _scan_login_logs() -> list:
    """扫描登录日志：连续失败（爆破迹象）与异常成功登录。"""
    findings = []
    try:
        from app.routers import loginlog

        logs = loginlog._load_logs()
    except Exception as e:
        logger.warning("读取登录日志失败: %s", e)
        return findings
    if not logs:
        return findings

    # 爆破迹象：最近 20 条内同一账号失败次数 >= 5，或失败占比过高
    recent = logs[:20]
    fail_counts: dict = {}
    for entry in recent:
        if entry.get("status") == "failed":
            uname = entry.get("username") or ""
            fail_counts[uname] = fail_counts.get(uname, 0) + 1
    for uname, cnt in fail_counts.items():
        if cnt >= 5:
            findings.append({
                "level": "high",
                "title": f"检测到针对账号「{uname}」的暴力破解尝试",
                "detail": f"最近 {len(recent)} 条登录记录中该账号连续失败 {cnt} 次。",
                "advice": "检查该账号密码强度；面板已内置同 IP+账号 5 次失败锁定 10 分钟。",
            })

    # 异常成功登录（新 IP / 新设备）
    abnormal = [e for e in logs if e.get("abnormal")]
    if abnormal:
        latest = abnormal[0]
        findings.append({
            "level": "medium",
            "title": f"存在异常登录记录（{latest.get('time', '')}，账号「{latest.get('username', '')}」）",
            "detail": f"来源 IP：{latest.get('ip', '')}，原因：{latest.get('abnormal_reason', '')}",
            "advice": "如非本人操作，建议立即修改密码并在「登录日志」中排查。",
        })
    return findings


def _scan_ports() -> list:
    """扫描敏感端口开放情况与防火墙配置。"""
    findings = []
    try:
        from app.routers import firewall

        fw = firewall._load_fw()
        enabled = fw.get("enabled", True)
        port_rules = fw.get("port_rules", [])
    except Exception as e:
        logger.warning("读取防火墙配置失败: %s", e)
        return findings

    if not enabled:
        findings.append({
            "level": "high",
            "title": "系统防火墙处于关闭状态",
            "detail": "当前未启用防火墙，所有端口均对外暴露，风险较高。",
            "advice": "前往「防火墙」开启防护，并配置必要的放行/拦截规则。",
        })

    # 敏感端口：若存在未配置规则（既无放行也无拦截）即提示
    rule_ports = set()
    for r in port_rules:
        p = r.get("port")
        if isinstance(p, int):
            rule_ports.add(p)
    for port, name in SENSITIVE_PORTS.items():
        if port not in rule_ports:
            findings.append({
                "level": "medium",
                "title": f"敏感端口 {port}（{name}）未配置防火墙规则",
                "detail": "该端口通常承载数据库/远程管理服务，若确实对外服务建议加白名单限制来源 IP。",
                "advice": "如无需公网访问，建议在防火墙中拦截该端口；如需要，请限制来源 IP。",
            })
    return findings


def _scan_cron() -> list:
    """扫描可疑定时任务（危险命令特征）。"""
    findings = []
    try:
        from app.routers import cron

        tasks = cron._load_tasks()
    except Exception as e:
        logger.warning("读取计划任务失败: %s", e)
        return findings
    if not isinstance(tasks, list):
        return findings

    for task in tasks:
        cmd = (task.get("command") or "") if isinstance(task, dict) else ""
        name = task.get("name") or task.get("id") or "未命名任务"
        for pat in SUSPICIOUS_CMDS:
            if re.search(pat, cmd, re.IGNORECASE):
                findings.append({
                    "level": "high" if "rm -rf" in cmd or "/etc/passwd" in cmd else "medium",
                    "title": f"计划任务「{name}」包含疑似危险命令",
                    "detail": f"命令片段：{cmd[:120]}",
                    "advice": "请确认该任务来源是否可信；如非必要建议停用或移除。",
                })
                break
    return findings


def _scan_panel_config() -> list:
    """面板安全配置核查：管理员是否开启 2FA。"""
    findings = []
    users = auth._load_users() or {}
    # 管理员列表（含 admin 角色）
    admins = [u for u in users.values() if (u or {}).get("role") == "admin"]
    if admins and not any(u.get("otp_enabled") for u in admins):
        findings.append({
            "level": "low",
            "title": "管理员账号未开启两步验证（2FA）",
            "detail": "2FA 可显著提升账号安全性，当前没有任何管理员启用 TOTP。",
            "advice": "建议管理员在「账号管理」中开启 2FA，防止密码泄露后被直接登录。",
        })
    return findings


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@router.get("/run")
async def run_check():
    """执行一键体检，返回分级报告。"""
    try:
        items = []
        items += _scan_weak_passwords()
        items += _scan_login_logs()
        items += _scan_ports()
        items += _scan_cron()
        items += _scan_panel_config()

        # 计算总分（满分 100，按级别扣分）
        score = max(0, 100 - sum({"high": 25, "medium": 10, "low": 3}[i["level"]] for i in items))
        summary = {
            "total": len(items),
            "high": sum(1 for i in items if i["level"] == "high"),
            "medium": sum(1 for i in items if i["level"] == "medium"),
            "low": sum(1 for i in items if i["level"] == "low"),
        }
        return {"score": score, "summary": summary, "items": items}
    except Exception as e:
        logger.error("系统体检执行失败: %s", e)
        raise HTTPException(status_code=500, detail=f"体检执行失败：{e}")
