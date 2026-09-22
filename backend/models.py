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
    # 用户名唯一（登录凭证）
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    # bcrypt 生成的哈希字符串，最多 128 字符
    password_hash = db.Column(db.String(128), nullable=False)
    # 角色：普通用户 user / 管理员 admin
    role = db.Column(db.String(16), nullable=False, default="user")
    # 显示昵称（V2 新增），可为空，为空时回退显示 username
    nickname = db.Column(db.String(50), nullable=True)
    # 个人简介（V2 新增），最多 200 字符
    bio = db.Column(db.Text, nullable=True)
    # 头像文件路径（V2 新增），相对路径如 /uploads/avatars/xxx.png
    avatar_url = db.Column(db.String(255), nullable=True)
    # 创建时间
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    # 关联关系
    posts = db.relationship("Post", backref="author", lazy="select")
    replies = db.relationship("Reply", backref="author", lazy="select")

    @property
    def display_name(self):
        """获取显示名称：优先昵称，其次用户名。"""
        return self.nickname or self.username

    def to_dict(self):
        """转换为可 JSON 序列化的字典（不含敏感字段）。"""
        return {
            "id": self.id,
            "username": self.username,
            "nickname": self.nickname,
            "bio": self.bio,
            "avatar_url": self.avatar_url,
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
    # 最后编辑时间；NULL 表示从未编辑
    updated_at = db.Column(db.DateTime, nullable=True)

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
            "nickname": self.author.nickname if self.author else None,
            "display_name": self.author.display_name if self.author else None,
            "avatar_url": self.author.avatar_url if self.author else None,
            "role": self.author.role if self.author else None,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_replies:
            data["replies"] = [r.to_dict() for r in self.replies]
        return data

    def __repr__(self):  # pragma: no cover
        return f"<Post {self.id} {self.title!r}>"


class Reply(db.Model):
    """回复表。

    V3 新增层级字段：
    - parent_id: 直接父回复 id，NULL 表示一级回复（话题根）
    - root_id:   所属话题的一级回复 id；一级回复在库中存 NULL，
                 读取时由后端统一解析为其自身 id，便于前端直接按 root_id 聚合话题

    depth 不落库，由后端读取时按 parent_id 链实时计算。
    """

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
    # 直接父回复（V3 新增）：NULL = 一级回复
    parent_id = db.Column(
        db.Integer, db.ForeignKey("replies.id"), nullable=True, index=True
    )
    # 所属话题根回复（V3 新增）：一级回复自身为 NULL
    root_id = db.Column(
        db.Integer, db.ForeignKey("replies.id"), nullable=True, index=True
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )
    # 最后编辑时间；NULL 表示从未编辑
    updated_at = db.Column(db.DateTime, nullable=True)

    # 直接子回复（自引用）。两张自引用外键（parent_id / root_id）存在歧义，
    # 故用 foreign() 显式标注 primaryjoin 的外键一侧。
    children = db.relationship(
        "Reply",
        primaryjoin="Reply.id == foreign(Reply.parent_id)",
        foreign_keys=[parent_id],
        lazy="select",
        order_by="Reply.created_at",
    )

    def to_dict(self):
        """转换为字典（含层级字段，但不含 depth/children 等需上下文的派生值）。"""
        return {
            "id": self.id,
            "post_id": self.post_id,
            "user_id": self.user_id,
            "username": self.author.username if self.author else None,
            "nickname": self.author.nickname if self.author else None,
            "display_name": self.author.display_name if self.author else None,
            "avatar_url": self.author.avatar_url if self.author else None,
            "role": self.author.role if self.author else None,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "parent_id": self.parent_id,
            "root_id": self.root_id,
        }

    def __repr__(self):  # pragma: no cover
        return f"<Reply {self.id} on post {self.post_id} parent={self.parent_id}>"
