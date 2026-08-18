# -*- coding: utf-8 -*-
"""
ShunX 网页防篡改核心逻辑单元测试（不依赖运行中的后端服务）

覆盖：
  - 忽略规则 glob 匹配（* ? ** 与目录前缀）
  - 相对路径合法性校验（拒绝绝对路径 / .. 穿越）
  - 站点根目录校验（拒绝相对路径 / 面板数据目录）
  - 快照（基线 + 备份副本）
  - 篡改检测与自动回滚（哈希不一致 / 文件被删除）
  - 命中忽略规则的生产动态文件「绝不改动」

用法：
  python test_tamper_unit.py
"""
import os
import shutil
import sys
import tempfile

# 确保可导入 app 包（与 test_security_regression.py 同级目录）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.routers import tamper  # noqa: E402

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


# ---------------------------------------------------------------------------
# 忽略规则 glob 匹配
# ---------------------------------------------------------------------------
def test_pattern_matches():
    print("[1] 忽略规则 glob 匹配")
    cases = [
        # (pattern, rel, 期望)
        ("**/logs/**", "logs/access.log", True),
        ("**/logs/**", "var/logs/app/access.log", True),
        ("logs/**", "logs/access.log", True),
        ("logs", "logs/access.log", False),         # 纯目录名需经 _matches_ignore 补 /** 才覆盖子树
        ("logs", "logs", True),
        ("logs/*", "logs/a.log", True),
        ("logs/*", "logs/sub/a.log", False),        # * 不跨目录
        ("**/cache/**", "var/cache/www/x", True),
        ("uploads/*", "uploads/1.png", True),
        ("uploads/*", "uploads/sub/1.png", False),
        ("*.php", "config.php", True),
        ("*.php", "config.js", False),
        ("index.html", "index.html", True),
        ("index.html", "sub/index.html", False),
        ("assets/**", "assets/js/app.js", True),
        ("assets/**", "assets", True),
        ("", "anything", False),
    ]
    for pattern, rel, expected in cases:
        got = tamper._pattern_matches(pattern, rel)
        check(f"_pattern_matches({pattern!r}, {rel!r})", got == expected, f"期望 {expected}, 实际 {got}")

    # 目录前缀：_matches_ignore 会把 logs 解释为 logs/**（整棵子树）
    check("_matches_ignore 目录前缀", tamper._matches_ignore(["logs"], "logs/access.log"))
    check("_matches_ignore 组合规则", tamper._matches_ignore(["cache/**", "uploads/*"], "uploads/1.png"))
    check("_matches_ignore 未命中", not tamper._matches_ignore(["logs/**"], "index.html"))


# ---------------------------------------------------------------------------
# 相对路径校验
# ---------------------------------------------------------------------------
def test_valid_rel():
    print("[2] 相对路径合法性校验")
    check("普通相对路径", tamper._valid_rel("index.html"))
    check("多级相对路径", tamper._valid_rel("app/config.js"))
    check("Windows 分隔符", tamper._valid_rel("app\\config.js"))
    check("拒绝绝对路径", not tamper._valid_rel("/etc/passwd"))
    check("拒绝 .. 穿越", not tamper._valid_rel("../secret"))
    check("拒绝混合穿越", not tamper._valid_rel("a/../../b"))
    check("拒绝空值", not tamper._valid_rel(""))
    check("拒绝空段", not tamper._valid_rel("a//b"))
    check("拒绝空字节", not tamper._valid_rel("a\x00b"))
    check("拒绝以 / 开头", not tamper._valid_rel("/index.html"))
    check("glob 允许", tamper._valid_rel("logs/*.log"))
    check("** 允许", tamper._valid_rel("**/cache/**"))


# ---------------------------------------------------------------------------
# 站点根目录校验
# ---------------------------------------------------------------------------
def test_validate_root(tmp):
    print("[3] 站点根目录校验")
    # 合法：临时目录（绝对路径）
    check("合法绝对路径", tamper._validate_root(tmp) == tmp)
    # 非法：相对路径
    try:
        tamper._validate_root("var/www")
        fail("相对路径根目录被拒绝", "")
    except Exception:
        ok("拒绝相对路径根目录")
    # 非法：整个文件系统根
    try:
        tamper._validate_root("/")
        fail("文件系统根被拒绝", "")
    except Exception:
        ok("拒绝文件系统根目录")


# ---------------------------------------------------------------------------
# 快照 / 篡改检测 / 自动回滚
# ---------------------------------------------------------------------------
def _build_site(root, site_id="testsite"):
    return {
        "site_id": site_id,
        "site_name": "Test Site",
        "root": root,
        "protected_files": ["index.html", "config.php", "logs/access.log"],
        "ignore_patterns": ["logs/**"],
        "backup_interval_minutes": 60,
        "scan_interval_seconds": 15,
        "enabled": True,
        "baseline": {},
    }


def test_snapshot_and_rollback(tmp_root, tmp_backup):
    print("[4] 快照 + 篡改检测 + 自动回滚")
    # 准备站点文件
    os.makedirs(os.path.join(tmp_root, "logs"), exist_ok=True)
    index = os.path.join(tmp_root, "index.html")
    config = os.path.join(tmp_root, "config.php")
    access = os.path.join(tmp_root, "logs", "access.log")
    with open(index, "w", encoding="utf-8") as f:
        f.write("<html>hello</html>")
    with open(config, "w", encoding="utf-8") as f:
        f.write("<?php echo 'ok';")
    with open(access, "w", encoding="utf-8") as f:
        f.write("2026-08-18 log line")

    site = _build_site(tmp_root)
    baseline = tamper._snapshot_site(site)
    # 实际调用路径（create/update/备份）会把快照写回 site["baseline"]，这里同样回填
    site["baseline"] = baseline
    check("快照建立基线 3 个文件", len(baseline) == 3, str(len(baseline)))
    # 备份副本存在
    check("备份副本已生成", os.path.isfile(os.path.join(tmp_backup, "testsite", "index.html")))
    check("忽略文件也备份", os.path.isfile(os.path.join(tmp_backup, "testsite", "logs", "access.log")))

    # 篡改 index.html（受保护，未忽略）→ 应检测并回滚
    with open(index, "w", encoding="utf-8") as f:
        f.write("<html>EVIL PAYLOAD</html>")
    events = tamper._scan_site(site)
    check("检测到 index.html 被篡改", len(events) == 1, str(len(events)))
    check("事件为自动回滚", events and events[0]["restored"] is True)
    with open(index, "r", encoding="utf-8") as f:
        content = f.read()
    check("index.html 已恢复为基线内容", content == "<html>hello</html>", content)

    # 篡改被忽略的 logs/access.log → 绝不改动
    with open(access, "w", encoding="utf-8") as f:
        f.write("2026-08-18 EVIL LOG")
    events2 = tamper._scan_site(site)
    check("忽略文件不被判为篡改", len(events2) == 0, str(len(events2)))
    with open(access, "r", encoding="utf-8") as f:
        content = f.read()
    check("忽略文件内容未被改动", content == "2026-08-18 EVIL LOG", content)

    # 删除受保护文件 config.php → 应检测并恢复
    os.remove(config)
    events3 = tamper._scan_site(site)
    check("检测到文件被删除", len(events3) == 1, str(len(events3)))
    check("缺失文件已恢复", os.path.isfile(config) and tamper._file_hash(config) == baseline["config.php"]["hash"])

    # 恢复后再次扫描 → 无事件（避免反复告警/回滚）
    events4 = tamper._scan_site(site)
    check("恢复后扫描无事件", len(events4) == 0, str(len(events4)))


def test_containment(tmp_root):
    print("[5] 路径包含性防护")
    site = _build_site(tmp_root)
    # 受保护文件里的 .. 穿越：_scan_site 应跳过，不产生事件
    site["protected_files"] = ["../outside.txt"]
    events = tamper._scan_site(site)
    check(".. 穿越文件被跳过", len(events) == 0, str(len(events)))
    # _resolve_site_file 对穿越路径返回 None
    check("_resolve_site_file 拒绝穿越", tamper._resolve_site_file(tmp_root, "../outside.txt") is None)
    # 正常路径可解析
    check("_resolve_site_file 正常解析", tamper._resolve_site_file(tmp_root, "index.html") is not None)


def test_default_ignore(tmp_root):
    print("[6] 内置默认忽略规则（.log/.db/.sqlite/.sdb 等）")
    # 即使未显式配置忽略规则，内置默认规则也必须兜底生效
    site = _build_site(tmp_root)
    site["ignore_patterns"] = []  # 清空自定义忽略，验证默认规则
    site["protected_files"] = ["index.html", "data/app.db", "logs/app.log", "cache/data.sqlite", "tmp/s.sdb"]

    # 准备文件
    os.makedirs(os.path.join(tmp_root, "data"), exist_ok=True)
    os.makedirs(os.path.join(tmp_root, "logs"), exist_ok=True)
    os.makedirs(os.path.join(tmp_root, "cache"), exist_ok=True)
    os.makedirs(os.path.join(tmp_root, "tmp"), exist_ok=True)
    files = {
        "index.html": os.path.join(tmp_root, "index.html"),
        "data/app.db": os.path.join(tmp_root, "data", "app.db"),
        "logs/app.log": os.path.join(tmp_root, "logs", "app.log"),
        "cache/data.sqlite": os.path.join(tmp_root, "cache", "data.sqlite"),
        "tmp/s.sdb": os.path.join(tmp_root, "tmp", "s.sdb"),
    }
    for rel, path in files.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write("original")
    site["baseline"] = tamper._snapshot_site(site)

    # 篡改被默认规则覆盖的动态文件（.db/.log/.sqlite/.sdb）→ 不应被判为篡改
    for rel in ["data/app.db", "logs/app.log", "cache/data.sqlite", "tmp/s.sdb"]:
        with open(files[rel], "w", encoding="utf-8") as f:
            f.write("EVIL-" + rel)
    events = tamper._scan_site(site)
    check("默认忽略的动态文件不被判为篡改", len(events) == 0, str(len(events)))
    with open(files["data/app.db"], "r", encoding="utf-8") as f:
        check(".db 内容未被改动", f.read() == "EVIL-data/app.db")

    # 普通受保护文件仍会被监控
    with open(files["index.html"], "w", encoding="utf-8") as f:
        f.write("EVIL-INDEX")
    events2 = tamper._scan_site(site)
    check("普通受保护文件仍会检测回滚", len(events2) == 1, str(len(events2)))
    with open(files["index.html"], "r", encoding="utf-8") as f:
        check("普通文件已回滚", f.read() == "original")

    # 默认规则列表非空且包含 .log/.db/.sqlite/.sdb
    check("默认规则包含 .log", any(p.endswith("*.log") for p in tamper.DEFAULT_IGNORE_PATTERNS))
    check("默认规则包含 .db", any(p.endswith("*.db") for p in tamper.DEFAULT_IGNORE_PATTERNS))
    check("默认规则包含 .sqlite", any(p.endswith("*.sqlite") for p in tamper.DEFAULT_IGNORE_PATTERNS))
    check("默认规则包含 .sdb", any(p.endswith("*.sdb") for p in tamper.DEFAULT_IGNORE_PATTERNS))

    # _site_summary 应暴露默认规则列表
    summary = tamper._site_summary(site)
    check("_site_summary 返回 default_ignore_patterns", "default_ignore_patterns" in summary
          and len(summary["default_ignore_patterns"]) == len(tamper.DEFAULT_IGNORE_PATTERNS))


def main():
    # 保存原始常量，测试结束后恢复
    orig_file = tamper.TAMPER_FILE
    orig_backup = tamper.BACKUP_ROOT
    orig_data = tamper.DATA_DIR

    tmpdir = tempfile.mkdtemp(prefix="graw_tamper_test_")
    # 站点根目录与面板数据目录需互为兄弟目录（数据目录内不允许作为防护范围）
    tmp_root = os.path.join(tmpdir, "www")
    tmp_data = os.path.join(tmpdir, "data")
    os.makedirs(tmp_root, exist_ok=True)
    os.makedirs(tmp_data, exist_ok=True)
    tmp_backup = os.path.join(tmp_data, "tamper_backups")
    os.makedirs(tmp_backup, exist_ok=True)
    # 让备份根目录指向临时目录，避免污染 backend/data
    tamper.BACKUP_ROOT = tmp_backup
    tamper.DATA_DIR = tmp_data
    tamper.TAMPER_FILE = os.path.join(tmp_data, "tamper.json")

    try:
        test_pattern_matches()
        test_valid_rel()
        test_validate_root(tmp_root)
        test_snapshot_and_rollback(tmp_root, tmp_backup)
        test_containment(tmp_root)
        test_default_ignore(tmp_root)
    finally:
        tamper.TAMPER_FILE = orig_file
        tamper.BACKUP_ROOT = orig_backup
        tamper.DATA_DIR = orig_data
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n结果：通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
