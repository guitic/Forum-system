# 在线论坛系统 V3.0

基于 HTML/JS + Python 的轻量级技术交流论坛，前后端分离架构，完全适配 Rocky Linux 生产环境部署。

- **V1.1**：注册登录 / 发帖 / 评论 / JWT 鉴权 / Markdown 渲染
- **V2.0**：用户中心（昵称、简介、头像、修改密码）
- **V3.0**：帖子详情 + 层级回复（楼中楼，最多 3 层）+ 楼主/Admin 徽标 + 回复级联删除

## 技术栈

| 层次 | 技术选型 |
|------|----------|
| **前端** | 原生 HTML + CSS + JavaScript |
| **Markdown** | marked.js + highlight.js（15 种语言高亮） |
| **后端** | Python 3 + Flask + SQLAlchemy |
| **数据库** | MariaDB / MySQL |
| **认证** | Bcrypt 密码哈希 + JWT（HS256, 24h 过期） |
| **部署** | Nginx + systemd + Crontab 监控 |

## 项目结构

```
Forum system/
├── backend/                    # 后端 API
│   ├── app.py                  # Flask 主入口（应用工厂 + 错误处理）
│   ├── config.py               # 配置（支持环境变量覆盖）
│   ├── models.py               # SQLAlchemy 模型（users/posts/replies）
│   ├── init_db.py              # 数据库初始化脚本（建表 + 管理员 + V3 补列）
│   ├── requirements.txt        # Python 依赖
│   ├── uploads/                # 头像上传目录（V2）
│   ├── tests/
│   │   └── test_reply_hierarchy.py  # V3 层级回复端到端自测（42 项）
│   └── routes/
│       ├── __init__.py
│       ├── auth.py             # 注册 / 登录路由
│       ├── posts.py            # 帖子 CRUD + 回复 + 层级树构建（V3）
│       ├── replies.py          # V3：删除回复（级联删除子孙）
│       └── user.py             # 用户中心路由（V2：资料/密码/头像）
├── frontend/                   # 前端页面
│   ├── index.html              # 首页（帖子列表 + 分页 + 发帖模态框）
│   ├── post.html               # 帖子详情（Markdown 渲染 + 层级回复 V3）
│   ├── auth.html               # 登录/注册页
│   ├── profile.html            # 用户中心页（V2）
│   ├── css/
│   │   └── style.css           # 现代蓝色系响应式样式（V3：层级缩进 + 徽标）
│   └── js/
│       ├── api.js              # API 封装（token + 401 跳转 + XSS 清洗）
│       ├── index.js            # 首页逻辑
│       ├── post.js             # 详情页逻辑（V3：递归回复树 + 内联回复 + 徽标）
│       ├── auth.js             # 鉴权逻辑
│       └── profile.js          # 用户中心逻辑（V2）
├── database/
│   ├── init.sql                # MariaDB 初始化 SQL（建库建表）
│   ├── migration_v2.sql        # V1.1→V2.0 迁移脚本
│   └── migration_v3.sql        # V2.0→V3.0 迁移脚本（回复层级字段）
├── deploy/                     # 生产部署
│   ├── nginx.conf              # Nginx 生产配置
│   ├── forum-api.service       # systemd 服务单元
│   ├── git_pull.sh             # Git 一键更新脚本
│   ├── monitor.py              # 日志巡检 + SMTP 告警
│   ├── crontab.txt             # 定时任务配置
│   └── README.md               # 部署指南
├── 需求文档.md
├── 需求文档-用户中心V2.md      # 用户中心功能需求文档（V2）
├── 需求文档-回复层级V3.md      # 层级回复功能需求文档（V3）
└── README.md
```

## API 接口

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/auth/register` | ❌ | 用户注册 |
| POST | `/api/auth/login` | ❌ | 用户登录，返回 JWT |
| GET | `/api/posts` | ❌ | 帖子列表（分页） |
| GET | `/api/posts/{id}` | ❌ | 帖子详情 + 层级回复树（V3：reply_tree / reply_count） |
| POST | `/api/posts` | ✅ Bearer | 发布新帖 |
| POST | `/api/posts/{id}/replies` | ✅ Bearer | 发布回复（V3：Body 可选 `parent_id` 实现楼中楼） |
| DELETE | `/api/posts/{id}` | ✅ Bearer | 删除帖子（仅作者/管理员） |
| DELETE | `/api/replies/{reply_id}` | ✅ Bearer | 删除回复（V3：级联删除子孙，仅作者/管理员） |
| GET | `/api/user/profile` | ✅ Bearer | 获取个人资料（V2） |
| PUT | `/api/user/profile` | ✅ Bearer | 更新昵称与简介（V2） |
| POST | `/api/user/password` | ✅ Bearer | 修改密码（V2） |
| POST | `/api/user/avatar` | ✅ Bearer | 上传头像（V2） |
| GET | `/uploads/<filename>` | ❌ | 头像静态资源服务（V2） |
| GET | `/health` | ❌ | 健康检查 |

## 快速启动

### 1. 后端环境

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

### 2. 配置数据库

编辑 `backend/config.py` 或设置环境变量：

```bash
export DB_HOST=localhost
export DB_PORT=3306
export DB_USER=forum_user
export DB_PASSWORD=forum_pass
export DB_NAME=forum_db
export SECRET_KEY=your-secret-key-here
```

### 3. 初始化数据库

```bash
# 方式一：执行 SQL 脚本
mysql -u root -p < database/init.sql

# 方式二：使用 Python 脚本（推荐）
cd backend
python init_db.py

# 自定义管理员账号
python init_db.py --admin admin --admin-pass MyStrongPass123

# V2 用户中心迁移（已存在的数据库执行）
mysql -u root -p < database/migration_v2.sql

# V3 回复层级迁移（已存在的数据库执行；本地 SQLite 跑 init_db.py 会自动补列）
mysql -u root -p < database/migration_v3.sql

# V3 层级回复端到端自测（42 项，使用独立测试库，不污染开发库）
cd backend && python tests/test_reply_hierarchy.py
```

### 4. 启动后端

```bash
cd backend
python app.py
# 服务运行在 http://localhost:5000
```

### 5. 访问前端

直接用浏览器打开 `frontend/index.html`，或通过 Nginx 部署。

> **开发模式提示**：前端通过 Nginx 反向代理 `/api/` 到后端，或直接在浏览器中打开 HTML 文件（需配置 CORS）。

## 生产部署

详见 [deploy/README.md](deploy/README.md)

```bash
# 一键部署概要
1. 安装 Rocky Linux 依赖：MariaDB + Python3 + Nginx
2. 执行 database/init.sql 初始化数据库
3. 部署代码到 /opt/forum
4. 配置 Nginx → deploy/nginx.conf
5. 部署 systemd 服务 → deploy/forum-api.service
6. 配置监控 crontab → deploy/crontab.txt
```

## 监控与告警

```bash
# 手动巡检
python deploy/monitor.py

# 配置 SMTP 告警后自动运行（每 5 分钟）
*/5 * * * * cd /opt/forum && python3 deploy/monitor.py >> /var/log/forum-monitor.log 2>&1
```

监控项：Nginx 5xx 状态码、error.log 异常、systemd 服务状态、MariaDB 连接、磁盘/内存/CPU。

## 环境变量参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DB_HOST` | localhost | 数据库主机 |
| `DB_PORT` | 3306 | 数据库端口 |
| `DB_USER` | forum_user | 数据库用户名 |
| `DB_PASSWORD` | forum_pass | 数据库密码 |
| `DB_NAME` | forum_db | 数据库名 |
| `DATABASE_URL` | (自动拼接) | 完整数据库连接 URL |
| `SECRET_KEY` | dev-secret-key | Flask 密钥 |
| `JWT_SECRET_KEY` | = SECRET_KEY | JWT 签名密钥 |
| `JWT_EXPIRATION_HOURS` | 24 | Token 过期时间（小时） |
| `PORT` | 5000 | 后端监听端口 |
| `HOST` | 0.0.0.0 | 后端监听地址 |
| `FLASK_DEBUG` | 0 | 调试模式开关 |
| `UPLOAD_DIR` | backend/uploads | 头像上传目录（V2） |
| `MAX_AVATAR_SIZE` | 2097152 | 头像最大字节数（V2） |

## 安全特性

- Bcrypt 密码哈希（密码 ≥8 字符，截断 72 字节）
- JWT 鉴权（HS256，24h 过期，自动刷新）
- XSS 白名单清洗（阻止 javascript:/data: 协议）
- 用户名枚举防护（登录错误信息统一）
- 密码强度要求（字母+数字+特殊字符）
- 头像上传安全校验（MIME+扩展名+文件大小 2MB+UUID文件名）
- 目录遍历防护（上传文件路径校验）
- Nginx 安全头（HSTS/CSP/X-Frame-Options/XSS-Protection）
- systemd 沙箱加固（ProtectSystem=strict/NoNewPrivileges）
- 请求限速（Nginx limit_req）
- 回复层级校验（V3：`parent_id` 必须归属同一帖子，跨帖挂接返回 403）
- 回复删除权限（V3：仅作者或管理员，级联删除子树避免孤立引用）

---

> **许可证**：本项目为学习/演示用途，按需求文档 V1.1 / V2.0 / V3.0 实现。
