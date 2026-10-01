# -*- coding: utf-8 -*-
"""
帖子与回复相关路由。

包含鉴权装饰器、分页、增删查逻辑。

V3 新增：
- 回复层级树构建（parent_id / root_id）
- GET  /api/posts/{id}          返回 reply_tree + replies + reply_count
- POST /api/posts/{id}/replies  Body 新增可选 parent_id（楼中楼）

编辑接口（模块 1 新增）：
- PUT  /api/posts/{id}          编辑帖子（作者或管理员），写入 updated_at

删除回复接口见 routes/replies.py。
"""

import functools
import itertools
import logging
import math
from datetime import datetime, timedelta, timezone

import jwt
import sqlalchemy as sa
from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from config import config
from models import Post, Reply, User, ViewLog, db
from utils.security import require_auth

posts_bp = Blueprint("posts", __name__, url_prefix="/api/posts")

logger = logging.getLogger("forum-api")


# ---------- 浏览量去重（模块 3 / 增强：持久化到 view_log 表） ----------
# 说明：原实现为进程内 TTL 字典，在 gunicorn 多 worker（生产 --workers 2）
#       下每个 worker 各持一份，同一访客轮询到不同 worker 会重复计数。
#       现改为数据库唯一键承载去重语义：
#         INSERT view_log(post_id, visitor_key) 冲突 → 窗口内已计过数
#       多 worker / 多实例共享同一份去重状态，语义与 Redis SETNX 一致。
_view_cleanup_counter = itertools.count(1)


def _visitor_key():
    """识别本次访问的访客：优先登录用户 id，无效 token / 匿名回退客户端 IP。"""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header[len("Bearer "):].strip()
        try:
            payload = jwt.decode(
                token,
                config.JWT_SECRET_KEY,
                algorithms=[config.JWT_ALGORITHM],
            )
            return f"u:{int(payload.get('sub'))}"
        except (jwt.InvalidTokenError, TypeError, ValueError):
            pass  # 无效/过期 token 不阻断浏览，降级按 IP 去重

    # Nginx 反代部署取 X-Forwarded-For 首跳；直连回退 remote_addr。
    # 注意：直连客户端可伪造该头绕过去重（仅影响统计偏高），可信代理内网部署无此问题。
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded.strip() else (
        request.remote_addr or "unknown"
    )
    return f"ip:{ip}"


def _cleanup_view_log(force=False):
    """清理超过去重窗口的 view_log 记录。

    每次详情访问都全表 DELETE 成本高，故按 VIEW_LOG_CLEANUP_EVERY 概率抽样触发；
    force=True 用于测试或显式清理。失败静默（清理属后台维护，不应影响请求）。
    """
    if not force:
        # itertools.count 原子自增，取模决定是否本次触发
        if next(_view_cleanup_counter) % max(1, config.VIEW_LOG_CLEANUP_EVERY) != 0:
            return
    try:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=config.VIEW_DEDUP_WINDOW_SECONDS
        )
        deleted = (
            db.session.query(ViewLog)
            .filter(ViewLog.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.session.commit()
        if deleted:
            logger.info("已清理 %s 条过期浏览去重记录", deleted)
    except Exception:
        db.session.rollback()
        logger.warning("清理 view_log 失败", exc_info=True)


def _reserve_view(post_id):
    """尝试为本次访问占位：窗口内未访问则写入 view_log 并返回 True。

    依赖 uk_view_post_visitor 唯一约束实现原子去重：
    - 插入成功 → 本次访问有效，返回 True
    - 唯一冲突 → 窗口内已计过数，返回 False

    并发下由数据库保证只会有一个请求插入成功，无需应用层加锁。
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        seconds=config.VIEW_DEDUP_WINDOW_SECONDS
    )
    visitor = _visitor_key()
    try:
        # 先尝试更新窗口内的既有记录时间（滑动窗口语义），无匹配再插入。
        updated = (
            db.session.query(ViewLog)
            .filter(
                ViewLog.post_id == post_id,
                ViewLog.visitor_key == visitor,
                ViewLog.created_at >= cutoff,
            )
            .update(
                {ViewLog.created_at: datetime.now(timezone.utc).replace(tzinfo=None)},
                synchronize_session=False,
            )
        )
        if updated:
            # 窗口内已有记录：属于重复访问，不计数
            db.session.commit()
            return False

        # 无窗口内记录：清理可能存在的过期记录后插入（唯一键冲突即并发重复）
        db.session.query(ViewLog).filter(
            ViewLog.post_id == post_id,
            ViewLog.visitor_key == visitor,
        ).delete(synchronize_session=False)
        db.session.add(ViewLog(post_id=post_id, visitor_key=visitor))
        db.session.commit()
        return True
    except IntegrityError:
        # 并发下另一请求已抢先插入 → 本次视为重复访问
        db.session.rollback()
        return False
    except Exception:
        db.session.rollback()
        logger.warning("浏览量占位写入失败", exc_info=True)
        # 占位失败时不阻断浏览，但也不计数（避免统计虚高）
        return False


def _release_view(post_id):
    """释放本次访问的占位（仅用于自增失败时回滚，使访客下次可重新计数）。"""
    visitor = _visitor_key()
    try:
        db.session.query(ViewLog).filter(
            ViewLog.post_id == post_id,
            ViewLog.visitor_key == visitor,
        ).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.warning("浏览量占位释放失败", exc_info=True)


# ---------- 鉴权装饰器 ----------
# require_auth 已下沉至 utils/security.py，供 auth/user/upload/replies/posts
# 统一引用，避免各模块反向依赖本文件（模块 5-重构）。
# 此处仅做再导出，保持既有 `from routes.posts import require_auth` 的兼容性。
__all__ = ["posts_bp", "require_auth"]


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


# ---------- 回复层级构建（V3 新增；P1-6 下沉至 services/reply_tree.py） ----------
# 领域逻辑已抽到 services/reply_tree.py，避免 replies.py 反向依赖本模块。
# 此处保留原名别名，兼容既有 `from routes.posts import _reply_node` 的引用。
from services.reply_tree import (  # noqa: E402
    build_reply_node as _build_reply_node,
    build_reply_tree as _build_reply_tree_service,
    prepare_reply_structure as _prepare_reply_structure,
    reply_chain_info as _reply_chain_info,
    reply_node_dict as _reply_node_dict,
)


def _build_reply_tree(replies, post_author_id, max_depth):
    """构建展示用嵌套回复树（转调 services.reply_tree.build_reply_tree）。"""
    return _build_reply_tree_service(replies, post_author_id, max_depth)


def _reply_node(reply, max_depth, post_author_id):
    """序列化单条回复（转调 services.reply_tree.build_reply_node）。"""
    return _build_reply_node(reply, max_depth, post_author_id)


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

    # 回复数子查询：按 Reply.post_id 统计全部子回复（含嵌套楼中楼）
    # 与详情接口 len(replies) 口径一致
    reply_count_subq = (
        sa.select(sa.func.count())
        .select_from(Reply)
        .where(Reply.post_id == Post.id)
        .correlate(Post)
        .scalar_subquery()
    )

    query = (
        db.session.query(
            Post.id,
            Post.user_id,
            Post.title,
            Post.content,
            Post.created_at,
            Post.updated_at,
            User.username,
            User.nickname,
            User.avatar_url,
            User.role,
            reply_count_subq.label("reply_count"),
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
            "nickname": r.nickname,
            "display_name": r.nickname or r.username,
            "avatar_url": r.avatar_url,
            "role": r.role,
            "title": r.title,
            "content": r.content,
            "created_at": _parse_dt(r.created_at),
            "updated_at": _parse_dt(r.updated_at),
            "reply_count": int(r.reply_count or 0),
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

    帖子详情，含回复数据。
    V3 新增：
    - replies:    扁平列表（按时间正序，含层级字段，向后兼容）
    - reply_tree: 嵌套树（展示用，最多 MAX_REPLY_DEPTH+1 层）
    - reply_count: 回复总数
    模块 3 新增：
    - view_count: 浏览次数。本次访问通过 30 分钟同用户/IP 去重后，
                  以原子 SQL（view_count = view_count + 1）自增；
                  计数写入与详情查询分离，写入失败不阻断详情访问。
    """
    post = Post.query.get(post_id)
    if not post:
        return jsonify({"error": "帖子不存在"}), 404

    # 浏览量自增（独立事务，原子表达式避免并发丢增量）。
    # 去重占位已下沉到 view_log 表，多 worker 下同样只有一次计数生效。
    if _reserve_view(post.id):
        try:
            Post.query.filter(Post.id == post.id).update(
                {Post.view_count: Post.view_count + 1},
                synchronize_session=False,
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            # 写入失败：释放占位，使该访客下次访问可重新计数；详情正常返回
            _release_view(post.id)
            logger.warning("帖子 %s 浏览量自增失败", post.id, exc_info=True)

    # 概率性清理过期的 view_log 记录（避免每请求全表删除）
    _cleanup_view_log()

    # 自增提交后会话已过期，此处访问即触发详情数据读取（读写分离）
    db.session.expire(post)

    # 按 created_at 升序返回回复（父回复必先于子回复出现）。
    # selectinload(Reply.author)：一次性把全部回复作者取回，避免逐条懒加载
    # 造成 N+1（500 回复帖原约 1000+ 次查询 → 现为固定 2~3 次）。
    replies = (
        Reply.query.filter_by(post_id=post.id)
        .options(selectinload(Reply.author))
        .order_by(Reply.created_at.asc(), Reply.id.asc())
        .all()
    )

    tree, flat = _build_reply_tree(
        replies, post.user_id, config.MAX_REPLY_DEPTH
    )

    return (
        jsonify(
            {
                "id": post.id,
                "user_id": post.user_id,
                "username": post.author.username if post.author else None,
                "nickname": post.author.nickname if post.author else None,
                "display_name": post.author.display_name if post.author else None,
                "avatar_url": post.author.avatar_url if post.author else None,
                "role": post.author.role if post.author else None,
                "title": post.title,
                "content": post.content,
                "created_at": _parse_dt(post.created_at),
                "updated_at": _parse_dt(post.updated_at),
                "view_count": int(post.view_count or 0),
                "reply_count": len(replies),
                "reply_tree": tree,
                "replies": flat,
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
                "nickname": current_user.nickname,
                "display_name": current_user.display_name,
                "avatar_url": current_user.avatar_url,
                "role": current_user.role,
                "title": post.title,
                "content": post.content,
                "created_at": _parse_dt(post.created_at),
                "updated_at": None,
            }
        ),
        201,
    )


@posts_bp.put("/<int:post_id>")
@require_auth
def update_post(post_id, current_user):
    """
    PUT /api/posts/{id}

    编辑帖子（标题与正文）。
    仅帖子作者或管理员 (role='admin') 可编辑。

    Header: Authorization: Bearer <token>
    Body: {"title": "...", "content": "..."}
    响应: 更新后的完整帖子信息（含 updated_at）
    """
    post = Post.query.get(post_id)
    if not post:
        return jsonify({"error": "帖子不存在"}), 404

    is_owner = post.user_id == current_user.id
    is_admin = current_user.role == "admin"
    if not (is_owner or is_admin):
        return jsonify({"error": "无权编辑该帖子"}), 403

    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    content = data.get("content") or ""

    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    if len(title) > 200:
        return jsonify({"error": "标题长度不能超过 200 个字符"}), 400
    if not content.strip():
        return jsonify({"error": "内容不能为空"}), 400

    try:
        post.title = title
        post.content = content
        post.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "编辑帖子失败"}), 500

    author = post.author
    return (
        jsonify(
            {
                "message": "编辑成功",
                "id": post.id,
                "user_id": post.user_id,
                "username": author.username if author else None,
                "nickname": author.nickname if author else None,
                "display_name": author.display_name if author else None,
                "avatar_url": author.avatar_url if author else None,
                "role": author.role if author else None,
                "title": post.title,
                "content": post.content,
                "created_at": _parse_dt(post.created_at),
                "updated_at": _parse_dt(post.updated_at),
            }
        ),
        200,
    )


@posts_bp.post("/<int:post_id>/replies")
@require_auth
def create_reply(post_id, current_user):
    """
    POST /api/posts/{id}/replies

    发布回复。
    Header: Authorization: Bearer <token>
    Body: {"content": "...", "parent_id": 123}
        - content:  必填，回复内容
        - parent_id: 可选；省略或 null 表示一级回复；
                     非空时父回复必须存在且属于同一帖子
    """
    post = Post.query.get(post_id)
    if not post:
        return jsonify({"error": "帖子不存在"}), 404

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "回复内容不能为空"}), 400
    if len(content) > config.MAX_REPLY_LENGTH:
        return jsonify({
            "error": f"回复长度不能超过 {config.MAX_REPLY_LENGTH} 个字符"
        }), 400

    # 解析并校验父回复
    parent = None
    parent_id_raw = data.get("parent_id")
    if parent_id_raw not in (None, "", 0):
        try:
            parent_id = int(parent_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "parent_id 无效"}), 400
        parent = Reply.query.get(parent_id)
        if not parent:
            return jsonify({"error": "被回复的回复不存在"}), 404
        if parent.post_id != post.id:
            return jsonify({"error": "被回复的回复不属于该帖子"}), 403

    # root_id：一级回复为 None；子回复继承父回复的话题根
    reply = Reply(
        post_id=post.id,
        user_id=current_user.id,
        content=content,
        parent_id=parent.id if parent is not None else None,
        root_id=(parent.root_id or parent.id) if parent is not None else None,
    )
    try:
        db.session.add(reply)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "发布回复失败"}), 500

    # 提交后重取，确保 created_at 等默认值已落库
    db.session.expire(reply)
    return jsonify(
        _reply_node(reply, config.MAX_REPLY_DEPTH, post.user_id)
    ), 201


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
        # 只删除帖子本身；全部关联回复由数据库外键
        # replies.post_id ON DELETE CASCADE 自动级联删除
        db.session.delete(post)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "删除失败"}), 500

    return jsonify({"message": "删除成功"}), 200
