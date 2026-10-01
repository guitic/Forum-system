# 在线论坛系统 V3.0 · 全仓库代码审查报告

**日期**：2026-10-01
**场景**：全流程代码审查（结构 / 模块职责 / 执行流程 / 逻辑缺陷 / 性能 / 安全 / 可维护性）
**参与成员**：产品官（product-reviewer）+ 安全卫士（security-officer）+ 质量门神（qa-lead）+ 设计师（designer）+ 排障手（investigator）
**审查范围**：`backend/`（6 蓝图 + 模型 + 配置 + 初始化）、`frontend/`（4 页面 + 6 JS + CSS）、`database/`（5 SQL）、`deploy/`（nginx/systemd/git_pull/monitor/crontab）

---

## 📌 TL;DR（执行摘要）

- 整体结论：🟡 **有条件通过** —— 架构分层与 V3 层级回复设计合理；原始发现 **6 项严重 + 12 项高**，其中 **4 项已在本次审查期间（14:23–14:26）由并发改动修复并复验通过**；当前剩余 **4 项严重 + 10 项高**。
- 阻塞项数量：**3 项 P0 仍待修**（JWT 弱默认密钥、默认管理员弱口令、XSS 清洗时序）
- ⚠️ **审查期间发生并发变更**：另一会话在审查进行中修改了 `backend/app.py`、`backend/routes/user.py`、`backend/requirements.txt`、`frontend/index.html`、`frontend/post.html`、`deploy/*`、`README.md`。本报告已按**当前磁盘代码**重新复验并更新（详见文末「审查期间并发变更」）。
- 下一步：修 P0 安全项 → 补注册/发帖/删帖/头像测试 → 统一 API 契约与文档。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| Go / No-Go | 🟡 条件 Go（生产部署前必须清掉 3 项 P0） |
| 严重度分布 | 🔴 4 / 🟠 10 / 🟡 14 / 🟢 6（另 4 项审查期间已修复） |
| 关键行动项 | 10 条（P0×3、P1×4、P2×2，另 1 项已完成） |
| 建议负责人 | 后端：安全官+排障手；前端：设计师；发布：质量门神 |
| 测试现状 | 4 个脚本 126 断言全绿，但注册/发帖/删帖/头像/前端 **零覆盖** |

---

## 1. 各成员核心结论

### 🔍 产品官（整体结构与产品评审）
- 核心判断：分层与模块边界基本清晰、级联删除与层级树设计合理；短板集中在 **文档与实现脱节**、**API 契约不统一**、**`routes/posts.py`（684 行）职责过重**、**缺迁移框架/CI/正式测试**。
- 关键建议：`CODE_WIKI.md` 声称 4 个蓝图/接口清单，实际 6 个蓝图且缺 5 个接口；统一响应封装与时间格式；把鉴权装饰器与回复树算法从 `posts.py` 下沉。

### 🛡️ 安全卫士（OWASP + STRIDE 审计）
- 核心判断：**密钥与口令的弱默认值**是最大风险（可离线伪造 admin token / 直接登录）；上传链路（头像无验真、分片无配额）与 nginx 安全头覆盖为高发面。
- 关键建议：启动强制校验密钥非默认；默认管理员改随机一次性密码；头像走 Pillow 验真；`/api/auth/login` 单独限速。
- 已正确防御（无需改动）：目录遍历（realpath + 分隔符边界）、上传会话归属校验、SQL 全参数化、bcrypt 72B 截断、全局异常不回显堆栈、systemd 沙箱。

### ✅ 质量门神（QA 与发布）
- 核心判断：V3 层级/编辑/上传/浏览量测试质量高（126 断言全绿、独立测试库不污染开发库）；但**注册、发帖、删帖、头像、前端为空白**，且**无 CI、无 pytest、无覆盖率门禁**。
- 关键建议：补 `create_post`/`delete_post`/`register` 三个核心接口的 HTTP 测试 + `require_auth` 四类失败分支 + 前端 XSS 清洗单测，并引入 pytest+CI。

### 🎨 设计师（前端代码与交互）
- 核心判断：**XSS 防护存在可绕过路径**（sanitize 前先 `innerHTML` 挂载未清洗 HTML），CDN 无 SRI；四个页面存在大段重复代码（`renderNav`×5、`handleLogout`×4、`showToast`×4）；每次回复操作 `loadPost()` 全量重载。
- 关键建议：先 sanitize 再高亮（或 DOMPurify）；抽 `common.js`；局部更新替代全量重载；补模态框焦点陷阱与 toast `aria-live`。

### 🔧 排障手（后端逻辑、性能与可维护性）
- 核心判断：2 个确定性功能 Bug（catch-all 吞 API 404、旧头像永不删除）需优先修；时间序列化不一致导致前端差 8 小时；性能瓶颈集中在 `get_post` 的 N+1 与 `_reply_node` 的 O(N²)。
- 关键建议：catch-all 内判别 api 前缀；旧头像路径用 `replace("/uploads/","",1)`；统一 `_parse_dt`；`joinedload` 预取作者。

---

## 2. 综合审查发现（去重合并，按严重度排序）

### 🔴 严重（6）

| # | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|------|------|---------|------|------|
| 1 | 安全 | `backend/config.py:35-36`、`deploy/forum-api.service:79-80` | JWT/SECRET_KEY 弱默认回退（`dev-secret-key-please-change-in-production` / `change_me_in_env_file`）。EnvironmentFile 缺失即生效 → 可离线伪造 `sub`/`role=admin` 的 token，全站接管 | 启动时强制校验密钥非默认否则拒绝启动；密钥 ≥32B 随机 | 安全官 |
| 2 | 安全 | `backend/init_db.py:261,346-349` | 默认管理员 `admin/admin123`，README 与部署指南均沿用，且该口令绕过 8 位用户名规则 | 改为随机一次性密码 + 首次登录强制改密 | 安全官/产品官 |
| 3 | 安全 | `frontend/js/post.js:95-98,117-132` | XSS 时序缺陷：`renderMarkdown` 顺序为 parse → `highlightCodeBlocks` → `sanitize`，而 `highlightCodeBlocks` 对**未清洗**的 marked 输出执行 `tmp.innerHTML = html`；detached 节点仍可能触发 `onerror` 并发起外链请求 | 先 `sanitize` 再高亮（hljs 仅加 class/span），或直接上 DOMPurify | 设计师/安全官 |
| 4 | ~~安全~~ | `frontend/index.html`、`frontend/post.html` | ~~外部 CDN 脚本无 SRI、marked 未锁版本 → 供应链投毒/静默升级~~ | ✅ **审查期间已修复**（14:24）：改为自托管 `frontend/vendor/`（marked v12.0.2 / highlight.js v11.9.0 / github-dark 主题），HTML 已无 CDN 引用，CSP 同步收紧为 `script-src 'self'` | 设计师/安全官 |
| 5 | ~~功能 Bug~~ | `backend/app.py:38,94,127` | ~~catch-all 路由吞掉 `/api` 未知路径，返回 `index.html`(200) 而非 JSON 404~~ | ✅ **审查期间已修复**（14:23）：新增 `_is_api_path()`，404 处理器与 catch-all 统一拦截 `/api` 命名空间。**已复验**：`GET /api/unknown`→JSON 404、`GET /api`→JSON 404、`GET /nope.html`→HTML 200 | 排障手 |
| 6 | 文档 | `CODE_WIKI.md` §4.1/§7、`README.md` | 文档与实现严重脱节：称"4 个蓝图"实为 6 个；文件清单缺 `routes/stats.py`、`routes/upload.py`、`js/image-upload.js`；tests 仅列 1 个（实为 4 个）；API 清单缺 `GET /api/stats` 与 4 个 `/api/upload/*`；README 称 JWT"自动刷新"但实现无 refresh | 全量同步文档；该文档自居"技术实现核心载体"，失真面大 | 产品官 |

### 🟠 高（12）

| # | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|------|------|---------|------|------|
| 7 | ~~功能 Bug~~ | `backend/routes/user.py:62,245` | ~~旧头像永不删除：`os.path.join(UPLOAD_DIR, old_url.lstrip("/"))` 多拼一层 `uploads`，`isfile` 恒 False → 磁盘只增不减~~ | ✅ **审查期间已修复**（14:23）：新增 `_uploads_url_to_fs_path()`，剥离 `/uploads/` 前缀 + realpath 目录遍历防护 | 排障手 |
| 8 | ~~部署~~ | `backend/requirements.txt:13` | ~~未声明 gunicorn，而 systemd `ExecStart` 调用 `/opt/forum/venv/bin/gunicorn` → 全新部署必启动失败~~ | ✅ **审查期间已修复**（14:23）：补 `gunicorn==23.0.0; sys_platform != "win32"`；仍建议补依赖 hash 锁定 | 排障手/产品官 |
| 9 | 一致性 | `backend/models.py:69/125/215` vs `routes/posts.py:159` | 时间序列化不一致：`to_dict()` 用 naive `.isoformat()`（无时区），`_parse_dt()` 补 `+00:00`；前端 `api.js:385` 对无时区串按**本地时间**解析 → 同一时刻 `/api/user/profile` 与 `/api/posts` 相差 8 小时（UTC+8）；`APP_TIMEZONE` 定义后从未使用 | 统一走 `_parse_dt`，或模型层统一附加 UTC 时区 | 排障手/产品官 |
| 10 | 数据正确性 | `backend/routes/posts.py:42`、`deploy/forum-api.service:89-93` | 浏览量去重表为**进程内 dict**，而生产用 `--workers 2` → 同访客窗口内被不同 worker 各计一次（最多翻倍），重启清零。代码注释"当前单实例"与部署不符 | 改 Redis `SETNX`+`EXPIRE`，或降为单 worker | 排障手/产品官 |
| 11 | 安全 | `backend/routes/user.py:183-190` | 头像**无真实图片校验**：仅信客户端 `content_type`，扩展名来自客户端文件名；且 `file.read()` 先全量入内存再判大小（内存放大）。与图片上传的 Pillow 校验不一致 | `Image.open()+verify()` 验真；白名单重设扩展名；限制读取长度 | 安全官/质量门神 |
| 12 | 安全 | `backend/routes/upload.py:174-176,81,149` | 分片上传磁盘 DoS：`total_chunks` 上限 1 万但与 `size` 无一致性校验，单会话可写 ≈ 10000×512KB ≈ 5GB；清理仅在 `init` 时触发 | 校验 `total_chunks == ceil(size/chunk_size)`；按用户限并发会话与配额；定时清理 | 安全官 |
| 13 | 安全 | `deploy/nginx.conf:122,128,135,142,206` | nginx `add_header` 继承陷阱：各 location 自带 `add_header` 后**不再继承 server 级**（:76-90）→ HTML/JS/CSS/uploads 响应丢失 CSP / HSTS / nosniff / X-Frame-Options | 安全头下沉到 `http` 级或用 `include` 统一注入 | 安全官 |
| 14 | 安全 | `backend/routes/user.py:146` | 改密后旧 token 仍有效（无 `jti`/版本号），24h 内旧 token 可继续使用 | 引入 `token_version` 或改密即失效 | 安全官 |
| 15 | 性能 | `backend/routes/posts.py:316-328` | 新建回复 O(N²)：每发一条回复都 `filter_by(post_id).all()` 拉全帖回复再全量建树 | 只按 parent 链回填本节点字段，或复用详情缓存 | 排障手 |
| 16 | 性能 | `backend/routes/posts.py:447-455` | `get_post` N+1：`_reply_node_dict` 逐条访问 `reply.author`（lazy select），N 条回复触发 N 次查询 | `.options(joinedload(Reply.author))` 预取 | 排障手 |
| 17 | API 一致性 | 多处 | 响应封装不统一：列表 `{"posts":…}` 有包裹；详情/资料/新回复返回裸对象；`PUT /api/posts/{id}` 又混入 `{"message",…}`；注册返回 `{"message","user"}`、登录返回扁平 token。`display_name`/`nickname`/`username` 字段集不一致 | 统一 envelope 或统一裸对象；统一用户资源字段集 | 产品官 |
| 18 | 可维护性 | `backend/routes/posts.py`（684 行） | 事实上的"上帝模块"：承载浏览量去重 + `require_auth` + 分页/时间工具 + 回复树算法 + 帖子 CRUD + 回复创建；`replies.py`/`user.py`/`upload.py` 均 `from routes.posts import require_auth` | 鉴权下沉 `security/decorators.py`；回复树独立 `services/reply_tree.py` | 产品官/排障手 |

### 🟡 中（14）

| # | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|------|------|---------|------|------|
| 19 | 结构 | `frontend/js/*.js` | 大段重复：`renderNav`(≈5 份)、`handleLogout`(4 份)、`showToast`(4 份)、`escapeHtml/escapeAttr`、`renderAvatar/buildAvatarHTML`、密码强度逻辑各写一份；字段命名 camelCase/snake_case 混用 | 抽 `common.js` / `ui.js` 公共模块 | 设计师 |
| 20 | 安全 | `frontend/js/api.js:499` | `isSafeUrl` 以 `/` 开头即放行 → `//evil.com` 协议相对地址通过；`img src` 又允许任意 http(s) 外链（追踪像素/混合内容） | 显式拒绝 `//` 开头；img 限同源或白名单域 | 设计师/安全官 |
| 21 | 交互 | `frontend/js/post.js:855,885,974,1055` | 每次发/改/删回复都 `loadPost()` 重建整棵回复树 → 滚动位置与输入上下文丢失，大树卡顿 | 局部插入/删除节点 | 设计师 |
| 22 | 安全 | `frontend/js/api.js:34`、`auth.js:218-226` | token / role / username 明文存 `localStorage`，XSS 即可窃取；`role` 客户端判权仅可用于显示，服务端须强校验（当前已强校验，属加固建议） | 评估 httpOnly Cookie；服务端保持权威判权 | 设计师/安全官 |
| 23 | 安全 | `deploy/nginx.conf:30,182` | 登录无应用层防爆破，仅 nginx 全局 10r/s（≈600/min） | `/api/auth/login` 单独严限 + 失败锁定 | 安全官 |
| 24 | 安全 | `backend/app.py:146` | `/health` 回显 DB 异常详情（`error: {str(exc)}`）且免日志/无限速 | 仅返回 `ok`/`error` 布尔 | 安全官 |
| 25 | 安全 | `deploy/monitor.py:130-136,328-330,76` | `run_cmd` 用 `shell=True` 拼接；DB 密码放命令行（`ps` 可见）；SMTP 口令明文硬编码 | 参数化连接；密钥走环境变量 | 安全官 |
| 26 | 部署 | `deploy/git_pull.sh:177-178,93-96` | 无条件 `git reset --hard` / `clean -fd` 丢本地改动；错误回滚判定 `grep 'HEAD'` 恒不命中 → 回滚失效（死代码）；root 下 `pip install` 第三方包 | 先 stash/备份；修正回滚判定；锁定依赖哈希 | 安全官 |
| 27 | 可维护性 | `auth.py:19/27`、`user.py:39/46`、`init_db.py:33` | 密码哈希/校验/强度逻辑三处重复，强度规则两套实现易漂移 | 抽 `security.py` 统一 | 排障手 |
| 28 | 可维护性 | `posts.py:133/424/544/608/629/666` 等 | 大量 `Query.get()` 在 SQLAlchemy 2.0 已 Legacy，持续告警且未来移除 | 改 `db.session.get(Model, id)` | 排障手 |
| 29 | 可维护性 | `posts.py:507/569/646/680` 等 | 多处 `except Exception: return 500` 吞掉细节，无上下文日志 | 至少 `logger.exception` | 排障手 |
| 30 | 可维护性 | `backend/app.py:172,60,184` | 模块级 `app = create_app()` + import 时 `makedirs` 产生导入副作用；`db.create_all()` 自动建表；无 Alembic，靠手写 ALTER 巡检 | 应用工厂按需创建；引入 Alembic | 排障手/产品官 |
| 31 | 功能 | `backend/routes/posts.py:355-371` | `list_posts` 不返回 `view_count` → 前端列表页浏览量恒显示 0 | 列表查询补 `view_count` 字段 | 产品官 |
| 32 | 正确性 | `frontend/js/api.js:679-681` | `API.el` 未跳过 null：`setAttribute(k, null)` 生成字符串 `"null"`（如 `aria-current="null"`） | `null/undefined` 时跳过该属性 | 设计师 |
| 33 | a11y | `post.html` 模态框、`confirm-backdrop`、lightbox | 模态框无焦点陷阱/焦点恢复；toast 容器无 `role="status"`/`aria-live`（屏幕阅读器不播报） | 补焦点管理与 `aria-live` | 设计师 |
| 34 | 产品 | 全站 | 仅有本人 `GET /api/user/profile`，无 `/api/user/{id}`，但已渲染"楼主/Admin"徽标 → 点击无法查看他人资料 | 补他人主页或调整徽标交互 | 产品官 |

### 🟢 低（6）

| # | 类别 | 位置 | 问题描述 | 建议 | 来源 |
|---|------|------|---------|------|------|
| 35 | 仓库卫生 | 根目录 + `backend/` | `AVATAR_OK`、`EVIL_DATA`、`TOP_SECRET_DATA` 为渗透测试残留（含本机临时路径），**已被 git 跟踪** | 从版本库移除并加 `.gitignore` | 安全官/产品官 |
| 36 | 可维护性 | `backend/init_db.py:177,228,247` | 变量名 `fairy` 无语义；`pending` 为空时 `cur.close()` 不执行（finally 作用域）；`PRAGMA foreign_key_check` 跨方言脆弱 | 重命名；修正资源释放；限 sqlite 分支 | 排障手 |
| 37 | 前端 | `profile.js:432-436` vs `auth.js:175` | 密码强度色一处硬编码 hex、一处用 CSS 变量，风格不一致 | 统一走 CSS 变量 | 设计师 |
| 38 | 性能 | `index.html` | 首页引入 marked + highlight.js 但首页不渲染 Markdown → 白载约 300KB | 按页按需加载 | 设计师 |
| 39 | 代码风格 | `frontend/js/*.js` | `var` 与 `let/const` 混用（整体偏 `var`） | 统一 `let/const` | 设计师 |
| 40 | 文档 | `database/migration_v3.sql:36` | 注释"应用层已做级联删除"与现状（改由 DB 外键级联）不符，误导维护者 | 更新注释 | 排障手 |

### ✅ 已正确防御（避免误报，无需改动）

- 目录遍历：`app.py:158-165` `realpath` + `os.sep` 边界前缀校验（含 `uploads_evil` 同级绕过）—— 正确。
- 上传会话：`upload_id` 32 位 hex 正则（`upload.py:34`）+ 归属校验（`:210-220`）—— 无 IDOR。
- SQL 注入：全 ORM 参数化 —— 已防御。
- 密码：bcrypt + 72 字节显式截断 —— 正确。
- 全局异常：`app.py:101-107` 不回显堆栈 —— 正确。
- 部署加固：nginx TLS/限连、systemd `NoNewPrivileges`/`CapabilityBoundingSet=`/`ReadWritePaths` —— 良好。

### 🧪 测试覆盖矩阵（质量门神）

| 功能 | 覆盖 | 说明 |
|------|------|------|
| 帖子详情 / 层级回复树 / 删除级联 / 编辑帖与回复 / 分片图片上传 / 浏览量 | ✅ 充分 | 4 脚本 126 断言全绿，独立测试库 |
| 登录 | 🟡 仅成功路径 | 无失败分支 |
| 帖子列表 | 🟡 仅取一条 | 无分页边界 |
| **注册 / 发帖 / 删帖 / 头像上传 / 资料更新 / 改密 / stats / health** | ❌ 零覆盖 | 核心写入路径裸奔 |
| **前端 `js/*.js`** | ❌ 无任何自动化测试 | XSS 清洗是安全关键却零验证 |

---

## ⚠️ 审查期间并发变更（重要）

本次审查开始时（14:16–14:20）读取的代码与审查结束时的磁盘代码**不一致**：另有会话在 **14:23–14:26** 修改了本仓库（`git status` 显示 `backend/app.py`、`backend/routes/user.py`、`backend/requirements.txt`、`frontend/index.html`、`frontend/post.html`、`deploy/README.md`、`deploy/crontab.txt`、`deploy/forum-api.service`、`deploy/git_pull.sh`、`deploy/nginx.conf`、`README.md` 均为已修改未提交；另新增未跟踪的 `frontend/404.html`、`frontend/50x.html`、`frontend/vendor/`）。

该批改动**恰好覆盖并修复了本报告的 4 项发现**，已逐项复验：

| 原发现 | 复验方式 | 结论 |
|--------|---------|------|
| #5 catch-all 吞 `/api` 404 | Flask `test_client` 实测 `GET /api/unknown`、`GET /api` | ✅ 均返回 JSON 404；`GET /nope.html` 仍 200 HTML |
| #7 旧头像路径拼接错误 | 读 `user.py:62,245`，确认改用 `_uploads_url_to_fs_path()` | ✅ 已修复 |
| #8 `requirements.txt` 缺 gunicorn | 读 `requirements.txt:13` | ✅ 已补 `gunicorn==23.0.0`（平台标记） |
| #4 CDN 无 SRI / 与 CSP 冲突 | 检查 `frontend/vendor/` 存在、HTML 无 CDN 引用、CSP 收紧 | ✅ 已自托管修复 |

**仍未修复**（本次已按当前代码复核）：#1 JWT 弱默认密钥（`config.py:35-36`、`forum-api.service:79-80`）、#2 默认管理员 `admin/admin123`（`init_db.py:261,348`）、#3 XSS 清洗时序（`post.js:95-98` 顺序未变）、#11 头像无真实图片校验（`user.py:212-215` 仍只信 `content_type`）、#12 分片上传无 `size`/`total_chunks` 一致性校验（`upload.py:174`）、#13 nginx `add_header` 覆盖（安全头仍在 `server` 块 :76-92，各 location 自带 `add_header`）、#10 多 worker 浏览量去重（`forum-api.service:91` 仍 `--workers 2`）、#31 `list_posts` 缺 `view_count`。

> 建议：与并行会话统一工作区，避免审查结论与代码状态错位；本报告剩余项请以「当前磁盘代码」为准复核。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 紧急度 | 期望完成 |
|---|------|--------|--------|---------|
| 1 | 启动强制校验 `SECRET_KEY`/`JWT_SECRET_KEY` 非默认值，否则拒绝启动；生成 ≥32B 随机密钥 | 后端 | P0 | 立即 |
| 2 | 默认管理员改随机一次性密码 + 首次登录强制改密；README/部署指南同步 | 后端 | P0 | 立即 |
| 3 | 前端改为「先 sanitize 再 highlight」，或引入 DOMPurify（CDN/SRI 部分已于 14:24 自托管修复，**XSS 时序仍未修**） | 前端 | P0 | 立即 |
| 4 | ~~修 catch-all 吞 API 404~~ | 后端 | ✅ 已完成 | 2026-10-01 |
| 5 | ~~修旧头像删除路径；补 `requirements.txt` 的 gunicorn~~ | 后端 | ✅ 已完成 | 2026-10-01 |
| 6 | 统一时间序列化（全走 `_parse_dt`）；`get_post` 加 `joinedload`；`_reply_node` 去 O(N²) | 后端 | P1 | 本迭代 |
| 7 | 头像上传改 Pillow 验真 + 限流读取；分片上传补 `total_chunks`/`size` 一致性校验与配额 | 后端 | P1 | 本迭代 |
| 8 | nginx 安全头下沉 `http` 级统一注入；`/api/auth/login` 单独限速 | 运维 | P1 | 本迭代 |
| 9 | 补注册/发帖/删帖/头像 4 类 HTTP 测试 + `require_auth` 失败分支；引入 pytest + CI 门禁 | QA | P2 | 下迭代 |
| 10 | 抽前端 `common.js`；`posts.py` 拆分 `security/` + `services/reply_tree.py`；同步 `CODE_WIKI.md`/`README.md` | 全栈 | P2 | 下迭代 |

---

## ⚠️ 待完善 / 已知局限

- 本报告基于**静态审查 + 定点实测**，未做完整动态渗透（如真实伪造 JWT 的端到端验证、分片磁盘 DoS 实压），标注"需运行时验证"项（`init_db.py` 外键重建边界、回复树脏数据成环）建议补最小复现用例。
- 前端 XSS 时序风险中"detached `<img>` 是否发起请求"依赖浏览器实现，建议在目标浏览器实测确认影响面。
- 性能结论为复杂度分析，未做压测基线；长帖（数千回复）下的实际退化需实测。
- `AVATAR_OK`/`EVIL_DATA`/`TOP_SECRET_DATA` 判定为历史渗透测试残留，非活漏洞（位于 `backend/` 非静态根，Web 不可达），但仍应清理。
- **审查结论存在时间窗**：审查期间仓库被并行会话修改（见「审查期间并发变更」），本报告已按结束时磁盘代码复验；若之后仍有改动，请以最新代码为准。

---

## 📚 成员产出索引

- gstack-product-reviewer（产品官）：整体结构与 API 契约评审，16 项发现
- gstack-security-officer（安全卫士）：OWASP Top 10 + STRIDE 审计，15 项发现 + 6 项已防御确认
- gstack-qa-lead（质量门神）：测试覆盖矩阵 + 8 项必补测试清单（实测 126 断言全绿）
- gstack-designer（设计师）：前端结构/安全/交互/a11y 审查，XSS 明确结论"存在可绕过路径"
- gstack-investigator（排障手）：后端逻辑/性能/可维护性，12 项发现（含 2 个确定性 Bug）

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
