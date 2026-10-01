# -*- coding: utf-8 -*-
"""
帖子/回复图片上传路由（分片上传 + 断点续传 + 服务端压缩）。

接口（均需 Bearer Token 鉴权）：
- POST /api/upload/init      初始化上传会话（校验格式/大小，返回 upload_id）
- POST /api/upload/chunk     上传单个分片（multipart: upload_id/index/chunk）
- GET  /api/upload/status    查询已接收分片（断点续传依据）
- POST /api/upload/complete  合并分片、Pillow 校验与压缩、生成唯一存储路径

存储布局：
- 分片暂存:  UPLOAD_DIR/tmp_chunks/{upload_id}/{index}.part + meta.json
- 最终图片:  UPLOAD_DIR/images/{yyyymm}/{uuid4}.{ext}
"""

import io
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request
from PIL import Image, UnidentifiedImageError

from config import config
from routes.posts import require_auth

upload_bp = Blueprint("upload", __name__, url_prefix="/api/upload")

# upload_id 仅允许 32 位十六进制（uuid4.hex），杜绝路径注入
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


# ---------- 路径辅助 ----------

def _chunk_root():
    """分片暂存根目录。"""
    return os.path.join(config.UPLOAD_DIR, config.IMAGE_CHUNK_DIRNAME)


def _session_dir(upload_id):
    """会话目录；upload_id 非法时返回 None。"""
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        return None
    return os.path.join(_chunk_root(), upload_id)


def _read_meta(session_dir):
    """读取会话元数据，缺失/损坏返回 None。"""
    meta_path = os.path.join(session_dir, "meta.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_meta(session_dir, meta):
    meta_path = os.path.join(session_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def _received_chunks(session_dir):
    """已接收的分片索引（升序）。"""
    chunks = []
    for name in os.listdir(session_dir):
        if name.endswith(".part"):
            try:
                chunks.append(int(name[:-5]))
            except ValueError:
                continue
    return sorted(chunks)


def _cleanup_stale_sessions():
    """清理超期未完成的上传会话，避免磁盘堆积。失败静默。"""
    root = _chunk_root()
    if not os.path.isdir(root):
        return
    now = time.time()
    for name in os.listdir(root):
        sdir = os.path.join(root, name)
        if not os.path.isdir(sdir):
            continue
        meta = _read_meta(sdir)
        created = (meta or {}).get("created_at", 0)
        try:
            age = now - float(created)
        except (TypeError, ValueError):
            age = config.IMAGE_CHUNK_TTL + 1  # 元数据损坏直接清理
        if age > config.IMAGE_CHUNK_TTL:
            shutil.rmtree(sdir, ignore_errors=True)


# ---------- 图片处理 ----------

def _compress_image(raw_bytes, ext):
    """压缩图片，返回 (最终字节, 实际扩展名, 宽, 高)。

    - GIF：保留原始字节（避免破坏动画），仅校验
    - 其他格式：最长边缩至 IMAGE_MAX_DIMENSION，JPEG/WebP 按质量压缩，
      PNG 做 optimize；过小的图不放大
    """
    img = Image.open(io.BytesIO(raw_bytes))
    img.load()
    width, height = img.size

    if ext == ".gif":
        return raw_bytes, ext, width, height

    max_dim = config.IMAGE_MAX_DIMENSION
    if max(width, height) > max_dim:
        ratio = max_dim / float(max(width, height))
        new_size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)
        width, height = img.size

    buf = io.BytesIO()
    if ext in (".jpg", ".jpeg"):
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=config.IMAGE_JPEG_QUALITY, optimize=True)
    elif ext == ".webp":
        img.save(buf, format="WEBP", quality=config.IMAGE_JPEG_QUALITY, method=4)
    else:  # .png
        if img.mode == "P":
            img = img.convert("RGBA")
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), ext, width, height


# ---------- 路由 ----------

@upload_bp.post("/init")
@require_auth
def init_upload(current_user):
    """
    POST /api/upload/init

    Body: {"filename": "a.jpg", "size": 12345, "total_chunks": 3, "mime_type": "image/jpeg"}
    响应: {"upload_id", "chunk_size", "received_chunks": []}
    """
    _cleanup_stale_sessions()

    data = request.get_json(silent=True) or {}
    filename = str(data.get("filename") or "").strip()
    mime_type = str(data.get("mime_type") or "").strip().lower()

    try:
        size = int(data.get("size"))
        total_chunks = int(data.get("total_chunks"))
    except (TypeError, ValueError):
        return jsonify({"error": "size 与 total_chunks 必须为整数"}), 400

    if not filename:
        return jsonify({"error": "请提供文件名"}), 400

    ext = os.path.splitext(filename.lower())[1]
    if ext not in config.ALLOWED_IMAGE_EXTENSIONS:
        return jsonify({"error": "不支持的图片格式，请上传 JPG/PNG/WebP/GIF 格式的图片"}), 400
    if mime_type and mime_type not in config.ALLOWED_IMAGE_MIMES:
        return jsonify({"error": f"不支持的图片类型 ({mime_type})"}), 400
    if size <= 0:
        return jsonify({"error": "文件内容为空"}), 400
    if size > config.MAX_IMAGE_SIZE:
        max_mb = config.MAX_IMAGE_SIZE / (1024 * 1024)
        return jsonify({"error": f"图片大小不能超过 {max_mb:.0f}MB"}), 400
    if total_chunks < 1 or total_chunks > 10000:
        return jsonify({"error": "分片数量无效"}), 400

    upload_id = uuid.uuid4().hex
    session_dir = _session_dir(upload_id)
    try:
        os.makedirs(session_dir, exist_ok=True)
    except OSError:
        return jsonify({"error": "上传服务暂不可用，请稍后重试"}), 500

    _write_meta(session_dir, {
        "user_id": current_user.id,
        "filename": filename,
        "ext": ext,
        "size": size,
        "total_chunks": total_chunks,
        "mime_type": mime_type,
        "created_at": time.time(),
    })

    return jsonify({
        "upload_id": upload_id,
        "chunk_size": config.IMAGE_CHUNK_SIZE,
        "received_chunks": [],
    }), 201


class _SessionError(Exception):
    """上传会话校验失败（携带对外错误信息与 HTTP 状态码）。"""

    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def _get_owned_session(upload_id, current_user):
    """返回 (session_dir, meta)；非法/非本人抛出 _SessionError。"""
    session_dir = _session_dir(upload_id)
    if session_dir is None or not os.path.isdir(session_dir):
        raise _SessionError("上传会话不存在或已过期，请重新上传", 404)
    meta = _read_meta(session_dir)
    if meta is None:
        raise _SessionError("上传会话不存在或已过期，请重新上传", 404)
    if meta.get("user_id") != current_user.id:
        raise _SessionError("无权操作该上传会话", 403)
    return session_dir, meta


@upload_bp.post("/chunk")
@require_auth
def upload_chunk(current_user):
    """
    POST /api/upload/chunk  (multipart/form-data)

    字段: upload_id / index / chunk(文件分片)
    响应: {"received": [0,1,...]}
    """
    upload_id = (request.form.get("upload_id") or "").strip()
    try:
        session_dir, meta = _get_owned_session(upload_id, current_user)
    except _SessionError as e:
        return jsonify({"error": e.message}), e.status

    try:
        index = int(request.form.get("index"))
    except (TypeError, ValueError):
        return jsonify({"error": "分片序号无效"}), 400
    if index < 0 or index >= meta["total_chunks"]:
        return jsonify({"error": "分片序号超出范围"}), 400

    if "chunk" not in request.files:
        return jsonify({"error": "缺少分片数据"}), 400
    chunk_file = request.files["chunk"]
    chunk_data = chunk_file.read()
    if not chunk_data:
        return jsonify({"error": "分片数据为空"}), 400
    # 单分片上限 = 配置分片大小 + 少量容差（最后一片更小，无需下限）
    if len(chunk_data) > config.IMAGE_CHUNK_SIZE + 1024:
        return jsonify({"error": "分片大小超出限制"}), 400

    # 原子写入：先写临时文件再改名，避免半截分片被当作有效
    part_path = os.path.join(session_dir, f"{index}.part")
    tmp_path = part_path + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(chunk_data)
        os.replace(tmp_path, part_path)
    except OSError:
        return jsonify({"error": "分片保存失败，请重试"}), 500

    return jsonify({"received": _received_chunks(session_dir)}), 200


@upload_bp.get("/status")
@require_auth
def upload_status(current_user):
    """
    GET /api/upload/status?upload_id=xxx

    响应: {"upload_id", "total_chunks", "received_chunks", "complete": bool}
    前端断点续传时据此跳过已上传分片。
    """
    upload_id = (request.args.get("upload_id") or "").strip()
    try:
        session_dir, meta = _get_owned_session(upload_id, current_user)
    except _SessionError as e:
        return jsonify({"error": e.message}), e.status

    received = _received_chunks(session_dir)
    return jsonify({
        "upload_id": upload_id,
        "total_chunks": meta["total_chunks"],
        "received_chunks": received,
        "complete": len(received) == meta["total_chunks"],
    }), 200


@upload_bp.post("/complete")
@require_auth
def complete_upload(current_user):
    """
    POST /api/upload/complete

    Body: {"upload_id": "..."}
    合并全部分片 → Pillow 校验真实图片 → 压缩 → 唯一路径落盘 → 清理会话。
    响应: {"url": "/uploads/images/202609/xxx.jpg", "width", "height", "size"}
    """
    data = request.get_json(silent=True) or {}
    upload_id = str(data.get("upload_id") or "").strip()
    try:
        session_dir, meta = _get_owned_session(upload_id, current_user)
    except _SessionError as e:
        return jsonify({"error": e.message}), e.status

    total = meta["total_chunks"]
    received = _received_chunks(session_dir)
    if len(received) != total or received != list(range(total)):
        missing = [i for i in range(total) if i not in set(received)]
        return jsonify({
            "error": "分片不完整，请继续上传剩余分片",
            "missing_chunks": missing[:20],
        }), 400

    # 按序合并
    raw = io.BytesIO()
    try:
        for i in range(total):
            with open(os.path.join(session_dir, f"{i}.part"), "rb") as f:
                raw.write(f.read())
    except OSError:
        return jsonify({"error": "读取分片失败，请重新上传"}), 500
    raw_bytes = raw.getvalue()

    if len(raw_bytes) > config.MAX_IMAGE_SIZE:
        shutil.rmtree(session_dir, ignore_errors=True)
        max_mb = config.MAX_IMAGE_SIZE / (1024 * 1024)
        return jsonify({"error": f"图片大小不能超过 {max_mb:.0f}MB"}), 400

    # Pillow 校验 + 压缩
    try:
        final_bytes, ext, width, height = _compress_image(raw_bytes, meta["ext"])
    except (UnidentifiedImageError, OSError, ValueError):
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({"error": "文件不是有效的图片，请重新选择"}), 400

    # 唯一存储路径：uploads/images/{yyyymm}/{uuid}.{ext}
    sub = datetime.now().strftime("%Y%m")
    image_dir = os.path.join(config.UPLOAD_DIR, config.IMAGE_DIRNAME, sub)
    try:
        os.makedirs(image_dir, exist_ok=True)
    except OSError:
        return jsonify({"error": "上传服务暂不可用，请稍后重试"}), 500

    new_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(image_dir, new_filename)
    try:
        with open(file_path, "wb") as f:
            f.write(final_bytes)
    except OSError:
        return jsonify({"error": "图片保存失败，请重试"}), 500

    # 清理分片会话（失败不影响结果）
    shutil.rmtree(session_dir, ignore_errors=True)

    url = f"/uploads/{config.IMAGE_DIRNAME}/{sub}/{new_filename}"
    return jsonify({
        "message": "图片上传成功",
        "url": url,
        "width": width,
        "height": height,
        "size": len(final_bytes),
    }), 201
