# 论坛系统部署与运维指南

> 适用于 Rocky Linux 8/9 + MariaDB + Python 3.9+ + Nginx 1.20+
> 部署目录: `/opt/forum/`

---

## 目录

- [1. 部署拓扑](#1-部署拓扑)
- [2. 环境准备](#2-环境准备)
- [3. 数据库初始化](#3-数据库初始化)
- [4. 代码部署](#4-代码部署)
- [5. 配置修改说明](#5-配置修改说明)
- [6. 服务启动](#6-服务启动)
- [7. 监控配置](#7-监控配置)
- [8. 定时任务](#8-定时任务)
- [9. 常用运维命令](#9-常用运维命令)
- [10. 故障排查](#10-故障排查)
- [11. 安全加固建议](#11-安全加固建议)

---

## 1. 部署拓扑

```
                    ┌─────────────────────────────────────────┐
                    │             Rocky Linux Server          │
                    │                                         │
  Internet ──────►  │  ┌──────────┐   proxy_pass  ┌─────────┐ │
  (HTTPS 443)      │  │  Nginx   │ ──────────►   │ Gunicorn│ │
                    │  │ :443/:80 │               │ :5000   │ │
                    │  └────┬─────┘               └────┬────┘ │
                    │       │                          │       │
                    │  static assets              MariaDB      │
                    │  /opt/forum/frontend         :3306      │
                    │                                         │
                    │  /etc/systemd/system/forum-api.service  │
                    │  cron.d/forum  →  monitor.py / git_pull │
                    └─────────────────────────────────────────┘
```

---

## 2. 环境准备

### 2.1 系统更新

```bash
# 更新系统包
sudo dnf update -y

# 安装必需工具
sudo dnf install -y git curl wget htop vim net-tools mariadb mariadb-server python3 python3-pip python3-devel nginx cronie firewalld

# 安装编译依赖 (pip 安装部分包需要)
sudo dnf install -y gcc make openssl-devel bzip2-devel libffi-devel zlib-devel perl-devel libcurl-devel libjpeg-turbo-devel libpng-devel
```

### 2.2 创建专用用户

```bash
sudo useradd -r -m -d /home/forum -s /bin/bash forum
sudo usermod -aG wheel forum   # 可选：赋予 sudo 权限（谨慎）
```

### 2.3 准备目录结构

```bash
sudo mkdir -p /opt/forum/{repo,frontend,scripts,venv,logs}
sudo mkdir -p /var/lib/forum
sudo mkdir -p /var/log/forum
sudo mkdir -p /var/log/nginx

sudo chown -R forum:forum /opt/forum /var/lib/forum /var/log/forum
sudo chmod -R 750 /opt/forum
```

### 2.4 配置防火墙

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
sudo firewall-cmd --list-all
```

### 2.5 关闭 SELinux 限制（或配置策略）

```bash
# 临时关闭 (重启后恢复)
sudo setenforce 0

# 永久关闭 (不推荐，生产环境建议配置 SELinux 策略)
sudo sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config
```

---

## 3. 数据库初始化

### 3.1 安装并启动 MariaDB

```bash
sudo dnf install -y mariadb mariadb-server
sudo systemctl enable --now mariadb
sudo mysql_secure_installation   # 交互式配置安全选项
```

### 3.2 创建数据库与用户

```bash
sudo mariadb -u root <<'SQL'
CREATE DATABASE IF NOT EXISTS forum
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'forum'@'localhost' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
CREATE USER IF NOT EXISTS 'forum'@'127.0.0.1' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

GRANT ALL PRIVILEGES ON forum.* TO 'forum'@'localhost';
GRANT ALL PRIVILEGES ON forum.* TO 'forum'@'127.0.0.1';

FLUSH PRIVILEGES;
SQL
```

### 3.3 创建表结构 (参照需求文档)

```bash
sudo mariadb -u forum -pCHANGE_ME_STRONG_PASSWORD forum <<'SQL'
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(50) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(20) NOT NULL DEFAULT 'user',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS posts (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    title      VARCHAR(100) NOT NULL,
    content    TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_posts_user_id (user_id),
    KEY idx_posts_created_at (created_at),
    CONSTRAINT fk_posts_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS replies (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    post_id    INT NOT NULL,
    user_id    INT NOT NULL,
    content    TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_replies_post_id (post_id),
    KEY idx_replies_user_id (user_id),
    CONSTRAINT fk_replies_post FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    CONSTRAINT fk_replies_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
SQL
```

### 3.4 创建配置环境变量文件

```bash
sudo tee /etc/forum/forum.env > /dev/null <<'ENV'
# ---- 数据库配置 ----
FORUM_DB_HOST=127.0.0.1
FORUM_DB_PORT=3306
FORUM_DB_USER=forum
FORUM_DB_PASSWORD=CHANGE_ME_STRONG_PASSWORD
FORUM_DB_NAME=forum

# ---- 应用配置 ----
FORUM_SECRET_KEY=CHANGE_ME_RANDOM_64_CHARS
FORUM_BIND=127.0.0.1
FORUM_PORT=5000
FORUM_LOG_LEVEL=INFO

# ---- JWT 配置 ----
FORUM_JWT_EXPIRE_HOURS=24
FORUM_JWT_ALGO=HS256
ENV
sudo chmod 600 /etc/forum/forum.env
sudo chown forum:forum /etc/forum/forum.env
```

---

## 4. 代码部署

### 4.1 克隆仓库

```bash
sudo -u forum git clone https://gitee.com/<your-org>/<your-repo>.git /opt/forum/repo
sudo chown -R forum:forum /opt/forum/repo
```

### 4.2 配置 SSH Key (供 git_pull.sh 使用)

```bash
sudo -u forum ssh-keygen -t ed25519 -N "" -f /home/forum/.ssh/id_ed25519
sudo -u forum cat /home/forum/.ssh/id_ed25519.pub
# 将公钥添加到 Gitee 账户的 SSH Key 设置中

sudo -u forum ssh -T git@gitee.com   # 验证连通性
```

### 4.3 创建虚拟环境并安装依赖

```bash
cd /opt/forum/repo

# 创建虚拟环境 (首次)
python3 -m venv /opt/forum/venv
source /opt/forum/venv/bin/activate

# 安装依赖
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install gunicorn flask-migrate alembic mysqlclient PyMySQL bcrypt PyJWT

# 退出虚拟环境
deactivate
```

### 4.4 复制部署脚本

```bash
cp deploy/git_pull.sh deploy/monitor.py /opt/forum/scripts/
sudo chown -R root:root /opt/forum/scripts
sudo chmod 750 /opt/forum/scripts/*.sh
sudo chmod 750 /opt/forum/scripts/*.py
```

---

## 5. 配置修改说明

### 5.1 Nginx 配置

**部署路径**: `/etc/nginx/conf.d/forum.conf`

```bash
sudo cp deploy/nginx.conf /etc/nginx/conf.d/forum.conf
sudo nginx -t                # 测试配置
sudo systemctl reload nginx  # 重载
```

**必须修改的项**:

| 项 | 位置 | 说明 |
| :--- | :--- | :--- |
| `server_name` | `nginx.conf` | 替换为论坛实际域名 |
| `ssl_certificate` | `nginx.conf` | 替换为实际证书路径 |
| `root` | `nginx.conf` | 替换为前端实际目录 |
| `proxy_pass` | `nginx.conf` | 替换为后端实际地址与端口 |

### 5.2 systemd 服务

**部署路径**: `/etc/systemd/system/forum-api.service`

```bash
sudo cp deploy/forum-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable forum-api
```

**必须修改的项**:

| 项 | 位置 | 说明 |
| :--- | :--- | :--- |
| `User=forum` | `forum-api.service` | 运行用户 (需存在) |
| `WorkingDirectory` | `forum-api.service` | 后端实际工作目录 |
| `ExecStart` | `forum-api.service` | gunicorn 命令与实际入口 |
| `EnvironmentFile` | `forum-api.service` | 指向实际 env 文件路径 |
| `Environment=...` | `forum-api.service` | 替换默认密码 / SECRET_KEY |

### 5.3 Git 更新脚本

**部署路径**: `/opt/forum/scripts/git_pull.sh`

修改环境变量或在脚本头部修改:

| 变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `FORUM_APP_ROOT` | `/opt/forum` | 应用根目录 |
| `FORUM_REPO_DIR` | `$FORUM_APP_ROOT/repo` | Git 仓库目录 |
| `FORUM_BACKEND_DIR` | `$FORUM_REPO_DIR/backend` | 后端代码目录 |
| `FORUM_FRONTEND_DIR` | `$FORUM_REPO_DIR/frontend` | 前端代码目录 |
| `FORUM_VENV_DIR` | `$FORUM_APP_ROOT/venv` | 虚拟环境目录 |
| `FORUM_SERVICE_NAME` | `forum-api` | systemd 服务名 |
| `FORUM_GIT_BRANCH` | `main` | 目标 Git 分支 |

### 5.4 监控脚本配置

**部署路径**: `/opt/forum/scripts/monitor.py`

修改 `CONFIG` 字典中的项:

| 键 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `nginx_access_log` | `/var/log/nginx/forum_access.log` | 实际访问日志路径 |
| `nginx_error_log` | `/var/log/nginx/forum_error.log` | 实际错误日志路径 |
| `service_name` | `forum-api` | systemd 服务名 |
| `error_5xx_threshold` | `10` | 5xx 阈值 |
| `disk_threshold` | `85` | 磁盘使用率阈值 (%) |
| `smtp_host` | `smtp.example.com` | SMTP 服务器地址 |
| `smtp_password` | `change_me` | SMTP 密码 |
| `alert_to` | `["admin@example.com"]` | 告警收件人 |
| `db_password` | `change_me` | 数据库密码 |

---

## 6. 服务启动

### 6.1 首次启动

```bash
# 1. 启用 systemd 服务
sudo systemctl daemon-reload
sudo systemctl enable forum-api

# 2. 启动后端
sudo systemctl start forum-api
sudo systemctl status forum-api

# 3. 测试 Nginx 配置
sudo nginx -t
sudo systemctl restart nginx

# 4. 验证访问
curl -s http://127.0.0.1:5000/healthz    # 直接访问后端
curl -s https://forum.example.com/healthz # 通过 Nginx 访问
```

### 6.2 查看服务日志

```bash
# 实时跟踪
sudo journalctl -u forum-api -f

# 最近 100 条
sudo journalctl -u forum-api -n 100 --no-pager

# 上次启动以来的日志
sudo journalctl -u forum-api -b -n 100 --no-pager

# 错误日志
sudo journalctl -u forum-api -p err -n 50 --no-pager
```

---

## 7. 监控配置

### 7.1 手动执行巡检

```bash
# 完整巡检
python3 /opt/forum/scripts/monitor.py

# 仅打印，不发邮件
python3 /opt/forum/scripts/monitor.py --dry-run

# JSON 输出 (便于对接监控平台)
python3 /opt/forum/scripts/monitor.py --json

# 详细调试
python3 /opt/forum/scripts/monitor.py --verbose
```

### 7.2 配置 SMTP 告警

**方案 A: 阿里云 / 腾讯云 邮件推送**

1. 创建发信地址并获取 SMTP 授权码
2. 修改 `monitor.py` 中的 `CONFIG`:
   - `smtp_host`: `smtp.mxhichina.com` (阿里云) / `smtp.qiye.aliyun.com`
   - `smtp_port`: `465`
   - `smtp_use_ssl`: `True`
   - `smtp_user`: `forum-alert@example.com`
   - `smtp_password`: SMTP 授权码
   - `alert_to`: 接收告警的邮箱

**方案 B: 企业邮箱**

1. 启用 SMTP 服务并获取密码
2. `smtp_host`: 企业邮箱 SMTP 服务器 (如 `smtp.corp.example.com`)
3. `smtp_port`: `465` (SSL) 或 `587` (STARTTLS)

**方案 C: 自建 Mailu / iRedMail**

1. 部署内部邮件服务器
2. `smtp_host`: `mail.example.com`
3. `smtp_port`: `25` 或 `465`

### 7.3 自定义告警阈值

修改 `monitor.py` 中的 `CONFIG`:

```python
CONFIG = {
    "error_5xx_threshold": 10,      # 5xx 阈值
    "error_log_threshold": 5,       # error.log 阈值
    "disk_threshold": 85,           # 磁盘阈值
    "memory_threshold": 90,         # 内存阈值
    "load_threshold": 2.0,          # CPU 负载比
    "consecutive_failures_threshold": 3,  # 连续失败抑制
    ...
}
```

### 7.4 使用外部配置文件

```bash
# 创建外部配置
sudo tee /etc/forum/monitor.json > /dev/null <<'JSON'
{
    "error_5xx_threshold": 15,
    "disk_threshold": 90,
    "smtp_password": "***",
    "alert_to": ["admin@corp.example.com"]
}
JSON

# 执行时指定
python3 /opt/forum/scripts/monitor.py --config /etc/forum/monitor.json
```

---

## 8. 定时任务

### 8.1 安装 crontab

**方案 A (推荐): 系统级**

```bash
sudo cp deploy/crontab.txt /etc/cron.d/forum
sudo chmod 644 /etc/cron.d/forum
```

**方案 B: 导入 root 用户**

```bash
sudo crontab -u root deploy/crontab.txt
sudo crontab -u root -l   # 验证
```

### 8.2 验证 cron 运行

```bash
sudo systemctl status cron
tail -f /var/log/cron
```

### 8.3 定时任务说明

| 任务 | 频率 | 说明 |
| :--- | :--- | :--- |
| 监控脚本 | 每 5 分钟 | 巡检日志与系统状态 |
| Git 更新 | 每日 03:00 | 拉取代码并重启服务 |
| 日志清理 | 每日 04:00 | 删除 30 天前日志 |
| 状态快照 | 每 15 分钟 | 记录资源使用 |
| 日志归档 | 每日 03:30 | 压缩 Nginx 日志 |

---

## 9. 常用运维命令

### 9.1 服务管理

```bash
# 启动 / 停止 / 重启 / 状态
sudo systemctl start forum-api
sudo systemctl stop forum-api
sudo systemctl restart forum-api
sudo systemctl status forum-api

# 启用开机自启
sudo systemctl enable forum-api

# 查看最近重启次数
sudo systemctl show forum-api -p NRestarts
```

### 9.2 日志查看

```bash
# 后端日志
sudo journalctl -u forum-api -f

# Nginx 日志
tail -f /var/log/nginx/forum_access.log
tail -f /var/log/nginx/forum_error.log

# 监控日志
tail -f /var/log/forum/monitor.log
tail -f /var/log/forum/git_pull.log
```

### 9.3 手动更新

```bash
# 完整更新
sudo /opt/forum/scripts/git_pull.sh

# 仅拉取不重启
sudo /opt/forum/scripts/git_pull.sh --dry-run

# 回滚上一版本
sudo /opt/forum/scripts/git_pull.sh --rollback
```

### 9.4 数据库备份

```bash
# 单库备份
sudo mariadb-dump -u forum -pCHANGE_ME forum > /backup/forum_$(date +%F).sql

# 自动定时备份 (crontab)
0 2 * * * root mariadb-dump -u forum -pCHANGE_ME forum | gzip > /backup/forum_$(date +\%F).sql.gz

# 保留最近 7 天
find /backup -name 'forum_*.sql.gz' -mtime +7 -delete
```

### 9.5 系统状态

```bash
# 磁盘
df -h

# 内存
free -m

# CPU
top -c

# 网络连接
ss -tlnp | grep -E '5000|3306|443|80'

# Nginx 连接数
sudo grep 'Worker Connections:' /etc/nginx/nginx.conf
```

---

## 10. 故障排查

### 10.1 服务无法启动

```bash
# 查看详细错误
sudo journalctl -u forum-api -n 100 --no-pager

# 检查端口占用
ss -tlnp | grep 5000

# 检查权限
ls -la /opt/forum/backend
ls -la /etc/forum/

# 检查 env 文件
cat /etc/forum/forum.env
```

### 10.2 Nginx 502 错误

```bash
# 后端是否存活
curl -v http://127.0.0.1:5000/healthz

# Nginx 错误日志
sudo tail -50 /var/log/nginx/forum_error.log

# 服务状态
sudo systemctl status forum-api
```

### 10.3 数据库连接失败

```bash
# 本地连接测试
sudo mariadb -u forum -pCHANGE_ME -h 127.0.0.1 -e "SELECT 1;"

# 检查 MariaDB 状态
sudo systemctl status mariadb

# 检查用户权限
sudo mariadb -e "SHOW GRANTS FOR 'forum'@'127.0.0.1';"
```

### 10.4 监控脚本无法发送邮件

```bash
# 测试 SMTP 连通性
python3 -c "import smtplib; s=smtplib.SMTP_SSL('smtp.example.com', 465, timeout=20); s.login('user@x.com','pwd'); print('OK')"

# 查看告警状态文件
cat /var/lib/forum/monitor/alert_state.json
```

### 10.5 更新失败

```bash
# 查看更新日志
sudo tail -100 /var/log/forum/git_pull.log

# 检查锁文件
ls -la /var/run/forum-update.lock

# 手动清理锁
sudo rm -f /var/run/forum-update.lock

# 回滚
sudo /opt/forum/scripts/git_pull.sh --rollback
```

---

## 11. 安全加固建议

1. **HTTPS 强制跳转**: 部署 Let's Encrypt 证书并启用 HSTS
   ```bash
   sudo dnf install -y certbot python3-certbot-nginx
   sudo certbot --nginx -d forum.example.com
   ```
2. **最小权限原则**: 服务以 `forum` 用户运行，非 root
3. **systemd 沙箱**: 启用 `ProtectSystem=strict`、`NoNewPrivileges` 等指令
4. **定期更新系统包**:
   ```bash
   sudo dnf update -y
   ```
5. **fail2ban**: 限制 SSH 与登录尝试
   ```bash
   sudo dnf install -y fail2ban
   sudo systemctl enable --now fail2ban
   ```
6. **文件权限**: 敏感配置文件 `chmod 600`
7. **网络隔离**: 仅开放 80/443，其余端口走防火墙
8. **定期备份**: 数据库 + 配置 + 代码，保留至少 7 天
9. **审计日志**: 开启 `auditd` 监控关键操作
10. **配置分离**: 敏感信息使用 `EnvironmentFile`，不进入 Git 仓库

---

## 附录: 文件清单

| 文件 | 路径 | 说明 |
| :--- | :--- | :--- |
| `nginx.conf` | `/etc/nginx/conf.d/forum.conf` | Nginx 反向代理配置 |
| `forum-api.service` | `/etc/systemd/system/` | systemd 服务单元 |
| `git_pull.sh` | `/opt/forum/scripts/` | Git 一键更新脚本 |
| `monitor.py` | `/opt/forum/scripts/` | 日志巡检与告警脚本 |
| `crontab.txt` | `/etc/cron.d/forum` | 定时任务配置 |
| `README.md` | `/opt/forum/` | 本部署指南 |

---

> 文档版本: v1.0
> 最后更新: $(date +%F)
