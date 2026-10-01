# -*- coding: utf-8 -*-
"""
模块：用户资料与密码变更 端到端自测脚本。

使用独立测试库 instance/_test_profile.db，不污染开发库。
覆盖：
  - GET  /api/user/profile  鉴权、字段完整性
  - PUT  /api/user/profile  昵称长度/空值校验、成功更新、鉴权、越权不可改他人
  - POST /api/user/password 旧密码校验、新密码强度、新旧不同、成功后可登录、
                            旧 token 因 pwd_ver 变更立即失效（M2 语义）

用法（可从任意目录运行）：
    python backend/tests/test_user_profile.py
退出码 0 = 全部通过，1 = 存在失败项。
"""

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

os.environ["DATABASE_URL"] = "sqlite:///./_test_profile.db"

DB_FILE = os.path.join("instance", "_test_profile.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

import bcrypt  # noqa: E402

from app import create_app  # noqa: E402
from models import User, db  # noqa: E402

app = create_app()
with app.app_context():
    db.create_all()

PWD = "Pass@12345"
NEW_PWD = "NewPass@67890"

with app.app_context():
    u = User(
        username="profileuser1",
        password_hash=bcrypt.hashpw(PWD.encode(), bcrypt.gensalt()).decode(),
        nickname="初始昵称",
    )
    db.session.add(u)
    db.session.commit()
    UID = u.id
    UNAME = u.username

client = app.test_client()
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))
    return cond


def login(username, password=PWD):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["token"]


def auth(token):
    return {"Authorization": "Bearer " + token}


token = login(UNAME)

# ---------- 1. GET /api/user/profile ----------
r = client.get("/api/user/profile", headers=auth(token))
check("1  携带有效 token 获取资料返回 200", r.status_code == 200, str(r.status_code))
body = r.get_json() or {}
check("2  资料包含用户名", body.get("username") == UNAME, str(body.get("username")))
check("3  资料包含昵称", body.get("nickname") == "初始昵称", str(body.get("nickname")))
check("4  资料包含角色字段", "role" in body, str(list(body.keys())))
check("5  资料不泄露密码哈希",
      "password_hash" not in body and "password" not in body,
      str(list(body.keys())))

r = client.get("/api/user/profile")
check("6  无 token 获取资料返回 401", r.status_code == 401, str(r.status_code))

r = client.get("/api/user/profile", headers=auth("bad.token.here"))
check("7  无效 token 获取资料返回 401", r.status_code == 401, str(r.status_code))

# ---------- 2. PUT /api/user/profile ----------
r = client.put("/api/user/profile", headers=auth(token),
               json={"nickname": "更新后的昵称"})
check("8  修改昵称返回 200", r.status_code == 200, r.get_data(as_text=True))
with app.app_context():
    uname_now = User.query.get(UID).nickname
check("9  昵称已持久化", uname_now == "更新后的昵称", str(uname_now))

# 边界：空串/纯空白按契约视为"清除昵称"（非错误），落库为 None
r = client.put("/api/user/profile", headers=auth(token), json={"nickname": "   "})
check("10 空白昵称按契约清除昵称（200）", r.status_code == 200, str(r.status_code))
with app.app_context():
    cleared = User.query.get(UID).nickname
check("10b 空白昵称落库为 None", cleared is None, repr(cleared))

r = client.put("/api/user/profile", headers=auth(token),
               json={"nickname": "x" * 200})
check("11 超长昵称被拒绝（4xx）", 400 <= r.status_code < 500, str(r.status_code))

# 恢复昵称，便于后续断言
r = client.put("/api/user/profile", headers=auth(token),
               json={"nickname": "更新后的昵称"})
check("11b 恢复昵称成功", r.status_code == 200, r.get_data(as_text=True))

r = client.put("/api/user/profile", json={"nickname": "未授权"})
check("12 无 token 修改昵称返回 401", r.status_code == 401, str(r.status_code))
with app.app_context():
    still = User.query.get(UID).nickname
check("13 未授权请求未改动昵称", still == "更新后的昵称", repr(still))

# ---------- 3. POST /api/user/password ----------
r = client.post("/api/user/password", headers=auth(token),
                json={"old_password": "WrongOld@1", "new_password": NEW_PWD})
check("14 旧密码错误被拒绝（4xx）", 400 <= r.status_code < 500, str(r.status_code))

r = client.post("/api/user/password", headers=auth(token),
                json={"old_password": PWD, "new_password": "123"})
check("15 新密码强度不足被拒绝（4xx）", 400 <= r.status_code < 500, str(r.status_code))

r = client.post("/api/user/password", headers=auth(token),
                json={"old_password": PWD, "new_password": PWD})
check("16 新旧密码相同被拒绝（4xx）", 400 <= r.status_code < 500, str(r.status_code))

r = client.post("/api/user/password", headers=auth(token),
                json={"old_password": PWD, "new_password": NEW_PWD})
check("17 合法修改密码返回 200", r.status_code == 200, r.get_data(as_text=True))

# 新密码可登录
try:
    new_token = login(UNAME, NEW_PWD)
    check("18 新密码可成功登录", bool(new_token), "登录失败")
except AssertionError as exc:
    check("18 新密码可成功登录", False, str(exc))
    new_token = None

# 旧密码不可登录
r = client.post("/api/auth/login", json={"username": UNAME, "password": PWD})
check("19 旧密码登录失败（401）", r.status_code == 401, str(r.status_code))

# ---------- 4. M2：pwd_ver 使旧 token 立即失效 ----------
r = client.get("/api/user/profile", headers=auth(token))
check("20 改密后旧 token 立即失效（401）", r.status_code == 401, str(r.status_code))

if new_token:
    r = client.get("/api/user/profile", headers=auth(new_token))
    check("21 改密后新 token 正常可用", r.status_code == 200, str(r.status_code))

# 连续两次改密：pwd_ver 递增，前一 token 也应失效
r = client.post("/api/user/password", headers=auth(new_token),
                json={"old_password": NEW_PWD, "new_password": "Third@Pass99"})
check("22 二次修改密码返回 200", r.status_code == 200, r.get_data(as_text=True))
r = client.get("/api/user/profile", headers=auth(new_token))
check("23 再次改密后上一 token 同样失效（401）",
      r.status_code == 401, str(r.status_code))

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
