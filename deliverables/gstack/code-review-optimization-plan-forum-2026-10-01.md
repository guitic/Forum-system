# 论坛系统 代码审查与优化计划报告

**日期**：2026-10-01  
**场景**：只读代码审查 + 优化计划（现状梳理 / 不足识别 / 优化建议 / 优先级排序）  
**审查范围**：`D:/桌面/deep/Forum system`（后端 Flask 约 2400 行 Python、前端原生 JS 约 4500 行、DDL/部署配置）  
**参与成员**：产品官（产品与架构评审）+ 安全卫士（OWASP Top 10 + STRIDE 审计）+ 排障手（性能与稳定性排查）  
**约束**：本次为**纯只读审查，未修改任何业务代码**（详见文末"审查过程说明"）

---

## 📌 TL;DR（执行摘要）

- **整体结论**：🟡 **有条件通过** —— 工程完成度高（4 套端到端测试全绿，共 126 项断言通过），安全性设计明显优于同类学生/中小项目，但存在 **2 个必须修复的稳定性/正确性缺陷** 与 **1 个必须修复的并发缺陷**。
- **阻塞项数量**：**3 条 P0**（浏览量去重跨 worker 失效、大帖详情 N+1 性能塌陷、回复写路径 O(n)）。
- **核心判断**：代码**质量远高于**其"论坛作业"的定位——防目录遍历、Pillow 真实格式校验、声明大小校验、幂等迁移脚本、启动期密钥 fail-fast 都已具备。真正的短板不在"写没写"，而在 **(1) 多 worker 部署下进程内状态失效、(2) 回复树未做预加载、(3) 目录结构未按领域拆分**。
- **下一步**：先修 P0 三项（改动量小、风险低、收益最大），再按中优先级补测试与可观测性。

---

## 🎯 核心结论卡片

| 项目         |   | 内容                                                                               |
| ---------- | - | -------------------------------------------------------------------------------- |
| Go / No-Go |   | 🟡 **条件 Go**（修完 3 条 P0 后可 Go）                                                    |
| 严重度分布      |   | 🔴 高 3 / 🟠 中 7 / 🟡 低 6（共 16 条）                                                 |
| 关键行动项      |   | 16 条（含 3 条 P0、7 条 P1、6 条 P2）                                                     |
| 建议负责人      |   | 后端 1 人主责 P0/P1；前端 1 人跟进可维护性项；运维确认 Nginx 限流与备份                                    |
| 测试现状       |   | 4 套自测脚本 **126/126 通过**（edit 35、reply_hierarchy 42、image_upload 28、view_count 21） |
| 无自动化 CI    |   | ⚠️ 测试需手动执行，无 CI 流水线                                                              |

---

## 1. 各成员核心结论

### 🔍 产品官（产品与架构评审）

- **核心判断**：需求文档（V1.1/V2.0/V3.0）所定义的**功能已全部落地**，包括三级楼中楼、深度拉平上提、编辑标记、图片分片上传、浏览量去重。不存在"需求写了但没做"的缺口——搜索/点赞/通知/私信等均**不在需求范围内**，属可选演进项而非缺失。
- **结构短板**：真正的架构问题是**领域边界模糊**。`routes/upload.py`、`routes/user.py`、`routes/replies.py` 都反向 `import` `routes/posts.py` 的私有符号（`require_auth`、`_reply_node`），使 `posts.py`（684 行）成为事实上的"公共依赖中心"；鉴权装饰器直接 `kwargs["current_user"] = user` 注入，绕开了 Flask 惯用的 `g`，风格非主流且隐式。
- **冗余**：`_hash_password`/`_verify_password` 在 `auth.py` 与 `user.py` **各有一份完全重复的实现**；`create_post` 与 `update_post` 的标题/内容校验逐行重复；权限判断（owner or admin）在 4 处重复书写。
- **关键建议**：抽出 `utils/security.py`（密码/鉴权）与 `utils/reply_tree.py`（回复树），把 `posts.py` 瘦身为纯路由层。

### 🛡️ 安全卫士（OWASP Top 10 + STRIDE 审计）

- **核心判断**：安全基线**扎实**——SQL 全量走 ORM 无注入（✅ 误报排除）、上传目录遍历防护到位、Pillow 真实格式校验 + 扩展名一致性比对、登录防用户名枚举、生产弱密钥 fail-fast、Nginx 侧 `script-src 'self'` CSP（**无 `unsafe-inline`**）显著抬高了 XSS 利用门槛。
- **真实缺陷**：① 登录**无按账号的失败锁定/节流**，Nginx 仅做全局 `10r/s` 每 IP 限速，分布式慢速爆破无缓解；② `/health` 把数据库异常原文回显（`f"error: {str(exc)}"`），泄露内部结构；③ 改密码后**旧 JWT 仍有效**（无 `password_changed_at` 校验、无黑名单）；④ 前端把 `role` 存 localStorage 并据此渲染 ADMIN 徽标，虽后端有真校验（**UI 信任问题，非真实越权**）。
- **关于 XSS 的重要澄清**：审查中曾怀疑 `marked.parse` → `innerHTML` 无净化，**经交叉验证为误报**——`api.js` 有自研白名单净化器（`ALLOWED_TAGS`/`ALLOWED_ATTRS`/`isSafeUrl`），且 CSP 双重兜底。但其白名单**缺 `<picture>`/`<source>`**，且净化发生在 `highlightCodeBlocks` **之后**（顺序上代码块 class 得以保留，属有意设计）。
- **关键建议**：补登录节流（复用现有进程内 TTL 模式或引入 `Flask-Limiter`）；`/health` 只回 `ok/error` 不带头；JWT 加 `pwd_ver` 声明。

### ✅ 质量门神（QA 与发布）—— 本次未独立上场，由排障手代查测试覆盖

- **核心判断**：4 套测试脚本**实测全部通过（126/126）**，覆盖编辑、回复层级、图片上传、浏览量四大模块的端到端 HTTP 链路，含并发断言（30 线程原子自增无丢失）与越权断言，**质量高于预期**。
- **覆盖缺口**：`stats.py`、`user.py` 的资料更新/密码修改/头像上传、`list_posts` 分页边界、回复树**深层拉平**的展示正确性、登录暴力破解场景 —— **均无测试**；且无 CI，测试靠手动跑。

### 🔧 排障手（性能与根因排查）

- **核心判断**：定位到 **3 个高影响性能/并发问题**，均有明确代码依据：
  - **N+1 主查询**：`_reply_node_dict`（`posts.py:254-278`）访问 `reply.author` 与 `parent.author`，而 `Reply`/`Post` 的 `lazy="select"`（`models.py:108,198`）→ **500 条回复的大帖约 1000+ 次查询**。
  - **写路径 O(n)**：`_reply_node`（`posts.py:316-325`）每次发/改回复都 `Reply.query.filter_by(post_id=...).all()` **全量拉取该帖所有回复**，只为序列化一条。
  - **进程内去重跨 worker 失效**：`_view_dedup`（`posts.py:42`）是模块级 dict，而 `forum-api.service:89-100` 实配 **`--workers 2 --threads 4`** → 同一访客被两个 worker 各计一次，**去重窗口实际被削弱**。
- **其它**：`_collect_descendant_ids`（`replies.py:20-51`）为算删除计数全量拉取；`stats.py` 每次 3 个全表 count 无缓存；图片压缩在请求线程内同步执行（CPU 密集阻塞 `gthread`）。
- **异常处理**：`upload.py::_write_meta`（`:63-66`）无 try（磁盘满/权限即 500 裸抛）；`user.py:254` `os.makedirs(avatar_dir, exist_ok=True)` 未包 try。
- **关键建议**：`selectinload` 预加载作者 + 用 `_reply_node` 改为增量查询；去重表下沉到 Redis 或改用数据库唯一键；抽后台任务做压缩。

---

## 2. 综合审查发现（去重合并后按严重度排序）

| #  | 严重度  | 类别     | 位置                                             | 问题描述                                                                                                  | 建议                                                                         | 来源成员         |
| -- | ---- | ------ | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ------------ |
| 1  | 🔴 高 | 并发/正确性 | `posts.py:42,85`；`forum-api.service:91`        | 浏览量去重表为**进程内 dict**，生产实配 `--workers 2` → 去重跨 worker 失效，同一访客被重复计数                                      | 下沉 Redis（`SETNX+EXPIRE`）或改 `view_log(post_id, visitor_key, ts)` 唯一键        | 排障手          |
| 2  | 🔴 高 | 性能     | `posts.py:254-278`；`models.py:108,198`         | 回复树序列化逐条懒加载作者，**N+M 次查询**；大帖详情页塌陷                                                                     | `selectinload(Reply.author)` 或一次性 `join(User)` 取作者映射                       | 排障手          |
| 3  | 🔴 高 | 性能     | `posts.py:316-325`                             | `_reply_node` 每次发/改回复**全量拉取该帖所有回复**再建树 → 写路径 O(n)                                                     | 改为只查该条 + 父/根链 + 直接子回复计数                                                    | 排障手          |
| 4  | 🟠 中 | 安全     | `auth.py:113-165`                              | 登录**无按账号失败节流/锁定**，仅 Nginx 全局 10r/s 每 IP                                                               | 引入 `Flask-Limiter` 或加失败计数（复用 TTL 模式）                                       | 安全卫士         |
| 5  | 🟠 中 | 安全     | `app.py:156-161`                               | `/health` 回显数据库异常原文 `str(exc)`，泄露内部信息                                                                 | 仅返回 `ok`/`error`，详情写日志                                                     | 安全卫士         |
| 6  | 🟠 中 | 安全     | `auth.py:141-153`                              | 改密码后**旧 JWT 仍有效**（无 `pwd_ver`/黑名单），最长 24h                                                             | JWT 加 `pwd_ver` 声明，校验不一致即 401                                              | 安全卫士         |
| 7  | 🟠 中 | 性能     | `replies.py:20-51`                             | `_collect_descendant_ids` 全量拉取该帖 (id,parent_id) 仅为算 `deleted_count`                                   | 用递归 CTE 或改返回"已删除"不报精确数                                                     | 排障手          |
| 8  | 🟠 中 | 性能     | `stats.py:27-29`                               | 每次请求 3 次全表 `count(*)`，无缓存                                                                             | 加 30-60s TTL 缓存；或维护计数表                                                     | 排障手          |
| 8b | 🟠 中 | 数据库    | `models.py:38,92`；`init.sql:38,57`             | `posts.created_at` / `replies.created_at` **均无索引**，而列表页 `ORDER BY created_at DESC` + 分页 → 全表 filesort | 加 `idx_posts_created`、`idx_replies_post_created(post_id, created_at)` 复合索引 | 排障手（主理人复核确认） |
| 9  | 🟠 中 | 性能/稳定性 | `upload.py:103,347`                            | 图片压缩在**请求线程内同步执行**（1920px 重采样 CPU 密集），阻塞 `gthread`                                                    | 移出请求线程（队列/后台 worker）或限制并发                                                  | 排障手          |
| 10 | 🟠 中 | 可维护性   | `auth.py:19-33` vs `user.py:41-54`             | 密码哈希/校验**两份完全重复**实现                                                                                   | 抽 `utils/security.py` 统一                                                   | 产品官 + 排障手    |
| 11 | 🟠 中 | 可维护性   | `posts.py`(684行) ← `upload/user/replies`       | 跨模块 import **私有函数**（`require_auth`/`_reply_node`），`posts.py` 成依赖中心                                    | 抽 `utils/` 或 `services/`，路由层只留 HTTP 编排                                     | 产品官          |
| 12 | 🟡 低 | 异常处理   | `upload.py:63-66`；`user.py:254`                | `_write_meta` / `os.makedirs(avatar_dir)` 未包 try，磁盘满/权限问题裸抛 500                                       | 补 try 并返回友好 5xx                                                            | 排障手          |
| 13 | 🟡 低 | 安全/UI  | `auth.js:225`；`post.js` 徽标                     | `role` 存 localStorage 并据以渲染 ADMIN 徽标（后端有真校验）                                                          | 徽标以后端响应字段为准，前端不缓存权威角色                                                      | 安全卫士         |
| 14 | 🟡 低 | 可维护性   | `create_post`/`update_post`；4 处 owner-or-admin | 校验逻辑与权限判断重复书写                                                                                         | 抽 `_validate_post_payload()` 与 `_can_modify()`                             | 产品官          |
| 15 | 🟡 低 | 性能     | `posts.py:161-165`；`reply_chain_info:176`      | `_prepare_reply_structure` 为 O(n·depth)，且 `_reply_node` 只序列化一条却重算全帖                                   | 随 #3 一并消除；必要时缓存链信息                                                         | 排障手          |
| 16 | 🟡 低 | 测试/工程  | `backend/tests/`；无 CI                          | user/stats/分页边界/暴力破解**无测试**；测试需手动跑                                                                    | 补 pytest 用例 + GitHub Actions/脚本化 CI                                        | 排障手          |

---

## 3. 优先级排序与优化建议明细

> 每条建议按 **功能名称 / 具体实现方式 / 涉及的核心模块 / 预期效果 / 对现有功能的影响** 呈现。

### 🔴 高优先级（影响核心流程或稳定性）

#### H1. 浏览量去重下沉到共享存储（修复跨 worker 失效）

- **具体实现方式**：当前 `_view_dedup` 是模块级 dict（`posts.py:42`），而生产 `--workers 2`（`forum-api.service:91`）使两个 worker 各持一份，去重窗口形同虚设。两级方案：**方案 A（推荐，零新依赖）**：新建 `view_log(post_id, visitor_key, created_at)` 表，`UNIQUE(post_id, visitor_key)`；自增前 `INSERT ... ON CONFLICT DO NOTHING`，受影响行数为 1 才自增，并定期清理超窗口记录。**方案 B**：引入 Redis，`SET key NX EX 1800`，与现有语义一一对应（代码注释已预留该演进路径）。
- **涉及的核心模块**：`routes/posts.py`（`_reserve_view`/`_release_view`/`get_post`）、`models.py`、`database/init.sql`、`config.py`（`VIEW_DEDUP_WINDOW_SECONDS` 复用）。
- **预期效果**：去重窗口在 2 worker 下恢复真实 30 分钟语义，浏览量统计准确；顺带解决多实例水平扩展问题。
- **对现有功能的影响**：🟢 低风险。API 契约与响应字段完全不变；需新增一张表与迁移脚本（可复用 `init_db.py` 的幂等 `ensure_*` 模式）。`test_view_count.py` 的 21 项断言需相应调整假设（当前测的是进程内行为）。

#### H2. 回复树作者信息预加载（消除 N+1）

- **具体实现方式**：`Reply.author` / `Post.author` 为 `lazy="select"`（`models.py:49,108`）。在 `get_post` 拉回复时改一次性取作者映射：
  ```python
  replies = (Reply.query.filter_by(post_id=post.id)
             .options(selectinload(Reply.author))
             .order_by(Reply.created_at.asc(), Reply.id.asc()).all())
  ```
  或更彻底：`join(User)` 一次取回 `(reply, username, nickname, avatar_url, role)` 元组，`_reply_node_dict` 改为消费该映射，彻底不触发懒加载。`parent.author` 同理从同一 `index` 内取（父回复已在列表中，避免二次查询）。
- **涉及的核心模块**：`routes/posts.py`（`get_post`、`_reply_node_dict:254`、`_build_reply_tree`）。
- **预期效果**：大帖详情查询数从 **3+N+M 降至 3**（500 回复：约 1000+ → 3 次），详情页响应时间可降一个数量级。
- **对现有功能的影响**：🟢 低风险。输出 JSON 结构完全不变，纯读取优化；需注意 `selectinload` 在 SQLite/MariaDB 行为一致。

#### H3. 回复写路径不再全量拉取（消除 O(n) 写放大）

- **具体实现方式**：`_reply_node`（`posts.py:316-325`）当前 `Reply.query.filter_by(post_id=...).all()` 拉全帖再建树，只为返回一条。改为**定点查询**：直接取该条 + 沿 `parent_id` 回溯的祖先链 + `count()` 其直接子回复数，按需组装 `depth`/`root_id`/`reply_to_display_name`/`reply_count`。
- **涉及的核心模块**：`routes/posts.py`（`_reply_node`、`_prepare_reply_structure`、`_reply_chain_info`）、`routes/replies.py`（编辑回复同样调用）。
- **预期效果**：发/改回复从 O(n·depth) 降为 O(depth)，热帖下写延迟稳定；同时消除 #15 的重复计算。
- **对现有功能的影响**：🟡 中风险（唯一需要谨慎的 P0）。返回字段必须逐项比对（`depth`、`root_id`、`reply_count`、`reply_to_display_name`），建议先补一组"写回复响应字段"回归断言再改；`test_reply_hierarchy` 42 项断言是最好的安全网。

### 🟠 中优先级（提升体验或可维护性）

#### M1. 登录失败节流与账号锁定

- **具体实现方式**：引入 `Flask-Limiter`（`key_func` 用 `username+IP`，如 `5/minute;20/hour`），或零依赖方案——复用现有 TTL 模式加 `_login_attempts: {username: (count, window_start)}`，超阈值返回 429/423。生产 Nginx 侧对 `/api/auth/login` 单独再加一道 `limit_req` 更严格 zone。
- **涉及的核心模块**：`routes/auth.py`（`login`）、`config.py`（新增阈值配置）、`deploy/nginx.conf`。
- **预期效果**：抵御在线暴力破解与撞库，补齐 OWASP A07 认证失败防护。
- **对现有功能的影响**：🟢 低。正常用户无感；需防止误伤（阈值放宽 + 成功登录清零计数）。

#### M2. `/health` 不泄露内部异常 + JWT 失效机制

- **具体实现方式**：`app.py:156-161` 改为只返回 `{"status":"ok","database":"ok"|"error"}`，异常详情落日志。同时给 JWT 载荷加 `pwd_ver`（= 密码哈希前 8 位或版本号），`require_auth` 解码后与 `user.password_hash` 派生值比对，不一致即 401 —— 实现"改密码即踢下线"。
- **涉及的核心模块**：`app.py`（`health`）、`routes/posts.py`（`require_auth`）、`routes/auth.py`（签发处）、`models.py`（可加 `pwd_ver` 列，或用现有 `password_hash` 派生）。
- **预期效果**：消除信息泄露 + 会话安全闭环（改密码/疑似泄露可立即失效旧 token）。
- **对现有功能的影响**：🟡 中。加 `pwd_ver` 后**存量 token 全部失效一次**（用户需重新登录），建议灰度或在维护窗口发版。

#### M3. 统计与删除计数去全表扫描

- **具体实现方式**：`stats.py` 三个 count 加 60s TTL 缓存（进程内即可，容忍轻微滞后）；`_collect_descendant_ids` 改用递归 CTE（MariaDB 10.2+/SQLite 3.8.3+ 均支持 `WITH RECURSIVE`）一次算出直接子节点数；**补索引（主理人已复核确认缺失）**——`posts.created_at` 与 `replies.created_at` 当前均无索引（`models.py:38,92`、`init.sql:38,57`），而 `list_posts` 按 `created_at DESC` 排序、回复按 `created_at ASC` 排序，MariaDB 下会触发 filesort。建议加 `idx_posts_created(created_at)` 与复合索引 `idx_replies_post_created(post_id, created_at)`（后者同时支撑"取某帖回复并排序"与 `reply_count` 计数）。
- **涉及的核心模块**：`routes/stats.py`、`routes/replies.py`、`routes/posts.py`、`database/init.sql`。
- **预期效果**：首页统计与删帖响应稳定；大表下排序/计数走索引。
- **对现有功能的影响**：🟢 低。缓存仅引入 60s 数据滞后（统计场景可接受）；索引新增对写略有开销，可忽略。

#### M4. 图片压缩移出请求线程

- **具体实现方式**：`complete_upload` 中 `_compress_image`（`upload.py:103,347`）是 CPU 密集（1920px LANCZOS 重采样）。可在 gunicorn 侧把该接口拆分到独立 worker 池，或引入轻量任务队列（RQ/Celery + Redis）；短期最省事方案是**限制并发**（`--threads` 下调 + 上传接口加信号量），并给压缩设尺寸/像素上限防"解压炸弹"。
- **涉及的核心模块**：`routes/upload.py`（`_compress_image`、`complete_upload`）、`deploy/forum-api.service`、`config.py`（`IMAGE_MAX_DIMENSION` 已有）。
- **预期效果**：上传大图不再阻塞同 worker 的浏览/发帖请求，P99 延迟改善。
- **对现有功能的影响**：🟡 中。若引入队列则上传从"同步返回 url"变为"异步/轮询"，属接口语义变化 —— 建议优先选"限并发"这类无契约变更的方案。


#### M5. 抽出公共安全工具与回复树服务（消除重复与反向依赖）

- **具体实现方式**：新建 `backend/utils/security.py`（`hash_password`/`verify_password`/`validate_password_strength`/`require_auth`），`backend/services/reply_tree.py`（`_prepare_reply_structure`/`_build_reply_tree`/`_reply_node`）。`auth.py`/`user.py`/`posts.py`/`replies.py` 统一引用；`require_auth` 可保留 kwargs 注入以兼容现有签名，或迁移到 Flask `g` 并同步改 4 个调用点。
- **涉及的核心模块**：新增 `utils/`、`services/`；改造 `routes/{auth,user,posts,replies,upload}.py`；`models.py` 不变。
- **预期效果**：`posts.py` 从 684 行显著瘦身；密码逻辑单点维护（改一次生效全局）；反向依赖解除，可测试性提升。
- **对现有功能的影响**：🟡 中。纯重构无行为变更，但改动面广（5 个文件 + 全部 import），**必须依赖现有 126 项测试作回归**；建议单独一个 PR，不与功能改动混合。

#### M6. 补齐测试与 CI

- **具体实现方式**：把 4 个自测脚本统一迁移/封装为 `pytest`（现有脚本已是 `test_*` 命名 + 退出码约定，迁移成本低），补 `test_user_profile.py`（资料/密码/头像）、`test_stats_pagination.py`（列表分页边界、stats）、`test_auth_throttle.py`（M1 落地后）。加一条 CI（push 时跑全量）。
- **涉及的核心模块**：`backend/tests/`、新增 CI 配置。
- **预期效果**：回归自动化，重构（M5）与优化（H2/H3）有安全网；覆盖率从"4 模块"扩到"全接口"。
- **对现有功能的影响**：🟢 无（仅新增测试资产）。

### 🟡 低优先级（锦上添花）

| #  | 功能名称      | 实现要点                                                  | 涉及模块                                       | 预期效果                  |
| -- | --------- | ----------------------------------------------------- | ------------------------------------------ | --------------------- |
| L1 | 上传路径异常兜底  | `_write_meta` / `os.makedirs` 补 try，返回友好 5xx          | `upload.py:63`、`user.py:254`               | 磁盘满/权限异常不再裸抛          |
| L2 | 前端不缓存权威角色 | ADMIN 徽标改用后端响应 `role`，localStorage 仅作展示缓存             | `auth.js:225`、`post.js` 徽标                 | 消除 UI 层角色伪造的视觉误导      |
| L3 | 响应式与可访问性  | 移动端断点、`aria-label`、焦点管理（`style.css` 已 3417 行）         | `frontend/css/style.css`、各 `*.html`        | 移动体验与无障碍合规            |
| L4 | 净化器白名单补充  | 补 `<picture>`/`<source>`；保持"先高亮后净化"顺序的注释说明            | `api.js:461-484`                           | 兼容现代图片语法，减少未来误报       |
| L5 | 帖子搜索      | 标题/内容 `LIKE` 起步，量大后接全文索引/MariaDB FTS                  | `routes/posts.py`、`index.js`               | **超出当前需求范围**，产品演进项    |
| L6 | 结构化日志与指标  | 统一 `logger` 字段化；接入现有 `deploy/monitor.py` 做 5xx/P99 告警 | `app.py`、`deploy/monitor.py`、`crontab.txt` | 线上可观测性，便于定位 H2/H3 类问题 |

---

## ✅ 行动清单

| #  | 行动                                               | 负责方     | 紧急度    | 期望完成         |
| -- | ------------------------------------------------ | ------- | ------ | ------------ |
| 1  | 浏览量去重下沉（加 `view_log` 唯一键或 Redis）                 | 后端      | **P0** | 本轮迭代         |
| 2  | 回复详情 `selectinload` 预加载作者，消除 N+1                 | 后端      | **P0** | 本轮迭代         |
| 3  | `_reply_node` 改为定点查询，消除写路径 O(n)                  | 后端      | **P0** | 本轮迭代（先补回归断言） |
| 4  | `/health` 停止回显异常 + JWT 加 `pwd_ver`               | 后端      | P1     | 下轮迭代         |
| 5  | 登录失败节流（`Flask-Limiter` 或 TTL 计数）                 | 后端      | P1     | 下轮迭代         |
| 6  | 抽 `utils/security.py` + `services/reply_tree.py` | 后端      | P1     | 下轮迭代（独立 PR）  |
| 7  | `stats` 缓存 + 复合索引 + 递归 CTE 统计                    | 后端      | P1     | 下轮迭代         |
| 8  | 测试迁移到 pytest + 补 3 组用例 + CI                      | 后端      | P2     | 排期           |
| 9  | 图片压缩移出请求线程（优先"限并发"方案）                            | 后端 + 运维 | P2     | 排期           |
| 10 | 上传路径 try 兜底、前端角色来源、可访问性                          | 前后端     | P2     | 排期           |

---

## ⚠️ 待完善 / 已知局限

- **需求范围外的功能未实现**（搜索、点赞/收藏、通知、私信、标签、举报、置顶、密码找回、邮箱验证）经核对 `需求文档.md` **不属于 V1.1–V3.0 需求**，未列为缺陷，仅作演进参考（L5）。
- **误报排除（已复核，不构成缺陷）**：SQL 注入（全 ORM，`db.text("SELECT 1")` 为常量无注入面）、上传目录遍历（`realpath` + 前缀边界校验到位）、XSS（自研白名单净化 + CSP 双重防护）、SQLite 外键级联（`PRAGMA foreign_keys=ON` 已正确开启）。
- **安全卫士曾判定"净化器可被绕过 XSS"为高危，经交叉复核下调**：生产 CSP 为 `script-src 'self'` 且**无 `unsafe-inline`**，注入的事件处理器与内联脚本会被浏览器直接拦截，实际可利用性显著低于初判，故未列入 P0。
- **未实测生产环境**：以上性能结论基于代码静态分析 + 本地 SQLite 测试库，未在 MariaDB + 大数据的生产形态下压测，建议 P0 修复后做一次真实数据量验证。
- **依赖版本漏洞未核实**：`Flask 3.0.3`/`Werkzeug 3.0.3`/`Pillow 11.1.0` 等固定版本是否有已知 CVE，需用 `pip-audit` 或 `safety` 联网核实（本次为离线审查）。

---

## 📚 成员产出索引（原始产出摘要）

- **gstack-product-reviewer（产品官）**：需求覆盖度逐条核对、模块职责表、领域边界与重复代码分析（`_hash_password` 双份、`posts.py` 依赖中心）。
- **gstack-security-officer（安全卫士）**：OWASP Top 10 检查表、STRIDE 四资产建模、登录节流/`/health` 泄露/JWT 失效三项真实缺陷及误报排除。
- **gstack-investigator（排障手）**：详情页数据流逐跳拆解（3+N+M 查询）、写路径 O(n)、跨 worker 去重失效、异常处理与测试覆盖缺口清单（含真实行号）。

---

## 🔎 审查过程说明（关于"未修改"约束）

- 本次审查**未修改任何业务代码**；`backend/`、`frontend/js/`、`database/` 均未改动。
- 复核中发现一名成员在执行时误在项目根目录生成了一份临时报告文件 `_investigation_report_investigator.md`，**已按"不修改"要求删除**，其内容已完整汇入本报告。
- 工作区中 `frontend/404.html`、`frontend/50x.html`、`frontend/css/style.css` 的既有改动，以及 `deliverables/gstack/` 下两份更早的报告，**均为本次审查开始前就已存在**，非本次产生。

---

> 本报告由软件工坊 AI 协作生成，关键决策请由工程负责人复核。
