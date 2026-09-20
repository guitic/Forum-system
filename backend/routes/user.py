# -*- coding: utf-8 -*-
"""
用户中心路由：个人资料查看、昵称/简介更新、密码修改、头像上传。

所有接口均需 Bearer Token 鉴权。
"""

import os
import re
import uuid

import bcrypt
from flask import Blueprint, jsonify, request, send_from_directory

from config import config
from models import User, db
from routes.posts import require_auth

user_bp = Blueprint("user", __name__, url_prefix="/api/user")


# ---------- 辅助函数 ----------

def _validate_password_strength(password: str):
    """校验密码强度，返回错误信息或 None。"""
    if len(password) < config.MIN_PASSWORD_LENGTH:
        return f"密码长度至少 {config.MIN_PASSWORD_LENGTH} 个字符"
    if len(password) > 72:
        return "密码长度不能超过 72 个字符"
    if not re.search(r"[a-zA-Z]", password):
        return "密码必须包含至少一个字母"
    if not re.search(r"\d", password):
        return "密码必须包含至少一个数字"
    if not re.search(r"[^\w]", password):
        return "密码必须包含至少一个特殊字符（如 . @ # $ 等）"
    return None


def _hash_password(plaintext: str) -> str:
    """使用 bcrypt 对明文密码进行哈希。"""
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


# ---------- 路由 ----------

@user_bp.get("/profile")
@require_auth
def get_profile(current_user):
    """
    GET /api/user/profile

    获取当前用户资料（含昵称、简介、头像）。
    """
    return jsonify(current_user.to_dict()), 200


@user_bp.put("/profile")
@require_auth
def update_profile(current_user):
    """
    PUT /api/user/profile

    更新昵称和个人简介。
    Body: {"nickname": "...", "bio": "..."}（两个字段均为可选，传哪个改哪个）
    """
    data = request.get_json(silent=True) or {}

    nickname = data.get("nickname")
    bio = data.get("bio")

    # 昵称校验
    if nickname is not None:
        nickname = str(nickname).strip()
        if len(nickname) > config.MAX_NICKNAME_LENGTH:
            return jsonify({
                "error": f"昵称长度不能超过 {config.MAX_NICKNAME_LENGTH} 个字符"
            }), 400
        # 不允许纯空白
        if nickname and not nickname.strip():
            return jsonify({"error": "昵称不能为纯空白字符"}), 400
        current_user.nickname = nickname if nickname else None

    # 简介校验
    if bio is not None:
        bio = str(bio).strip()
        if len(bio) > config.MAX_BIO_LENGTH:
            return jsonify({
                "error": f"简介长度不能超过 {config.MAX_BIO_LENGTH} 个字符"
            }), 400
        current_user.bio = bio if bio else None

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "资料更新失败"}), 500

    return jsonify({
        "message": "资料更新成功",
        "user": {
            "nickname": current_user.nickname,
            "bio": current_user.bio,
        },
    }), 200


@user_bp.post("/password")
@require_auth
def change_password(current_user):
    """
    POST /api/user/password

    修改密码。需验证旧密码。
    Body: {"old_password": "...", "new_password": "..."}
    """
    data = request.get_json(silent=True) or {}
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")

    if not old_password or not new_password:
        return jsonify({"error": "旧密码和新密码不能为空"}), 400

    # 验证旧密码
    if not _verify_password(old_password, current_user.password_hash):
        return jsonify({"error": "旧密码不正确"}), 400

    # 校验新密码强度
    strength_error = _validate_password_strength(new_password)
    if strength_error:
        return jsonify({"error": strength_error}), 400

    # 不允许新旧密码相同
    if new_password == old_password:
        return jsonify({"error": "新密码不能与旧密码相同"}), 400

    # 更新密码
    current_user.password_hash = _hash_password(new_password)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "密码修改失败"}), 500

    return jsonify({"message": "密码修改成功"}), 200


@user_bp.post("/avatar")
@require_auth
def upload_avatar(current_user):
    """
    POST /api/user/avatar

    上传头像图片。
    请求体: multipart/form-data，字段名 file
    """
    # 检查是否有文件
    if "file" not in request.files:
        return jsonify({"error": "请提供头像图片文件"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "请提供头像图片文件"}), 400

    # 校验扩展名
    filename = file.filename.lower()
    ext = os.path.splitext(filename)[1]
    if ext not in config.ALLOWED_AVATAR_EXTENSIONS:
        return jsonify({
            "error": f"不支持的图片格式，请上传 JPG/PNG/WebP/GIF 格式的图片"
        }), 400

    # 校验 MIME 类型
    content_type = file.content_type
    if content_type not in config.ALLOWED_AVATAR_MIMES:
        return jsonify({
            "error": f"不支持的图片类型 ({content_type})，请上传 JPG/PNG/WebP/GIF 格式的图片"
        }), 400

    # 读取文件内容并校验大小
    file_data = file.read()
    if len(file_data) > config.MAX_AVATAR_SIZE:
        max_mb = config.MAX_AVATAR_SIZE / (1024 * 1024)
        return jsonify({
            "error": f"图片大小不能超过 {max_mb:.0f}MB"
        }), 400

    # 创建头像目录
    avatar_dir = os.path.join(config.UPLOAD_DIR, config.AVATAR_DIRNAME)
    os.makedirs(avatar_dir, exist_ok=True)

    # 生成随机文件名
    new_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(avatar_dir, new_filename)

    try:
        with open(file_path, "wb") as f:
            f.write(file_data)
    except Exception:
        return jsonify({"error": "头像保存失败"}), 500

    # 构建存储路径
    avatar_url = f"/uploads/{config.AVATAR_DIRNAME}/{new_filename}"

    # 删除旧头像文件（如果存在）
    if current_user.avatar_url:
        old_url = current_user.avatar_url
        old_path = os.path.join(config.UPLOAD_DIR, old_url.lstrip("/"))
        if os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass  # 旧文件删除失败不影响新上传

    # 更新数据库
    current_user.avatar_url = avatar_url

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        # 回滚后删除已上传的新文件
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        return jsonify({"error": "头像更新失败"}), 500

    return jsonify({
        "message": "头像上传成功",
        "avatar_url": avatar_url,
    }), 200
