# -*- coding: utf-8 -*-
"""
论坛系统后端配置文件。

所有配置项都支持通过环境变量覆盖，环境变量优先级高于默认值。
"""

import os


class Config:
    """应用配置类。"""

    # ---------- 数据库配置 ----------
    # 默认使用 MariaDB，可通过环境变量覆盖。
    # 支持 SQLite 以便本地快速调试：
    #   DATABASE_URL=sqlite:///./forum.db
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "3306")
    DB_USER = os.getenv("DB_USER", "forum_user")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "forum_pass")
    DB_NAME = os.getenv("DB_NAME", "forum_db")

    # 如果设置了完整的 DATABASE_URL，则直接使用该值。
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        f"mysql+mysqlconnector://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4",
    )

    # SQL 执行超时时间（秒）
    DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

    # ---------- JWT 配置 ----------
    # JWT 密钥：务必在生产环境中通过环境变量注入强随机密钥。
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-please-change-in-production")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    # Token 过期时间（秒），默认 24 小时。
    JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))

    # ---------- Flask 配置 ----------
    # 关闭 JSON key 排序，避免响应字段被字典序打乱，提升可读性。
    JSON_SORT_KEYS = False
    # 时区，用于 UTC 时间转换
    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "UTC")

    # ---------- 业务配置 ----------
    # 用户名长度范围：8-14 位
    MIN_USERNAME_LENGTH = int(os.getenv("MIN_USERNAME_LENGTH", "8"))
    MAX_USERNAME_LENGTH = int(os.getenv("MAX_USERNAME_LENGTH", "14"))
    # 密码最少长度
    MIN_PASSWORD_LENGTH = int(os.getenv("MIN_PASSWORD_LENGTH", "8"))
    # 列表分页默认值
    DEFAULT_PAGE_SIZE = int(os.getenv("DEFAULT_PAGE_SIZE", "20"))
    MAX_PAGE_SIZE = int(os.getenv("MAX_PAGE_SIZE", "100"))

    # ---------- 文件上传配置（V2 新增） ----------
    # 头像上传目录（相对于项目根目录）
    UPLOAD_DIR = os.getenv(
        "UPLOAD_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads"),
    )
    # 头像子目录
    AVATAR_DIRNAME = "avatars"
    # 头像最大文件大小（2MB）
    MAX_AVATAR_SIZE = int(os.getenv("MAX_AVATAR_SIZE", str(2 * 1024 * 1024)))
    # 允许的图片 MIME 类型
    ALLOWED_AVATAR_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    # 允许的扩展名
    ALLOWED_AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    # ---------- 帖子/回复图片上传配置 ----------
    # 图片子目录
    IMAGE_DIRNAME = "images"
    # 分片暂存子目录（断点续传用）
    IMAGE_CHUNK_DIRNAME = "tmp_chunks"
    # 单张图片最大大小（10MB）
    MAX_IMAGE_SIZE = int(os.getenv("MAX_IMAGE_SIZE", str(10 * 1024 * 1024)))
    # 单帖/单回复最大图片数
    MAX_IMAGES_PER_CONTENT = int(os.getenv("MAX_IMAGES_PER_CONTENT", "9"))
    # 分片大小（512KB）
    IMAGE_CHUNK_SIZE = int(os.getenv("IMAGE_CHUNK_SIZE", str(512 * 1024)))
    # 允许的图片 MIME 类型（与头像一致）
    ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    # 允许的扩展名
    ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    # 压缩后最长边（像素）
    IMAGE_MAX_DIMENSION = int(os.getenv("IMAGE_MAX_DIMENSION", "1920"))
    # JPEG 压缩质量
    IMAGE_JPEG_QUALITY = int(os.getenv("IMAGE_JPEG_QUALITY", "82"))
    # 未完成的分片上传保留时长（秒），超期在下次 init 时清理
    IMAGE_CHUNK_TTL = int(os.getenv("IMAGE_CHUNK_TTL", str(24 * 3600)))

    # 昵称最大长度
    MAX_NICKNAME_LENGTH = 50
    # 简介最大长度
    MAX_BIO_LENGTH = 200

    # ---------- 回复层级配置（V3 新增） ----------
    # 回复最大嵌套深度：0 = 一级回复，1 = 二级，2 = 三级（展示期最多 3 层）。
    # 超出该深度的回复在展示阶段被上提到 depth=1 的祖先下作为同级，
    # 数据库保留真实父子关系不修改。
    MAX_REPLY_DEPTH = int(os.getenv("MAX_REPLY_DEPTH", "2"))
    # 回复内容最大长度
    MAX_REPLY_LENGTH = int(os.getenv("MAX_REPLY_LENGTH", "5000"))


# 默认配置实例
config = Config()
