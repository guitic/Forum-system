# -*- coding: utf-8 -*-
"""
论坛系统后端主应用入口。

初始化 Flask 应用、数据库、蓝图，并注册统一错误处理。
"""

import os
import sys

from flask import Flask, jsonify, request, send_from_directory

# 将当前目录加入 sys.path，确保可以导入同包下的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config
from models import db

# ---------- 应用工厂 ----------

def create_app(config_obj=None):
    """创建并配置 Flask 应用。"""
    app = Flask(
        __name__,
        # 本地开发：直接由 Flask 提供前端静态文件，消除跨域问题
        static_folder=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"),
        static_url_path="",
    )
    app.config.from_object(config_obj or config)
    app.json.sort_keys = False

    # Flask-SQLAlchemy 需要 SQLALCHEMY_DATABASE_URI，映射自 DATABASE_URL
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config.get("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # 初始化数据库
    db.init_app(app)

    # 确保上传根目录存在。UPLOAD_DIR 默认位于 backend 下且被 .gitignore 忽略，
    # 全新克隆/部署时目录缺失，需在启动阶段提前创建，避免文件上传时因目录不存在而失败。
    # 创建失败（如生产环境目录只读）不应阻断启动，仅记录警告，由上传接口暴露具体错误。
    try:
        os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    except OSError as exc:
        import logging
        logging.getLogger("forum-api").warning(
            "创建上传目录失败 %s: %s", config.UPLOAD_DIR, exc
        )

    # 注册蓝图
    from routes.auth import auth_bp
    from routes.posts import posts_bp
    from routes.replies import replies_bp
    from routes.user import user_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(posts_bp)
    app.register_blueprint(replies_bp)
    app.register_blueprint(user_bp)

    # ---------- 统一错误处理 ----------

    @app.errorhandler(404)
    def not_found(e):
        # API 路由返回 JSON 错误，前端路由尝试提供静态文件
        if request.path.startswith("/api/"):
            return jsonify({"error": "请求的路径不存在"}), 404
        # 前端页面：返回 index.html（单页应用回退）
        try:
            return send_from_directory(app.static_folder, "index.html"), 200
        except Exception:
            return jsonify({"error": "请求的路径不存在"}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "请求方法不允许"}), 405

    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"error": "服务器内部错误"}), 500

    @app.errorhandler(Exception)
    def handle_exception(e):
        # 生产环境不暴露异常详情
        import logging
        logging.getLogger("forum-api").error(
            "Unhandled exception: %s", str(e), exc_info=True
        )
        return jsonify({"error": "服务器内部错误"}), 500

    # ---------- 首页路由（根路径） ----------

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_frontend(path):
        """提供前端静态文件。未匹配的路径回退到 index.html。"""
        if path and os.path.exists(os.path.join(app.static_folder, path)):
            return send_from_directory(app.static_folder, path)
        return send_from_directory(app.static_folder, "index.html")

    # ---------- CORS 支持（本地开发跨端口） ----------

    @app.after_request
    def add_cors_headers(response):
        """为所有响应添加 CORS 头，允许前端跨端口访问。
        生产环境通过 Nginx 同域部署，此中间件无副作用。"""
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        # 预检请求直接返回 204
        if request.method == "OPTIONS":
            resp = app.response_class(status=204)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            return resp
        return response

    # ---------- 健康检查 ----------

    @app.route("/health", methods=["GET"])
    def health():
        """健康检查接口，供监控脚本使用。"""
        try:
            db.session.execute(db.text("SELECT 1"))
            db_status = "ok"
        except Exception as exc:
            db_status = f"error: {str(exc)}"
        return jsonify({"status": "ok", "database": db_status}), 200

    # ---------- 头像静态资源服务（V2 新增） ----------

    @app.route("/uploads/<path:filename>", methods=["GET"])
    def serve_uploads(filename):
        """提供上传文件（头像等）的静态资源服务。"""
        upload_dir = os.path.join(config.UPLOAD_DIR)
        # 安全校验：防止目录遍历
        safe_filename = filename.replace("..", "")
        full_path = os.path.join(upload_dir, safe_filename)
        # 确保文件在上传目录内
        real_upload_dir = os.path.realpath(upload_dir)
        real_file_path = os.path.realpath(full_path)
        if not real_file_path.startswith(real_upload_dir):
            return jsonify({"error": "访问被拒绝"}), 403
        if not os.path.isfile(full_path):
            return jsonify({"error": "文件不存在"}), 404
        return send_from_directory(upload_dir, safe_filename)

    return app


# ---------- 全局实例 ----------

app = create_app()


# ---------- 直接运行入口 ----------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "0.0.0.0")
    debug = os.getenv("FLASK_DEBUG", "0") == "1"

    # 自动建表（开发环境方便调试；生产环境建议用 init_db.py）
    with app.app_context():
        db.create_all()

    print(f"[Forum API] Starting on http://{host}:{port}")
    print(f"[Forum API] Debug mode: {debug}")
    print(f"[Forum API] Database URL: {config.DATABASE_URL[:30]}...")
    app.run(host=host, port=port, debug=debug, use_reloader=False)
