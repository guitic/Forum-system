# -*- coding: utf-8 -*-
"""
V3 回复层级功能端到端自测脚本。

使用独立测试库 instance/_test_forum.db，不污染开发库。
直接通过 Flask test_client 走完整 HTTP 链路，覆盖：
  游客读取 / 鉴权 / 一级二级三级回复 / 深度拉平 / 树结构 /
  跨帖与非法 parent_id / 删除权限 / 级联删除 / 边界校验

用法（可从任意目录运行）：
    python backend/tests/test_reply_hierarchy.py
退出码 0 = 全部通过，1 = 存在失败项。
"""

import os
import sys

# 定位 backend/ 目录：保证 import 与 instance/ 相对路径正确
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

os.environ["DATABASE_URL"] = "sqlite:///./_test_forum.db"
os.environ["MAX_REPLY_DEPTH"] = "2"

DB_FILE = os.path.join("instance", "_test_forum.db")
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
    lz = mkuser("lzuser0001")
    bob = mkuser("bobuser0002")
    alice = mkuser("aliceuser003")
    admin = mkuser("adminuser004", role="admin")
    other = mkuser("otheruser005")
    post = Post(user_id=lz.id, title="层级回复测试", content="# Hello\n\n内容")
    db.session.add(post)
    db.session.commit()

    # 在退出上下文前取出纯值，避免 DetachedInstanceError
    info = {
        "lz": (lz.id, lz.username),
        "bob": (bob.id, bob.username),
        "alice": (alice.id, alice.username),
        "admin": (admin.id, admin.username),
        "other": (other.id, other.username),
        "pid": post.id,
        "bob_display": bob.display_name,
        "alice_display": alice.display_name,
    }

pid = info["pid"]
pid2 = None

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
    "lz": login(info["lz"][1]),
    "bob": login(info["bob"][1]),
    "alice": login(info["alice"][1]),
    "admin": login(info["admin"][1]),
    "other": login(info["other"][1]),
}
HDR = lambda k: {"Authorization": "Bearer " + T[k]}  # noqa: E731


def post_reply(key, content, parent_id=None):
    body = {"content": content}
    if parent_id is not None:
        body["parent_id"] = parent_id
    return client.post(f"/api/posts/{pid}/replies", json=body, headers=HDR(key))


# ---------- 读取与鉴权 ----------
r = client.get(f"/api/posts/{pid}")
j = r.get_json()
check("1  游客可读帖子详情且含层级字段",
      r.status_code == 200 and "reply_tree" in j and "replies" in j and "reply_count" in j,
      str(list(j.keys())))
check("2  初始 reply_count=0 / 空树", j["reply_count"] == 0 and j["reply_tree"] == [])
check("3  帖子详情含 is_author 语义字段", "user_id" in j)

r = client.post(f"/api/posts/{pid}/replies", json={"content": "hi"})
check("4  未登录回复返回 401", r.status_code == 401)

r = post_reply("bob", "   ")
check("5  空内容回复返回 400", r.status_code == 400)

r = post_reply("bob", "x" * (config.MAX_REPLY_LENGTH + 1))
check("6  超长回复返回 400", r.status_code == 400)

# ---------- 层级创建 ----------
r = post_reply("bob", "第一条一级回复")
n1 = r.get_json()
check("7  一级回复创建成功", r.status_code == 201)
check("8  一级回复 depth=0 / parent_id=None / root_id=自身 id",
      n1["depth"] == 0 and n1["parent_id"] is None and n1["root_id"] == n1["id"], str(n1))
check("9  一级回复 is_author=False", n1["is_author"] is False)
rid1 = n1["id"]

r = post_reply("lz", "楼主来回复一下", rid1)
n2 = r.get_json()
check("10 二级回复 depth=1 / parent_id 正确",
      r.status_code == 201 and n2["depth"] == 1 and n2["parent_id"] == rid1, str(n2))
check("11 二级回复 root_id=父回复 id", n2["root_id"] == rid1)
check("12 楼主回复 is_author=True", n2["is_author"] is True)
check("13 reply_to_display_name 指向被回复人",
      n2["reply_to_display_name"] == info["bob_display"], str(n2["reply_to_display_name"]))
check("14 管理员标识正确(role=user)", n2["role"] == "user")
rid2 = n2["id"]

r = post_reply("alice", "再往下追一层", rid2)
n3 = r.get_json()
check("15 三级回复 depth=2", r.status_code == 201 and n3["depth"] == 2, str(n3["depth"]))
check("16 三级回复 root_id 继承话题根", n3["root_id"] == rid1)
rid3 = n3["id"]

r = post_reply("bob", "第四层（应被展示拉平）", rid3)
n4 = r.get_json()
check("17 第四层回复创建成功", r.status_code == 201)
check("18 第四层展示深度被拉平到 ≤ MAX_REPLY_DEPTH",
      n4["depth"] <= config.MAX_REPLY_DEPTH, f"depth={n4['depth']}")
check("19 第四层引用对象仍为真实被回复人",
      n4["reply_to_display_name"] == info["alice_display"], str(n4["reply_to_display_name"]))
rid4 = n4["id"]

# ---------- 树结构 ----------
j = client.get(f"/api/posts/{pid}").get_json()
check("20 reply_count=4", j["reply_count"] == 4, str(j["reply_count"]))
check("21 replies 扁平列表 4 条", len(j["replies"]) == 4)
check("22 扁平列表按时间升序",
      [x["id"] for x in j["replies"]] == sorted(x["id"] for x in j["replies"]))

tree = j["reply_tree"]
check("23 树根仅 1 个一级回复", len(tree) == 1, f"roots={len(tree)}")
root = tree[0]
check("24 根节点 depth=0", root["depth"] == 0)
check("25 根节点 reply_count == children 长度",
      root["reply_count"] == len(root["children"]),
      f"{root['reply_count']} vs {len(root['children'])}")


def walk(nodes, expect_depth=0):
    """深度遍历树，返回 [(id, depth)]，并校验 depth 与位置一致。"""
    out = []
    for n in nodes:
        out.append((n["id"], n["depth"]))
        assert n["depth"] == expect_depth, f"id={n['id']} depth={n['depth']} expect={expect_depth}"
        assert n["reply_count"] == len(n["children"]), f"id={n['id']} reply_count 不一致"
        out.extend(walk(n["children"], expect_depth + 1))
    return out


flat_tree = walk(tree)
check("26 树中所有节点展示深度 ≤ 2", all(d <= 2 for _, d in flat_tree), str(flat_tree))
check("27 树节点总数=4 且无遗漏", sorted(i for i, _ in flat_tree) == [rid1, rid2, rid3, rid4],
      str(sorted(i for i, _ in flat_tree)))

# ---------- parent_id 校验 ----------
with app.app_context():
    p2 = Post(user_id=info["bob"][0], title="另一个帖子", content="x")
    db.session.add(p2)
    db.session.commit()
    pid2 = p2.id

r = client.post(f"/api/posts/{pid2}/replies", json={"content": "x", "parent_id": rid1},
                headers=HDR("alice"))
check("28 跨帖 parent_id 返回 403", r.status_code == 403, str(r.status_code))

r = post_reply("alice", "x", 99999)
check("29 不存在的父回复返回 404", r.status_code == 404, str(r.status_code))

r = post_reply("alice", "x", "abc")
check("30 非法 parent_id 返回 400", r.status_code == 400, str(r.status_code))

r = client.get("/api/posts/999999")
check("31 不存在的帖子返回 404", r.status_code == 404)

# ---------- 删除权限与级联 ----------
r = client.delete(f"/api/replies/{rid1}", headers=HDR("other"))
check("32 非作者非管理员删除返回 403", r.status_code == 403, str(r.status_code))

r = client.delete(f"/api/replies/{rid1}")
check("33 未登录删除返回 401", r.status_code == 401, str(r.status_code))

r = client.delete(f"/api/replies/{rid1}", headers=HDR("admin"))
j = r.get_json()
check("34 管理员级联删除整棵子树(4 条)",
      r.status_code == 200 and j.get("deleted_count") == 4, str(j))

j = client.get(f"/api/posts/{pid}").get_json()
check("35 删除后 reply_count=0 / 树为空",
      j["reply_count"] == 0 and j["reply_tree"] == [])

with app.app_context():
    check("36 数据库中无残留回复", Reply.query.count() == 0, str(Reply.query.count()))
    check("37 悬空引用不影响其他帖子",
          Reply.query.filter_by(post_id=pid2).count() == 0)

r = client.delete("/api/replies/999999", headers=HDR("admin"))
check("38 删除不存在的回复返回 404", r.status_code == 404)

# ---------- 作者删除含子回复 ----------
a = post_reply("bob", "A").get_json()["id"]
b = post_reply("alice", "B", a).get_json()["id"]
c3 = post_reply("bob", "C", b).get_json()["id"]
r = client.delete(f"/api/replies/{a}", headers=HDR("bob"))
j = r.get_json()
check("39 作者删除含 2 条孙回复的子树",
      r.status_code == 200 and j.get("deleted_count") == 3, str(j))
with app.app_context():
    check("40 孙回复一并清除",
          all(Reply.query.get(x) is None for x in (a, b, c3)))

# ---------- 悬空引用降级 ----------
# 数据库外键开启后（含 MySQL 生产与当前 SQLite），正常路径无法写入
# 悬挂 parent_id；此处用关闭外键检查的底层连接模拟历史遗留脏数据，
# 验证读取期的降级容错仍然成立
import sqlite3  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

_raw = sqlite3.connect(DB_FILE)
try:
    _raw.execute("PRAGMA foreign_keys=OFF")
    _cur = _raw.execute(
        "INSERT INTO replies "
        "(post_id, user_id, content, parent_id, root_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            pid,
            info["alice"][0],
            "悬挂父引用",
            777777,
            None,
            datetime.now(timezone.utc).replace(tzinfo=None),
        ),
    )
    orphan_child = _cur.lastrowid
    _raw.commit()
finally:
    _raw.close()
j = client.get(f"/api/posts/{pid}").get_json()
flat = {x["id"]: x for x in j["replies"]}
check("41 悬空 parent_id 降级为一级回复",
      flat[orphan_child]["depth"] == 0 and flat[orphan_child]["parent_id"] == 777777,
      str(flat.get(orphan_child)))
check("42 悬空节点出现在树根", any(n["id"] == orphan_child for n in j["reply_tree"]))

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
