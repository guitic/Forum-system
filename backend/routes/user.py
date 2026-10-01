# -*- coding: utf-8 -*-
"""
用户中心路由：个人资料查看、昵称/简介更新、密码修改、头像上传。

所有接口均需 Bearer Token 鉴权。
"""

import io
import logging
import os
import uuid

from flask import Blueprint, jsonify, request
from PIL import Image, UnidentifiedImageError

from config import config
from models import User, db
from utils.security import (
    hash_password,
    require_auth,
    validate_password_strength,
    verify_password,
)

user_bp = Blueprint("user", __name__, url_prefix="/api/user")
logger = logging.getLogger("forum-api")


# ---------- 辅助函数 ----------
# 密码哈希/校验/强度规则已统一至 utils/security.py：
# 原先 auth.py 与 user.py 各存一份完全相同的实现，规则改动需同步多处，
# 抽取后单点维护（模块 5-重构）。


# ---------- 上传路径辅助 ----------

# 上传资源的 URL 前缀，与 app.py 的 /uploads/<path:filename> 路由
# 以及下方 avatar_url 的构造方式保持一致
_UPLOADS_URL_PREFIX = "/uploads/"


def _uploads_url_to_fs_path(url):
    """把 /uploads/xxx 形式的 URL 还原为磁盘绝对路径；非法输入返回 None。

    注意：config.UPLOAD_DIR 本身就指向 "uploads" 目录，因此必须剥掉 URL 里的
    "/uploads/" 前缀，而不能只做 lstrip("/")。否则会拼成
    UPLOAD_DIR/uploads/...（即 backend/uploads/uploads/...），文件永远找不到，
    旧头像无法删除、磁盘持续泄漏。

    同时做目录遍历防护：解析后的真实路径必须仍位于上传目录内。
    """
    if not url or not url.startswith(_UPLOADS_URL_PREFIX):
        return None
    rel = url[len(_UPLOADS_URL_PREFIX):]
    if not rel:
        return None
    base = os.path.realpath(config.UPLOAD_DIR)
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(base + os.sep):
        return None
    return full


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
        # 空串（含纯空白输入 strip 后）视为清除昵称
        current_user.nickname = nickname or None

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
    if not verify_password(old_password, current_user.password_hash):
        return jsonify({"error": "旧密码不正确"}), 400

    # 校验新密码强度
    strength_error = validate_password_strength(new_password)
    if strength_error:
        return jsonify({"error": strength_error}), 400

    # 不允许新旧密码相同
    if new_password == old_password:
        return jsonify({"error": "新密码不能与旧密码相同"}), 400

    # 更新密码，并递增密码版本号：
    # 使此前签发的全部 token 立即失效（require_auth 会校验 pwd_ver），
    # 从而实现「改密码即踢下线」，被盗号后可自助止损。
    current_user.password_hash = hash_password(new_password)
    current_user.pwd_ver = int(current_user.pwd_ver or 0) + 1

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

    # 用 Pillow 校验文件「真实内容」是图片。
    # 仅校验扩展名与 MIME 是不够的：二者都由客户端提供、可随意伪造，
    # 攻击者可上传改名后的任意文件（如 HTML/SVG/脚本）到 uploads 目录。
    # 此处解析文件头并完整 load()，确保确实是一张可解码的图片。
    try:
        probe = Image.open(io.BytesIO(file_data))
        probe.load()
        real_format = (probe.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify({"error": "文件不是有效的图片，请重新选择"}), 400

    # 真实格式必须与声明的扩展名一致，防止「改扩展名绕过」
    allowed_formats = {
        ".jpg": {"JPEG", "MPO"},
        ".jpeg": {"JPEG", "MPO"},
        ".png": {"PNG"},
        ".webp": {"WEBP"},
        ".gif": {"GIF"},
    }
    if real_format not in allowed_formats.get(ext, set()):
        return jsonify({
            "error": "图片内容与文件格式不符，请上传真实的 JPG/PNG/WebP/GIF 图片"
        }), 400

    # 创建头像目录（磁盘只读/权限不足时给出友好错误，不再裸抛 500）
    avatar_dir = os.path.join(config.UPLOAD_DIR, config.AVATAR_DIRNAME)
    try:
        os.makedirs(avatar_dir, exist_ok=True)
    except OSError as exc:
        logger.error("创建头像目录失败 %s: %s", avatar_dir, exc)
        return jsonify({"error": "上传服务暂不可用，请稍后重试"}), 500

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
        old_path = _uploads_url_to_fs_path(current_user.avatar_url)
        if old_path and os.path.isfile(old_path):
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
