# -*- coding: utf-8 -*-
"""
SQLAlchemy 数据模型定义。

包含四张表：
- users:    用户表
- posts:    主贴表
- replies:  回复表
- view_log: 浏览去重记录表（模块 3-增强，替代进程内去重字典）
"""

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint

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
    # 密码版本号（安全增强）：每次修改密码自增 1。
    # JWT 载荷中携带签发时的 pwd_ver，require_auth 校验其与当前值一致，
    # 从而实现「改密码后旧 token 立即失效」，无需维护服务端 token 黑名单。
    pwd_ver = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    # 创建时间
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    # 关联关系。
    # passive_deletes=True：删除用户时由数据库外键 ON DELETE CASCADE
    # 级联清理其帖子/回复，ORM 不要预先把子行外键 UPDATE 成 NULL
    # （那样会使数据库级联失效并产生悬挂数据）
    posts = db.relationship(
        "Post", backref="author", lazy="select", passive_deletes=True
    )
    replies = db.relationship(
        "Reply", backref="author", lazy="select", passive_deletes=True
    )

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
    # 发帖人外键；删除用户时由数据库级联删除其帖子（与 database/init.sql 对齐）
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(200), nullable=False)
    # content 存 Markdown 源码
    content = db.Column(db.Text, nullable=False)
    # created_at 建索引：列表页按 created_at DESC 排序分页，无索引会全表 filesort
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        index=True,
    )
    # 最后编辑时间；NULL 表示从未编辑
    updated_at = db.Column(db.DateTime, nullable=True)
    # 浏览次数（模块 3）：原子自增维护，应用层做 30 分钟同用户/IP 去重
    view_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    # 关联关系。
    # passive_deletes=True：删帖时由 replies.post_id ON DELETE CASCADE
    # 自动删除全部回复，ORM 不做解除关联的 UPDATE
    replies = db.relationship(
        "Reply",
        backref="post",
        lazy="select",
        order_by="Reply.created_at",
        passive_deletes=True,
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
            "view_count": int(self.view_count or 0),
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
    # 所属帖子外键（需求要求建立索引）；删帖时数据库级联删除全部回复
    post_id = db.Column(
        db.Integer,
        db.ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = db.Column(db.Text, nullable=False)
    # 直接父回复（V3 新增）：NULL = 一级回复。
    # 删除父回复时由数据库级联删除整棵子树（级联删除的唯一执行点）
    parent_id = db.Column(
        db.Integer,
        db.ForeignKey("replies.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # 所属话题根回复（V3 新增）：一级回复自身为 NULL。
    # root 仅为聚合冗余指针，root 行被删时整树已由 parent_id 级联删除，
    # SET NULL 与 init.sql 保持一致
    root_id = db.Column(
        db.Integer,
        db.ForeignKey("replies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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
    # passive_deletes=True：删除父回复时由 parent_id ON DELETE CASCADE
    # 级联删除整棵子树，ORM 不做解除关联的 UPDATE
    children = db.relationship(
        "Reply",
        primaryjoin="Reply.id == foreign(Reply.parent_id)",
        foreign_keys=[parent_id],
        lazy="select",
        order_by="Reply.created_at",
        passive_deletes=True,
    )

    # 复合索引：帖子详情/回复列表按 (post_id, created_at) 取数并排序，
    # 同时支撑 reply_count 计数子查询，避免 filesort。
    __table_args__ = (
        db.Index("idx_replies_post_created", "post_id", "created_at"),
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


class ViewLog(db.Model):
    """浏览去重记录表（模块 3-增强）。

    背景：原实现用进程内 dict 做 30 分钟去重窗口，在 gunicorn 多 worker
    （deploy/forum-api.service 实配 --workers 2）下每个 worker 各持一份，
    同一访客轮询到不同 worker 会被重复计数，去重窗口形同虚设。

    改用数据库唯一键承载去重语义：
    - (post_id, visitor_key) 唯一约束，插入冲突即表示窗口内已计过数
    - visitor_key：登录用户为 "u:<id>"，匿名访客为 "ip:<addr>"
    - created_at 用于判定窗口是否过期并清理历史记录

    与 Redis SETNX+EXPIRE 语义一一对应，未来若引入 Redis 可平滑替换。
    """

    __tablename__ = "view_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    post_id = db.Column(
        db.Integer,
        db.ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 访客标识：u:<user_id> 或 ip:<addr>
    visitor_key = db.Column(db.String(128), nullable=False, index=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        index=True,
    )

    # 去重窗口的唯一性由数据库保证（多 worker / 多实例一致）
    __table_args__ = (
        UniqueConstraint("post_id", "visitor_key", name="uk_view_post_visitor"),
    )

    def __repr__(self):  # pragma: no cover
        return f"<ViewLog post={self.post_id} visitor={self.visitor_key!r}>"
