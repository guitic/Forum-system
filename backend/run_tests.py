# -*- coding: utf-8 -*-
"""统一测试入口：依次运行 backend/tests 下的全部端到端自测脚本。

每个测试脚本都是独立进程（各自建库、各自 Flask 应用），因此这里用子进程
逐个执行，避免测试间的模块级状态（如 SQLAlchemy 元数据、节流全局表）互相污染。

用法：
    python backend/run_tests.py            # 运行全部
    python backend/run_tests.py view edit  # 只运行名称含这些关键字的脚本

退出码：0 = 全部通过；1 = 存在失败套件。
"""

import os
import subprocess
import sys

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(BACKEND_DIR, "tests")

# 显式排序，保证输出稳定可读
SUITES = [
    "test_reply_hierarchy.py",
    "test_edit.py",
    "test_view_count.py",
    "test_image_upload.py",
    "test_user_profile.py",
    "test_stats_pagination.py",
    "test_auth_throttle.py",
]


def discover(filters):
    """按关键字过滤套件名；无关键字则返回全部。"""
    if not filters:
        return list(SUITES)
    selected = []
    for name in SUITES:
        if any(f.lower() in name.lower() for f in filters):
            selected.append(name)
    return selected


def main(argv):
    filters = argv[1:]
    suites = discover(filters)
    if not suites:
        print(f"没有匹配的测试套件（关键字={filters}）")
        print("可用套件：", ", ".join(SUITES))
        return 1

    print(f"将运行 {len(suites)} 个测试套件\n" + "=" * 60)
    failed = []
    for name in suites:
        path = os.path.join(TESTS_DIR, name)
        print(f"\n### {name}")
        print("-" * 60)
        proc = subprocess.run(
            [sys.executable, path],
            cwd=BACKEND_DIR,
            capture_output=False,
        )
        if proc.returncode != 0:
            failed.append(name)

    print("\n" + "=" * 60)
    if failed:
        print(f"失败套件（{len(failed)}/{len(suites)}）：")
        for name in failed:
            print(f"  - {name}")
        return 1
    print(f"全部通过：{len(suites)}/{len(suites)} 个套件")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
