# -*- coding: utf-8 -*-
"""结构化日志配置（L6 可观测性）。

背景：原先各模块以 `logging.getLogger("forum-api")` 取 logger，但从未调用
`basicConfig`，日志格式完全取决于运行环境（本地/ gunicorn / systemd 各不相同），
生产排障时缺少统一字段（请求 id、路径、方法、耗时、客户端 IP），难以按维度检索。

本模块提供一次性、幂等的日志初始化：
  - text 格式：单行人类可读，适合本地开发与 journalctl 直接查看；
  - json 格式：每行一个 JSON 对象，便于 ELK / Loki 等按字段索引；
  - 可选的 LOG_FILE：同时落盘（自动轮转，避免磁盘写满）。

设计要点：
  - `setup_logging()` 幂等：gunicorn 多 worker 各自 import 时不会重复添加 handler
    （通过自定义标记属性判断）；
  - 只配置 `forum-api` 这一命名空间并向上传播，不劫持 root logger，
    避免影响 Flask/SQLAlchemy 等第三方库的日志行为；
  - 通过 logging.Filter 为每条记录补齐 request_id / method / path / ip 等
    request 上下文（无请求上下文时填 "-"，不抛错）。
"""

import json
import logging
import logging.handlers
import os
import uuid

from flask import g, has_request_context, request

_LOGGER_NAME = "forum-api"
_CONFIGURED_FLAG = "_forum_logging_configured"

# 额外字段：由 RequestContextFilter 注入，供 JSON 格式化器输出
_EXTRA_FIELDS = ("request_id", "method", "path", "client_ip")


class RequestContextFilter(logging.Filter):
    """为日志记录补齐 HTTP 请求上下文。

    无请求上下文（如启动阶段、后台清理线程）时统一填 "-"，
    保证 JSON 结构稳定、下游解析不必做空值判断。
    """

    def filter(self, record):
        request_id = "-"
        method = "-"
        path = "-"
        client_ip = "-"

        if has_request_context():
            # request_id 在 before_request 里生成并存入 flask.g；若缺失则现场补
            request_id = getattr(g, "request_id", None) or "-"
            method = request.method or "-"
            path = request.path or "-"
            # 反代场景优先 X-Forwarded-For 首跳，与浏览量去重的访客口径一致
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded.strip():
                client_ip = forwarded.split(",")[0].strip()
            else:
                client_ip = request.remote_addr or "-"

        record.request_id = request_id
        record.method = method
        record.path = path
        record.client_ip = client_ip
        return True


class JsonFormatter(logging.Formatter):
    """把日志记录序列化为单行 JSON。"""

    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            payload[field] = getattr(record, field, "-")

        # 业务代码可通过 logger.info("...", extra={"event": "..."}) 附加自定义字段，
        # 这里把非标准属性一并带上（跳过 logging 内部字段，避免污染）。
        for key, value in record.__dict__.items():
            if key in payload or key in _RESERVED_LOG_ATTRS:
                continue
            if key.startswith("_"):
                continue
            try:
                json.dumps(value)  # 只收录可 JSON 序列化的值
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # ensure_ascii=False：中文日志可直接阅读，同时保留合法 JSON
        return json.dumps(payload, ensure_ascii=False)


# logging.LogRecord 的内置属性集合，用于 JSON 输出时剔除噪声字段
_RESERVED_LOG_ATTRS = frozenset(
    (
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
        "request_id", "method", "path", "client_ip",
    )
)


def _build_formatter(log_format):
    """按配置构造格式化器。"""
    if log_format == "json":
        return JsonFormatter()
    # text：单行，含请求上下文，便于 grep
    return logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(request_id)s] "
        "%(client_ip)s %(method)s %(path)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def setup_logging(app=None):
    """初始化 forum-api logger。幂等，可安全重复调用。

    :param app: 可选的 Flask app，仅用于读取其 config 覆盖默认值。
    :return: 配置好的 logger 实例
    """
    logger = logging.getLogger(_LOGGER_NAME)

    # 幂等：已配置过则直接返回，避免 gunicorn 多 worker / 重复 import 时叠加 handler
    if getattr(logger, _CONFIGURED_FLAG, False):
        return logger

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "text").lower()
    log_file = os.getenv("LOG_FILE", "")

    if app is not None:
        level_name = str(app.config.get("LOG_LEVEL", level_name)).upper()
        log_format = str(app.config.get("LOG_FORMAT", log_format)).lower()
        log_file = app.config.get("LOG_FILE", log_file) or ""

    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    # 保持向上传播交给 root 可能造成重复输出，故独立持有 handler 且不再传播
    logger.propagate = False
    logger.handlers = []

    formatter = _build_formatter(log_format)
    ctx_filter = RequestContextFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(ctx_filter)
    logger.addHandler(stream_handler)

    if log_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
            # 轮转：单文件 10MB，保留 5 份，防止日志写满磁盘
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(ctx_filter)
            logger.addHandler(file_handler)
        except OSError as exc:
            # 日志文件不可写不应阻断启动，降级为仅 stdout
            logger.warning("日志文件初始化失败，降级为仅标准输出: %s", exc)

    setattr(logger, _CONFIGURED_FLAG, True)
    return logger


def new_request_id():
    """生成短请求 id（12 位十六进制），用于串联同一请求的多条日志。"""
    return uuid.uuid4().hex[:12]
