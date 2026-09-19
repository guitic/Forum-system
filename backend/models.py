# -*- coding: utf-8 -*-
"""
SQLAlchemy 数据模型定义。

包含三张表：
- users: 用户表
- posts: 主贴表
- replies: 回复表
"""

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

# 初始化 SQLAlchemy 实例（在 app.py 中绑定到 Flask 应用）
db = SQLAlchemy()


class User(db.Model):
    """用户表。"""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 用户名唯一
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    # bcrypt 生成的哈希字符串，最多 128 字符
    password_hash = db.Column(db.String(128), nullable=False)
    # 角色：普通用户 user / 管理员 admin
    role = db.Column(db.String(16), nullable=False, default="user")
    # 创建时间
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    # 关联关系
    posts = db.relationship("Post", backref="author", lazy="select")
    replies = db.relationship("Reply", backref="author", lazy="select")

    def to_dict(self):
        """转换为可 JSON 序列化的字典（不含敏感字段）。"""
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):  # pragma: no cover
        return f"<User {self.username}>"


class Post(db.Model):
    """主贴表。"""

    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 发帖人外键
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    title = db.Column(db.String(200), nullable=False)
    # content 存 Markdown 源码
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    # 关联关系
    replies = db.relationship(
        "Reply", backref="post", lazy="select", order_by="Reply.created_at"
    )

    def to_dict(self, include_replies=False):
        """转换为字典，可选择性携带回复列表。"""
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.author.username if self.author else None,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_replies:
            data["replies"] = [r.to_dict() for r in self.replies]
        return data

    def __repr__(self):  # pragma: no cover
        return f"<Post {self.id} {self.title!r}>"


class Reply(db.Model):
    """回复表。"""

    __tablename__ = "replies"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 所属帖子外键（需求要求建立索引）
    post_id = db.Column(
        db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    def to_dict(self):
        """转换为字典。"""
        return {
            "id": self.id,
            "post_id": self.post_id,
            "user_id": self.user_id,
            "username": self.author.username if self.author else None,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):  # pragma: no cover
        return f"<Reply {self.id} on post {self.post_id}>"
