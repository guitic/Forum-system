# -*- coding: utf-8 -*-
"""
模块 3：浏览量统计功能 端到端自测脚本（适配 view_log 持久化去重）。

使用独立测试库 instance/_test_views.db，不污染开发库。
直接通过 Flask test_client 走完整 HTTP 链路，覆盖：
  新帖默认 0 / 首次访问 +1 / 同 IP 30 分钟窗口去重 /
  不同 IP 分别计数 / X-Forwarded-For 识别 /
  登录用户跨 IP 只计一次 / 无效 token 降级 IP /
  窗口过期后重新计数 / 不同帖子去重互不影响 /
  不存在帖子 404 不计数 / 极大值不溢出 /
  自增写入失败时降级放行且释放去重占位 /
  去重记录持久化在 view_log 表（跨"进程"即跨连接可见） /
  30 线程不同访客并发原子自增无丢失

与旧版差异：去重状态已从进程内字典迁移到 view_log 表，
因此不再有 _view_dedup / _view_dedup_lock，改为直接操作数据库记录。

用法（可从任意目录运行）：
    python backend/tests/test_view_count.py
退出码 0 = 全部通过，1 = 存在失败项。
"""

import os
import sys
import threading
from datetime import datetime, timedelta, timezone

# 定位 backend/ 目录：保证 import 与 instance/ 相对路径正确
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

# timeout=15：并发用例下 SQLite 写锁等待，避免偶发 "database is locked"
os.environ["DATABASE_URL"] = "sqlite:///./_test_views.db?timeout=15"

DB_FILE = os.path.join("instance", "_test_views.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

import bcrypt  # noqa: E402

from app import create_app  # noqa: E402
from models import Post, User, ViewLog, db  # noqa: E402
from config import config  # noqa: E402

app = create_app()
with app.app_context():
    db.create_all()

PWD = "Pass@12345"


def mkuser(username):
    u = User(
        username=username,
        password_hash=bcrypt.hashpw(PWD.encode(), bcrypt.gensalt()).decode(),
    )
    db.session.add(u)
    db.session.commit()
    return u


with app.app_context():
    user_a = mkuser("vieweruser01")
    username_a = user_a.username
    posts = {}
    for name, title in (
        ("p1", "浏览量测试帖一号"),
        ("p2", "浏览量测试帖二号"),
        ("p3", "并发测试帖"),
    ):
        p = Post(user_id=user_a.id, title=title, content="正文")
        db.session.add(p)
        db.session.commit()
        posts[name] = p.id

P1, P2, P3 = posts["p1"], posts["p2"], posts["p3"]

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


def view(post_id, ip=None, xff=None, token=None):
    """以指定访客身份请求详情，返回 (status, json)。"""
    headers = {}
    if xff:
        headers["X-Forwarded-For"] = xff
    if token:
        headers["Authorization"] = "Bearer " + token
    overrides = {"REMOTE_ADDR": ip} if ip else None
    r = client.get(
        f"/api/posts/{post_id}",
        headers=headers,
        environ_overrides=overrides,
    )
    return r.status_code, (r.get_json() if r.is_json else None)


def db_view_count(post_id):
    with app.app_context():
        return int(Post.query.get(post_id).view_count)


def reset_dedup():
    """清空 view_log 去重表，隔离各用例。"""
    with app.app_context():
        db.session.query(ViewLog).delete()
        db.session.commit()


def expire_dedup(post_id, visitor_key):
    """把某访客在某帖上的去重记录时间拨到窗口外，模拟 30 分钟已流逝。"""
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        seconds=config.VIEW_DEDUP_WINDOW_SECONDS + 60
    )
    with app.app_context():
        db.session.query(ViewLog).filter(
            ViewLog.post_id == post_id,
            ViewLog.visitor_key == visitor_key,
        ).update({ViewLog.created_at: old}, synchronize_session=False)
        db.session.commit()


def count_dedup_rows(post_id, visitor_key):
    with app.app_context():
        return (
            db.session.query(ViewLog)
            .filter(
                ViewLog.post_id == post_id,
                ViewLog.visitor_key == visitor_key,
            )
            .count()
        )


token_a = login(username_a)

# ---------- 1. 新帖初始浏览量为 0，且模型 to_dict 携带字段 ----------
check("1  新帖数据库 view_count 默认为 0", db_view_count(P2) == 0,
      str(db_view_count(P2)))
with app.app_context():
    d = Post.query.get(P2).to_dict()
check("2  Post.to_dict 含 view_count 字段", d.get("view_count") == 0, str(d.get("view_count")))

# ---------- 2. 首次访问自增为 1，响应携带字段 ----------
reset_dedup()
st, j = view(P1, ip="10.0.0.1")
check("3  首次访问详情返回 200 且 view_count=1",
      st == 200 and j.get("view_count") == 1, f"status={st}, body={j.get('view_count') if j else None}")
check("4  数据库浏览量已落库为 1", db_view_count(P1) == 1, str(db_view_count(P1)))
check("4b 去重记录已持久化到 view_log 表",
      count_dedup_rows(P1, "ip:10.0.0.1") == 1,
      str(count_dedup_rows(P1, "ip:10.0.0.1")))

# ---------- 3. 同 IP 窗口内连续刷新 3 次，不重复计数 ----------
for _ in range(3):
    st, j = view(P1, ip="10.0.0.1")
check("5  同 IP 窗口内多次访问只计一次（响应仍为 1）",
      st == 200 and j.get("view_count") == 1, str(j.get("view_count")))
check("6  同 IP 窗口内多次访问数据库仍为 1", db_view_count(P1) == 1, str(db_view_count(P1)))

# ---------- 4. 不同 IP 分别计数 ----------
st, j = view(P1, ip="10.0.0.2")
check("7  第二个 IP 访问计数为 2", st == 200 and j.get("view_count") == 2,
      str(j.get("view_count") if j else None))
st, j = view(P1, ip="10.0.0.3")
check("8  第三个 IP 访问计数为 3", st == 200 and j.get("view_count") == 3,
      str(j.get("view_count") if j else None))

# ---------- 5. X-Forwarded-For 首跳作为匿名身份（Nginx 反代场景） ----------
# 相同 XFF、不同直连 IP 视为同一访客
st, j1 = view(P1, ip="192.168.1.1", xff="203.0.113.7, 10.0.0.254")
st2, j2 = view(P1, ip="192.168.1.2", xff="203.0.113.7, 10.0.0.254")
check("9  相同 XFF 首跳跨直连 IP 只计一次",
      j1.get("view_count") == 4 and j2.get("view_count") == 4,
      f"{j1.get('view_count')} -> {j2.get('view_count')}")

# ---------- 6. 登录用户身份优先于 IP：同一用户换 IP 不重复计数 ----------
st, j1 = view(P1, ip="172.16.0.1", token=token_a)
st2, j2 = view(P1, ip="172.16.0.2", token=token_a)
check("10 同一登录用户跨 IP 只计一次",
      j1.get("view_count") == 5 and j2.get("view_count") == 5,
      f"{j1.get('view_count')} -> {j2.get('view_count')}")

# ---------- 7. 无效/过期 token 降级按 IP 去重，不影响计数 ----------
st, j1 = view(P1, ip="172.16.0.9", token="not-a-valid-jwt")
st2, j2 = view(P1, ip="172.16.0.10", token="not-a-valid-jwt")
check("11 无效 token 按 IP 区分，两个 IP 各计一次",
      j1.get("view_count") == 6 and j2.get("view_count") == 7,
      f"{j1.get('view_count')} -> {j2.get('view_count')}")

# ---------- 8. 去重窗口过期后重新计数 ----------
# 把该访客在 P1 上的去重记录时间拨到窗口外，精确模拟 30 分钟窗口已流逝
expire_dedup(P1, "ip:10.0.0.1")
st, j = view(P1, ip="10.0.0.1")  # 用例 3/4 的老访客
check("12 窗口过期后老访客重新计数",
      st == 200 and j.get("view_count") == 8, str(j.get("view_count")))

# ---------- 9. 不同帖子的去重互不影响 ----------
reset_dedup()
st, ja = view(P1, ip="10.1.1.1")
st, jb = view(P2, ip="10.1.1.1")
check("13 同一访客访问不同帖子分别计数",
      ja.get("view_count") >= 1 and jb.get("view_count") == 1,
      f"p1={ja.get('view_count')}, p2={jb.get('view_count')}")

# ---------- 10. 不存在的帖子 404，且不产生计数 ----------
st, j = view(999999, ip="10.2.2.2")
check("14 不存在帖子返回 404", st == 404, str(st))

# ---------- 11. 极大值自增（SQLite 无 INT 上限问题，前端负责千分位） ----------
reset_dedup()
with app.app_context():
    db.session.query(Post).filter(Post.id == P1).update(
        {Post.view_count: 1234567}, synchronize_session=False
    )
    db.session.commit()
st, j = view(P1, ip="10.3.3.3")
check("15 极大浏览量原子 +1 正确", st == 200 and j.get("view_count") == 1234568,
      str(j.get("view_count") if j else None))

# ---------- 12. 自增写入失败：详情仍 200，占位释放后可重新计数 ----------
reset_dedup()
# 故障注入方式：在 SQLAlchemy 执行层拦截"浏览量自增"这一条 UPDATE（view_count = view_count + 1），
# 让它抛错；其余语句（含 view_log 占位写入 / 释放）照常执行。
# 说明：不能简单让 session.commit 的第 1 次调用失败——那时 _reserve_view 内部
#       的占位 INSERT 会先提交，故障会打在占位阶段而非自增阶段，语义不符。
#       按 SQL 文本精确拦截，才能真实模拟"占位成功、自增提交失败"的场景。
from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402


class _IncrementFailure(Exception):
    pass


_fail_increment = {"on": False}


@event.listens_for(Engine, "before_cursor_execute")
def _intercept_increment(conn, cursor, statement, parameters, context, executemany):
    # 只拦截真正的"浏览量自增"语句：必须以 UPDATE 开头且命中 view_count = view_count + 1。
    # 注意不能只用 `"UPDATE" in statement`——SELECT 列表里的 updated_at 同样包含
    # 子串 "UPDATE"，会把详情查询误伤成 500。
    if not _fail_increment["on"]:
        return
    head = statement.lstrip().upper()
    if head.startswith("UPDATE") and "view_count" in statement:
        raise _IncrementFailure("simulated view_count increment failure")


try:
    _fail_increment["on"] = True
    _base_before_fail = db_view_count(P2)  # 故障前基线（此前用例已使 P2 非 0）
    st, j = view(P2, ip="10.4.4.4")
finally:
    _fail_increment["on"] = False

check("16 自增写入失败时详情接口仍返回 200", st == 200, str(st))
check("17 写入失败时返回故障前的浏览量值（不回退、不虚增）",
      j is not None and j.get("view_count") == _base_before_fail,
      f"期望 {_base_before_fail}，实际 {j.get('view_count') if j else None}")
check("17b 写入失败后数据库浏览量未被修改",
      db_view_count(P2) == _base_before_fail,
      f"期望 {_base_before_fail}，实际 {db_view_count(P2)}")

st, j = view(P2, ip="10.4.4.4")  # 同一访客：上次占位应已释放
check("18 写入失败后占位被释放，同访客重试计数成功（+1）",
      st == 200 and j.get("view_count") == _base_before_fail + 1,
      f"期望 {_base_before_fail + 1}，实际 {j.get('view_count') if j else None}")

# ---------- 13. 并发：30 个不同访客同时访问，原子自增无丢失 ----------
reset_dedup()
N = 30
barrier = threading.Barrier(N)
errors = []
statuses = []
status_lock = threading.Lock()


def concurrent_view(i):
    try:
        barrier.wait(timeout=30)
        st, _ = view(P3, ip=f"10.9.0.{i + 1}")
        with status_lock:
            statuses.append(st)
    except Exception as exc:  # noqa: BLE001
        with status_lock:
            errors.append(repr(exc))


threads = [threading.Thread(target=concurrent_view, args=(i,)) for i in range(N)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=60)

final_count = db_view_count(P3)
check("19 30 并发访客无线程异常", not errors and len(statuses) == N,
      f"errors={errors[:2]}, statuses={len(statuses)}")
check("20 30 并发访客全部 200", all(s == 200 for s in statuses), str(sorted(set(statuses))))
check("21 并发原子自增无丢失：最终浏览量=30", final_count == N,
      f"期望 {N}，实际 {final_count}")

# ---------- 14. 并发同访客：去重表唯一键保证只计一次 ----------
reset_dedup()
M = 10
barrier2 = threading.Barrier(M)
for i in range(M):
    pass


def same_visitor_view(i):
    try:
        barrier2.wait(timeout=30)
        view(P2, ip="10.8.8.8")  # 全部同一 IP，模拟同一访客并发刷新
    except Exception:  # noqa: BLE001
        pass


base = db_view_count(P2)
threads2 = [threading.Thread(target=same_visitor_view, args=(i,)) for i in range(M)]
for t in threads2:
    t.start()
for t in threads2:
    t.join(timeout=60)

delta = db_view_count(P2) - base
check("22 同一访客并发刷新只计一次（唯一键去重）", delta == 1,
      f"期望增量 1，实际 {delta}")

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
