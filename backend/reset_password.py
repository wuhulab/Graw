#!/usr/bin/env python3
"""在服务器本地命令行重置任意用户密码。

用法：
    cd backend
    python reset_password.py [username]

不带参数启动时会列出所有账号并交互式提示输入。
密码通过 getpass 隐藏输入，避免出现在 shell 历史中。
"""

import argparse
import os
import sys

# 把 backend 目录加入 sys.path，以便 import app 包
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.auth import _load_users, _save_users, hash_password, USERS_FILE


def _read_hidden(prompt: str) -> str:
    """跨平台隐藏输入；若 getpass 不可用则回退到明文 input。"""
    try:
        import getpass as _gp

        return _gp.getpass(prompt)
    except Exception:
        return input(prompt)


def list_users() -> None:
    users = _load_users()
    if not users:
        print(f"未找到用户数据文件：{USERS_FILE}")
        return
    print("已存在的账号：")
    for name, info in users.items():
        role = info.get("role", "user")
        flag = " [待改密]" if info.get("must_change_password") else ""
        print(f"  - {name} ({role}){flag}")


def reset_password(username: str) -> None:
    users = _load_users()
    if not users:
        print(f"错误：未找到用户数据文件 {USERS_FILE}")
        sys.exit(1)

    if username not in users:
        print(f"错误：账号 '{username}' 不存在")
        print("可用账号：", ", ".join(users.keys()))
        sys.exit(1)

    pwd1 = _read_hidden("请输入新密码（至少 6 位）：")
    if len(pwd1) < 6:
        print("错误：密码长度不足 6 位")
        sys.exit(1)

    pwd2 = _read_hidden("请再次输入新密码：")
    if pwd1 != pwd2:
        print("错误：两次输入的密码不一致")
        sys.exit(1)

    users[username]["password"] = hash_password(pwd1)
    users[username]["must_change_password"] = False
    _save_users(users)
    print(f"账号 '{username}' 的密码已重置。")


def interactive() -> None:
    users = _load_users()
    if not users:
        print(f"错误：未找到用户数据文件 {USERS_FILE}")
        sys.exit(1)

    print("可用账号：", ", ".join(users.keys()))
    username = input("请输入要重置密码的账号：").strip()
    if not username:
        print("错误：账号不能为空")
        sys.exit(1)

    reset_password(username)


def main() -> None:
    parser = argparse.ArgumentParser(description="重置 Graw 面板用户密码")
    parser.add_argument("username", nargs="?", help="要重置的账号名")
    parser.add_argument("-l", "--list", action="store_true", help="列出所有账号")
    args = parser.parse_args()

    if args.list:
        list_users()
        return

    if args.username:
        reset_password(args.username)
    else:
        interactive()


if __name__ == "__main__":
    main()
