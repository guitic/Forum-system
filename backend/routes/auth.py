# -*- coding: utf-8 -*-
"""
认证相关路由：注册和登录。
"""

import re
from datetime import datetime, timedelta, timezone

import jwt
from flask import Blueprint, jsonify, request

from config import config
from models import User, db
from utils.security import (
    hash_password,
    validate_password_strength,
    verify_password,
)
from utils import throttle

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.post("/register")
def register():
    """
    POST /api/auth/register

    请求体：
        {
            "username": "至少3个字符",
            "password": "至少6个字符"
        }
    """
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    # 用户名校验：8-14 位，首位小写英文字母，仅含字母和数字，且必须同时包含字母和数字
    if len(username) < config.MIN_USERNAME_LENGTH or len(username) > config.MAX_USERNAME_LENGTH:
        return jsonify(
            {"error": f"用户名长度须在 {config.MIN_USERNAME_LENGTH}-{config.MAX_USERNAME_LENGTH} 位之间"}
        ), 400
    # 首位必须是小写英文字母
    if not re.match(r"^[a-z]", username):
        return jsonify({"error": "用户名首位必须是小写英文字母"}), 400
    # 仅允许字母和数字
    if not re.match(r"^[a-zA-Z0-9]+$", username):
        return jsonify({"error": "用户名只能包含字母和数字"}), 400
    # 必须同时包含字母和数字
    if not re.search(r"\d", username) or not re.search(r"[a-zA-Z]", username):
        return jsonify({"error": "用户名必须同时包含字母和数字"}), 400

    # 密码强度校验（与改密码共用同一规则，单点维护）
    strength_error = validate_password_strength(password)
    if strength_error:
        return jsonify({"error": strength_error}), 400

    # 冲突检测（大小写不敏感）
    existing = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if existing:
        return jsonify({"error": "用户名已存在"}), 409

    user = User(
        username=username,
        password_hash=hash_password(password),
        role="user",
    )
    try:
        db.session.add(user)
        db.session.commit()
    except Exception:
        # 并发下可能仍出现重复
        db.session.rollback()
        return jsonify({"error": "注册失败，用户名可能已被占用"}), 409

    return (
        jsonify(
            {
                "message": "注册成功",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "role": user.role,
                },
            }
        ),
        201,
    )


@auth_bp.post("/login")
def login():
    """
    POST /api/auth/login

    请求体：
        {
            "username": "...",
            "password": "..."
        }
    响应：
        {
            "token": "eyJ..."
        }
    """
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400

    # 登录失败节流：命中锁定直接拒绝，避免在线暴力破解/撞库
    client_ip = throttle.request_headers_forwarded_for()
    locked_seconds = throttle.check_locked(username, client_ip)
    if locked_seconds > 0:
        return jsonify({
            "error": f"登录失败次数过多，请 {locked_seconds} 秒后重试"
        }), 429

    user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    # 用户不存在或密码错误，统一返回同一错误信息，防止用户名枚举
    if not user or not verify_password(password, user.password_hash):
        throttle.record_failure(username, client_ip)
        return jsonify({"error": "用户名或密码错误"}), 401

    # 登录成功：清零该 (username, ip) 的失败计数
    throttle.reset(username, client_ip)

    # 签发 JWT
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        # 密码版本号：require_auth 校验其与库中值一致，
        # 改密码后旧 token 立即失效（无需服务端黑名单）
        "pwd_ver": int(user.pwd_ver or 0),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=config.JWT_EXPIRATION_HOURS)).timestamp()),
    }
    token = jwt.encode(
        payload,
        config.JWT_SECRET_KEY,
        algorithm=config.JWT_ALGORITHM,
    )
    # PyJWT 2.x 返回 str；1.x 返回 bytes，这里兼容处理
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    return jsonify({
        "token": token,
        "username": user.username,
        "nickname": user.nickname,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "role": user.role,
    }), 200
