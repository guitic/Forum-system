# -*- coding: utf-8 -*-
"""
认证相关路由：注册和登录。
"""

import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from flask import Blueprint, jsonify, request

from config import config
from models import User, db

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def _hash_password(plaintext: str) -> str:
    """使用 bcrypt 对明文密码进行哈希。"""
    # bcrypt 最多处理 72 字节的输入，超出部分会被静默截断；这里做一次显式限制。
    raw = plaintext.encode("utf-8")[:72]
    hashed = bcrypt.hashpw(raw, bcrypt.gensalt())
    return hashed.decode("utf-8")


def _verify_password(plaintext: str, hashed: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    raw = plaintext.encode("utf-8")[:72]
    try:
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


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

    # 密码校验：至少 8 位，必须包含字母、数字和特殊字符
    if len(password) < config.MIN_PASSWORD_LENGTH:
        return jsonify(
            {"error": f"密码长度至少 {config.MIN_PASSWORD_LENGTH} 个字符"}
        ), 400
    if len(password) > 72:
        return jsonify({"error": "密码长度不能超过 72 个字符"}), 400
    if not re.search(r"[a-zA-Z]", password):
        return jsonify({"error": "密码必须包含至少一个字母"}), 400
    if not re.search(r"\d", password):
        return jsonify({"error": "密码必须包含至少一个数字"}), 400
    if not re.search(r"[^\w]", password):
        return jsonify({"error": "密码必须包含至少一个特殊字符（如 . @ # $ 等）"}), 400

    # 冲突检测（大小写不敏感）
    existing = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if existing:
        return jsonify({"error": "用户名已存在"}), 409

    user = User(
        username=username,
        password_hash=_hash_password(password),
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

    user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    # 用户不存在或密码错误，统一返回同一错误信息，防止用户名枚举
    if not user or not _verify_password(password, user.password_hash):
        return jsonify({"error": "用户名或密码错误"}), 401

    # 签发 JWT
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
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
