# -*- coding: utf-8 -*-
"""
数据库初始化脚本。

功能：
1. 创建所有数据表（如不存在）
2. 创建默认管理员账号（如不存在）
3. 支持指定管理员用户名和密码

用法：
    python init_db.py                    # 使用默认管理员 admin / admin123
    python init_db.py --admin admin --admin-pass admin123
    python init_db.py --drop             # 删除所有表后重建
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bcrypt
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from config import config
from app import app
from models import User, db


def _hash_password(plaintext: str) -> str:
    """使用 bcrypt 哈希密码。"""
    raw = plaintext.encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def ensure_reply_hierarchy_columns(verbose: bool = True) -> list:
    """幂等地为存量 replies 表补充 V3 层级字段与索引。

    本地 SQLite 开发库不会因 db.create_all() 自动加列（create_all 只建不存在的表，
    不修改已有表结构），因此在初始化时做一次结构巡检并补齐。
    MySQL/MariaDB 生产环境也可重复执行此函数，或直接跑 database/migration_v3.sql。

    返回本次实际补充的列名与索引名列表（空列表表示结构已是最新）。
    """
    added = []

    with app.app_context():
        inspector = sa_inspect(db.engine)
        if not inspector.has_table("replies"):
            return added

        existing_cols = {c["name"] for c in inspector.get_columns("replies")}

        # 1) 补列（SQLite / MySQL / MariaDB 均支持普通 ALTER TABLE ADD COLUMN）
        with db.engine.begin() as conn:
            if "parent_id" not in existing_cols:
                conn.execute(text(
                    "ALTER TABLE replies ADD COLUMN parent_id INTEGER"
                ))
                added.append("column:parent_id")
            if "root_id" not in existing_cols:
                conn.execute(text(
                    "ALTER TABLE replies ADD COLUMN root_id INTEGER"
                ))
                added.append("column:root_id")

        # 2) 补索引（已存在或方言不支持时静默跳过，不阻断初始化）
        existing_idx = {i["name"] for i in inspector.get_indexes("replies")}
        for idx_name, col in (
            ("idx_reply_parent", "parent_id"),
            ("idx_reply_root", "root_id"),
        ):
            if idx_name in existing_idx:
                continue
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        f"CREATE INDEX {idx_name} ON replies ({col})"
                    ))
                added.append(f"index:{idx_name}")
            except Exception as exc:  # noqa: BLE001
                if verbose:
                    print(f"[init_db] 索引 {idx_name} 创建失败（可忽略）: {exc}")

    return added


def ensure_updated_at_columns(verbose: bool = True) -> list:
    """幂等地为存量 posts / replies 表补充 updated_at 字段（模块 1）。

    与 ensure_reply_hierarchy_columns 同理：db.create_all() 只建不存在的表，
    不修改已有表结构，因此对存量库做一次结构巡检并补齐。
    MySQL/MariaDB 生产环境也可直接跑 database/migration_edit.sql。

    返回本次实际补充的 "表.列" 列表（空列表表示结构已是最新）。
    """
    added = []

    with app.app_context():
        inspector = sa_inspect(db.engine)
        with db.engine.begin() as conn:
            for table in ("posts", "replies"):
                if not inspector.has_table(table):
                    continue
                existing_cols = {c["name"] for c in inspector.get_columns(table)}
                if "updated_at" not in existing_cols:
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN updated_at DATETIME"
                    ))
                    added.append(f"{table}.updated_at")

    return added


def init_database(drop: bool = False, admin_user: str = "admin", admin_pass: str = "admin123"):
    """
    初始化数据库：建表 + 创建管理员。

    Args:
        drop: 是否先删除所有表再重建
        admin_user: 管理员用户名
        admin_pass: 管理员密码
    """
    with app.app_context():
        if drop:
            print("[init_db] 删除所有数据表...")
            db.drop_all()

        print("[init_db] 创建数据表...")
        db.create_all()

        # 存量库结构巡检：补齐 V3 回复层级字段与索引
        added = ensure_reply_hierarchy_columns()
        if added:
            print(f"[init_db] 已补充层级结构: {', '.join(added)}")
        else:
            print("[init_db] 层级字段与索引已就绪，无需变更")

        # 存量库结构巡检：补齐模块 1 编辑时间字段
        added_edit = ensure_updated_at_columns()
        if added_edit:
            print(f"[init_db] 已补充编辑时间字段: {', '.join(added_edit)}")
        else:
            print("[init_db] updated_at 字段已就绪，无需变更")

        # 检查管理员是否已存在
        existing = User.query.filter_by(username=admin_user).first()
        if existing:
            print(f"[init_db] 管理员 '{admin_user}' 已存在，跳过创建")
        else:
            print(f"[init_db] 创建管理员 '{admin_user}'...")
            admin = User(
                username=admin_user,
                password_hash=_hash_password(admin_pass),
                role="admin",
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.session.add(admin)
            db.session.commit()
            print(f"[init_db] 管理员创建成功 (ID: {admin.id})")

        # 统计当前数据
        user_count = User.query.count()
        from models import Post, Reply
        post_count = Post.query.count()
        reply_count = Reply.query.count()

        print(f"\n[init_db] 数据库初始化完成：")
        print(f"  - 用户: {user_count}")
        print(f"  - 帖子: {post_count}")
        print(f"  - 回复: {reply_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="论坛系统数据库初始化脚本")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="删除所有表后重建（慎用！）",
    )
    parser.add_argument(
        "--admin",
        default="admin",
        help="管理员用户名（默认：admin）",
    )
    parser.add_argument(
        "--admin-pass",
        default="admin123",
        help="管理员密码（默认：admin123）",
    )

    args = parser.parse_args()

    if args.drop:
        confirm = input("[init_db] 警告：将删除所有数据！确定继续？(yes/no) ")
        if confirm.strip().lower() != "yes":
            print("[init_db] 操作已取消")
            sys.exit(0)

    init_database(
        drop=args.drop,
        admin_user=args.admin,
        admin_pass=args.admin_pass,
    )
