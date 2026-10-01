"""Local administration commands for My Changli.

Passwords are read with getpass and never echoed or written to command history.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import uuid

import server
from runtime_config import PLACEHOLDER_MARKERS


def read_password(confirm: bool = True) -> str:
    password = getpass.getpass("管理员密码（至少 12 位）: ")
    if len(password) < 12:
        raise ValueError("管理员密码至少需要 12 位")
    if confirm and password != getpass.getpass("再次输入管理员密码: "):
        raise ValueError("两次输入的密码不一致")
    return password


def create_admin(username: str) -> None:
    if server.RUNTIME.is_production:
        raise ValueError(
            "Production 禁止使用 create-admin；请由现有 SUPER_ADMIN 通过后台/API 创建运营管理员"
        )
    username = username.strip()
    if not username or len(username) > 48:
        raise ValueError("管理员用户名长度应为 1 至 48 个字符")
    password = read_password()
    digest, salt = server.password_record(password)
    with server.connection() as db:
        if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("该用户名已经存在")
        db.execute(
            """INSERT INTO users(
                   id,username,password_hash,password_salt,role,account_type,nickname,
                   admin_permissions_json,admin_role,admin_enabled,created_at
               ) VALUES(?,?,?,?, 'ADMIN','ADMIN','本地管理员','["*"]','SUPER_ADMIN',1,?)""",
            (str(uuid.uuid4()), username, digest, salt, server.now_ms()),
        )
    print("管理员账号创建成功。")


def reset_password(username: str) -> None:
    password = read_password()
    digest, salt = server.password_record(password)
    with server.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        target = db.execute(
            "SELECT id FROM users WHERE username=? AND role='ADMIN'",
            (username.strip(),),
        ).fetchone()
        if not target:
            raise ValueError("未找到该管理员账号")
        db.execute(
            """UPDATE users SET password_hash=?,password_salt=?
               WHERE id=?""",
            (digest, salt, target["id"]),
        )
        db.execute("DELETE FROM sessions WHERE user_id=?", (target["id"],))
    print("管理员密码已更新，全部旧登录会话已撤销。")


def has_admin() -> int:
    with server.connection() as db:
        exists = db.execute(
            "SELECT 1 FROM users WHERE role='ADMIN' LIMIT 1"
        ).fetchone()
    return 0 if exists else 1


def bootstrap_production() -> None:
    if not server.RUNTIME.is_production:
        raise ValueError("bootstrap-production 只允许在 MY_CHUANNONG_ENV=production 下执行")
    username = os.getenv("MY_CHUANNONG_SUPER_ADMIN_USERNAME", "").strip()
    password = os.getenv("MY_CHUANNONG_SUPER_ADMIN_PASSWORD", "")
    if (
        not username
        or len(username) > 48
        or any(marker in username.upper() for marker in PLACEHOLDER_MARKERS)
    ):
        raise ValueError("必须从仓库外提供有效的 MY_CHUANNONG_SUPER_ADMIN_USERNAME")
    if len(password) < 16 or any(
        marker in password.upper() for marker in PLACEHOLDER_MARKERS
    ):
        raise ValueError(
            "MY_CHUANNONG_SUPER_ADMIN_PASSWORD 必须是至少 16 位的非占位随机值"
        )
    tables_that_must_be_empty = (
        "reservations",
        "lost_found_posts",
        "lost_found_claims",
        "lost_found_reports",
        "feedback",
        "feedback_events",
        "announcements",
        "resource_favorites",
        "resource_views",
        "resource_complaints",
    )
    with server.connection() as db:
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise ValueError("生产库已有用户；拒绝重复 bootstrap")
        dirty = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables_that_must_be_empty
        }
        dirty = {table: count for table, count in dirty.items() if count}
        if dirty:
            raise ValueError(f"生产库不是干净业务状态：{dirty}")
        digest, salt = server.password_record(password)
        db.execute(
            """INSERT INTO users(
                   id,username,password_hash,password_salt,role,account_type,nickname,
                   admin_permissions_json,admin_role,admin_enabled,account_status,created_at
               ) VALUES(?,?,?,?, 'ADMIN','ADMIN','超级管理员','["*"]',
                        'SUPER_ADMIN',1,'ACTIVE',?)""",
            (str(uuid.uuid4()), username, digest, salt, server.now_ms()),
        )
    os.environ.pop("MY_CHUANNONG_SUPER_ADMIN_PASSWORD", None)
    server.ensure_production_database_ready()
    print("生产数据库已初始化，并创建唯一首个 SUPER_ADMIN。")


def main() -> int:
    server.initialize_database()
    parser = argparse.ArgumentParser(description="我的川农本地后台账号管理")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="创建管理员")
    create.add_argument("username")
    reset = commands.add_parser("reset-password", help="重置管理员密码")
    reset.add_argument("username")
    commands.add_parser("has-admin", help="检查是否已有管理员")
    commands.add_parser(
        "bootstrap-production",
        help="在干净生产库中从仓库外环境变量创建首个 SUPER_ADMIN",
    )
    args = parser.parse_args()
    try:
        if args.command == "create-admin":
            create_admin(args.username)
        elif args.command == "reset-password":
            reset_password(args.username)
        elif args.command == "bootstrap-production":
            bootstrap_production()
        else:
            return has_admin()
        return 0
    except (ValueError, KeyboardInterrupt) as error:
        print(f"操作未完成：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
