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
from config import config
from app import app
from models import User, db


def _hash_password(plaintext: str) -> str:
    """使用 bcrypt 哈希密码。"""
    raw = plaintext.encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


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
