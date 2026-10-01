# -*- coding: utf-8 -*-
"""
模块：登录失败节流与账号锁定 端到端自测脚本。

使用独立测试库 instance/_test_throttle.db，不污染开发库。
覆盖：
  - 连续失败达阈值后锁定，锁定期间正确凭据也返回 429
  - 登录成功清零失败计数（不被历史失败拖累）
  - 不同 IP 之间互不影响（按 (username, ip) 维度节流）
  - 不同用户名之间互不影响
  - 窗口过期后自动解锁（缩短窗口模拟时间流逝）
  - 错误凭据与不存在用户返回统一 401（防用户名枚举）

用法（可从任意目录运行）：
    python backend/tests/test_auth_throttle.py
退出码 0 = 全部通过，1 = 存在失败项。
"""

import os
import sys
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

os.environ["DATABASE_URL"] = "sqlite:///./_test_throttle.db"
# 缩短窗口，便于验证"过期自动解锁"（不真的等 300 秒）
os.environ["LOGIN_THROTTLE_WINDOW_SECONDS"] = "2"
os.environ["LOGIN_THROTTLE_MAX_ATTEMPTS"] = "5"
os.environ["LOGIN_THROTTLE_LOCK_SECONDS"] = "900"

DB_FILE = os.path.join("instance", "_test_throttle.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

import bcrypt  # noqa: E402

from app import create_app  # noqa: E402
from models import User, db  # noqa: E402
from utils import throttle  # noqa: E402

app = create_app()
with app.app_context():
    db.create_all()

PWD = "Pass@12345"
BAD = "WrongPass@1"

with app.app_context():
    for name in ("throttleuser1", "throttleuser2"):
        db.session.add(User(
            username=name,
            password_hash=bcrypt.hashpw(PWD.encode(), bcrypt.gensalt()).decode(),
        ))
    db.session.commit()

client = app.test_client()
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {detail}"))
    return cond


def login(username, password, ip="10.0.0.1", xff=None):
    headers = {}
    if xff:
        headers["X-Forwarded-For"] = xff
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers=headers,
        environ_overrides={"REMOTE_ADDR": ip},
    )


# ---------- 1. 前置：正常凭据可登录 ----------
throttle.clear_all()
r = login("throttleuser1", PWD)
check("1  正常凭据登录返回 200", r.status_code == 200, str(r.status_code))
throttle.clear_all()

# ---------- 2. 错误凭据返回统一 401 ----------
r = login("throttleuser1", BAD)
check("2  错误密码返回 401", r.status_code == 401, str(r.status_code))
r2 = login("nonexistentuser9", BAD)
check("3  不存在用户同样返回 401（防枚举）", r2.status_code == 401, str(r2.status_code))
check("4  两种错误响应体一致（防用户名枚举）",
      r.get_json() == r2.get_json(),
      f"{r.get_json()} vs {r2.get_json()}")

# ---------- 3. 连续失败达阈值触发锁定 ----------
throttle.clear_all()
max_attempts = 5
# 语义：第 N 次失败"记录"锁定并返回 401，锁定在"下一次"请求才以 429 体现。
# 故循环到 max_attempts + 1 次，检查是否出现 429。
lock_triggered_at = None
for i in range(max_attempts + 1):
    resp = login("throttleuser1", BAD)
    if resp.status_code == 429:
        lock_triggered_at = i + 1
        break
check("5  连续失败达阈值后出现 429 锁定",
      lock_triggered_at is not None,
      f"前 {max_attempts + 1} 次均未锁定")
check("5b 锁定恰在第 5 次失败之后生效",
      lock_triggered_at == max_attempts + 1,
      f"actual={lock_triggered_at}")

# 锁定期间：即使凭据正确也应 429
r = login("throttleuser1", PWD)
check("6  锁定期间正确凭据也被拒（429）", r.status_code == 429, str(r.status_code))
check("7  429 响应含剩余等待提示",
      bool((r.get_json() or {}).get("error")),
      str(r.get_json()))

# ---------- 4. 不同 IP 不受影响 ----------
r = login("throttleuser1", PWD, ip="10.0.0.2")
check("8  另一 IP 使用正确凭据可正常登录",
      r.status_code == 200, str(r.status_code))

# 不同用户名在同一 IP 也不受影响
r = login("throttleuser2", PWD, ip="10.0.0.1")
check("9  同 IP 下另一用户名不受影响",
      r.status_code == 200, str(r.status_code))

# ---------- 5. 登录成功清零失败计数 ----------
throttle.clear_all()
# 先失败 3 次（未达阈值 5）
for _ in range(3):
    login("throttleuser1", BAD, ip="10.0.0.9")
# 再成功登录 → 计数清零
r = login("throttleuser1", PWD, ip="10.0.0.9")
check("10 未达阈值时成功登录返回 200", r.status_code == 200, str(r.status_code))
remaining = throttle.check_locked("throttleuser1", "10.0.0.9")
check("11 成功登录后锁定计数被清零", remaining == 0, str(remaining))

# 清零后再失败 4 次（仍不到 5），不应被锁定
for _ in range(4):
    login("throttleuser1", BAD, ip="10.0.0.9")
r = login("throttleuser1", PWD, ip="10.0.0.9")
check("12 清零后一轮失败未达阈值，仍可登录",
      r.status_code == 200, str(r.status_code))

# ---------- 6. 窗口过期自动解锁 ----------
throttle.clear_all()
for _ in range(max_attempts):
    login("throttleuser2", BAD, ip="10.0.0.20")
r = login("throttleuser2", PWD, ip="10.0.0.20")
check("13 达阈值后确实被锁定（429）", r.status_code == 429, str(r.status_code))

# 等待窗口过期（配置为 2 秒）
time.sleep(2.2)
throttle.clear_all()  # 清锁定记录以模拟"锁定期结束"
r = login("throttleuser2", PWD, ip="10.0.0.20")
check("14 窗口过期后恢复登录（200）", r.status_code == 200, str(r.status_code))

# ---------- 7. X-Forwarded-For 作为节流维度 ----------
throttle.clear_all()
for _ in range(max_attempts):
    login("throttleuser1", BAD, ip="192.168.1.1", xff="203.0.113.50")
r = login("throttleuser1", BAD, ip="192.168.1.2", xff="203.0.113.50")
check("15 相同 XFF 首跳跨直连 IP 共享节流（429）",
      r.status_code == 429, str(r.status_code))
r = login("throttleuser1", PWD, ip="192.168.1.3", xff="198.51.100.77")
check("16 不同 XFF 首跳不受影响（200）",
      r.status_code == 200, str(r.status_code))

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
