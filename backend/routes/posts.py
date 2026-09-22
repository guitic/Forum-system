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
import math
from datetime import datetime, timezone

import jwt
import sqlalchemy as sa
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


# ---------- 回复层级构建（V3 新增） ----------

def _reply_chain_info(reply, index):
    """一次回溯同时计算真实链信息。

    返回 (真实深度, 话题根回复 id)：
    - 真实深度：沿 parent_id 链向上回溯到一级回复的步数（一级回复 = 0）
    - 话题根 id：回溯到的那条一级回复的 id

    含环保护与悬空引用保护，避免脏数据导致死循环。
    """
    depth = 0
    cur = reply
    seen = set()
    while cur is not None and cur.parent_id:
        if cur.id in seen:
            break
        seen.add(cur.id)
        parent = index.get(cur.parent_id)
        if parent is None:
            break
        cur = parent
        depth += 1
    return depth, cur.id


def _prepare_reply_structure(replies, max_depth):
    """预计算回复树的展示结构。

    replies 必须已按 created_at 升序（父回复必先于子回复出现，便于迭代求解）。

    返回 (index, depth_map, root_map, children_map, display_parent)：
    - index:          id → Reply ORM 对象
    - depth_map:      id → 展示深度（已按 max_depth 收敛，0/1/2…）
    - root_map:       id → 话题根回复 id（真实值，一级回复为 None）
    - children_map:   展示父 id → [展示子 id, ...]
    - display_parent: id → 展示父 id（None 表示展示为一级）
    """
    index = {r.id: r for r in replies}

    # 1) 真实链信息，用于话题根 id 与溢出判定
    chain = {r.id: _reply_chain_info(r, index) for r in replies}

    # 2) 决定每个节点的"展示父节点"
    #    - 未超过深度上限：挂到真实父节点下
    #    - 超过深度上限：上提到父节点所在的展示层级，作为同级
    display_parent = {}
    for r in replies:
        if r.parent_id is None:
            display_parent[r.id] = None
            continue
        real_parent = index.get(r.parent_id)
        if real_parent is None:
            # 悬空引用（父回复已被单独删除等）：降级为一级回复
            display_parent[r.id] = None
            continue
        if chain[r.parent_id][0] + 1 <= max_depth:
            display_parent[r.id] = r.parent_id
        else:
            display_parent[r.id] = display_parent.get(r.parent_id)

    # 3) 展示深度（迭代求解，父节点必先于子节点）
    depth_map = {}
    for r in replies:
        parent_id = display_parent[r.id]
        depth_map[r.id] = 0 if parent_id is None else depth_map[parent_id] + 1

    # 4) 话题根 id（取真实值，一级回复为 None）
    root_map = {rid: info[1] for rid, info in chain.items()}

    # 5) 展示父子映射
    children_map = {}
    for r in replies:
        parent_id = display_parent[r.id]
        if parent_id is not None:
            children_map.setdefault(parent_id, []).append(r.id)

    return index, depth_map, root_map, children_map, display_parent


def _reply_node_dict(reply, index, depth_map, root_map, children_map, post_author_id):
    """将单条 Reply 序列化为对外 JSON 节点（不含 children）。"""
    parent = index.get(reply.parent_id) if reply.parent_id else None
    author = reply.author
    return {
        "id": reply.id,
        "post_id": reply.post_id,
        "user_id": reply.user_id,
        "username": author.username if author else None,
        "nickname": author.nickname if author else None,
        "display_name": author.display_name if author else None,
        "avatar_url": author.avatar_url if author else None,
        "role": author.role if author else None,
        # 是否为楼主（帖子作者），供前端佩戴「楼主」徽标
        "is_author": reply.user_id == post_author_id,
        "content": reply.content,
        "created_at": _parse_dt(reply.created_at),
        "updated_at": _parse_dt(reply.updated_at),
        "parent_id": reply.parent_id,
        "root_id": root_map.get(reply.id),
        "depth": depth_map.get(reply.id, 0),
        # 被"真实"父回复人的显示名（即使展示上被拉平，引用对象仍准确）
        "reply_to_display_name": (
            parent.author.display_name if parent is not None and parent.author else None
        ),
        "reply_count": len(children_map.get(reply.id, [])),
    }


def _build_reply_tree(replies, post_author_id, max_depth):
    """构建展示用嵌套回复树。

    返回 (tree, flat)：
    - tree: 嵌套结构，每个节点含 children（最多 max_depth+1 层）
    - flat: 扁平列表（按时间升序），字段同 tree 节点，用于兼容与统计
    """
    index, depth_map, root_map, children_map, display_parent = _prepare_reply_structure(
        replies, max_depth
    )

    nodes = {
        r.id: _reply_node_dict(
            r, index, depth_map, root_map, children_map, post_author_id
        )
        for r in replies
    }
    flat = [nodes[r.id] for r in replies]

    def attach_children(node):
        node["children"] = [nodes[kid] for kid in children_map.get(node["id"], [])]
        for child in node["children"]:
            attach_children(child)
        return node

    tree = [
        attach_children(nodes[r.id])
        for r in replies
        if display_parent[r.id] is None
    ]
    return tree, flat


def _reply_node(reply, max_depth, post_author_id):
    """序列化单条回复（用于新建回复的响应体），含层级字段。"""
    siblings = (
        Reply.query.filter_by(post_id=reply.post_id)
        .order_by(Reply.created_at.asc(), Reply.id.asc())
        .all()
    )
    index, depth_map, root_map, children_map, _ = _prepare_reply_structure(
        siblings, max_depth
    )
    return _reply_node_dict(
        reply, index, depth_map, root_map, children_map, post_author_id
    )


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
    """
    post = Post.query.get(post_id)
    if not post:
        return jsonify({"error": "帖子不存在"}), 404

    # 按 created_at 升序返回回复（父回复必先于子回复出现）
    replies = (
        Reply.query.filter_by(post_id=post.id)
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
        # 级联删除所有关联回复
        Reply.query.filter_by(post_id=post.id).delete()
        db.session.delete(post)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "删除失败"}), 500

    return jsonify({"message": "删除成功"}), 200
