# -*- coding: utf-8 -*-
"""社区统计路由（前端重构新增）。

GET /api/stats  返回帖子 / 成员 / 回复总数，供首页「社区统计」卡片展示。
只读聚合接口，不涉及鉴权与写操作。

性能说明：三个 count(*) 在大表上是全表聚合，而统计数字对实时性要求低，
故引入进程内 TTL 缓存（默认 60 秒）。缓存为单键缓存，命中率极高；
多 worker 下各 worker 独立缓存，最多引入 TTL 级别的数据滞后，可接受。
"""

import logging
import threading
import time

from flask import Blueprint, jsonify

import sqlalchemy as sa

from config import config
from models import Post, Reply, User, db

stats_bp = Blueprint("stats", __name__, url_prefix="/api")
logger = logging.getLogger("forum-api")

# 缓存：{"payload": dict, "expire_at": float(monotonic)}
_cache = {"payload": None, "expire_at": 0.0}
_cache_lock = threading.Lock()


def _compute_stats():
    """执行三个聚合查询，返回统计字典。"""
    posts_count = db.session.query(sa.func.count()).select_from(Post).scalar() or 0
    users_count = db.session.query(sa.func.count()).select_from(User).scalar() or 0
    replies_count = db.session.query(sa.func.count()).select_from(Reply).scalar() or 0
    return {
        "posts": int(posts_count),
        "users": int(users_count),
        "replies": int(replies_count),
    }


def clear_cache():
    """清空统计缓存（供测试或数据变更后主动失效使用）。"""
    with _cache_lock:
        _cache["payload"] = None
        _cache["expire_at"] = 0.0


@stats_bp.get("/stats")
def get_stats():
    """
    GET /api/stats

    社区统计：帖子数 / 成员数 / 回复数。
    结果带 TTL 缓存，减少大表重复全表聚合。
    """
    ttl = config.STATS_CACHE_TTL_SECONDS
    now = time.monotonic()

    if ttl > 0:
        with _cache_lock:
            if _cache["payload"] is not None and _cache["expire_at"] > now:
                return jsonify(_cache["payload"]), 200

    try:
        payload = _compute_stats()
    except Exception:
        # 查询失败不写缓存，直接返回错误
        logger.exception("统计信息查询失败")
        return jsonify({"error": "统计信息获取失败"}), 500

    if ttl > 0:
        with _cache_lock:
            _cache["payload"] = payload
            _cache["expire_at"] = now + ttl

    return jsonify(payload), 200
