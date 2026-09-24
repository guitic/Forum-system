# 在线论坛系统 — Code Wiki

> 基于原生 HTML/JS + Python Flask 的轻量级技术交流论坛,前后端分离架构,适配 Rocky Linux 生产环境部署。
> 版本:V3.0(注册登录 / 发帖 / 用户中心 / 层级回复 / Markdown 渲染)

---

## 目录

1. [项目概览](#1-项目概览)
2. [整体架构](#2-整体架构)
3. [目录结构](#3-目录结构)
4. [核心模块职责](#4-核心模块职责)
5. [关键类与函数说明](#5-关键类与函数说明)
6. [数据模型](#6-数据模型)
7. [API 接口清单](#7-api-接口清单)
8. [依赖关系](#8-依赖关系)
9. [运行方式](#9-运行方式)
10. [配置项参考](#10-配置项参考)
11. [安全特性](#11-安全特性)
12. [测试](#12-测试)
13. [已知问题与勘误](#13-已知问题与勘误)
14. [文档与规范参考](#14-文档与规范参考)

---

## 1. 项目概览

| 维度 | 说明 |
| :--- | :--- |
| 项目类型 | 前后端分离的轻量级论坛系统 |
| 后端语言 | Python 3.9+ |
| 后端框架 | Flask 3.0.3 + Flask-SQLAlchemy 3.1.1 + SQLAlchemy 2.0.35 |
| 前端技术 | 原生 HTML + CSS + JavaScript(无构建工具) |
| 数据库 | MariaDB / MySQL(默认),SQLite(本地调试兼容) |
| 认证机制 | Bcrypt 密码哈希 + JWT(HS256,默认 24h 过期) |
| 部署方式 | Nginx 反向代理 + systemd 服务 + Crontab 监控 |
| 版本演进 | V1.1 基础论坛 → V2.0 用户中心 → V3.0 层级回复 |

**核心能力**:用户注册/登录、发帖、Markdown 渲染(15 种语言高亮)、用户中心(昵称/简介/头像/密码)、最多 3 层的楼中楼回复、楼主/Admin 徽标、回复级联删除、健康检查、SMTP 告警监控。

---

## 2. 整体架构

### 2.1 分层架构

```
┌──────────────────────────────────────────────────────────┐
│                       浏览器(前端)                       │
│  index.html / post.html / auth.html / profile.html      │
│  + js/api.js(请求封装 + XSS 清洗) + js/{index,post,...} │
└────────────────────────────┬─────────────────────────────┘
                             │ HTTP + Bearer Token
                             ▼
┌──────────────────────────────────────────────────────────┐
│                    Nginx(反向代理 + 静态托管)             │
│  - 前端静态文件直出(/、/css、/js)                        │
│  - /api/、/uploads 反代至 Flask 后端                     │
│  - 安全头(HSTS/CSP/X-Frame)、限速、Gzip                  │
└────────────────────────────┬─────────────────────────────┘
                             │ proxy_pass http://127.0.0.1:5000
                             ▼
┌──────────────────────────────────────────────────────────┐
│                Flask 应用(app.py 应用工厂)                │
│  ┌────────┐  ┌────────┐  ┌──────────┐  ┌──────────┐     │
│  │auth_bp │  │posts_bp│  │replies_bp│  │ user_bp  │     │
│  │/api/auth│ │/api/posts│ │/api/replies│ │/api/user │     │
│  └────────┘  └────────┘  └──────────┘  └──────────┘     │
│       │           │            │             │            │
│       └───────────┴────────────┴─────────────┘            │
│                   require_auth 装饰器(JWT 校验)            │
│                         │                                 │
│             SQLAlchemy ORM(models.py)                     │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│            MariaDB / MySQL / SQLite 数据库                │
│  users · posts · replies(V3:parent_id/root_id 层级字段)   │
└──────────────────────────────────────────────────────────┘
```

### 2.2 请求生命周期

1. 浏览器加载 HTML,由 `js/api.js` 统一封装请求(注入 Bearer Token,15s 超时,401 自动跳转 `auth.html`)。
2. Nginx 接收请求,静态资源直接返回,`/api/*` 与 `/uploads/*` 反代到 Flask。
3. Flask 在 `create_app()` 中注册蓝图、错误处理、CORS、健康检查、头像静态服务。
4. 命中鉴权接口的路由经 `require_auth` 解析 JWT 并注入 `current_user`。
5. 路由调用 SQLAlchemy ORM 读写数据库,经 `to_dict()` 序列化为 JSON 返回。
6. 帖子详情接口调用 `_build_reply_tree()` 构建 V3 层级回复树。

---

## 3. 目录结构

```
Forum system/
├── backend/                         # 后端 API
│   ├── app.py                       # Flask 主入口(应用工厂 + 错误处理 + 静态服务)
│   ├── config.py                    # 配置类(支持环境变量覆盖)
│   ├── models.py                    # SQLAlchemy 模型(User / Post / Reply)
│   ├── init_db.py                   # 数据库初始化脚本(建表 + 管理员 + V3 补列)
│   ├── requirements.txt             # Python 依赖
│   ├── uploads/                     # 头像上传目录(V2,运行期生成)
│   ├── tests/
│   │   └── test_reply_hierarchy.py  # V3 层级回复端到端自测(42 项)
│   └── routes/
│       ├── __init__.py              # 蓝图包标记
│       ├── auth.py                  # 注册 / 登录路由
│       ├── posts.py                 # 帖子 CRUD + 回复 + 层级树构建(V3)
│       ├── replies.py               # V3 删除回复(级联删除子孙)
│       └── user.py                  # 用户中心路由(V2:资料/密码/头像)
├── frontend/                        # 前端页面
│   ├── index.html                   # 首页(帖子列表 + 分页 + 发帖模态框)
│   ├── post.html                     # 帖子详情(Markdown 渲染 + 层级回复 V3)
│   ├── auth.html                     # 登录/注册页
│   ├── profile.html                  # 用户中心页(V2)
│   ├── css/style.css                 # 现代蓝色系响应式样式
│   └── js/
│       ├── api.js                    # API 封装(token + 401 跳转 + XSS 清洗)
│       ├── index.js                  # 首页逻辑
│       ├── post.js                   # 详情页逻辑(V3 递归回复树 + 内联回复 + 徽标)
│       ├── auth.js                   # 鉴权逻辑
│       └── profile.js                # 用户中心逻辑(V2)
├── database/                        # 数据库脚本
│   ├── init.sql                      # MariaDB 初始化(建库 + 建表)
│   ├── migration_v2.sql              # V1.1 → V2.0 迁移
│   └── migration_v3.sql              # V2.0 → V3.0 迁移(回复层级字段)
├── deploy/                          # 生产部署
│   ├── nginx.conf                    # Nginx 生产配置
│   ├── forum-api.service             # systemd 服务单元(沙箱加固)
│   ├── git_pull.sh                   # Git 一键更新脚本
│   ├── monitor.py                    # 日志巡检 + SMTP 告警
│   ├── crontab.txt                   # 定时任务配置
│   └── README.md                     # 部署指南
├── .trae/rules/git-commit-message.md # Trae IDE 提交信息规则(只读)
├── .claude/rules/graphflow.md        # Claude Code GraphFlow 规则(只读)
├── AGENTS.md                         # AI 协作规范(GraphFlow)
├── CODE_WIKI.md                     # 本文档(结构化代码 Wiki)
├── README.md                         # 项目总览与快速启动
└── 需求文档.md                       # V1.1 / V2.0 / V3.0 合并需求
```

---

## 4. 核心模块职责

### 4.1 后端

| 文件 | 模块定位 | 主要职责 |
| :--- | :--- | :--- |
| [app.py](backend/app.py) | 应用入口 | 应用工厂 `create_app()`;初始化数据库与上传目录;注册 4 个蓝图;统一 404/405/500/Exception 错误处理;CORS 中间件;健康检查 `/health`;头像静态服务 `/uploads/<filename>`(含目录遍历防护);根路径 SPA 回退 |
| [config.py](backend/config.py) | 配置中心 | `Config` 类集中管理数据库、JWT、Flask、业务、上传、回复层级等配置;所有项支持环境变量覆盖,提供默认实例 `config` |
| [models.py](backend/models.py) | 数据层 | SQLAlchemy 实例 `db`;定义 `User` / `Post` / `Reply` 三张表与关联关系;`to_dict()` 序列化方法;`User.display_name` 显示名优先级(昵称 > 用户名) |
| [init_db.py](backend/init_db.py) | 数据库初始化 | `init_database()` 建表 + 创建管理员;`ensure_reply_hierarchy_columns()` 幂等补齐 V3 层级字段与索引(适配 SQLite 不会自动加列的场景);支持 `--drop` / `--admin` / `--admin-pass` 参数 |
| [routes/auth.py](backend/routes/auth.py) | 认证蓝图 | `POST /api/auth/register` 注册(用户名 8-14 位 + 字母数字混合校验);`POST /api/auth/login` 登录签发 JWT;bcrypt 72 字节截断;统一错误信息防用户名枚举 |
| [routes/posts.py](backend/routes/posts.py) | 帖子蓝图 | `require_auth` 鉴权装饰器;帖子 CRUD;V3 层级回复核心算法(`_reply_chain_info` / `_prepare_reply_structure` / `_build_reply_tree`);分页参数边界收敛 |
| [routes/replies.py](backend/routes/replies.py) | 回复蓝图 | `DELETE /api/replies/{id}` 级联删除子孙回复;BFS 遍历避免递归栈溢出;仅作者/管理员可删 |
| [routes/user.py](backend/routes/user.py) | 用户中心蓝图 | `GET/PUT /api/user/profile` 资料读写;`POST /api/user/password` 改密;`POST /api/user/avatar` 头像上传(MIME + 扩展名 + 大小 + UUID 文件名) |
| [tests/test_reply_hierarchy.py](backend/tests/test_reply_hierarchy.py) | 端到端自测 | 独立 SQLite 测试库;通过 Flask `test_client` 走完整 HTTP 链路;覆盖 42 项 V3 场景 |

### 4.2 前端

| 文件 | 职责 |
| :--- | :--- |
| `js/api.js` | 全局 `ForumAPI` 命名空间;fetch 封装(Token 注入、15s 超时、401 跳转);白名单 XSS 清洗(过滤危险标签/属性/协议);时间友好格式化;DOM 工具 `el` / 文本摘要 `excerpt` / 头像字母 `avatarChar` |
| `js/index.js` | 首页:帖子列表分页、发帖模态框、用户徽章下拉 |
| `js/post.js` | 详情页:Markdown 渲染、递归构建层级回复树、内联回复、楼主/Admin 徽标 |
| `js/auth.js` | 登录/注册表单、Token 持久化、`redirect` 参数回跳 |
| `js/profile.js` | 用户中心:资料编辑、密码修改、头像上传预览 |
| `css/style.css` | 现代蓝色系响应式样式,V3 新增层级缩进与徽标样式 |
| `*.html` | 4 个页面壳层,引入对应 JS/CSS |

### 4.3 部署与运维

| 文件 | 职责 |
| :--- | :--- |
| `deploy/nginx.conf` | Nginx 反代 + 静态托管;TLS 1.2/1.3;安全响应头;Gzip;5MB 上传限制;限速 |
| `deploy/forum-api.service` | systemd 服务单元;专用 `forum` 用户;沙箱加固(ProtectSystem=strict、NoNewPrivileges、SystemCallFilter 等);Gunicorn 启动;CPU/内存/IO 限制;崩溃自动重启 |
| `deploy/git_pull.sh` | Git 一键拉取 + 依赖同步 + 服务重启;支持 `--dry-run` / `--rollback` |
| `deploy/monitor.py` | 日志巡检:Nginx 5xx、error.log、systemd 状态、MariaDB 健康、磁盘/内存/CPU;SMTP 告警;支持 `--dry-run` / `--json` / `--verbose` / `--config` |
| `deploy/crontab.txt` | 定时任务:5 分钟监控、03:00 Git 更新、04:00 日志清理、15 分钟状态快照 |
| `deploy/README.md` | Rocky Linux 部署完整指南 |

---

## 5. 关键类与函数说明

### 5.1 [models.py](backend/models.py) — 数据模型

#### `User(db.Model)` — 用户表 `users`

| 字段/方法 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | Integer PK | 自增主键 |
| `username` | String(64) unique | 登录用户名 |
| `password_hash` | String(128) | bcrypt 哈希(72 字节截断) |
| `role` | String(16) | `user` / `admin` |
| `nickname` | String(50) | 显示昵称(V2,可空) |
| `bio` | Text | 个人简介(≤200 字符) |
| `avatar_url` | String(255) | 头像相对路径 |
| `created_at` | DateTime | 注册时间(UTC) |
| `posts` | relationship | 反向关联 Post(`author`) |
| `replies` | relationship | 反向关联 Reply(`author`) |
| `display_name` | property | 优先返回 nickname,空则回退 username |
| `to_dict()` | method | 序列化为 JSON 安全字典(不含 password_hash) |

#### `Post(db.Model)` — 主贴表 `posts`

| 字段/方法 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | Integer PK | 自增主键 |
| `user_id` | FK→users.id | 发帖人外键 |
| `title` | String(200) | 标题 |
| `content` | Text | Markdown 源码 |
| `created_at` | DateTime | 发布时间(UTC) |
| `replies` | relationship | 关联回复(按 `created_at` 升序) |
| `to_dict(include_replies=False)` | method | 序列化;`include_replies=True` 时附带 replies 列表 |

#### `Reply(db.Model)` — 回复表 `replies`(V3 层级)

| 字段/方法 | 类型 | 说明 |
| :--- | :--- | :--- |
| `id` | Integer PK | 自增主键 |
| `post_id` | FK→posts.id | 所属帖子(index) |
| `user_id` | FK→users.id | 回复人(index) |
| `content` | Text | 回复内容 |
| `parent_id` | FK→replies.id, nullable | 直接父回复;NULL = 一级回复(V3) |
| `root_id` | FK→replies.id, nullable | 所属话题根回复;一级回复存 NULL,读取时由后端解析为自身 id(V3) |
| `created_at` | DateTime | 回复时间(UTC) |
| `children` | relationship | 自引用直接子回复(`primaryjoin` 显式标注外键侧,避免歧义) |
| `to_dict()` | method | 序列化含层级字段,但不含 depth/children 等派生值 |

> **设计要点**:`depth` 不落库,由后端按 `parent_id` 链实时计算;超过 `MAX_REPLY_DEPTH` 的回复在展示阶段被拉平到一级祖先下作为同级,数据库保留真实父子关系。

### 5.2 [app.py](backend/app.py) — 应用工厂

#### `create_app(config_obj=None) -> Flask`

应用工厂主入口,完成以下初始化:

1. 创建 Flask 实例,`static_folder` 指向 `frontend/`,`static_url_path=""`(同根服务消除跨域);
2. 加载 `config`(默认实例或传入对象),映射 `DATABASE_URL` → `SQLALCHEMY_DATABASE_URI`;
3. `db.init_app(app)` 绑定 SQLAlchemy;
4. `os.makedirs(UPLOAD_DIR, exist_ok=True)` 创建上传目录,`OSError` 不阻断启动(只记日志);
5. 注册 4 个蓝图:`auth_bp` / `posts_bp` / `replies_bp` / `user_bp`;
6. 注册 404/405/500/Exception 统一错误处理器(API 路由返回 JSON,前端路由回退 `index.html`);
7. 注册 `after_request` 钩子添加 CORS 头,处理 OPTIONS 预检;
8. 注册 `/health` 健康检查(执行 `SELECT 1` 探测数据库);
9. 注册 `/uploads/<filename>` 头像静态服务(目录遍历防护:`os.path.realpath` + `os.sep` 边界检查)。

模块末尾创建全局实例 `app = create_app()`,`__main__` 入口从环境变量读取 `PORT`/`HOST`/`FLASK_DEBUG`,执行 `db.create_all()` 后 `app.run()`。

### 5.3 [routes/posts.py](backend/routes/posts.py) — 鉴权与层级回复算法

#### `require_auth(fn)` — 鉴权装饰器

- 解析 `Authorization: Bearer <token>` 头;
- `jwt.decode` 校验签名与过期(HS256);
- 取 `sub` 字段按 id 查 `User`,失败返回 401;
- 将 `current_user` 注入被装饰函数的 kwargs。

#### 层级回复核心算法

```text
_create_reply        →  写入 parent_id + root_id(继承父话题根)
        ↓
get_post             →  查询所有 replies(按 created_at 升序)
        ↓
_prepare_reply_structure(replies, max_depth)
  ├ _reply_chain_info(reply, index)   回溯 parent_id 链,返回(真实深度, 话题根 id)
  ├ display_parent 决策              未超 max_depth → 真实父;超限 → 上提为同级
  ├ depth_map 迭代求解                父节点必先于子节点出现
  ├ root_map                         一级回复 id 取自身
  └ children_map                     展示父 → [展示子 id]
        ↓
_reply_node_dict(...)                 序列化为 JSON 节点(含 depth/root_id/is_author/reply_count)
        ↓
_build_reply_tree(...)                拼装嵌套树 tree + 扁平列表 flat
```

| 函数 | 入参 | 输出 | 用途 |
| :--- | :--- | :--- | :--- |
| `_reply_chain_info(reply, index)` | 单条 reply + id→Reply 索引 | `(真实深度, 话题根 id)` | 沿 `parent_id` 回溯到一级回复,含环保护与悬空引用保护 |
| `_prepare_reply_structure(replies, max_depth)` | 全部 replies + 最大深度 | `(index, depth_map, root_map, children_map, display_parent)` | 预计算展示结构,溢出深度上提到祖先同级 |
| `_reply_node_dict(...)` | reply + 5 张映射 + 帖子作者 id | dict | 单条序列化(含 `is_author` 楼主标记、`reply_count`、`reply_to_display_name`) |
| `_build_reply_tree(replies, post_author_id, max_depth)` | 全部 replies + 帖子作者 + 最大深度 | `(tree, flat)` | 嵌套树 + 扁平列表(向后兼容) |
| `_reply_node(reply, max_depth, post_author_id)` | 单条新 reply | dict | 新建回复响应体;内部重查全部 siblings 以重建索引 |

#### `list_posts()`

- `GET /api/posts`,`page`/`limit` 经 `_get_pagination()` 边界收敛(`limit` ∈ [1, MAX_PAGE_SIZE]);
- 用 SQLAlchemy 子查询统计每个帖子的回复数(含嵌套楼中楼,与详情接口口径一致);
- 联表 `User` 一次性取 `username/nickname/avatar_url/role`;
- 返回 `{posts, total, page, limit, pages}`。

#### `get_post(post_id)`

- `GET /api/posts/{id}`,返回帖子详情 + `replies`(扁平)+ `reply_tree`(嵌套)+ `reply_count` + `view_count`;
- replies 按 `created_at` 升序(保证父先于子出现);
- 调用 `_build_reply_tree(replies, post.user_id, config.MAX_REPLY_DEPTH)`;
- **浏览量统计(模块 3)**:`_reserve_view(post_id)` 按"登录用户 id → XFF 首跳/IP"识别访客,
  30 分钟窗口内同一访客只计一次(`VIEW_DEDUP_WINDOW_SECONDS`,进程内 TTL 表 + 锁,超阈值顺带清理);
  通过后执行原子语句 `UPDATE posts SET view_count = view_count + 1`(独立事务,严禁读-改-写),
  与详情查询分离;写入失败回滚并 `_release_view` 释放占位,详情仍正常返回。

#### `create_post(current_user)`、`create_reply(post_id, current_user)`、`delete_post(post_id, current_user)`

- 创建帖子:校验标题非空且 ≤200 字符、内容非空;
- 创建回复:校验 `parent_id` 必须存在且属于同一帖子(跨帖挂接返回 403),`root_id` 自动继承父话题根或父 id;
- 删除帖子:仅作者或 admin,先删该帖全部 replies 再删 post(级联)。

### 5.4 [routes/replies.py](backend/routes/replies.py)

#### `_collect_descendant_ids(root_id, post_id)`

- 收集 `root_id` 及所有子孙 id;
- **单次查询**该帖全部回复的 `(id, parent_id)`,在内存中构建父子映射后迭代 BFS,
  查询次数与嵌套深度无关(避免逐层 `IN` 查询的 N+1 问题);
- 含环保护(`if kid not in to_delete` 判断)。

#### `delete_reply(reply_id, current_user)`

- `DELETE /api/replies/{id}`,仅作者或 admin;
- 一次性 `Reply.id.in_(...)` 删除整棵子树,避免孤立引用;
- 返回 `{message, deleted_count}`。

### 5.5 [routes/auth.py](backend/routes/auth.py)

| 函数 | 说明 |
| :--- | :--- |
| `_hash_password(plaintext)` | bcrypt 哈希,72 字节截断 |
| `_verify_password(plaintext, hashed)` | bcrypt 校验,异常返回 False |
| `register()` | `POST /api/auth/register`;用户名 8-14 位、首位小写字母、字母数字混合;密码 ≥8 位、字母+数字+特殊字符;大小写不敏感冲突检测 |
| `login()` | `POST /api/auth/login`;统一错误信息防用户名枚举;签发 JWT(`sub`/`username`/`role`/`iat`/`exp`) |

### 5.6 [routes/user.py](backend/routes/user.py)

| 函数 | 说明 |
| :--- | :--- |
| `_validate_password_strength(password)` | 密码强度校验,返回错误信息或 None |
| `get_profile(current_user)` | `GET /api/user/profile`,返回 `User.to_dict()` |
| `update_profile(current_user)` | `PUT /api/user/profile`,只改传入字段(nickname/bio) |
| `change_password(current_user)` | `POST /api/user/password`,验证旧密码 + 强度 + 新旧不同 |
| `upload_avatar(current_user)` | `POST /api/user/avatar`,multipart/form-data;扩展名+MIME+大小(2MB)+UUID 文件名;成功后清理旧头像;DB 回滚时回退已上传文件 |

### 5.7 [init_db.py](backend/init_db.py)

| 函数 | 说明 |
| :--- | :--- |
| `ensure_reply_hierarchy_columns(verbose=True)` | 幂等补齐 `parent_id` / `root_id` 列与 `idx_reply_parent` / `idx_reply_root` 索引;适配 SQLite 不会自动加列的场景 |
| `ensure_updated_at_columns(verbose=True)` | 幂等补齐 posts / replies 的 `updated_at` 列(模块 1) |
| `ensure_view_count_column(verbose=True)` | 幂等补齐 posts 的 `view_count` 列,默认 0(模块 3) |
| `init_database(drop, admin_user, admin_pass)` | `drop_all()` + `create_all()` + 调用上面巡检函数 + 创建管理员账号;打印统计 |

### 5.8 [frontend/js/api.js](frontend/js/api.js) — 前端核心

| 方法 | 说明 |
| :--- | :--- |
| `ForumAPI.getToken/setToken/clearToken/isLoggedIn` | localStorage Token 管理(key=`token`) |
| `ForumAPI.request(path, opts)` | 通用 fetch 封装;15s 超时;Bearer 注入;JSON 序列化;401 自动跳 `auth.html?redirect=...`;错误对象带 `.status`/`.data`/`.network` |
| `ForumAPI.login/register/getPosts/getPost/createPost/createReply/deleteReply/deletePost` | 各业务接口的便捷封装 |
| `ForumAPI.getProfile/updateProfile/changePassword/uploadAvatar` | V2 用户中心接口;`uploadAvatar` 走 FormData 不走通用封装(30s 超时) |
| `ForumAPI.formatTime/formatDateTime` | 友好时间("刚刚"/"3 分钟前"/"2 天前")与完整时间格式化 |
| `ForumAPI.sanitize(html)` | **白名单 XSS 清洗**:DOMParser 解析后递归遍历,危险标签(script/style/iframe 等)整删,非白名单标签解包保留文本,过滤 `on*`/`style`/`formaction` 属性,校验 `href`/`src` 协议(阻止 javascript:/data:),`a` 标签自动加 `rel="noopener noreferrer"`;异常时降级为纯文本转义 |
| `ForumAPI.el(tag, props, children)` | DOM 元素创建工具 |
| `ForumAPI.excerpt(text, max)` | Markdown 文本摘要 |
| `ForumAPI.avatarChar(name)` | 头像首字母占位(中英文兼容) |
| `ForumAPI.setupBadgeDropdown(wrap)` | 用户徽章 hover 下拉(带 250ms 隐藏延迟) |

---

## 6. 数据模型

### 6.1 ER 关系

```
users (1) ──── (N) posts  (1) ──── (N) replies
  │                          │
  └────── (N) replies ───────┘
                  │
                  └── self-ref (parent_id, root_id)
```

- `users.id ← posts.user_id`:外键 `ON DELETE CASCADE`;
- `users.id ← replies.user_id`:外键 `ON DELETE CASCADE`;
- `posts.id ← replies.post_id`:外键 `ON DELETE CASCADE`;
- `replies.id ← replies.parent_id`:自引用,直接父回复;
- `replies.id ← replies.root_id`:自引用,所属话题根回复。

### 6.2 表结构(以 [database/init.sql](database/init.sql) 为准)

| 表 | 主要字段 | 索引 |
| :--- | :--- | :--- |
| `users` | id, username(unique), password_hash, role, nickname, bio, avatar_url, created_at | uk_username, idx_username |
| `posts` | id, user_id(FK), title, content, created_at, updated_at, view_count | fk_posts_user |
| `replies` | id, post_id(FK), user_id(FK), content, parent_id(FK), root_id(FK), created_at, updated_at | fk_replies_post, fk_replies_user, idx_post_id, idx_reply_parent, idx_reply_root |

`updated_at` 为模块 1(编辑功能)新增字段,`DATETIME` 允许为 `NULL`,`NULL` 表示内容从未被编辑;迁移脚本见 [database/migration_edit.sql](database/migration_edit.sql)。

`view_count` 为模块 3(浏览量统计)新增字段,`INT NOT NULL DEFAULT 0`,由详情接口原子自增维护(30 分钟同用户/IP 去重);迁移脚本见 [database/migration_views.sql](database/migration_views.sql)。

外键级联策略:posts→replies 为 `ON DELETE CASCADE`;replies 自引用 `parent_id` 为 `CASCADE`,`root_id` 为 `SET NULL`。

---

## 7. API 接口清单

| 方法 | 路径 | 鉴权 | 说明 |
| :--- | :--- | :--- | :--- |
| POST | `/api/auth/register` | ❌ | 用户注册(8-14 位用户名 + 字母数字特殊字符密码) |
| POST | `/api/auth/login` | ❌ | 用户登录,返回 JWT + 用户基本信息 |
| GET | `/api/posts?page=&limit=` | ❌ | 帖子列表分页(默认 20 条/页,最大 100) |
| GET | `/api/posts/{id}` | ❌ | 帖子详情 + `replies`(扁平)+ `reply_tree`(嵌套)+ `reply_count` + `view_count`;同一用户/IP 30 分钟内只计一次浏览 |
| POST | `/api/posts` | ✅ Bearer | 发布新帖 |
| PUT | `/api/posts/{id}` | ✅ Bearer | 编辑帖子(仅作者/admin);Body `{title, content}`;写入 `updated_at`,返回更新后的完整帖子 |
| POST | `/api/posts/{id}/replies` | ✅ Bearer | 发布回复;Body 可选 `parent_id` 实现楼中楼 |
| DELETE | `/api/posts/{id}` | ✅ Bearer | 删除帖子(仅作者/admin),级联删 replies |
| PUT | `/api/replies/{reply_id}` | ✅ Bearer | 编辑回复(仅作者/admin);Body `{content}`;写入 `updated_at`,返回更新后的完整回复节点 |
| DELETE | `/api/replies/{reply_id}` | ✅ Bearer | 删除回复(仅作者/admin),级联删子孙 |
| GET | `/api/user/profile` | ✅ Bearer | 获取个人资料 |
| PUT | `/api/user/profile` | ✅ Bearer | 更新昵称与简介 |
| POST | `/api/user/password` | ✅ Bearer | 修改密码 |
| POST | `/api/user/avatar` | ✅ Bearer | 上传头像(multipart/form-data) |
| GET | `/uploads/<filename>` | ❌ | 头像静态资源服务(目录遍历防护) |
| GET | `/health` | ❌ | 健康检查(执行 `SELECT 1` 探测数据库) |

### 7.1 关键请求/响应示例

**登录响应**(`POST /api/auth/login`):

```json
{
  "token": "eyJ...",
  "username": "aliceuser003",
  "nickname": "Alice",
  "display_name": "Alice",
  "avatar_url": "/uploads/avatars/abc.png",
  "role": "user"
}
```

**帖子详情响应**(`GET /api/posts/{id}`):

```json
{
  "id": 1,
  "user_id": 1,
  "username": "lzuser0001",
  "display_name": "LZ",
  "title": "...",
  "content": "# Hello\n\n内容",
  "created_at": "2026-09-22T10:00:00+00:00",
  "updated_at": "2026-09-22T13:08:15+00:00",
  "view_count": 42,
  "reply_count": 5,
  "reply_tree": [
    {
      "id": 10,
      "depth": 0,
      "root_id": 10,
      "parent_id": null,
      "is_author": true,
      "updated_at": null,
      "reply_count": 2,
      "children": [
        { "id": 11, "depth": 1, "parent_id": 10, "root_id": 10, "children": [ /* ... */ ] }
      ]
    }
  ],
  "replies": [ /* 扁平列表,字段同 tree 节点 */ ]
}
```

> `updated_at` 为 `null` 表示内容从未编辑;非空时前端在内容下方显示「编辑于 X 前」灰色小字。

> `view_count` 为本次请求返回时的最新浏览量(整数)。浏览量在详情接口内原子自增,
> 同一登录用户(未登录按 IP,反代环境取 `X-Forwarded-For` 首跳)30 分钟内重复访问只计一次;
> 前端在 meta 栏显示「👁 N 浏览」,数字经千位分隔符格式化(如 `1,234`),数据到达前显示 `--` 占位。

**编辑帖子响应**(`PUT /api/posts/{id}`):

```json
{
  "message": "编辑成功",
  "id": 1,
  "user_id": 1,
  "username": "lzuser0001",
  "display_name": "LZ",
  "title": "新标题",
  "content": "新内容",
  "created_at": "2026-09-22T10:00:00+00:00",
  "updated_at": "2026-09-22T13:08:15+00:00"
}
```

**编辑回复响应**(`PUT /api/replies/{reply_id}`):返回更新后的完整回复节点,字段同 `reply_tree` 节点(含 `depth` / `parent_id` / `root_id` / `updated_at`)。

错误响应:未登录 `401`;非作者且非管理员 `403`;标题/内容为空或超长 `400`;资源不存在 `404`。

---

## 8. 依赖关系

### 8.1 后端 Python 依赖([requirements.txt](backend/requirements.txt))

| 包 | 版本 | 用途 |
| :--- | :--- | :--- |
| Flask | 3.0.3 | Web 框架,蓝图、错误处理、静态服务、CORS |
| SQLAlchemy | 2.0.35 | ORM 核心 |
| Flask-SQLAlchemy | 3.1.1 | Flask 与 SQLAlchemy 集成 |
| PyJWT | 2.9.0 | JWT 签发与校验 |
| bcrypt | 4.2.1 | 密码哈希 |
| mysql-connector-python | 8.4.0 | MySQL/MariaDB 驱动 |
| python-dotenv | 1.0.1 | `.env` 文件加载(可选) |
| Werkzeug | 3.0.3 | Flask 底层依赖,`send_from_directory` 等 |

### 8.2 前端依赖(CDN,无构建工具)

- `marked.js` — Markdown 渲染;
- `highlight.js` — 15 种语言代码高亮;
- 原生 `fetch` / `localStorage` / `DOMParser` 浏览器 API。

### 8.3 模块依赖关系

```text
app.py
 ├─ config.config              (配置)
 ├─ models.db / User / Post / Reply   (ORM)
 └─ routes/
     ├─ auth.py    → config, models(User, db), bcrypt, jwt
     ├─ posts.py   → config, models(Post, Reply, User, db), jwt, sqlalchemy
     │              ↑ 定义 require_auth 装饰器(被 replies.py、user.py 复用)
     ├─ replies.py → models(Reply, db), routes.posts.require_auth
     └─ user.py    → config, models(User, db), bcrypt, routes.posts.require_auth

init_db.py
 ├─ app.app        (应用实例)
 ├─ config.config  (配置)
 ├─ models.User / db / Post / Reply
 └─ bcrypt, sqlalchemy.inspect / text

frontend/js/api.js    暴露 window.ForumAPI
 ├─ js/index.js     调用 ForumAPI.getPosts / createPost
 ├─ js/post.js      调用 ForumAPI.getPost / createReply / deleteReply / deletePost
 ├─ js/auth.js     调用 ForumAPI.login / register
 └─ js/profile.js  调用 ForumAPI.getProfile / updateProfile / changePassword / uploadAvatar
```

### 8.4 生产部署依赖

| 组件 | 用途 |
| :--- | :--- |
| Gunicorn | WSGI 服务器(systemd 中 `--workers 2 --threads 4 --worker-class gthread`) |
| Nginx 1.20+ | 反向代理 + 静态托管 + 安全头 + 限速 |
| MariaDB | 数据库 |
| systemd | 服务管理与沙箱加固 |
| cron | 定时任务:监控、Git 更新、日志清理 |
| SMTP 服务 | `monitor.py` 告警邮件发送 |

---

## 9. 运行方式

### 9.1 本地开发

```bash
# 1. 后端环境
cd backend
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. 配置数据库(可选,默认 SQLite 走 init_db.py 即可)
export DATABASE_URL=sqlite:///./forum.db   # 快速调试
# 或连接 MariaDB
export DB_HOST=localhost DB_PORT=3306 DB_USER=forum_user DB_PASSWORD=forum_pass DB_NAME=forum_db
export SECRET_KEY=your-secret-key-here

# 3. 初始化数据库(建表 + 创建管理员 admin/admin123)
python init_db.py
# 自定义管理员
python init_db.py --admin admin --admin-pass MyStrongPass123
# 危险:删除所有表后重建
python init_db.py --drop

# 4. 启动后端(开发模式)
python app.py
# 服务运行在 http://0.0.0.0:5000,启动时自动 db.create_all()

# 5. 访问前端
# Flask 已将 static_folder 指向 frontend/,直接打开 http://localhost:5000/ 即可
# 也可直接用浏览器打开 frontend/index.html(需 CORS,app.py 已默认开启 Access-Control-Allow-Origin: *)

# 6. V3 层级回复端到端自测(独立测试库,不污染开发库)
cd backend && python tests/test_reply_hierarchy.py
```

### 9.2 生产部署(Rocky Linux)

详见 [deploy/README.md](deploy/README.md)。一键部署概要:

1. 安装系统依赖:`dnf install -y mariadb mariadb-server python3 python3-pip nginx cronie`;
2. 初始化数据库:`mysql -u root -p < database/init.sql` 或 `python init_db.py`;
3. 部署代码到 `/opt/forum`;
4. 配置 Nginx:`sudo cp deploy/nginx.conf /etc/nginx/conf.d/forum.conf`;
5. 部署 systemd:`sudo cp deploy/forum-api.service /etc/systemd/system/` + `systemctl enable --now forum-api`;
6. 配置监控 crontab:`sudo cp deploy/crontab.txt /etc/cron.d/forum`;
7. 填写 `/etc/forum/forum.env`(数据库密码、SECRET_KEY 等)。

### 9.3 启动入口说明

| 入口 | 命令 | 适用场景 |
| :--- | :--- | :--- |
| Flask 开发服务器 | `python app.py` | 本地开发,自动 `db.create_all()` |
| Gunicorn + systemd | `gunicorn --bind 127.0.0.1:5000 --workers 2 --threads 4 app:app` | 生产 |
| 初始化脚本 | `python init_db.py [--drop] [--admin X --admin-pass Y]` | 建表 + 创建管理员 |
| 自测脚本 | `python tests/test_reply_hierarchy.py` | V3 层级回复 42 项端到端验证 |

环境变量 `PORT`(默认 5000)、`HOST`(默认 0.0.0.0)、`FLASK_DEBUG`(默认 0)控制开发服务器;`DATABASE_URL`、`SECRET_KEY`、`JWT_SECRET_KEY` 等控制运行时行为。

---

## 10. 配置项参考

[config.py](backend/config.py) 的 `Config` 类(全部支持环境变量覆盖):

### 10.1 数据库

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | localhost / 3306 / forum_user / forum_pass / forum_db | MariaDB 连接参数 |
| `DATABASE_URL` | 自动拼接 mysql+mysqlconnector URL | 完整 URL 优先级最高,可设为 `sqlite:///./forum.db` 走 SQLite |
| `DB_CONNECT_TIMEOUT` | 10 | SQL 执行超时(秒) |

### 10.2 JWT

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `SECRET_KEY` | dev-secret-key-please-change-in-production | Flask 密钥(生产必改) |
| `JWT_SECRET_KEY` | = SECRET_KEY | JWT 签名密钥 |
| `JWT_ALGORITHM` | HS256 | JWT 算法 |
| `JWT_EXPIRATION_HOURS` | 24 | Token 过期(小时) |

### 10.3 业务

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `MIN_USERNAME_LENGTH` / `MAX_USERNAME_LENGTH` | 8 / 14 | 用户名长度范围 |
| `MIN_PASSWORD_LENGTH` | 8 | 密码最小长度 |
| `DEFAULT_PAGE_SIZE` / `MAX_PAGE_SIZE` | 20 / 100 | 分页默认值与上限 |
| `MAX_REPLY_DEPTH` | 2 | 回复最大嵌套深度(0=一级,2=三级) |
| `MAX_REPLY_LENGTH` | 5000 | 回复最大字符数 |
| `MAX_NICKNAME_LENGTH` / `MAX_BIO_LENGTH` | 50 / 200 | 昵称/简介长度 |

### 10.4 上传(V2)

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `UPLOAD_DIR` | backend/uploads | 上传根目录 |
| `AVATAR_DIRNAME` | avatars | 头像子目录 |
| `MAX_AVATAR_SIZE` | 2MB | 头像最大字节 |
| `ALLOWED_AVATAR_MIMES` | jpeg/png/webp/gif | 允许的 MIME |
| `ALLOWED_AVATAR_EXTENSIONS` | .jpg/.jpeg/.png/.webp/.gif | 允许的扩展名 |

### 10.5 Flask

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `JSON_SORT_KEYS` | False | 关闭响应字段排序 |
| `APP_TIMEZONE` | UTC | 时区 |
| `PORT` / `HOST` / `FLASK_DEBUG` | 5000 / 0.0.0.0 / 0 | 运行参数 |

---

## 11. 安全特性

| 类别 | 实现 |
| :--- | :--- |
| 密码哈希 | bcrypt 72 字节截断,`gensalt()` 加盐 |
| 鉴权 | JWT HS256,默认 24h 过期;`require_auth` 装饰器统一校验 |
| 用户名枚举防护 | 登录失败统一返回"用户名或密码错误" |
| 密码强度 | ≥8 位 + 字母 + 数字 + 特殊字符 |
| 用户名规则 | 8-14 位、首位小写字母、字母数字混合 |
| 上传安全 | 扩展名 + MIME + 大小三重校验;UUID 文件名;DB 失败回退已上传文件 |
| 目录遍历防护 | `os.path.realpath` 解析 + `os.sep` 边界前缀比较(防止 `uploads_evil` 同级目录误判) |
| 跨帖挂接防护 | `parent_id` 必须归属同一帖子,否则 403 |
| 回复删除权限 | 仅作者或 admin;BFS 级联删除子孙避免孤立引用 |
| XSS 清洗 | 前端 `ForumAPI.sanitize` 白名单标签/属性/协议,危险标签整删,异常降级为纯文本 |
| CORS | `app.py` `after_request` 默认 `Access-Control-Allow-Origin: *`(生产同域部署无副作用) |
| Nginx 安全头 | HSTS / X-Content-Type-Options / X-Frame-Options / X-XSS-Protection / CSP / Referrer-Policy / Permissions-Policy |
| systemd 沙箱 | `ProtectSystem=strict`、`NoNewPrivileges`、`PrivateTmp`、`SystemCallFilter`、`MemoryDenyWriteExecute` 等 |
| 限速 | Nginx `limit_req`(详见 nginx.conf) |
| 日志巡检 | `monitor.py` 检测 5xx/error.log/服务状态/数据库/磁盘内存;SMTP 告警 |
| 资源限制 | systemd `CPUQuota=200%`、`MemoryMax=1G`、`TasksMax=512`、IO IOPS 限制 |

### 11.2 攻击向量 → 防御位置对照表

| 攻击向量 | 主要防御 | 精确位置 |
| :--- | :--- | :--- |
| 暴力破解登录 | 用户名枚举防护(同文案)+ 前后端双校验 + `limit_req` 10r/s + `limit_conn` 50 | [backend/routes/auth.py:136-138](backend/routes/auth.py)、[deploy/nginx.conf:162,173-176](deploy/nginx.conf) |
| 弱口令 | bcrypt + 长度 8-72 + 字母数字特殊字符三要素 | [backend/routes/auth.py:19-33,67-78](backend/routes/auth.py)、[backend/routes/user.py:24-36](backend/routes/user.py) |
| bcrypt 超长输入 | 显式 `encode("utf-8")[:72]` 截断 | [backend/routes/auth.py:21-22](backend/routes/auth.py)、[backend/init_db.py:33](backend/init_db.py) |
| 用户名注入 / 大小写绕过 | 长度 + 首位小写 + 仅字母数字 + 必含字母数字 + `db.func.lower` 查重 | [backend/routes/auth.py:52-83](backend/routes/auth.py) |
| SQL 注入 | 全程 SQLAlchemy ORM,无字符串拼接 SQL;迁移脚本用参数化 prepared statement | [backend/models.py:19-179](backend/models.py)、[database/migration_v3.sql:40-69](database/migration_v3.sql) |
| XSS(存储型) | 前端白名单清洗器 + DOMParser 隔离文档 + 属性重建 + 危险标签删除 | [frontend/js/api.js:419-618](frontend/js/api.js) |
| XSS(URL 协议) | `javascript:`/`data:`/`vbscript:`/`file:` 协议黑名单 + `isSafeUrl` | [frontend/js/api.js:444-465](frontend/js/api.js) |
| XSS(事件属性) | `filterAttrs` 跳过所有 `on*` 与 `style` | [frontend/js/api.js:473-503](frontend/js/api.js) |
| 反向 Tabnabbing | 强制 `rel="noopener noreferrer"` | [frontend/js/api.js:610-614](frontend/js/api.js) |
| Markdown 注入 | marked 管线末端必经 `API.sanitize` | [frontend/js/post.js:76-98](frontend/js/post.js) |
| 开放重定向 | `getRedirect()` 同源 + 文件名白名单 | [frontend/js/auth.js:238-251](frontend/js/auth.js) |
| CSRF | 同源部署(`BASE_URL=""`)+ `credentials: same-origin` + CSP `form-action 'self'` | [frontend/js/api.js:14,170](frontend/js/api.js)、[deploy/nginx.conf:76](deploy/nginx.conf) |
| 目录遍历(上传) | `..` 过滤 + `realpath` 前缀校验 + `uuid4` 随机文件名 + 扩展名/MIME 双白名单 | [backend/app.py:121-135](backend/app.py)、[backend/routes/user.py:176-205](backend/routes/user.py) |
| 上传体积 | 2MB 前端 + 后端双重校验 + Nginx `client_max_body_size 5m` | [backend/routes/user.py:191-197](backend/routes/user.py)、[frontend/js/profile.js:183-200](frontend/js/profile.js)、[deploy/nginx.conf:51](deploy/nginx.conf) |
| 跨帖回复(越权) | 父回复必须属于同一帖子(403) | [backend/routes/posts.py:447-459](backend/routes/posts.py) |
| 删除越权 | 仅作者或管理员,否则 403 | [backend/routes/posts.py:483-499](backend/routes/posts.py)、[backend/routes/replies.py:50-53](backend/routes/replies.py) |
| 级联误删 | 话题根外键 `ON DELETE SET NULL`,父回复 `CASCADE` | [database/init.sql:66-69](database/init.sql)、[database/migration_v3.sql:48,64](database/migration_v3.sql) |
| 死循环(脏数据) | 层级回溯 `seen` 集合检测自引用环 + 迭代式 BFS | [backend/routes/posts.py:117-121](backend/routes/posts.py)、[backend/routes/replies.py:16-31](backend/routes/replies.py) |
| 悬空引用 | 父不存在时降级为一级回复 + 终止回溯 | [backend/routes/posts.py:122-124,156-159](backend/routes/posts.py) |
| JWT 伪造 | HS256 + `SECRET_KEY`(外部化到 `/etc/forum/forum.env`) | [backend/routes/auth.py:149-153](backend/routes/auth.py)、[deploy/forum-api.service:65](deploy/forum-api.service) |
| JWT 过期 | 24h 过期 + `ExpiredSignatureError` 分支 401 | [backend/config.py:39](backend/config.py)、[backend/routes/posts.py:52-55](backend/routes/posts.py) |
| 敏感信息泄露 | `to_dict()` 排除 `password_hash`;500 返回通用错误不泄露堆栈 | [backend/models.py:53-63](backend/models.py)、[backend/app.py:70-77](backend/app.py) |
| 服务器信息泄露 | `server_tokens off` | [deploy/nginx.conf:48](deploy/nginx.conf) |
| 中间人攻击 | TLS 1.2/1.3 + 前向加密套件 + OCSP stapling + HSTS preload | [deploy/nginx.conf:38-45,66](deploy/nginx.conf) |
| 点击劫持 | `X-Frame-Options: SAMEORIGIN` + CSP `frame-ancestors 'self'` | [deploy/nginx.conf:70,76](deploy/nginx.conf) |
| MIME 嗅探 | `X-Content-Type-Options: nosniff` | [deploy/nginx.conf:68](deploy/nginx.conf) |
| 敏感文件访问 | `location ~ /\.` → `deny all`(拒绝 `.git` 等) | [deploy/nginx.conf:191-195](deploy/nginx.conf) |
| DDoS / 爬虫 | `limit_req` 10r/s + `limit_conn` 50 + 单点 `burst=20` | [deploy/nginx.conf:162,173-176](deploy/nginx.conf) |
| 请求体过大 | `client_max_body_size 5m` + 60s body timeout | [deploy/nginx.conf:51-54](deploy/nginx.conf) |
| 权限提升 | `NoNewPrivileges`、`ProtectSystem=strict`、`RestrictSUIDSGID`、`CapabilityBoundingSet` 置空 | [deploy/forum-api.service:31-48](deploy/forum-api.service) |
| 任意代码执行 | `MemoryDenyWriteExecute`(W^X)+ `SystemCallFilter` 白名单 | [deploy/forum-api.service:43-46](deploy/forum-api.service) |
| 容器逃逸 | `RestrictNamespaces`、`ProtectKernelTunables/Modules/ControlGroups` | [deploy/forum-api.service:36-41](deploy/forum-api.service) |
| fork 炸弹 | `TasksMax=512` | [deploy/forum-api.service:121](deploy/forum-api.service) |
| 资源耗尽 | `CPUQuota=200%`、`MemoryMax=1G`、`MemoryHigh=800M`、`IO*Max` | [deploy/forum-api.service:116-125](deploy/forum-api.service) |
| 日志泄露 | 错误页 `internal`(本地只读)、隐藏文件 `access_log off` | [deploy/nginx.conf:181-195](deploy/nginx.conf) |
| 静态资源缓存被污染 | 关键页 `no-cache`、哈希资源 `immutable` 365d | [deploy/nginx.conf:105-122](deploy/nginx.conf) |
| 告警疲劳 | 连续失败 3 次才发信(`AlertState`) | [deploy/monitor.py:425-451](deploy/monitor.py) |

### 11.3 三层纵深防御

1. **网关层**([deploy/nginx.conf](deploy/nginx.conf)):TLS、HSTS、CSP、限速、隐藏文件拒绝、body 限制。
2. **应用层**([backend/](backend/)):JWT + bcrypt + 参数校验 + ORM + 目录遍历防护 + 越权判定。
3. **展示层**([frontend/js/api.js](frontend/js/api.js)):白名单清洗器 + 协议黑名单 + 重定向同源校验。

---

## 12. 测试

### 12.1 V3 层级回复端到端自测

[backend/tests/test_reply_hierarchy.py](backend/tests/test_reply_hierarchy.py):

- 使用独立 SQLite 测试库 `instance/_test_forum.db`,不污染开发库;
- 通过 Flask `test_client` 走完整 HTTP 链路;
- 预置 4 个角色用户(楼主 / 普通用户 / admin / 其他);
- 覆盖 42 项场景,包括:
  - 游客读取 / 鉴权失败
  - 一级 / 二级 / 三级回复
  - 深度拉平(超过 `MAX_REPLY_DEPTH` 上提)
  - 树结构正确性
  - 跨帖与非法 `parent_id`
  - 删除权限(仅作者/admin)
  - 级联删除子孙
  - 边界校验

运行方式:`cd backend && python tests/test_reply_hierarchy.py`,退出码 0 = 全部通过,1 = 存在失败。

### 12.2 健康检查

`GET /health` 执行 `SELECT 1` 探测数据库,返回:

```json
{ "status": "ok", "database": "ok" }
```

供 `deploy/monitor.py` 与外部监控平台使用。

### 12.3 测试覆盖矩阵

| 维度 | 覆盖点 |
| :--- | :--- |
| 鉴权 | 游客读取 / 未登录写入 / 未登录删除 / 已登录操作 |
| 角色 | 普通用户 / 作者 / 管理员 |
| 层级 | 一级 / 二级 / 三级 / 深度溢出拉平 |
| 边界 | 跨帖 `parent_id` / 非法 `parent_id` / 悬空 `parent_id` |
| 删除 | 单条 / 含子树 / 不存在的回复 / 越权删除 |
| 一致性 | 删除后 DB 无残留 / 其它帖子不受影响 / 树根完整性 |

### 12.4 跨文件依赖链

- 四个 HTML 均**先加载 `js/api.js`,再加载各页业务脚本**,因为业务脚本首行即校验 `global.ForumAPI` 是否存在([frontend/js/index.js:13-17](frontend/js/index.js)、[post.js:18-22](frontend/js/post.js)、[auth.js:13-17](frontend/js/auth.js)、[profile.js:13-17](frontend/js/profile.js))。
- `post.js` 还依赖 CDN 的 `window.marked`([post.js:25](frontend/js/post.js))与 `window.hljs`([post.js:115](frontend/js/post.js)),缺失时分别降级为纯文本渲染([post.js:86-89](frontend/js/post.js))与跳过高亮([post.js:116](frontend/js/post.js))。
- XSS 清洗的唯一入口是 `API.sanitize`([api.js:518](frontend/js/api.js)),被 [post.js:95](frontend/js/post.js) 调用,是 Markdown → HTML 的必经关卡。

### 12.5 前端 → 后端调用关系

| 调用方 | 调用 | 行号 |
| :--- | :--- | :--- |
| `index.js` | `API.getPosts(currentPage, PAGE_SIZE)` | `frontend/js/index.js:287` |
| `index.js` | `API.createPost(title, content)` | `frontend/js/index.js:354` |
| `post.js` | `API.getPost(id)` | `frontend/js/post.js:717` |
| `post.js` | `API.createReply(currentPost.id, content, parentId)`(内联) | `frontend/js/post.js:557` |
| `post.js` | `API.createReply(currentPost.id, content)`(一级) | `frontend/js/post.js:613` |
| `post.js` | `API.deletePost(currentPost.id)` | `frontend/js/post.js:670` |
| `post.js` | `API.deleteReply(replyId)` | `frontend/js/post.js:694` |
| `auth.js` | `API.login(username, password)` | `frontend/js/auth.js:145` |
| `auth.js` | `API.register(username, password)` | `frontend/js/auth.js:203` |
| `profile.js` | `API.getProfile()` | `frontend/js/profile.js:108` |
| `profile.js` | `API.uploadAvatar(file)` | `frontend/js/profile.js:203` |
| `profile.js` | `API.updateProfile({nickname, bio})` | `frontend/js/profile.js:257` |
| `profile.js` | `API.changePassword(oldPwd, newPwd)` | `frontend/js/profile.js:324` |

### 12.6 页面跳转关系

| 从 | 到 | 触发位置 |
| :--- | :--- | :--- |
| 任意页 → 首页 | `index.html` | `frontend/index.html:24`、`post.html:24`、`auth.html:19`、`profile.html:19`;JS 侧 `index.js:69`、`post.js:218`、`auth.js:38`、`profile.js:32` |
| 首页 → 详情 | `post.html?id=<encodeURIComponent(id)>` | `frontend/js/index.js:176-180` |
| 首页 → 登录 | `auth.html`(未登录导航按钮) | `frontend/js/index.js:90-94` |
| 首页 → 发帖弹窗 | 未登录时重定向 `auth.html?redirect=<当前URL>` | `frontend/js/index.js:310-314` |
| 任意页 → 个人中心 | `profile.html` | `frontend/js/index.js:68-71`、`post.js:226-228,235-238`、`profile.js:46-48` |
| 详情页 → 登录 | `auth.html?redirect=<encodeURIComponent(location.href)>` | `frontend/js/post.js:173-175,250,585` |
| 个人中心 → 登录 | `auth.html?redirect=<encodeURIComponent("profile.html")>` | `frontend/js/profile.js:59,143,473` |
| 登录/注册成功 → 回跳 | `getRedirect()` 结果(默认 `index.html`) | `frontend/js/auth.js:163-165,212-214` |
| 任意 401 → 登录 | `auth.html?redirect=<pathname + search>` | `frontend/js/api.js:105-114`(由 `api.js:187-196` 调用) |
| 删帖成功 → 首页 | `index.html` | `frontend/js/post.js:672-674` |

---

## 13. 已知问题与勘误

### 13.1 代码内发现的命名与实现不一致

1. **`deploy/monitor.py:222` 的 `shell_awk()` 实际执行 `grep -cE`**
   - 函数名为 `shell_awk`,但实现使用 `grep -cE` 计数([deploy/monitor.py:224](deploy/monitor.py))。
   - 影响:可读性问题,不影响功能。建议重命名为 `shell_count` 或改用真正的 awk。

### 13.2 生产配置遗留占位

1. **`deploy/forum-api.service:70-72` 的默认数据库密码与密钥是 `change_me_in_env_file`**
   - 必须由 `/etc/forum/forum.env` 覆盖,否则生产环境使用弱凭据。
   - 参见 [deploy/README.md:168-187](deploy/README.md)(forum.env 配置示例)。
2. **`deploy/monitor.py:71-76` 的 SMTP 配置是占位值**
   - `smtp_user` / `smtp_password` 为 `change_me`,未配置时告警邮件无法发出。
3. **`deploy/nginx.conf:31,76` 的域名是 `forum.example.com`**
   - CSP 中的 `connect-src https://api.example.com` 需按实际域名替换。

### 13.3 兼容性注意事项

1. **`database/migration_v3.sql:5-8` 注明 MySQL 8.x 不支持 `ADD COLUMN IF NOT EXISTS`**
   - 脚本面向 MariaDB;在 MySQL 8.x 上需手动删除该子句后执行。
2. **`frontend/js/post.js:43-69` 的 marked 双分支**
   - 同时兼容 v4+(`marked.use`)与旧版(`marked.setOptions` + `sanitize:false`)。
   - 旧版的 `sanitize` 选项已弃用,必须显式关闭并交由自研清洗器处理。
3. **`backend/routes/auth.py:154-156` 的 PyJWT 版本兼容**
   - PyJWT 1.x 返回 `str`、2.x 返回 `bytes`,此处用 `isinstance` 判断并 `decode("utf-8")`。

### 13.4 安全与边界说明

1. **`backend/app.py:91-105` 的 CORS 返回 `Access-Control-Allow-Origin: *`**
   - 生产部署下前端与后端同源(`BASE_URL=""`),CORS 头不会生效,可保留用于开发。
   - 若未来前端独立部署,需改为白名单式 `Origin`。
2. **`backend/app.py:121-135` 的上传路径防护**
   - 已用 `..` 过滤 + `realpath` 前缀校验 + `uuid4` 文件名三重防护,但生产环境仍建议在 Nginx 侧用独立 `location /uploads/` 限制访问白名单。
3. **`frontend/js/api.js:419-433` 的 XSS 白名单包含 `img` 标签**
   - 允许 `data:` / `blob:` 图片(CSP 已同步放开 `img-src data: blob:`)。
   - 若业务不需要 base64 图片,可移除 `img` 标签与对应协议以降低攻击面。
4. **`backend/routes/replies.py:16-31` 的 BFS 无最大深度限制**
   - 依赖数据库真实层级,理论上可无限深;前端展示层通过 `MAX_REPLY_DEPTH=2` 拉平。
   - 若担心恶意构造超深链,可在 BFS 层数循环上加 `MAX_REPLY_DEPTH` 限制。

### 13.5 前端与后端字段对齐

1. **`frontend/js/index.js:153-156` 头像用 `innerHTML` 拼接本地缓存的 `avatar_url`**
   - 该值来自后端 `to_dict()`(已过滤敏感字段),风险可控;但严格来说仍建议改用 `API.el()` 构造 `<img>`。
2. **`frontend/js/post.js:721-725` 的层级字段兼容**
   - 当后端未返回 `reply_count` / `reply_tree` 时前端自动降级为扁平列表并本地建树,保证版本混跑时不白屏。

---

## 14. 文档与规范参考

### 14.1 项目文档

| 文档 | 定位 |
| :--- | :--- |
| [README.md](README.md) | 项目总览与快速启动 |
| [CODE_WIKI.md](CODE_WIKI.md) | 本文档:结构化代码 Wiki(14 章,技术实现核心载体) |
| [需求文档.md](需求文档.md) | V1.1 / V2.0 / V3.0 合并需求规格 |
| [deploy/README.md](deploy/README.md) | Rocky Linux 生产部署完整指南(11 章) |
| [AGENTS.md](AGENTS.md) | AI 协作规范(GraphFlow 上下文编排) |

### 14.2 工具配置规则(只读引用,不在本文件修改)

| 规则文件 | 用途 |
| :--- | :--- |
| [.trae/rules/git-commit-message.md](.trae/rules/git-commit-message.md) | Trae IDE 提交信息风格自定义(模板) |
| [.claude/rules/graphflow.md](.claude/rules/graphflow.md) | Claude Code GraphFlow 规则(与 [AGENTS.md](AGENTS.md) 内容一致,工具配置载体) |

### 14.3 行号索引(全项目技术点速查)

| 文件 | 关键技术点(位置) |
| :--- | :--- |
| [backend/app.py](backend/app.py) | 应用工厂 `L21-29`;静态服务 `L23-28`;蓝图注册 `L40-47`;错误处理 `L51-77`;SPA 回退 `L81-87`;CORS `L91-105`;健康检查 `L109-117`;上传服务 `L121-135`;入口 `L142,147-159` |
| [backend/config.py](backend/config.py) | 数据库五元组 `L18-28`;JWT `L35-39`;用户名/密码规则 `L49-52`;分页 `L54-55`;上传白名单 `L59-70`;回复层级 `L81-83` |
| [backend/models.py](backend/models.py) | User `L19-66`;display_name `L48-51`;Post `L69-112`;Reply 自引用 `L115-179`(`primaryjoin`/`foreign()` `L153-159`) |
| [backend/init_db.py](backend/init_db.py) | bcrypt 72 字节截断 `L32-35`;Inspector 补列 `L38-87`;建表 `L90-139`;CLI `L143-166` |
| [backend/routes/auth.py](backend/routes/auth.py) | bcrypt `L19-33`;用户名五重校验 `L52-83`;密码强度 `L67-78`;JWT 载荷 `L141-148`;签发 `L149-156` |
| [backend/routes/posts.py](backend/routes/posts.py) | require_auth `L30-70`;分页 `L75-86`;时间序列化 `L89-101`;层级回溯 `L106-127`;展示结构预计算 `L130-181`;**深度拉平 `L150-163`**;树构建 `L212-257`;列表 `L262-321`;详情 `L324-369`;发帖 `L372-418`;回复 `L421-480`;删帖 `L483-510` |
| [backend/routes/replies.py](backend/routes/replies.py) | BFS 子孙收集 `L16-31`;级联删除 `L34-70` |
| [backend/routes/user.py](backend/routes/user.py) | 密码强度 `L24-36`;资料 `L57-115`;改密 `L118-156`;头像上传 `L159-244`(uuid4 `L203-205`、回滚补偿 `L229-239`) |
| [backend/tests/test_reply_hierarchy.py](backend/tests/test_reply_hierarchy.py) | 独立库 `L23`;深度注入 `L24`;工厂 `L43-51`;断言器 `L84-87`;42 项断言 |
| [database/init.sql](database/init.sql) | utf8mb4 `L7-9`;users `L16-28`;posts `L33-43`;replies `L48-70`;外键策略 `L66-69` |
| [database/migration_v2.sql](database/migration_v2.sql) | `ADD COLUMN IF NOT EXISTS` `L9-21` |
| [database/migration_v3.sql](database/migration_v3.sql) | 幂等加列 `L17-27`;索引 `L32-33`;动态外键 `L40-69`;回填说明 `L71-75` |
| [deploy/nginx.conf](deploy/nginx.conf) | upstream `L14-17`;log_format `L20-24`;TLS `L38-45`;安全头 `L66-80`;gzip `L83-89`;缓存策略 `L105-122`;反向代理 `L131-169`;限速 `L173-176`;错误页 `L179-188`;隐藏文件 `L191-195`;HTTP→HTTPS `L202-208` |
| [deploy/forum-api.service](deploy/forum-api.service) | After/Wants `L17-18`;User/Group `L28-29`;隔离 `L31-48`;路径 `L51-56`;环境 `L59-75`;Gunicorn `L80-91`;优雅停止 `L97-99`;重启 `L102-105`;日志 `L108-112`;资源 `L116-125`;只读/可写 `L128-131` |
| [deploy/monitor.py](deploy/monitor.py) | CONFIG `L44-93`;日志 `L100-120`;时间戳解析 `L152-168`;时间窗过滤 `L171-193`;状态码解析 `L196-213`;CheckResult `L237-241`;7 项巡检 `L244,278,305,319,347,373,393`;AlertState `L425-451`;SMTP `L465-497`;报告 `L500-564`;主入口 `L594-682` |
| [deploy/git_pull.sh](deploy/git_pull.sh) | 严格模式 `L19-20`;参数 `L46-61`;日志 `L63-78`;清理 `L80-103`;前置 `L105-146`;锁 `L148-163`;git `L165-211`;依赖 `L213-240`;迁移 `L242-280`;构建 `L282-301`;权限 `L303-312`;重启 `L324-377`;回滚 `L379-397`;主流程 `L399-436` |
| [deploy/crontab.txt](deploy/crontab.txt) | 环境 `L32-38`;监控 `L44`;更新 `L51`;清理 `L56`;快照 `L61`;归档 `L66`;健康检查 `L71` |
| [deploy/README.md](deploy/README.md) | 11 章部署手册(`L49-665`) |
| [frontend/index.html](frontend/index.html) | 禁缓存 `L5-7`;CDN `L14-17`;模态 `L66-108` |
| [frontend/post.html](frontend/post.html) | 三态容器 `L38,41,91`;登录态双分支 `L62,65`;确认弹窗 `L104-114` |
| [frontend/auth.html](frontend/auth.html) | Tab `L42-43`;原生校验 `L96-99,113-114,128` |
| [frontend/profile.html](frontend/profile.html) | 上传 `L43-47`;字数统计 `L95-104`;密码显隐 `L121-131` |
| [frontend/css/style.css](frontend/css/style.css) | CSS 变量 `L6-44`;焦点环 `L107-110`;Flex 骨架 `L113-128`;动画 `L492,640,649,1241`;模态毛玻璃 `L566-582`;表单错误 `L725-738`;Markdown 排版 `L886-1046`;hljs 主题 `L1352-1419`;媒体查询 `L1422,1513,1525,1831,2103`;楼中楼缩进 `L2002-2012`;层级字号 `L2014-2025`;内联表单 `L2027-2093` |
| [frontend/js/api.js](frontend/js/api.js) | IIFE `L8-9`;Token `L24-47`;buildUrl `L58-69`;request `L133-210`;AbortController `L159-182`;时间 `L343-407`;**XSS 清洗 `L419-618`**;DOM 工具 `L630-658`;上传 `L735-783` |
| [frontend/js/index.js](frontend/js/index.js) | 依赖守卫 `L10-17`;导航 `L31-98`;列表 `L115-191`;分页 `L196-276`;模态 `L310-435`;URL 深链 `L438-441` |
| [frontend/js/post.js](frontend/js/post.js) | marked 兼容 `L43-69`;管线 `L76-98`;hljs `L114-129`;权限 `L143-185`;帖子渲染 `L273-326`;树渲染 `L331-456`;内联表单 `L465-541`;删除 `L628-701`;加载 `L706-761`;初始化 `L798-858` |
| [frontend/js/auth.js](frontend/js/auth.js) | 校验 `L88-111`;登录 `L130-177`;注册 `L182-233`;**防开放重定向 `L238-251`**;回车 `L325-335` |
| [frontend/js/profile.js](frontend/js/profile.js) | 未登录跳转 `L57-61`;加载 `L103-138`;头像上传 `L183-218`;保存 `L229-280`;改密 `L285-338`;强度可视化 `L343-393`;显隐 `L398-406` |

---

> 文档版本:V2.0 · 适配代码版本:V3.0 · 最后更新:2026-09-22
> 变更记录:V1.0 → V2.0 合并原 `技术文档-在线论坛系统V3.md` 的攻击向量对照表、已知问题勘误、跨文件依赖链、行号索引章节,删除原技术文档。
