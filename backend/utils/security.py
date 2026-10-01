# -*- coding: utf-8 -*-
"""公共安全工具：密码哈希/校验、密码强度、JWT 鉴权装饰器。

抽取自原 routes/posts.py（require_auth）与 routes/auth.py、routes/user.py
（各自重复一份的 _hash_password / _verify_password）。

集中维护的意义：
- 密码哈希与强度规则单点定义，避免多份实现漂移
- require_auth 不再依赖 routes.posts，解除 replies/user/upload → posts 的反向耦合
- JWT 载荷新增 pwd_ver 校验，实现「改密码即失效旧 token」
"""

import functools
import re

import bcrypt
import jwt
from flask import jsonify, request

from config import config
from models import User

# bcrypt 最多处理 72 字节输入，超出部分被静默截断，这里显式限制
_BCRYPT_MAX_BYTES = 72


def hash_password(plaintext: str) -> str:
    """使用 bcrypt 对明文密码进行哈希。"""
    raw = plaintext.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plaintext: str, hashed: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    raw = plaintext.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str):
    """校验密码强度，返回错误信息；通过则返回 None。

    规则（与注册保持一致）：
    - 长度 >= MIN_PASSWORD_LENGTH 且 <= 72
    - 至少一个字母、一个数字、一个特殊字符
    """
    if len(password) < config.MIN_PASSWORD_LENGTH:
        return f"密码长度至少 {config.MIN_PASSWORD_LENGTH} 个字符"
    if len(password) > _BCRYPT_MAX_BYTES:
        return "密码长度不能超过 72 个字符"
    if not re.search(r"[a-zA-Z]", password):
        return "密码必须包含至少一个字母"
    if not re.search(r"\d", password):
        return "密码必须包含至少一个数字"
    if not re.search(r"[^\w]", password):
        return "密码必须包含至少一个特殊字符（如 . @ # $ 等）"
    return None


# ---------- JWT 鉴权 ----------

def decode_token(token: str):
    """解码并校验 JWT，返回 payload；失败抛出 jwt 异常。"""
    return jwt.decode(
        token,
        config.JWT_SECRET_KEY,
        algorithms=[config.JWT_ALGORITHM],
    )


def require_auth(fn):
    """鉴权装饰器：从 Authorization: Bearer <token> 解析 JWT，
    并将当前用户注入被装饰函数的 `current_user` 参数。

    安全增强：除签名与过期时间外，额外校验载荷中的 pwd_ver
    与数据库中当前值是否一致，不一致说明密码已被修改，
    该 token 立即视为失效（401）。
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
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token 已过期，请重新登录"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token 无效"}), 401

        try:
            user_id = int(payload.get("sub"))
        except (TypeError, ValueError):
            return jsonify({"error": "Token 无效"}), 401

        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "Token 对应的用户不存在"}), 401

        # 密码版本校验：改密码后旧 token 立即失效
        token_pwd_ver = payload.get("pwd_ver")
        if token_pwd_ver is None or int(token_pwd_ver) != int(user.pwd_ver or 0):
            return jsonify({"error": "登录状态已失效，请重新登录"}), 401

        kwargs["current_user"] = user
        return fn(*args, **kwargs)

    return wrapper
