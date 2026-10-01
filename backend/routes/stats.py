# -*- coding: utf-8 -*-
"""
社区统计路由（前端重构新增）。

GET /api/stats  返回帖子 / 成员 / 回复总数，供首页「社区统计」卡片展示。
只读聚合接口，不涉及鉴权与写操作。
"""

from flask import Blueprint, jsonify

import sqlalchemy as sa

from models import Post, Reply, User, db

stats_bp = Blueprint("stats", __name__, url_prefix="/api")


@stats_bp.get("/stats")
def get_stats():
    """
    GET /api/stats

    社区统计：帖子数 / 成员数 / 回复数。
    单条 SQL 聚合，量级小，无需缓存。
    """
    try:
        posts_count = db.session.query(sa.func.count()).select_from(Post).scalar() or 0
        users_count = db.session.query(sa.func.count()).select_from(User).scalar() or 0
        replies_count = db.session.query(sa.func.count()).select_from(Reply).scalar() or 0
    except Exception:
        return jsonify({"error": "统计信息获取失败"}), 500

    return jsonify(
        {
            "posts": int(posts_count),
            "users": int(users_count),
            "replies": int(replies_count),
        }
    ), 200
