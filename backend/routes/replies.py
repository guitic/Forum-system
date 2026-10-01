# -*- coding: utf-8 -*-
"""
回复相关路由（V3 新增）。

包含删除回复接口，级联删除该回复及其所有子孙回复。
编辑回复接口（模块 1 新增）：PUT /api/replies/{reply_id}，写入 updated_at。
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from config import config
from models import Post, Reply, db
from routes.posts import _reply_node, require_auth

replies_bp = Blueprint("replies", __name__, url_prefix="/api/replies")


def _collect_descendant_ids(root_id, post_id):
    """统计 root_id 自身及所有子孙回复的 id 集合（仅用于删除计数）。

    级联删除本身统一由数据库外键 ON DELETE CASCADE 执行
    （replies.parent_id → replies.id），应用层不再批量删除子孙，
    避免与数据库级联重复操作。

    单次查询该帖全部回复的 (id, parent_id)，在内存中构建父子映射后
    迭代遍历，避免逐层查询的 N+1 问题（查询次数与嵌套深度无关）。
    含环保护，避免脏数据导致死循环。
    """
    rows = (
        db.session.query(Reply.id, Reply.parent_id)
        .filter(Reply.post_id == post_id)
        .all()
    )
    children_map = {}
    for rid, pid in rows:
        if pid is not None:
            children_map.setdefault(pid, []).append(rid)

    to_delete = {root_id}
    frontier = [root_id]
    while frontier:
        next_frontier = []
        for rid in frontier:
            for kid in children_map.get(rid, []):
                if kid not in to_delete:
                    to_delete.add(kid)
                    next_frontier.append(kid)
        frontier = next_frontier
    return to_delete


@replies_bp.put("/<int:reply_id>")
@require_auth
def update_reply(reply_id, current_user):
    """
    PUT /api/replies/{reply_id}

    编辑回复内容。
    仅回复作者本人或管理员 (role='admin') 可编辑。

    Header: Authorization: Bearer <token>
    Body: {"content": "..."}
    响应: 更新后的完整回复节点（含层级字段与 updated_at）
    """
    reply = Reply.query.get(reply_id)
    if not reply:
        return jsonify({"error": "回复不存在"}), 404

    is_owner = reply.user_id == current_user.id
    is_admin = current_user.role == "admin"
    if not (is_owner or is_admin):
        return jsonify({"error": "无权编辑该回复"}), 403

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "回复内容不能为空"}), 400
    if len(content) > config.MAX_REPLY_LENGTH:
        return jsonify({
            "error": f"回复长度不能超过 {config.MAX_REPLY_LENGTH} 个字符"
        }), 400

    try:
        reply.content = content
        reply.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "编辑回复失败"}), 500

    post = Post.query.get(reply.post_id)
    post_author_id = post.user_id if post else None
    return jsonify(
        _reply_node(reply, config.MAX_REPLY_DEPTH, post_author_id)
    ), 200


@replies_bp.delete("/<int:reply_id>")
@require_auth
def delete_reply(reply_id, current_user):
    """
    DELETE /api/replies/{reply_id}

    删除回复及其所有子孙回复。
    仅回复作者本人或管理员 (role='admin') 可以删除。

    Header: Authorization: Bearer <token>
    响应: {"message": "删除成功", "deleted_count": N}
    """
    reply = Reply.query.get(reply_id)
    if not reply:
        return jsonify({"error": "回复不存在"}), 404

    is_owner = reply.user_id == current_user.id
    is_admin = current_user.role == "admin"
    if not (is_owner or is_admin):
        return jsonify({"error": "无权删除该回复"}), 403

    # 仅统计将被级联删除的总数用于响应；实际删除只针对根回复，
    # 其全部子孙由数据库 replies.parent_id ON DELETE CASCADE 自动删除
    deleted_count = len(_collect_descendant_ids(reply.id, reply.post_id))

    try:
        db.session.delete(reply)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "删除失败"}), 500

    return (
        jsonify({"message": "删除成功", "deleted_count": deleted_count}),
        200,
    )
