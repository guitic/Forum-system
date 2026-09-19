# -*- coding: utf-8 -*-
"""
帖子与回复相关路由。

包含鉴权装饰器、分页、增删查逻辑。
"""

import functools
import math
from datetime import datetime, timezone

import jwt
from flask import Blueprint, jsonify, request

from config import config
from models import Post, Reply, User, db

posts_bp = Blueprint("posts", __name__, url_prefix="/api/posts")


# ---------- 鉴权装饰器 ----------

def require_auth(fn):
    """
    鉴权装饰器：从 Authorization: Bearer <token> 中解析 JWT，
    并将当前用户注入被装饰函数的 `current_user` 参数。
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "缺少有效的 Bearer Token"}), 401

        token = header[len("Bearer "):].strip()
        if not token:
            return jsonify({"error": "缺少有效的 Bearer Token"}), 401

        try:
            payload = jwt.decode(
                token,
                config.JWT_SECRET_KEY,
                algorithms=[config.JWT_ALGORITHM],
            )
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token 已过期，请重新登录"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token 无效"}), 401

        # 优先按 id 查用户；查询失败则视为无效
        try:
            user_id = int(payload.get("sub"))
        except (TypeError, ValueError):
            return jsonify({"error": "Token 无效"}), 401

        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "Token 对应的用户不存在"}), 401

        kwargs["current_user"] = user
        return fn(*args, **kwargs)

    return wrapper


# ---------- 辅助函数 ----------

def _get_pagination():
    """从查询参数中读取分页信息，并做边界收敛。"""
    try:
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", config.DEFAULT_PAGE_SIZE))
    except ValueError:
        page = 1
        limit = config.DEFAULT_PAGE_SIZE

    page = max(1, page)
    limit = max(1, min(limit, config.MAX_PAGE_SIZE))
    return page, limit


def _parse_dt(value):
    """将 datetime 转为 ISO 字符串，None 安全。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        # 如果数据库返回的是 naive datetime，附加 UTC 时区信息
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    except AttributeError:
        return str(value)


# ---------- 路由 ----------

@posts_bp.get("")
@posts_bp.get("/")
def list_posts():
    """
    GET /api/posts

    主贴列表，按 created_at 倒序。
    查询参数：page（默认1）、limit（默认20，最大100）
    """
    page, limit = _get_pagination()
    offset = (page - 1) * limit

    query = (
        db.session.query(
            Post.id,
            Post.user_id,
            Post.title,
            Post.content,
            Post.created_at,
            User.username,
        )
        .join(User, Post.user_id == User.id)
        .order_by(Post.created_at.desc())
    )

    total = query.count()
    rows = query.offset(offset).limit(limit).all()

    posts = [
        {
            "id": r.id,
            "user_id": r.user_id,
            "username": r.username,
            "title": r.title,
            "content": r.content,
            "created_at": _parse_dt(r.created_at),
        }
        for r in rows
    ]

    pages = int(math.ceil(total / limit)) if limit else 0
    return (
        jsonify(
            {
                "posts": posts,
                "total": total,
                "page": page,
                "limit": limit,
                "pages": pages,
            }
        ),
        200,
    )


@posts_bp.get("/<int:post_id>")
def get_post(post_id):
    """
    GET /api/posts/{id}

    帖子详情，含所有回复列表（按时间正序）。
    """
    post = Post.query.get(post_id)
    if not post:
        return jsonify({"error": "帖子不存在"}), 404

    # 按 created_at 升序返回回复
    replies = (
        Reply.query.filter_by(post_id=post.id)
        .order_by(Reply.created_at.asc())
        .all()
    )

    return (
        jsonify(
            {
                "id": post.id,
                "user_id": post.user_id,
                "username": post.author.username if post.author else None,
                "title": post.title,
                "content": post.content,
                "created_at": _parse_dt(post.created_at),
                "replies": [
                    {
                        "id": r.id,
                        "post_id": r.post_id,
                        "user_id": r.user_id,
                        "username": r.author.username if r.author else None,
                        "content": r.content,
                        "created_at": _parse_dt(r.created_at),
                    }
                    for r in replies
                ],
            }
        ),
        200,
    )


@posts_bp.post("")
@posts_bp.post("/")
@require_auth
def create_post(current_user):
    """
    POST /api/posts

    发布新贴。
    Header: Authorization: Bearer <token>
    Body: {"title": "...", "content": "..."}
    """
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    content = data.get("content") or ""

    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    if len(title) > 200:
        return jsonify({"error": "标题长度不能超过 200 个字符"}), 400
    if not content.strip():
        return jsonify({"error": "内容不能为空"}), 400

    post = Post(user_id=current_user.id, title=title, content=content)
    try:
        db.session.add(post)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "发布帖子失败"}), 500

    return (
        jsonify(
            {
                "id": post.id,
                "user_id": post.user_id,
                "username": current_user.username,
                "title": post.title,
                "content": post.content,
                "created_at": _parse_dt(post.created_at),
            }
        ),
        201,
    )


@posts_bp.post("/<int:post_id>/replies")
@require_auth
def create_reply(post_id, current_user):
    """
    POST /api/posts/{id}/replies

    发布回复。
    Header: Authorization: Bearer <token>
    Body: {"content": "..."}
    """
    post = Post.query.get(post_id)
    if not post:
        return jsonify({"error": "帖子不存在"}), 404

    data = request.get_json(silent=True) or {}
    content = data.get("content") or ""
    if not content.strip():
        return jsonify({"error": "回复内容不能为空"}), 400

    reply = Reply(
        post_id=post.id, user_id=current_user.id, content=content
    )
    try:
        db.session.add(reply)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "发布回复失败"}), 500

    return (
        jsonify(
            {
                "id": reply.id,
                "post_id": reply.post_id,
                "user_id": reply.user_id,
                "username": current_user.username,
                "content": reply.content,
                "created_at": _parse_dt(reply.created_at),
            }
        ),
        201,
    )


@posts_bp.delete("/<int:post_id>")
@require_auth
def delete_post(post_id, current_user):
    """
    DELETE /api/posts/{id}

    删除主贴及其全部回复。
    只有发帖人或管理员 (role='admin') 可以删除。
    """
    post = Post.query.get(post_id)
    if not post:
        return jsonify({"error": "帖子不存在"}), 404

    is_owner = post.user_id == current_user.id
    is_admin = current_user.role == "admin"
    if not (is_owner or is_admin):
        return jsonify({"error": "无权删除该帖子"}), 403

    try:
        # 级联删除所有关联回复
        Reply.query.filter_by(post_id=post.id).delete()
        db.session.delete(post)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "删除失败"}), 500

    return jsonify({"message": "删除成功"}), 200
