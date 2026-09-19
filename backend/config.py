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


# 默认配置实例
config = Config()
