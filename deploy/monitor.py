#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
论坛系统日志巡检与告警脚本
============================================================================
部署位置: /opt/forum/scripts/monitor.py
适用环境: Rocky Linux 8/9 + Nginx + systemd + MariaDB
说明:
  - 检查 Nginx access.log 中的 5xx 状态码统计
  - 检查 Nginx error.log 中的错误级别 (error/crit/alert/emerg)
  - 检查 systemd 后端服务 (forum-api) 是否处于 active 状态
  - 检查系统资源 (磁盘、内存、CPU 负载)
  - 检查 MariaDB 连接健康
  - 超阈值或异常时通过 SMTP 发送告警邮件到管理员邮箱
  - 配置全部集中在 CONFIG 字典，便于修改
用法:
  python3 monitor.py               # 执行巡检并按阈值告警
  python3 monitor.py --dry-run     # 仅打印巡检结果，不发送邮件
  python3 monitor.py --json        # 以 JSON 格式输出巡检结果
  python3 monitor.py --verbose     # 输出调试信息
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import re
import smtplib
import subprocess
import sys
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================================
# 配置区 - 修改此处即可适配实际环境
# ============================================================================

CONFIG: Dict[str, Any] = {
    # ---------------------------- 日志路径 ----------------------------
    "nginx_access_log": "/var/log/nginx/forum_access.log",
    "nginx_error_log":  "/var/log/nginx/forum_error.log",
    # 兼容默认 Nginx 日志
    "nginx_default_access_log": "/var/log/nginx/access.log",
    "nginx_default_error_log":  "/var/log/nginx/error.log",

    # ---------------------------- systemd 服务 ----------------------------
    "service_name": "forum-api",

    # ---------------------------- 阈值 ----------------------------
    # 5xx 状态码: 检查时间窗口 (分钟) 内出现次数超过此值即告警
    "error_window_minutes": 5,
    "error_5xx_threshold": 10,
    # error.log 中 error 级别及以上出现次数阈值
    "error_log_threshold": 5,
    # 磁盘使用率 (%) 超过告警
    "disk_threshold": 85,
    # 内存使用率 (%) 超过告警
    "memory_threshold": 90,
    # 5 分钟 CPU 负载 / CPU 核数 超过告警
    "load_threshold": 2.0,
    # 连续失败次数 (用于抑制重复告警)
    "consecutive_failures_threshold": 3,

    # ---------------------------- SMTP ----------------------------
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "smtp_use_ssl": True,
    "smtp_use_starttls": False,
    "smtp_user": "forum-alert@example.com",
    "smtp_password": "change_me",
    "alert_from": "Forum Monitor <forum-alert@example.com>",
    "alert_to": ["admin@example.com"],

    # ---------------------------- 数据库 ----------------------------
    "db_host": "127.0.0.1",
    "db_port": 3306,
    "db_user": "forum",
    "db_password": "change_me",
    "db_name": "forum",
    "db_check_timeout": 5,

    # ---------------------------- 报告 ----------------------------
    "state_dir": "/var/lib/forum/monitor",
    "log_file": "/var/log/forum/monitor.log",
    "report_retention_days": 7,
    "verbose": False,
}


# ============================================================================
# 日志
# ============================================================================

def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("forum_monitor")
    logger.setLevel(logging.DEBUG if CONFIG["verbose"] else logging.INFO)
    logger.handlers.clear()

    fh = logging.FileHandler(CONFIG["log_file"], encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if CONFIG["verbose"] else logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(ch)

    return logger


LOG = _setup_logger()


# ============================================================================
# 工具函数
# ============================================================================

def run_cmd(cmd: str, timeout: int = 15) -> Tuple[int, str, str]:
    """执行 shell 命令并返回 (exit_code, stdout, stderr)。"""
    try:
        p = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as exc:  # pragma: no cover
        return -1, "", str(exc)


def file_recently_written(path: str) -> bool:
    """判断文件在过去若干秒内被写入。"""
    p = Path(path)
    if not p.exists():
        return False
    age = time.time() - p.stat().st_mtime
    return age < 3600  # 1 小时内


def parse_log_ts(line: str) -> Optional[_dt.datetime]:
    """从 Nginx access log 中解析时间戳 (支持常见格式)。"""
    # Nginx 默认格式: 2024/01/01 12:34:56 +0800
    m = re.search(r"\[([^\]]+)\]", line)
    if not m:
        return None
    raw = m.group(1)
    for fmt in (
        "%d/%b/%Y:%H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return _dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def tail_lines(path: str, since: Optional[_dt.datetime] = None) -> List[str]:
    """读取日志，可按时间窗口过滤。"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as exc:
        LOG.warning("读取日志失败 %s: %s", path, exc)
        return []
    if since is None:
        return lines
    result: List[str] = []
    for ln in lines:
        ts = parse_log_ts(ln)
        if ts is None:
            # 无时间戳的行（如 error.log 多行），保守保留
            result.append(ln)
            continue
        if ts >= since:
            result.append(ln)
    return result


def parse_access_log_status(line: str) -> Optional[int]:
    """解析 access log 中的 HTTP 状态码。"""
    # 标准 Nginx 格式: ... "GET / HTTP/1.1" 200 ...
    m = re.search(r'"[^"]*"\s+(\d{3})\s', line)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    # 兼容自定义格式: 状态码作为单独的字段
    m = re.search(r"\s(\d{3})\s+(\d+)", line)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def parse_error_log_level(line: str) -> Optional[str]:
    """解析 error.log 中的级别 (debug/info/notice/warning/error/crit/alert/emerg)。"""
    # Nginx error.log 格式: 2024/01/01 12:34:56 [error] ...
    m = re.search(r"\[(\w+)\]", line)
    return m.group(1).lower() if m else None


def shell_awk(pattern: str, file: str) -> Tuple[int, str]:
    """调用系统 awk 做轻量检索。"""
    cmd = f"grep -cE {pattern!r} {file!r} 2>/dev/null"
    code, out, _ = run_cmd(cmd)
    try:
        return int(out.strip() or "0")
    except ValueError:
        return 0


# ============================================================================
# 检查项 - 返回 (状态, 详情, 是否告警)
# ============================================================================

@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    alert: bool = False


def check_5xx() -> CheckResult:
    """检查 access.log 中 5xx 状态码。"""
    access = CONFIG["nginx_access_log"]
    if not Path(access).exists():
        access = CONFIG["nginx_default_access_log"]

    since = _dt.datetime.now() - _dt.timedelta(minutes=CONFIG["error_window_minutes"])
    lines = tail_lines(access, since=since)

    count_5xx = 0
    count_5xx_sample: List[str] = []
    for ln in lines:
        status = parse_access_log_status(ln)
        if status is not None and 500 <= status < 600:
            count_5xx += 1
            if len(count_5xx_sample) < 5:
                count_5xx_sample.append(ln.strip()[:200])

    threshold = CONFIG["error_5xx_threshold"]
    ok = count_5xx <= threshold
    detail = (
        f"5xx 状态码: {count_5xx} 次 / {threshold} 阈值 "
        f"(窗口 {CONFIG['error_window_minutes']} 分钟, 日志: {access})"
    )
    if count_5xx_sample:
        detail += f"\n  样本: {count_5xx_sample[0]}"
    return CheckResult(
        name="Nginx 5xx 错误率",
        ok=ok,
        detail=detail,
        alert=not ok,
    )


def check_error_log() -> CheckResult:
    """检查 error.log 中 error 及以上级别。"""
    err = CONFIG["nginx_error_log"]
    if not Path(err).exists():
        err = CONFIG["nginx_default_error_log"]

    # 用 grep -c 统计 [error]/[crit]/[alert]/[emerg]
    pattern = r"\[(error|crit|alert|emerg)\]"
    if Path(err).exists():
        since = _dt.datetime.now() - _dt.timedelta(minutes=CONFIG["error_window_minutes"])
        count = sum(
            1 for ln in tail_lines(err, since=since)
            if (lvl := parse_error_log_level(ln)) in {"error", "crit", "alert", "emerg"}
        )
    else:
        count = 0

    threshold = CONFIG["error_log_threshold"]
    ok = count <= threshold
    return CheckResult(
        name="Nginx 错误日志",
        ok=ok,
        detail=f"error+ 级别日志: {count} 条 / {threshold} 阈值 (日志: {err})",
        alert=not ok,
    )


def check_service() -> CheckResult:
    """检查 systemd 服务 forum-api 是否 active。"""
    svc = CONFIG["service_name"]
    code, out, _ = run_cmd(f"systemctl is-active {svc} 2>&1")
    status = out.strip() if out else "unknown"
    ok = status == "active"
    return CheckResult(
        name=f"systemd 服务 {svc}",
        ok=ok,
        detail=f"状态: {status}",
        alert=not ok,
    )


def check_database() -> CheckResult:
    """检查 MariaDB 连接。"""
    db_host = CONFIG["db_host"]
    db_port = CONFIG["db_port"]
    db_user = CONFIG["db_user"]
    db_pwd = CONFIG["db_password"]
    db_name = CONFIG["db_name"]
    timeout = CONFIG["db_check_timeout"]

    code, out, _ = run_cmd(
        f'mariadb -h{db_host} -P{db_port} -u{db_user} -p{db_pwd} '
        f'-D{db_name} -e "SELECT 1;" --connect-timeout={timeout} 2>&1',
        timeout=timeout + 2,
    )
    if code == 0 and "Access denied" not in out:
        ok = True
        detail = f"数据库 {db_host}:{db_port}/{db_name} 连接正常"
    else:
        ok = False
        detail = f"数据库连接失败: {out.strip()[:200]}"
    return CheckResult(
        name="MariaDB 连接",
        ok=ok,
        detail=detail,
        alert=not ok,
    )


def check_disk() -> CheckResult:
    """检查磁盘使用率。"""
    code, out, _ = run_cmd("df -P /opt / 2>/dev/null")
    threshold = CONFIG["disk_threshold"]
    worst = 0
    worst_path = ""
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) < 6 or not parts[4].endswith("%"):
            continue
        try:
            used = int(parts[4].rstrip("%"))
        except ValueError:
            continue
        if used > worst:
            worst = used
            worst_path = parts[1]
    ok = worst <= threshold
    return CheckResult(
        name="磁盘使用率",
        ok=ok,
        detail=f"最高使用率: {worst}% (挂载: {worst_path}) / {threshold}% 阈值",
        alert=not ok,
    )


def check_memory() -> CheckResult:
    """检查内存使用率。"""
    code, out, _ = run_cmd("free -m 2>/dev/null")
    threshold = CONFIG["memory_threshold"]
    try:
        parts = out.splitlines()[0].split()
        total = int(parts[1])
        used = int(parts[2])
        pct = round(used * 100 / total, 1) if total else 0
    except Exception:
        pct, total, used = 0, 0, 0
    ok = pct <= threshold
    return CheckResult(
        name="内存使用率",
        ok=ok,
        detail=f"内存: {used}/{total} MB ({pct}%) / {threshold}% 阈值",
        alert=not ok,
    )


def check_load() -> CheckResult:
    """检查 CPU 负载。"""
    code, out, _ = run_cmd("cat /proc/loadavg 2>/dev/null")
    try:
        load1, load5, load15 = (float(x) for x in out.split()[:3])
    except Exception:
        load1 = load5 = load15 = 0.0

    code2, out2, _ = run_cmd("nproc 2>/dev/null")
    try:
        cores = max(1, int(out2.strip()))
    except Exception:
        cores = 1

    ratio = round(load5 / cores, 2)
    threshold = CONFIG["load_threshold"]
    ok = ratio <= threshold
    return CheckResult(
        name="CPU 负载",
        ok=ok,
        detail=(
            f"load(1/5/15): {load1}/{load5}/{load15}, "
            f"核数={cores}, 比={ratio} / {threshold} 阈值"
        ),
        alert=not ok,
    )


# ============================================================================
# 告警状态跟踪 (防止重复告警)
# ============================================================================

class AlertState:
    """记录每个检查项的连续失败次数，用于抑制重复告警。"""

    def __init__(self, state_dir: str) -> None:
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "alert_state.json"
        self.data: Dict[str, int] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def increment(self, name: str) -> int:
        self.data[name] = self.data.get(name, 0) + 1
        self._save()
        return self.data[name]

    def reset(self, name: str) -> None:
        self.data.pop(name, None)
        self._save()

    def get(self, name: str) -> int:
        return self.data.get(name, 0)

    def _save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            LOG.warning("保存告警状态失败: %s", exc)


# ============================================================================
# 邮件发送
# ============================================================================

def send_alert_email(subject: str, body_html: str, body_text: str) -> bool:
    """通过 SMTP 发送告警邮件。"""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = CONFIG["alert_from"]
    msg["To"] = ", ".join(CONFIG["alert_to"])
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")

    host = CONFIG["smtp_host"]
    port = CONFIG["smtp_port"]
    user = CONFIG["smtp_user"]
    pwd = CONFIG["smtp_password"]

    try:
        if CONFIG["smtp_use_ssl"]:
            smtp = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            if CONFIG["smtp_use_starttls"]:
                smtp.starttls()
        smtp.login(user, pwd)
        smtp.send_message(msg)
        smtp.quit()
        LOG.info("告警邮件发送成功 → %s", CONFIG["alert_to"])
        return True
    except Exception as exc:
        LOG.error("告警邮件发送失败: %s", exc)
        return False


# ============================================================================
# 报告渲染
# ============================================================================

def render_report(results: List[CheckResult],
                  consecutive: Optional[Dict[str, int]] = None,
                  ) -> Tuple[str, str]:
    """返回 (html, text) 双格式报告。"""
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    host = os.uname().nodename
    critical = [r for r in results if r.alert]
    status = "🟢 正常" if not critical else "🔴 异常"

    # 文本格式
    text_lines = [
        f"论坛系统巡检报告  {now}",
        f"主机: {host}    总体状态: {status}",
        "=" * 60,
    ]
    for r in results:
        flag = "✅" if r.ok else ("🚨" if r.alert else "⚠️")
        text_lines.append(f"{flag} [{r.name}]")
        for dline in r.detail.splitlines():
            text_lines.append(f"    {dline}")
        if consecutive:
            cnt = consecutive.get(r.name, 0)
            if cnt:
                text_lines.append(f"    连续失败: {cnt} 次")
        text_lines.append("")

    # HTML 格式
    html_rows = []
    for r in results:
        color = "#28a745" if r.ok else ("#dc3545" if r.alert else "#ffc107")
        icon = "✅" if r.ok else ("🚨" if r.alert else "⚠️")
        cnt = (consecutive or {}).get(r.name, 0)
        cnt_html = f'<div style="font-size:0.85em;color:#666;">连续失败 {cnt} 次</div>' if cnt else ""
        html_rows.append(
            f'<tr>'
            f'<td style="padding:10px;border-bottom:1px solid #eee;">{icon} {r.name}</td>'
            f'<td style="padding:10px;border-bottom:1px solid #eee;">'
            f'<span style="color:{color};font-weight:bold;">'
            f'{"正常" if r.ok else "告警"}</span>'
            f'<br><small style="color:#666;">{r.detail.replace(chr(10), "<br>")}</small>'
            f'{cnt_html}</td>'
            f'</tr>'
        )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; max-width: 820px; margin: 24px auto; color: #333; }}
h1 {{ color: #333; border-bottom: 3px solid {'#dc3545' if critical else '#28a745'}; padding-bottom: 8px; }}
.meta {{ color: #666; margin-bottom: 16px; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; padding: 12px; background: #f8f9fa; }}
.footer {{ margin-top: 24px; color: #999; font-size: 0.85em; }}
</style></head><body>
<h1>论坛系统巡检报告</h1>
<div class="meta">时间: {now}　|　主机: {host}　|　总体状态: <b style="color:{'#dc3545' if critical else '#28a745'}">{status}</b></div>
<table><tr><th>检查项</th><th>结果</th></tr>{''.join(html_rows)}</table>
<div class="footer">本报告由 forum monitor.py 自动生成，若未主动修复请通知运维团队。</div>
</body></html>"""

    return html, "\n".join(text_lines)


# ============================================================================
# 主流程
# ============================================================================

def run_checks() -> List[CheckResult]:
    """执行所有检查项。"""
    checks = [
        check_5xx,
        check_error_log,
        check_service,
        check_database,
        check_disk,
        check_memory,
        check_load,
    ]
    results: List[CheckResult] = []
    for fn in checks:
        try:
            r = fn()
            results.append(r)
        except Exception as exc:
            LOG.exception("检查 %s 抛出异常: %s", fn.__name__, exc)
            results.append(CheckResult(
                name=fn.__name__,
                ok=False,
                detail=f"检查抛出异常: {exc}",
                alert=True,
            ))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="论坛系统日志巡检与告警脚本")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印巡检结果，不发送邮件")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 格式输出")
    parser.add_argument("--verbose", action="store_true",
                        help="输出调试信息")
    parser.add_argument("--config", type=str, default=None,
                        help="指定配置文件 (JSON 格式)，覆盖内置配置")
    args = parser.parse_args()

    if args.verbose:
        CONFIG["verbose"] = True
        LOG.setLevel(logging.DEBUG)

    if args.config:
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                overrides = json.loads(f.read())
            CONFIG.update(overrides)
            LOG.info("已加载配置文件: %s", args.config)
        except Exception as exc:
            LOG.error("加载配置文件失败: %s", exc)
            return 2

    LOG.info("=== 论坛系统巡检开始 ===")
    LOG.info("参数: dry_run=%s json=%s verbose=%s",
             args.dry_run, args.json, args.verbose)

    start = time.time()
    results = run_checks()
    elapsed = round(time.time() - start, 2)

    state = AlertState(CONFIG["state_dir"])
    consecutive: Dict[str, int] = {}
    alert_list: List[CheckResult] = []

    for r in results:
        if r.alert:
            cnt = state.increment(r.name)
            consecutive[r.name] = cnt
            if cnt >= CONFIG["consecutive_failures_threshold"]:
                alert_list.append(r)
        else:
            state.reset(r.name)

    critical = bool(alert_list)
    LOG.info("巡检耗时 %.2fs，异常项 %d / %d", elapsed, len(alert_list), len(results))

    if args.json:
        payload = {
            "timestamp": _dt.datetime.now().isoformat(),
            "duration_seconds": elapsed,
            "overall_status": "critical" if critical else "ok",
            "checks": [
                {
                    "name": r.name,
                    "ok": r.ok,
                    "alert": r.alert,
                    "detail": r.detail,
                    "consecutive_failures": consecutive.get(r.name, 0),
                }
                for r in results
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if critical else 0

    # 文本输出
    html, text = render_report(results, consecutive)
    print(text)

    # 告警邮件
    if critical and not args.dry_run:
        subject = (
            f"[论坛告警][{'CRITICAL' if critical else 'WARNING'}] "
            f"{os.uname().nodename} - 巡检异常 {len(alert_list)} 项 "
            f"({time.strftime('%Y-%m-%d %H:%M')})"
        )
        send_alert_email(subject, html, text)
    elif critical:
        LOG.info("dry-run 模式，跳过邮件发送")

    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
