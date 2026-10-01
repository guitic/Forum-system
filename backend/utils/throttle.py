# -*- coding: utf-8 -*-
"""登录失败节流（防暴力破解）。

以 (username, client_ip) 为键做进程内 TTL 滑动窗口计数：

- 连续失败达到阈值 → 锁定一段时间，期间该键的登录请求直接 429
- 登录成功 → 清零该键（避免正常用户被历史失败拖累）
- 窗口过期自动失效，防止误伤长期用户

局限说明：进程内计数在 gunicorn 多 worker 下各 worker 独立，
只能提供"近似"节流（攻击者轮询 worker 会稀释效果）。生产环境应
在 Nginx 层对 /api/auth/login 再加一道更严格的 limit_req（见
deploy/nginx.conf），两者叠加即可覆盖慢速分布式爆破。若要精确
跨 worker 一致，可替换为 Redis 计数器（接口语义不变）。
"""

import threading
import time

from config import config

# {(username_lower, ip): {"count": int, "first_ts": float, "locked_until": float}}
_attempts = {}
_lock = threading.Lock()


def _client_ip():
    """取客户端 IP：Nginx 反代优先 X-Forwarded-For 首跳，否则 remote_addr。"""
    forwarded = request_headers_forwarded_for()
    if forwarded:
        return forwarded
    return "unknown"


def request_headers_forwarded_for():
    """读取 X-Forwarded-For 首跳；无则返回空串。

    单独抽函数便于测试替换与复用（与 posts.py 的访客识别口径一致）。
    """
    from flask import request

    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded.strip():
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _prune(now):
    """清理已过期且未锁定的键，防止内存无限增长。"""
    if len(_attempts) <= config.LOGIN_THROTTLE_MAX_ENTRIES:
        return
    window = config.LOGIN_THROTTLE_WINDOW_SECONDS
    dead = [
        k
        for k, v in _attempts.items()
        if now - v["first_ts"] > window and v["locked_until"] <= now
    ]
    for k in dead:
        _attempts.pop(k, None)


def check_locked(username, ip):
    """检查该 (username, ip) 是否处于锁定状态。

    返回剩余锁定秒数（>0 表示锁定中），未锁定返回 0。
    """
    key = ((username or "").lower(), ip)
    now = time.monotonic()
    with _lock:
        rec = _attempts.get(key)
        if not rec:
            return 0
        if rec["locked_until"] > now:
            return int(rec["locked_until"] - now) + 1
        # 锁已到期：清掉记录，允许重新尝试
        if rec["locked_until"]:
            _attempts.pop(key, None)
        return 0


def record_failure(username, ip):
    """记录一次登录失败；达到阈值则锁定，返回是否刚刚进入锁定。"""
    key = ((username or "").lower(), ip)
    now = time.monotonic()
    window = config.LOGIN_THROTTLE_WINDOW_SECONDS
    with _lock:
        rec = _attempts.get(key)
        # 窗口过期或首次：重置计数
        if rec is None or now - rec["first_ts"] > window:
            rec = {"count": 0, "first_ts": now, "locked_until": 0.0}
            _attempts[key] = rec
        rec["count"] += 1
        if rec["count"] >= config.LOGIN_THROTTLE_MAX_ATTEMPTS:
            rec["locked_until"] = now + config.LOGIN_THROTTLE_LOCK_SECONDS
            _prune(now)
            return True
        _prune(now)
        return False


def reset(username, ip):
    """登录成功后清零该键的失败计数。"""
    key = ((username or "").lower(), ip)
    with _lock:
        _attempts.pop(key, None)


def clear_all():
    """清空全部节流状态（供测试使用）。"""
    with _lock:
        _attempts.clear()
