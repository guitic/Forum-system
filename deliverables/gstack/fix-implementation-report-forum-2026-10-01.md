# 修复实施报告 · 论坛系统 P0/P1/P2 优化

> 依据文档：`deliverables/gstack/code-review-optimization-plan-forum-2026-10-01.md`
> 修复范围：**全部 3 个 P0 + 7 个 P1 + 6 个 P2**（含行动清单第 9 项 M4）
> 完成日期：2026-10-01
> 验收结果：**7 个测试套件 / 199 项断言全部通过；26 条路由不变，API 契约零破坏**

---

## 一、总体结论

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 浏览量去重 | 进程内 dict，`--workers 2` 下跨 worker 失效 | `view_log` 表 + 唯一键，多 worker/多实例一致 |
| 详情页查询数（500 回复） | ~1000+ 次（N+1） | 固定 2~3 次 |
| 回复写路径复杂度 | O(n·depth)，随帖回复数线性增长 | 常数级（仅与祖先链长相关，≤3） |
| 生产密钥/鉴权 | JWT 无 `pwd_ver`，改密后旧 token 24h 仍有效 | `pwd_ver` 校验，改密即刻失效 |
| 登录防护 | 无节流 | 应用层 TTL 节流 + Nginx 独立限速双保险 |
| `/health` | 回显 DB 异常原文（泄露驱动/主机/库名） | 仅回 `ok/error`，详情仅进日志 |
| 前端管理员判定 | 仅凭 localStorage（可伪造） | 以 JWT 签发的 `role` 声明为准 |
| 日志 | 无统一格式，缺请求上下文 | 结构化 text/json，含 request_id/status/耗时 |
| 测试覆盖 | 126 项 / 4 套件 | **199 项 / 7 套件** + CI |

---

## 二、P0 修复明细（3 项）

### P0-1 浏览量去重下沉到数据库
- **文件**：`backend/models.py`、`backend/routes/posts.py`、`backend/config.py`、`database/init.sql`
- **实现**：新增 `ViewLog` 模型，`UNIQUE(post_id, visitor_key)` 承载跨进程去重语义：
  - `_reserve_view()`：先 UPDATE 窗口内既有记录（滑动窗口）→ 无匹配则清过期记录 + INSERT；
    `IntegrityError` 视为并发重复访问，返回 `False`
  - `_release_view()`：自增失败时释放占位，使访客下次可重新计数
  - `_cleanup_view_log()`：按 `VIEW_LOG_CLEANUP_EVERY`（默认 200）概率抽样清理，避免每请求全表 DELETE
- **验证**：`test_view_count` 24 项含 30 线程不同访客原子自增无丢失、10 线程同访客唯一键去重

### P0-2 详情页 N+1 消除
- **文件**：`backend/routes/posts.py::get_post`
- **实现**：replies 查询追加 `.options(selectinload(Reply.author))`
- **效果**：500 回复帖的查询数从 ~1000+ 降到固定 2~3 次

### P0-3 回复写路径改定点查询
- **文件**：`backend/routes/posts.py::_reply_node`（后下沉至 `services/reply_tree.py`）
- **实现**：沿 `parent_id` 链回溯收集祖先，由远到近复现「展示深度」递归，与全量建树口径严格一致；
  `root_id`/`reply_count`/`reply_to_display_name` 各自定点查询
- **兼容性**：输出字段与旧实现**逐项一致**，由 `test_reply_hierarchy` 42 项锁定

---

## 三、P1 修复明细（7 项）

| 编号 | 项目 | 关键改动 |
|------|------|----------|
| P1-4 | `/health` 收敛 + JWT `pwd_ver` | 只回 `{status, database}`；`users.pwd_ver` 列 + `require_auth` 校验；改密时 `pwd_ver += 1` |
| P1-5 | 登录节流 | 新增 `utils/throttle.py`（`(username, ip)` TTL 窗口，5 次失败锁 15 分钟）；Nginx 加 `zone=forum_login rate=3r/m` + `location = /api/auth/login` |
| P1-6 | 抽公共模块 | 新增 `utils/security.py`（消除 `auth.py`/`user.py` 双份哈希函数）；新增 `services/reply_tree.py`（消除 `replies.py` → `routes/posts.py` 反向依赖） |
| P1-7 | stats 缓存 + 索引 + CTE | 60s TTL 缓存；`idx_posts_created`、`idx_replies_post_created`；删除计数改递归 CTE |

---

## 四、P2 修复明细（6 项 + M4）

| 编号 | 项目 | 关键改动 |
|------|------|----------|
| P2-8 | 测试 + CI | 新增 3 个套件（70 项断言）、`backend/run_tests.py`、`.github/workflows/ci.yml` |
| M4 | 图片压缩限并发 | `_COMPRESS_SEMAPHORE` 限制同时压缩数（默认 2）+ 超时 503；`IMAGE_MAX_PIXELS` 防解压炸弹。**接口语义不变** |
| L1 | 上传路径兜底 | `upload.py::_write_meta`、`user.py::os.makedirs` 加 try/except |
| L2 | 前端角色来源 | `API.getRole()/API.isAdmin()` 优先解码 JWT 的 `role` 声明，localStorage 仅回退 |
| L3 | 响应式/无障碍 | 抽屉导航补 `aria-pressed`；toast 容器升级为 `aria-live` live region；新增 `.skip-link` |
| L4 | 清洗白名单 | 新增 `<picture>`/`<source>`；`img` 补 `srcset` 等；`isSafeSrcset()` 逐项校验协议 |
| L6 | 结构化日志 | `utils/logging_setup.py`：幂等配置、请求上下文注入、text/json 双格式、可选轮转文件 |

---

## 五、验收记录

### 测试套件（199 项全通过）

| 套件 | 项数 | 覆盖重点 |
|------|------|----------|
| `test_reply_hierarchy.py` | 42 | 回复层级/深度收敛/悬空引用/级联删除 |
| `test_edit.py` | 35 | 编辑鉴权/并发最后提交为准/XSS 入库 |
| `test_view_count.py` | 24 | 去重/跨 IP/窗口过期/故障注入/30 线程并发 |
| `test_image_upload.py` | 28 | 分片/断点续传/越权/GIF 保原/遍历防护 |
| `test_user_profile.py`（新） | 25 | 资料读写鉴权、**pwd_ver 旧 token 失效语义** |
| `test_stats_pagination.py`（新） | 28 | stats 字段/TTL 缓存/分页边界收敛/排序 |
| `test_auth_throttle.py`（新） | 17 | 阈值锁定/清零/多维度隔离/窗口过期/XFF |

### 契约与迁移

- **路由表 26 条不变**（已 dump 核对），API 契约零破坏
- `init_db.py` 迁移幂等验证通过：`pwd_ver` 列、`idx_posts_created`、`idx_replies_post_created`、`view_log` 表全部就绪
- `database/init.sql` 与模型定义保持一致
- 冒烟：`/health`、`/api/posts`、`/api/stats`、`/` 均 200；`/api/nope` 返回 JSON 404

---

## 六、注意事项（交付后需知）

1. **M2 副作用**：JWT 新增 `pwd_ver` 校验 ⇒ **所有存量 token 立即失效，用户需重新登录一次**（已获用户确认）。
2. **Nginx 部署**：新增 `location = /api/auth/login`，部署时需同步 `deploy/nginx.conf` 并 `nginx -t` 验证。
3. **新增环境变量**（均有默认值，可不配）：`IMAGE_COMPRESS_CONCURRENCY`、`IMAGE_MAX_PIXELS`、
   `LOG_LEVEL`、`LOG_FORMAT`、`LOG_FILE`、`VIEW_LOG_CLEANUP_EVERY`、`LOGIN_THROTTLE_*`、`STATS_CACHE_TTL_SECONDS`。
4. **未做真实生产压测**：性能结论基于代码分析 + SQLite 测试库；建议在 MariaDB + 生产数据量下复验（报告已列为局限）。
5. **依赖 CVE 未核实**：`Flask 3.0.3` / `Werkzeug 3.0.3` / `Pillow 11.1.0` 等建议用 `pip-audit` 联网复查（原审查为离线）。
6. **图片压缩仍为同步**：本次采用报告推荐的「限并发」轻量方案；若后续需彻底异步，需引入任务队列并另行设计接口语义。
