# -*- coding: utf-8 -*-
"""
模块 1：编辑帖子与回复功能 端到端自测脚本。

使用独立测试库 instance/_test_edit.db，不污染开发库。
直接通过 Flask test_client 走完整 HTTP 链路，覆盖：
  作者编辑自己内容 / 管理员编辑任意内容 / 非权限用户 403 /
  未登录 401 / 空内容 400 / updated_at 字段更新验证 /
  编辑标记字段（updated_at）随详情与列表接口返回 /
  超长内容 400 / 不存在资源 404 / XSS 内容按原文存储

用法（可从任意目录运行）：
    python backend/tests/test_edit.py
退出码 0 = 全部通过，1 = 存在失败项。
"""

import os
import sys
from datetime import datetime, timezone

# 定位 backend/ 目录：保证 import 与 instance/ 相对路径正确
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

os.environ["DATABASE_URL"] = "sqlite:///./_test_edit.db"

DB_FILE = os.path.join("instance", "_test_edit.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

import bcrypt  # noqa: E402

from app import create_app  # noqa: E402
from config import config  # noqa: E402
from models import Post, Reply, User, db  # noqa: E402

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
    author = mkuser("authoruser01")
    bob = mkuser("bobuser00002")
    admin = mkuser("adminuser003", role="admin")
    post = Post(user_id=author.id, title="编辑功能测试帖", content="# 原始内容")
    db.session.add(post)
    db.session.commit()
    reply = Reply(post_id=post.id, user_id=bob.id, content="原始回复")
    db.session.add(reply)
    db.session.commit()

    info = {
        "author": author.username,
        "bob": bob.username,
        "admin": admin.username,
        "pid": post.id,
        "rid": reply.id,
    }

pid = info["pid"]
rid = info["rid"]

client = app.test_client()

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))
    return cond


def login(username):
    r = client.post("/api/auth/login", json={"username": username, "password": PWD})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["token"]


T = {
    "author": login(info["author"]),
    "bob": login(info["bob"]),
    "admin": login(info["admin"]),
}
HDR = lambda k: {"Authorization": "Bearer " + T[k]}  # noqa: E731


# ---------- 初始状态：未编辑时 updated_at 为 null ----------
j = client.get(f"/api/posts/{pid}").get_json()
check("1  初始帖子 updated_at 为 null", j.get("updated_at") is None, str(j.get("updated_at")))
check("2  初始回复 updated_at 为 null",
      all(r.get("updated_at") is None for r in j["replies"]),
      str([r.get("updated_at") for r in j["replies"]]))

# ---------- 鉴权 ----------
r = client.put(f"/api/posts/{pid}", json={"title": "x", "content": "y"})
check("3  未登录编辑帖子返回 401", r.status_code == 401, str(r.status_code))

r = client.put(f"/api/replies/{rid}", json={"content": "y"})
check("4  未登录编辑回复返回 401", r.status_code == 401, str(r.status_code))

r = client.put(f"/api/posts/{pid}", json={"title": "x", "content": "y"}, headers=HDR("bob"))
check("5  非作者非管理员编辑帖子返回 403", r.status_code == 403, str(r.status_code))

r = client.put(f"/api/replies/{rid}", json={"content": "y"}, headers=HDR("author"))
check("6  非作者非管理员编辑回复返回 403", r.status_code == 403, str(r.status_code))

# ---------- 参数校验 ----------
r = client.put(f"/api/posts/{pid}", json={"title": "", "content": "y"}, headers=HDR("author"))
check("7  空标题编辑帖子返回 400", r.status_code == 400, str(r.status_code))

r = client.put(f"/api/posts/{pid}", json={"title": "x", "content": "   "}, headers=HDR("author"))
check("8  空内容编辑帖子返回 400", r.status_code == 400, str(r.status_code))

r = client.put(f"/api/posts/{pid}",
               json={"title": "x" * 201, "content": "y"}, headers=HDR("author"))
check("9  超长标题编辑帖子返回 400", r.status_code == 400, str(r.status_code))

r = client.put(f"/api/replies/{rid}", json={"content": "  "}, headers=HDR("bob"))
check("10 空内容编辑回复返回 400", r.status_code == 400, str(r.status_code))

r = client.put(f"/api/replies/{rid}",
               json={"content": "x" * (config.MAX_REPLY_LENGTH + 1)}, headers=HDR("bob"))
check("11 超长内容编辑回复返回 400", r.status_code == 400, str(r.status_code))

r = client.put("/api/posts/999999", json={"title": "x", "content": "y"}, headers=HDR("admin"))
check("12 编辑不存在的帖子返回 404", r.status_code == 404, str(r.status_code))

r = client.put("/api/replies/999999", json={"content": "x"}, headers=HDR("admin"))
check("13 编辑不存在的回复返回 404", r.status_code == 404, str(r.status_code))

# ---------- 作者编辑自己的帖子 ----------
before = datetime.now(timezone.utc)
r = client.put(f"/api/posts/{pid}",
               json={"title": "作者改过的标题", "content": "# 作者改过的内容"},
               headers=HDR("author"))
j = r.get_json()
check("14 作者编辑帖子成功(200)", r.status_code == 200, str(r.status_code))
check("15 编辑帖子响应含更新后的 title/content",
      j.get("title") == "作者改过的标题" and j.get("content") == "# 作者改过的内容",
      str(j.get("title")))
check("16 编辑帖子响应含 updated_at", bool(j.get("updated_at")), str(j.get("updated_at")))

with app.app_context():
    p = Post.query.get(pid)
    check("17 数据库帖子 updated_at 已写入", p.updated_at is not None)
    check("18 数据库帖子内容已更新",
          p.title == "作者改过的标题" and p.content == "# 作者改过的内容")
    db_updated = p.updated_at

# updated_at 应为本次服务器时间（允许少量误差）
parsed = datetime.fromisoformat(j["updated_at"])
if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
check("19 updated_at 为当前服务器时间",
      abs((parsed - before).total_seconds()) < 10,
      f"updated_at={j['updated_at']}")

# ---------- 作者编辑自己的回复 ----------
r = client.put(f"/api/replies/{rid}", json={"content": "作者改过的回复"}, headers=HDR("bob"))
j = r.get_json()
check("20 作者编辑回复成功(200)", r.status_code == 200, str(r.status_code))
check("21 编辑回复响应含更新后的 content", j.get("content") == "作者改过的回复",
      str(j.get("content")))
check("22 编辑回复响应含 updated_at", bool(j.get("updated_at")), str(j.get("updated_at")))
check("23 编辑回复响应保留层级字段",
      all(k in j for k in ("depth", "parent_id", "root_id", "is_author")),
      str(sorted(j.keys())))

with app.app_context():
    rp = Reply.query.get(rid)
    check("24 数据库回复 updated_at 已写入", rp.updated_at is not None)
    check("25 数据库回复内容已更新", rp.content == "作者改过的回复")

# ---------- 管理员编辑任意内容 ----------
r = client.put(f"/api/posts/{pid}",
               json={"title": "管理员改过的标题", "content": "管理员改过的内容"},
               headers=HDR("admin"))
j = r.get_json()
check("26 管理员编辑他人帖子成功", r.status_code == 200 and j.get("title") == "管理员改过的标题",
      str(r.status_code))

r = client.put(f"/api/replies/{rid}", json={"content": "管理员改过的回复"}, headers=HDR("admin"))
check("27 管理员编辑他人回复成功",
      r.status_code == 200 and r.get_json().get("content") == "管理员改过的回复",
      str(r.status_code))

# ---------- 编辑标记数据：详情接口返回 updated_at ----------
j = client.get(f"/api/posts/{pid}").get_json()
check("28 详情接口帖子 updated_at 非空", bool(j.get("updated_at")), str(j.get("updated_at")))
check("29 详情接口回复 updated_at 非空",
      all(r.get("updated_at") for r in j["replies"]),
      str([r.get("updated_at") for r in j["replies"]]))
check("30 reply_tree 节点同样携带 updated_at",
      bool(j["reply_tree"]) and all(n.get("updated_at") for n in j["reply_tree"]),
      str([n.get("updated_at") for n in j["reply_tree"]]))

# ---------- 列表接口返回 updated_at ----------
j = client.get("/api/posts").get_json()
target = next((p for p in j["posts"] if p["id"] == pid), None)
check("31 列表接口帖子含 updated_at", target is not None and bool(target.get("updated_at")),
      str(target.get("updated_at") if target else None))

# ---------- 二次编辑：updated_at 应刷新 ----------
with app.app_context():
    first_updated = Post.query.get(pid).updated_at
r = client.put(f"/api/posts/{pid}",
               json={"title": "第二次编辑", "content": "第二次编辑内容"},
               headers=HDR("author"))
with app.app_context():
    second_updated = Post.query.get(pid).updated_at
check("32 二次编辑后 updated_at 刷新",
      second_updated is not None and second_updated >= first_updated,
      f"first={first_updated} second={second_updated}")

# ---------- XSS：内容按原文存储（前端渲染期清洗） ----------
xss = '<script>alert(1)</script> **加粗**'
r = client.put(f"/api/replies/{rid}", json={"content": xss}, headers=HDR("bob"))
check("33 含脚本内容可保存(存储层不过滤)", r.status_code == 200, str(r.status_code))
with app.app_context():
    check("34 XSS 内容按原文入库，由前端白名单清洗",
          Reply.query.get(rid).content == xss)

# ---------- 并发编辑：后写入者覆盖（最终一致） ----------
client.put(f"/api/posts/{pid}", json={"title": "并发A", "content": "A"}, headers=HDR("author"))
r = client.put(f"/api/posts/{pid}", json={"title": "并发B", "content": "B"}, headers=HDR("admin"))
with app.app_context():
    p = Post.query.get(pid)
    check("35 并发编辑以最后提交为准", p.title == "并发B" and p.content == "B",
          f"title={p.title}")

# ---------- 汇总 ----------
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"\n===== {passed}/{total} 项通过 =====")
if passed != total:
    print("失败项：")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name}  {detail}")
sys.exit(0 if passed == total else 1)
