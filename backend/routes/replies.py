# -*- coding: utf-8 -*-
"""
回复相关路由（V3 新增）。

包含删除回复接口，级联删除该回复及其所有子孙回复。
"""

from flask import Blueprint, jsonify

from models import Reply, db
from routes.posts import require_auth

replies_bp = Blueprint("replies", __name__, url_prefix="/api/replies")


def _collect_descendant_ids(root_id):
    """BFS 收集 root_id 及所有子孙回复的 id 集合。

    使用迭代式广度优先遍历，避免深度嵌套导致递归栈溢出。
    """
    to_delete = {root_id}
    frontier = [root_id]
    while frontier:
        kids = Reply.query.filter(Reply.parent_id.in_(frontier)).all()
        frontier = []
        for kid in kids:
            if kid.id in to_delete:
                continue
            to_delete.add(kid.id)
            frontier.append(kid.id)
    return to_delete


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

    to_delete = _collect_descendant_ids(reply.id)
    deleted_count = len(to_delete)

    try:
        Reply.query.filter(
            Reply.id.in_(list(to_delete))
        ).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "删除失败"}), 500

    return (
        jsonify({"message": "删除成功", "deleted_count": deleted_count}),
        200,
    )
