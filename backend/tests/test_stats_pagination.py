# -*- coding: utf-8 -*-
"""
模块：社区统计与帖子分页 端到端自测脚本。

使用独立测试库 instance/_test_stats.db，不污染开发库。
覆盖：
  - GET /api/stats  字段完整性、数值正确性、TTL 缓存命中与失效
  - GET /api/posts  分页边界（page/limit 收敛）、total/pages 计算、
                    越界页空列表、排序稳定（created_at 倒序）

用法（可从任意目录运行）：
    python backend/tests/test_stats_pagination.py
退出码 0 = 全部通过，1 = 存在失败项。
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

os.environ["DATABASE_URL"] = "sqlite:///./_test_stats.db"
os.environ["STATS_CACHE_TTL_SECONDS"] = "60"

DB_FILE = os.path.join("instance", "_test_stats.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

import bcrypt  # noqa: E402

from app import create_app  # noqa: E402
from config import config  # noqa: E402
from models import Post, Reply, User, db  # noqa: E402
from routes import stats as stats_routes  # noqa: E402

app = create_app()
with app.app_context():
    db.create_all()

PWD = "Pass@12345"
N_POSTS = 25  # 制造跨越两页的数据量（默认 limit 20）

with app.app_context():
    u = User(
        username="statsuser001",
        password_hash=bcrypt.hashpw(PWD.encode(), bcrypt.gensalt()).decode(),
    )
    db.session.add(u)
    db.session.commit()
    UID = u.id

    posts = []
    for i in range(N_POSTS):
        p = Post(user_id=UID, title=f"统计测试帖 {i:02d}", content="正文")
        db.session.add(p)
        posts.append(p)
    db.session.commit()

    # 给第一条帖子加 3 条回复
    root = posts[0]
    db.session.add(Reply(post_id=root.id, user_id=UID, content="回复1"))
    db.session.add(Reply(post_id=root.id, user_id=UID, content="回复2"))
    db.session.add(Reply(post_id=root.id, user_id=UID, content="回复3"))
    db.session.commit()

client = app.test_client()
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))
    return cond


# ---------- 1. /api/stats 字段与数值 ----------
stats_routes.clear_cache()
r = client.get("/api/stats")
check("1  /api/stats 返回 200", r.status_code == 200, str(r.status_code))
body = r.get_json() or {}
check("2  含 posts 字段", "posts" in body, str(list(body.keys())))
check("3  含 users 字段", "users" in body, str(list(body.keys())))
check("4  含 replies 字段", "replies" in body, str(list(body.keys())))
check("5  posts 计数正确", body.get("posts") == N_POSTS, str(body.get("posts")))
check("6  replies 计数正确", body.get("replies") == 3, str(body.get("replies")))
check("7  users 计数 ≥1", (body.get("users") or 0) >= 1, str(body.get("users")))
check("8  数值均为整数且非负",
      all(isinstance(body.get(k), int) and body[k] >= 0
          for k in ("posts", "users", "replies")),
      str(body))

# ---------- 2. /api/stats TTL 缓存 ----------
stats_routes.clear_cache()
r1 = client.get("/api/stats")
first = r1.get_json()

# 缓存生效：新增一条帖子后立刻查询，应仍命中旧缓存（返回旧值）
with app.app_context():
    db.session.add(Post(user_id=UID, title="缓存期新增帖", content="x"))
    db.session.commit()

r2 = client.get("/api/stats")
cached = r2.get_json()
check("9  TTL 内命中缓存（数值未变）",
      cached.get("posts") == first.get("posts"),
      f"before={first.get('posts')}, after={cached.get('posts')}")

# 清缓存后应反映新增数据
stats_routes.clear_cache()
r3 = client.get("/api/stats")
fresh = r3.get_json()
check("10 清缓存后数值刷新（+1）",
      fresh.get("posts") == first.get("posts") + 1,
      f"expected={first.get('posts') + 1}, got={fresh.get('posts')}")

# 还原数据（删掉缓存期新增帖），保持后续分页断言口径
with app.app_context():
    extra = Post.query.filter_by(title="缓存期新增帖").first()
    if extra:
        db.session.delete(extra)
        db.session.commit()
stats_routes.clear_cache()

# ---------- 3. /api/posts 分页 ----------
r = client.get("/api/posts")
check("11 默认列表返回 200", r.status_code == 200, str(r.status_code))
body = r.get_json() or {}
check("12 响应含 total", "total" in body, str(list(body.keys())))
check("13 响应含 page", body.get("page") == 1, str(body.get("page")))
check("14 响应含 limit", "limit" in body, str(body.get("limit")))
check("15 响应含 pages", "pages" in body, str(body.get("pages")))
check("16 total 等于帖子总数", body.get("total") == N_POSTS, str(body.get("total")))

default_limit = body.get("limit")
items = body.get("posts") or body.get("items") or body.get("data") or []
check("17 首页返回条数 = min(limit, total)",
      len(items) == min(default_limit, N_POSTS),
      f"len={len(items)}, limit={default_limit}")

expected_pages = -(-N_POSTS // default_limit)  # 向上取整
check("18 pages 计算正确", body.get("pages") == expected_pages,
      f"expected={expected_pages}, got={body.get('pages')}")

# 显式 limit=10
r = client.get("/api/posts?page=1&limit=10")
b = r.get_json() or {}
items1 = b.get("posts") or b.get("items") or b.get("data") or []
check("19 limit=10 首页返回 10 条", len(items1) == 10, str(len(items1)))
check("20 limit=10 时 limit 字段回显 10", b.get("limit") == 10, str(b.get("limit")))
check("21 limit=10 时 pages = 3", b.get("pages") == 3, str(b.get("pages")))

r = client.get("/api/posts?page=3&limit=10")
b3 = r.get_json() or {}
items3 = b3.get("posts") or b3.get("items") or b3.get("data") or []
check("22 第 3 页返回剩余 5 条", len(items3) == 5, str(len(items3)))

# 越界页：应为空列表而非报错
r = client.get("/api/posts?page=99&limit=10")
check("23 越界页仍返回 200", r.status_code == 200, str(r.status_code))
b99 = r.get_json() or {}
items99 = b99.get("posts") or b99.get("items") or b99.get("data") or []
check("24 越界页返回空列表", items99 == [], str(items99))

# page 非法值收敛为 1
r = client.get("/api/posts?page=0")
check("25 page=0 收敛为第 1 页", (r.get_json() or {}).get("page") == 1,
      str((r.get_json() or {}).get("page")))
r = client.get("/api/posts?page=abc")
check("26 page=abc 收敛为第 1 页", (r.get_json() or {}).get("page") == 1,
      str((r.get_json() or {}).get("page")))

# limit 超过上限被收敛到 MAX_PAGE_SIZE
r = client.get("/api/posts?limit=99999")
bmax = r.get_json() or {}
check("27 超大 limit 被收敛到上限", bmax.get("limit") == config.MAX_PAGE_SIZE,
      f"expected={config.MAX_PAGE_SIZE}, got={bmax.get('limit')}")

# ---------- 4. 排序：created_at 倒序（新帖在前） ----------
r = client.get("/api/posts?page=1&limit=5")
b = r.get_json() or {}
top = b.get("posts") or b.get("items") or b.get("data") or []
if top:
    created = [it.get("created_at") for it in top if it.get("created_at")]
    check("28 首页按创建时间倒序",
          created == sorted(created, reverse=True),
          str(created))
else:
    check("28 首页按创建时间倒序", False, "空列表")

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
