# -*- coding: utf-8 -*-
"""
图片上传模块（分片上传 + 断点续传 + 压缩）端到端自测脚本。

使用独立测试库 instance/_test_upload.db 与独立上传目录，不污染开发环境。
直接通过 Flask test_client 走完整 HTTP 链路，覆盖：
  init 校验（格式/大小/参数/鉴权）/ chunk 上传与越权 /
  status 断点续传 / complete 合并压缩 / 伪造图片拒绝 /
  GIF 原样保留 / 会话归属校验 / 路径遍历防护

用法（可从任意目录运行）：
    python backend/tests/test_image_upload.py
退出码 0 = 全部通过，1 = 存在失败项。
"""

import io
import os
import shutil
import sys

# 定位 backend/ 目录
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

os.environ["DATABASE_URL"] = "sqlite:///./_test_upload.db"
# 独立上传目录，测试结束清理
TEST_UPLOAD_DIR = os.path.join(BACKEND_DIR, "_test_uploads")
os.environ["UPLOAD_DIR"] = TEST_UPLOAD_DIR
# 小分片便于制造多分片场景
os.environ["IMAGE_CHUNK_SIZE"] = "4096"

DB_FILE = os.path.join("instance", "_test_upload.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)
if os.path.isdir(TEST_UPLOAD_DIR):
    shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)

import bcrypt  # noqa: E402
from PIL import Image  # noqa: E402

from app import create_app  # noqa: E402
from config import config  # noqa: E402
from models import User, db  # noqa: E402

app = create_app(config)
with app.app_context():
    db.create_all()

PWD = "Pass@12345"


def mkuser(username, role="user"):
    u = User(
        username=username,
        password_hash=bcrypt.hashpw(PWD.encode(), bcrypt.gensalt()).decode(),
        role=role,
    )
    db.session.add(u)
    db.session.commit()
    return u


with app.app_context():
    alice = mkuser("aliceuser001")
    bob = mkuser("bobbuser0002")

client = app.test_client()
results = []


def login(username):
    r = client.post("/api/auth/login", json={"username": username, "password": PWD})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": "Bearer " + r.get_json()["token"]}


H_ALICE = login("aliceuser001")
H_BOB = login("bobbuser0002")


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), "-", name, ("| " + str(detail) if detail and not cond else ""))


def make_png(width=2400, height=1600, color=(200, 60, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def make_gif():
    buf = io.BytesIO()
    Image.new("P", (100, 80), 5).save(buf, format="GIF")
    return buf.getvalue()


def init_upload(headers, filename, data, mime="image/png"):
    chunks = max(1, (len(data) + config.IMAGE_CHUNK_SIZE - 1) // config.IMAGE_CHUNK_SIZE)
    return client.post("/api/upload/init", headers=headers, json={
        "filename": filename, "size": len(data),
        "total_chunks": chunks, "mime_type": mime,
    })


def send_chunk(headers, upload_id, index, data):
    return client.post(
        "/api/upload/chunk", headers=headers,
        data={"upload_id": upload_id, "index": str(index),
              "chunk": (io.BytesIO(data), "chunk.bin")},
        content_type="multipart/form-data",
    )


def split_chunks(data):
    cs = config.IMAGE_CHUNK_SIZE
    return [data[i:i + cs] for i in range(0, len(data), cs)]


# ==================== 1. init 校验 ====================
png = make_png()

r = init_upload(H_ALICE, "photo.png", png)
check("init 正常返回 201 + upload_id", r.status_code == 201 and len(r.get_json()["upload_id"]) == 32, r.get_json())

r = init_upload(H_ALICE, "virus.exe", b"x" * 100, mime="application/octet-stream")
check("init 拒绝非法扩展名 400", r.status_code == 400 and "格式" in r.get_json()["error"], r.get_json())

r = init_upload(H_ALICE, "big.jpg", b"x", mime="image/jpeg")
# 手动构造超大 size
r = client.post("/api/upload/init", headers=H_ALICE, json={
    "filename": "big.jpg", "size": 99 * 1024 * 1024, "total_chunks": 1, "mime_type": "image/jpeg"})
check("init 拒绝超过 10MB 400", r.status_code == 400 and "10MB" in r.get_json()["error"], r.get_json())

r = client.post("/api/upload/init", headers=H_ALICE, json={
    "filename": "a.png", "size": 10, "total_chunks": 1, "mime_type": "text/html"})
check("init 拒绝非法 MIME 400", r.status_code == 400, r.get_json())

r = client.post("/api/upload/init", json={
    "filename": "a.png", "size": 10, "total_chunks": 1})
check("init 未登录 401", r.status_code == 401)

r = client.post("/api/upload/init", headers=H_ALICE, json={
    "filename": "", "size": 10, "total_chunks": 1})
check("init 空文件名 400", r.status_code == 400)

r = client.post("/api/upload/init", headers=H_ALICE, json={
    "filename": "a.png", "size": 0, "total_chunks": 1})
check("init 空文件 400", r.status_code == 400)

r = client.post("/api/upload/init", headers=H_ALICE, json={
    "filename": "a.png", "size": 10, "total_chunks": "abc"})
check("init 非法 total_chunks 400", r.status_code == 400)

# ==================== 2. 分片上传 + 断点续传 ====================
chunks = split_chunks(png)
r = init_upload(H_ALICE, "photo.png", png)
uid = r.get_json()["upload_id"]

# 上传前一半
for i, c in enumerate(chunks[: len(chunks) // 2]):
    rr = send_chunk(H_ALICE, uid, i, c)
    check(f"chunk[{i}] 上传成功", rr.status_code == 200)

r = client.get(f"/api/upload/status?upload_id={uid}", headers=H_ALICE)
st = r.get_json()
check("status 返回已收分片（断点续传依据）",
      r.status_code == 200 and len(st["received_chunks"]) == len(chunks) // 2 and st["complete"] is False, st)

# 提前 complete 应 400
r = client.post("/api/upload/complete", headers=H_ALICE, json={"upload_id": uid})
check("complete 分片不完整 400 + missing_chunks",
      r.status_code == 400 and "missing_chunks" in r.get_json(), r.get_json())

# 越权：bob 不能操作 alice 的会话
r = client.get(f"/api/upload/status?upload_id={uid}", headers=H_BOB)
check("status 他人会话 403", r.status_code == 403)
r = send_chunk(H_BOB, uid, 0, chunks[0])
check("chunk 他人会话 403", r.status_code == 403)

# 非法 upload_id（路径遍历尝试）
r = client.get("/api/upload/status?upload_id=../../etc", headers=H_ALICE)
check("status 非法 upload_id 404", r.status_code == 404)
r = send_chunk(H_ALICE, "../" * 5 + "evil", 0, b"x")
check("chunk 路径遍历 upload_id 404", r.status_code == 404)

# 分片序号越界
r = send_chunk(H_ALICE, uid, len(chunks) + 5, b"x")
check("chunk 序号越界 400", r.status_code == 400)
r = send_chunk(H_ALICE, uid, -1, b"x")
check("chunk 负序号 400", r.status_code == 400)

# 补齐剩余分片（模拟断点续传：跳过已传）
for i, c in enumerate(chunks[len(chunks) // 2:], start=len(chunks) // 2):
    rr = send_chunk(H_ALICE, uid, i, c)
    check(f"chunk[{i}] 续传成功", rr.status_code == 200)

r = client.post("/api/upload/complete", headers=H_ALICE, json={"upload_id": uid})
done = r.get_json()
check("complete 合并成功 201 + url", r.status_code == 201 and done["url"].startswith("/uploads/images/"), done)
check("complete 压缩生效（最长边 <= 1920）", done["width"] <= 1920 and done["height"] <= 1920, done)
check("complete 压缩后体积更小", done["size"] < len(png), f'{done["size"]} vs {len(png)}')

# 静态访问图片
r = client.get(done["url"])
img = Image.open(io.BytesIO(r.data))
check("上传后图片可访问且为有效图片", r.status_code == 200 and img.size == (done["width"], done["height"]))

# complete 后会话已清理
r = client.get(f"/api/upload/status?upload_id={uid}", headers=H_ALICE)
check("complete 后会话清理 404", r.status_code == 404)

# ==================== 3. 伪造图片内容拒绝 ====================
fake = b"this is definitely not an image file content"
r = init_upload(H_ALICE, "fake.png", fake)
uid2 = r.get_json()["upload_id"]
for i, c in enumerate(split_chunks(fake)):
    send_chunk(H_ALICE, uid2, i, c)
r = client.post("/api/upload/complete", headers=H_ALICE, json={"upload_id": uid2})
check("complete 伪造图片 400", r.status_code == 400 and "不是有效的图片" in r.get_json()["error"], r.get_json())

# ==================== 4. GIF 原样保留 ====================
gif = make_gif()
r = init_upload(H_ALICE, "anim.gif", gif, mime="image/gif")
uid3 = r.get_json()["upload_id"]
for i, c in enumerate(split_chunks(gif)):
    send_chunk(H_ALICE, uid3, i, c)
r = client.post("/api/upload/complete", headers=H_ALICE, json={"upload_id": uid3})
d3 = r.get_json()
check("GIF 上传成功且保留原始字节", r.status_code == 201 and d3["size"] == len(gif), d3)

# ==================== 5. 单分片大小限制 ====================
r = init_upload(H_ALICE, "ok.png", png)
uid4 = r.get_json()["upload_id"]
r = send_chunk(H_ALICE, uid4, 0, b"x" * (config.IMAGE_CHUNK_SIZE + 2048))
check("chunk 超过分片上限 400", r.status_code == 400)

# ==================== 汇总 ====================
passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\n===== 图片上传测试：{passed} 通过 / {failed} 失败（共 {len(results)} 项） =====")

# 清理测试上传目录
shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)

sys.exit(1 if failed else 0)
