/* ==========================================================================
   index.js - 首页逻辑
   - 帖子信息流（首页 / 最新 / 热门 / 标签 四个 Tab）
   - 加载骨架屏、空状态、错误状态
   - 搜索（标题 / 内容，当前数据源内过滤）
   - 侧栏：热门帖子 TOP5 / 社区统计 / 关于本站
   - 发帖模态框（Markdown + 图片上传，Ctrl/Cmd+Enter 快捷发布）
   - 分页（页码 + 省略号 + 上/下一页）
   ========================================================================== */

(function (global) {
  "use strict";

  var API = global.ForumAPI;
  if (!API) {
    console.error("[index.js] ForumAPI 未加载，请先引入 js/api.js");
    return;
  }

  // 默认每页条数
  var PAGE_SIZE = 20;
  var currentPage = 1;
  var totalPages = 1;
  var totalCount = 0;

  // Tab：default(首页) / latest(最新) / hot(热门) / tags(标签)
  var currentTab = "default";
  // 搜索词（对当前数据源过滤）
  var currentQuery = "";
  // 当前信息流数据缓存（供搜索过滤）
  var feedCache = [];

  // DOM 引用（在 init 中绑定）
  var dom = {};

  // 发帖图片上传器（image-upload.js 工厂创建）
  var postUploader = null;
  // 搜索输入防抖
  var searchTimer = null;

  /* ------------------------------------------------------------------------
   * 工具
   * ---------------------------------------------------------------------- */
  function escapeAttr(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* ------------------------------------------------------------------------
   * Markdown 渲染（与详情页 post.js 保持一致的管线）
   * - 列表卡片需要展示正文中的图片，因此不能再只用纯文本摘要
   * - 复用全局 marked + API.sanitize，安全策略与详情页完全一致
   * ---------------------------------------------------------------------- */

  /** 是否存在 marked 全局（index.html 已引入 vendor/marked.min.js） */
  function getMarked() {
    return global.marked || (typeof window !== "undefined" ? window.marked : null);
  }

  /** marked 选项配置（一次性，与详情页对齐） */
  var markedConfigured = false;
  function setupMarked() {
    var marked = getMarked();
    if (!marked || markedConfigured) return;
    try {
      if (marked.use) {
        marked.use({ gfm: true, breaks: true, pedantic: false, async: false });
      } else if (marked.setOptions) {
        marked.setOptions({
          gfm: true, breaks: true, pedantic: false,
          smartLists: true, mangle: false, headerIds: false, sanitize: false,
        });
      }
      markedConfigured = true;
    } catch (e) {
      console.warn("[index.js] marked 配置失败:", e);
    }
  }

  /**
   * 渲染 Markdown 为安全 HTML
   * @param {string} md
   * @returns {string} 已 sanitize 的 HTML
   */
  function renderMarkdown(md) {
    if (!md) return "";
    var marked = getMarked();
    var html;
    if (marked) {
      try {
        html = marked.parse(md);
      } catch (e) {
        console.warn("[index.js] marked 解析失败，回退纯文本:", e);
        html = "<p>" + escapeAttr(md) + "</p>";
      }
    } else {
      html = "<p>" + escapeAttr(md) + "</p>";
    }
    return API.sanitize(html);
  }

  /**
   * URL 安全校验（与 api.js 内部 isSafeUrl 协议白名单严格一致）
   * api.js 未导出该函数，此处等价实现，确保列表侧不会渲染危险 URL
   */
  var SAFE_PROTOCOLS = ["http:", "https:", "mailto:", "tel:", "#"];
  function isSafeUrl(url) {
    if (!url) return false;
    var u = String(url).trim().toLowerCase();
    if (/[\u0000-\u001f\u007f]/.test(u)) return false;
    if (/^(javascript|data|vbscript|file):/i.test(u)) return false;
    var c0 = u.charAt(0);
    if (c0 === "/" || c0 === "." || c0 === "#" || c0 === "?") return true;
    for (var i = 0; i < SAFE_PROTOCOLS.length; i++) {
      if (u.indexOf(SAFE_PROTOCOLS[i]) === 0) return true;
    }
    return false;
  }

  /**
   * 从 Markdown 正文中提取图片 URL 列表（按出现顺序，去重）
   * 仅保留安全 URL；上限由调用方控制。
   * @param {string} md
   * @param {number} max
   * @returns {string[]}
   */
  function extractImageUrls(md, max) {
    if (!md) return [];
    var urls = [];
    var seen = Object.create(null);
    var re = /!\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)/g;
    var m;
    while ((m = re.exec(md)) !== null) {
      var url = (m[1] || "").trim();
      if (!url) continue;
      if (!isSafeUrl(url)) continue;
      if (seen[url]) continue;
      seen[url] = true;
      urls.push(url);
      if (max && urls.length >= max) break;
    }
    return urls;
  }

  /**
   * 缩略图缩放比例：统一缩至原图尺寸的 1/3（即上一版 1/6 的 2 倍）
   * 通过 CSS 自定义属性下发，由 CSS 换算为响应式尺寸
   */
  var THUMB_SCALE = 1 / 3;

  /**
   * 构建列表卡片的图片缩略区
   * 处理三类边界：懒加载、占位图、加载失败
   * 尺寸策略：统一缩放至原图 1/3，保持原始宽高比，且不溢出容器
   * @param {string[]} urls
   * @returns {HTMLElement|null}
   */
  function buildPostImages(urls) {
    if (!urls || !urls.length) return null;

    var wrap = document.createElement("div");
    wrap.className = "post-images" + (urls.length === 1 ? " single" : "");

    urls.forEach(function (url) {
      var fig = document.createElement("figure");
      fig.className = "post-image-item is-loading";

      var img = document.createElement("img");
      img.className = "post-image-thumb";
      img.alt = "帖子图片";
      // 懒加载：视口外不请求；异步解码避免阻塞主线程
      img.loading = "lazy";
      img.decoding = "async";
      img.src = url;

      // 加载成功：按原图宽高比换算 1/3 尺寸，写入 CSS 变量
      img.addEventListener("load", function () {
        fig.classList.remove("is-loading", "is-error");
        fig.classList.add("is-loaded");

        var nw = img.naturalWidth;
        var nh = img.naturalHeight;
        if (nw > 0 && nh > 0) {
          // 1/3 目标尺寸（px），作为宽度基准
          var targetW = Math.round(nw * THUMB_SCALE);

          // 宽高比（CSS aspect-ratio 支持单个数值：W/H）
          var ratio = nw / nh;
          // 写入“基准宽度”变量；最终宽度由 CSS 派生，
          // 以便窄屏媒体查询能用 min() 稳定压小（内联变量无法被媒体查询覆盖）
          fig.style.setProperty("--thumb-w-base", targetW + "px");
          fig.style.setProperty("--thumb-ratio-num", String(ratio));
        }
      });

      // 加载失败：降级为占位块（不回退成破图）
      img.addEventListener("error", function () {
        fig.classList.remove("is-loading", "is-loaded");
        fig.classList.add("is-error");
        img.removeAttribute("src");
        img.classList.add("post-image-thumb--broken");
        var ph = document.createElement("div");
        ph.className = "post-image-fallback";
        ph.textContent = "图片加载失败";
        if (!fig.querySelector(".post-image-fallback")) fig.appendChild(ph);
      });

      fig.appendChild(img);
      wrap.appendChild(fig);
    });

    return wrap;
  }

  /** 数字千分位格式化 */
  function formatNum(n) {
    n = Number(n);
    if (!isFinite(n) || n < 0) n = 0;
    return n.toLocaleString("zh-CN");
  }

  /* ------------------------------------------------------------------------
   * 头部导航渲染
   * ---------------------------------------------------------------------- */
  function renderNav() {
    var actions = dom.navActions;
    if (!actions) return;
    actions.innerHTML = "";

    // 同步侧栏中的"发布新帖"按钮可见性
    var sidePostBtn = document.getElementById("open-post-btn");

    if (API.isLoggedIn()) {
      var username = "";
      var nickname = "";
      var avatarUrl = "";
      var role = "";
      try {
        username = localStorage.getItem("username") || "";
        nickname = localStorage.getItem("nickname") || "";
        avatarUrl = localStorage.getItem("avatar_url") || "";
        // L2：角色以 JWT 声明为准，localStorage 仅作回退
        role = API.getRole();
      } catch (e) { /* ignore */ }
      var displayName = nickname || username;

      // 头像 + 下拉菜单（个人中心 / 退出登录）
      var wrap = API.el("div", { class: "user-badge-wrap" });
      var trigger = API.el("div", {
        class: "user-badge user-badge-trigger",
        attrs: {
          title: displayName || "账号菜单",
          "aria-label": "账号菜单",
          "aria-haspopup": "true",
        },
      });
      var navAvatarCls = "avatar" + (role === "admin" ? " avatar-admin" : "");
      if (avatarUrl) {
        trigger.innerHTML =
          '<span class="' + navAvatarCls + ' avatar-img-wrap">' +
          '<img class="avatar-img" src="' + escapeAttr(avatarUrl) + '" alt="用户头像" />' +
          "</span>";
      } else {
        trigger.innerHTML =
          '<span class="' + navAvatarCls + '">' +
          API.avatarChar(displayName || "U") + "</span>";
      }

      var menu = API.el("div", { class: "user-badge-menu" });
      var headerLine = API.el("div", { class: "user-badge-menu-header" });
      headerLine.textContent = displayName + (role === "admin" ? " · ADMIN" : "");
      menu.appendChild(headerLine);

      var profileItem = API.el("a", {
        class: "user-badge-menu-item",
        attrs: { href: "profile.html" },
      }, ["⚙", " 个人中心"]);
      menu.appendChild(profileItem);

      var logoutItem = API.el("a", {
        class: "user-badge-menu-item user-badge-menu-danger",
        attrs: { href: "javascript:void(0)" },
      }, ["🚪", " 退出登录"]);
      logoutItem.addEventListener("click", handleLogout);
      menu.appendChild(logoutItem);

      wrap.appendChild(trigger);
      wrap.appendChild(menu);
      API.setupBadgeDropdown(wrap);
      actions.appendChild(wrap);

      // 导航发帖按钮（移动端侧栏按钮在信息流下方，导航入口保证可达）
      var postBtn = API.el("a", {
        class: "btn btn-primary btn-sm",
        attrs: { href: "javascript:void(0)" },
      }, ["✍ ", API.el("span", { class: "btn-text" }, ["发帖"])]);
      postBtn.addEventListener("click", openPostModal);
      actions.appendChild(postBtn);

      if (sidePostBtn) sidePostBtn.classList.remove("hidden");
    } else {
      var loginBtn = API.el("a", {
        class: "btn btn-primary btn-sm",
        attrs: { href: "auth.html?redirect=" + encodeURIComponent(window.location.href) },
      }, ["登录 / 注册"]);
      actions.appendChild(loginBtn);

      if (sidePostBtn) sidePostBtn.classList.add("hidden");
    }
  }

  function handleLogout() {
    API.clearToken();
    try {
      localStorage.removeItem("username");
      localStorage.removeItem("nickname");
      localStorage.removeItem("avatar_url");
      localStorage.removeItem("role");
    } catch (e) { /* ignore */ }
    renderNav();
    showToast("已退出登录", "info");
  }

  /* ------------------------------------------------------------------------
   * 信息流渲染
   * ---------------------------------------------------------------------- */
  function renderSkeleton() {
    var list = dom.postList;
    list.innerHTML = "";
    for (var i = 0; i < 6; i++) {
      list.appendChild(API.el("div", { class: "skeleton-card" }, [
        API.el("div", { class: "sk-avatar" }),
        API.el("div", { class: "sk-lines" }, [
          API.el("div", { class: "sk-line w40" }),
          API.el("div", { class: "sk-line w90" }),
          API.el("div", { class: "sk-line w75" }),
        ]),
      ]));
    }
  }

  function renderErrorState(err) {
    dom.postList.innerHTML = "";
    dom.postList.appendChild(API.el("div", { class: "error-banner" }, [
      "⚠ ", " ", err.message || "加载失败",
    ]));
    dom.postList.appendChild(API.el("div", { class: "empty-state" }, [
      API.el("div", { class: "icon", text: "🔌" }),
      API.el("div", { class: "text", text: "无法连接到后端服务" }),
      API.el("div", { class: "hint", text: "请确认后端 API 已启动，并刷新页面重试" }),
    ]));
  }

  /** 按搜索词过滤 */
  function applyFilter(posts) {
    if (!currentQuery) return posts;
    return posts.filter(function (p) {
      var t = ((p.title || "") + " " + (p.content || "")).toLowerCase();
      return t.indexOf(currentQuery) !== -1;
    });
  }

  function renderList(posts) {
    var list = dom.postList;
    if (!list) return;
    list.innerHTML = "";

    if (!posts || posts.length === 0) {
      var isFiltered = !!currentQuery;
      list.appendChild(
        API.el("div", { class: "empty-state" }, [
          API.el("div", { class: "icon", text: isFiltered ? "🔍" : "📭" }),
          API.el("div", { class: "text", text: isFiltered ? "没有匹配的帖子" : "还没有任何帖子" }),
          API.el("div", { class: "hint", text: isFiltered ? "换个关键词试试，或清除搜索条件" : "登录并创建第一个帖子，开启技术交流之旅" }),
        ])
      );
      return;
    }

    posts.forEach(function (post) {
      list.appendChild(renderPostCard(post));
    });
  }

  function renderPostCard(post) {
    var id = post.id;
    var title = post.title || "（无标题）";
    var author = post.display_name || post.nickname || post.username || post.user || "匿名";
    var content = post.content || "";
    var excerpt = API.excerpt(content, 100);
    var time = API.formatTime(post.created_at);
    var fullTime = API.formatDateTime(post.created_at);
    var replyCount = post.reply_count !== undefined
      ? post.reply_count
      : post.replies_count !== undefined
        ? post.replies_count
        : (post.replies ? post.replies.length : 0);
    var viewCount = post.view_count !== undefined ? post.view_count : 0;
    var isAdmin = post.role === "admin";

    // 左列：头像（按角色配色）+ 回复数徽章
    var avatarCls = "post-avatar" + (isAdmin ? " avatar-admin" : "");
    var voteCol;
    if (post.avatar_url) {
      voteCol = API.el("div", { class: "post-vote" });
      var avatarWrap = API.el("div", { class: avatarCls + " post-avatar-img-wrap" });
      avatarWrap.innerHTML = '<img class="avatar-img" src="' + escapeAttr(post.avatar_url) + '" alt="" />';
      voteCol.appendChild(avatarWrap);
      voteCol.appendChild(API.el("div", {
        class: "vote-count",
        attrs: { title: "回复数" },
        text: replyCount > 0 ? String(replyCount) : "",
      }));
    } else {
      voteCol = API.el("div", { class: "post-vote" }, [
        API.el("div", { class: avatarCls, text: API.avatarChar(author) }),
        API.el("div", {
          class: "vote-count",
          attrs: { title: "回复数" },
          text: replyCount > 0 ? String(replyCount) : "",
        }),
      ]);
    }

    // 作者行：昵称 + 管理员徽标 + 时间
    var authorLine = API.el("div", { class: "post-card-author" }, [
      API.el("span", { class: "author", text: author }),
    ]);
    if (isAdmin) {
      authorLine.appendChild(API.el("span", { class: "tag-admin", text: "ADMIN" }));
    }
    authorLine.appendChild(API.el("span", {
      class: "meta-item",
      attrs: { title: fullTime },
      text: time,
    }));

    // 元信息行：回复数 + 浏览数
    var meta = API.el("div", { class: "post-meta" }, [
      API.el("a", {
        class: "meta-item replies-count reply-link",
        attrs: {
          href: "post.html?id=" + encodeURIComponent(id) + "#reply",
          title: "跳转到回复区并回复该帖",
        },
      }, ["💬", " ", String(replyCount), " 回复"]),
      API.el("span", {
        class: "meta-item meta-views",
        attrs: { title: "浏览数" },
      }, ["👁", " ", formatNum(viewCount), " 浏览"]),
    ]);

    var body = API.el("div", { class: "post-body" }, [
      authorLine,
      API.el("a", {
        class: "post-title-link",
        attrs: { href: "post.html?id=" + encodeURIComponent(id) },
        text: title,
      }),
      // 摘要：去 Markdown 标记后的纯文本（图片语法已在 excerpt 中剔除）
      API.el("div", {
        class: "post-excerpt" + (excerpt ? "" : " text-light"),
        text: excerpt || "（无内容）",
      }),
    ]);

    // 正文图片缩略区：与详情页一致地渲染（懒加载 + 占位 + 失败降级）
    // 单独的容器，避免被 .post-excerpt 的 2 行截断裁掉
    var imageUrls = extractImageUrls(content, 3);
    var images = buildPostImages(imageUrls);
    if (images) body.appendChild(images);
    body.appendChild(meta);

    return API.el("article", {
      class: "card card-hover post-card",
      attrs: { "data-id": id },
    }, [voteCol, body]);
  }

  /* ------------------------------------------------------------------------
   * 分页
   * ---------------------------------------------------------------------- */
  function renderPagination(state) {
    var pg = dom.pagination;
    if (!pg) return;
    pg.innerHTML = "";
    if (!state || totalPages <= 1) return;

    var page = state.page || 1;
    var pages = totalPages;

    var info = API.el("span", {
      class: "page-info",
      text: "第 " + page + " / " + pages + " 页 · 共 " + formatNum(totalCount) + " 篇",
    });

    // 上一页
    var prevBtn = API.el("button", {
      class: "page-btn",
      text: "‹ 上一页",
    });
    prevBtn.setAttribute("aria-label", "上一页");
    if (page <= 1) prevBtn.disabled = true;
    prevBtn.addEventListener("click", function () { if (page > 1) gotoPage(page - 1); });
    pg.appendChild(prevBtn);

    // 页码列表（中间省略）
    var pageButtons = buildPageButtons(page, pages);
    pageButtons.forEach(function (item) {
      if (item === "...") {
        pg.appendChild(API.el("span", { class: "ellipsis", text: "…" }));
      } else {
        var active = item === page;
        var btn = API.el("button", {
          class: "page-btn" + (active ? " active" : ""),
          attrs: { "aria-current": active ? "page" : null },
          text: String(item),
        });
        btn.addEventListener("click", function () { gotoPage(item); });
        pg.appendChild(btn);
      }
    });

    // 下一页
    var nextBtn = API.el("button", {
      class: "page-btn",
      text: "下一页 ›",
    });
    nextBtn.setAttribute("aria-label", "下一页");
    if (page >= pages) nextBtn.disabled = true;
    nextBtn.addEventListener("click", function () { if (page < pages) gotoPage(page + 1); });
    pg.appendChild(nextBtn);

    pg.appendChild(info);
  }

  function buildPageButtons(current, total) {
    // 显示 current 附近 5 个页码
    var buttons = [];
    var range = 2; // 当前页前后各 2 个
    var start = Math.max(1, current - range);
    var end = Math.min(total, current + range);

    if (start > 1) {
      buttons.push(1);
      if (start > 2) buttons.push("...");
    }
    for (var i = start; i <= end; i++) buttons.push(i);
    if (end < total) {
      if (end < total - 1) buttons.push("...");
      buttons.push(total);
    }
    return buttons;
  }

  function gotoPage(p) {
    if (p < 1 || p > totalPages || p === currentPage) return;
    currentPage = p;
    if (currentTab === "hot") {
      loadHotFeed();
    } else {
      loadPosts();
    }
    // 滚动到列表顶部
    if (dom.postList) {
      dom.postList.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* ------------------------------------------------------------------------
   * 数据加载
   * ---------------------------------------------------------------------- */

  /** 首页 / 最新：分页列表 */
  async function loadPosts() {
    renderSkeleton();
    dom.pagination.innerHTML = "";

    try {
      var data = await API.getPosts(currentPage, PAGE_SIZE);
      var posts = data.posts || [];
      totalCount = data.total || 0;
      totalPages = data.pages || Math.ceil(totalCount / PAGE_SIZE) || 1;
      feedCache = posts;
      renderList(applyFilter(posts));
      renderPagination(data);
    } catch (err) {
      feedCache = [];
      renderErrorState(err);
    }
  }

  /** 热门：合并前 3 页，按回复数（浏览数次级）排序 */
  async function loadHotFeed() {
    renderSkeleton();
    dom.pagination.innerHTML = "";

    try {
      var first = await API.getPosts(1, PAGE_SIZE);
      var pages = first.pages || 1;
      var fetchPages = Math.min(pages, 3);
      var all = (first.posts || []).slice();

      var jobs = [];
      for (var p = 2; p <= fetchPages; p++) {
        jobs.push(API.getPosts(p, PAGE_SIZE));
      }
      var results = await Promise.all(jobs);
      results.forEach(function (d) {
        all = all.concat(d.posts || []);
      });

      all.sort(function (a, b) {
        var rc = (b.reply_count || 0) - (a.reply_count || 0);
        if (rc !== 0) return rc;
        return (b.view_count || 0) - (a.view_count || 0);
      });

      feedCache = all.slice(0, PAGE_SIZE);
      renderList(applyFilter(feedCache));
    } catch (err) {
      feedCache = [];
      renderErrorState(err);
    }
  }

  /** 标签：占位面板（后端暂无标签体系） */
  function showTagPanel() {
    dom.postList.innerHTML = "";
    dom.pagination.innerHTML = "";

    var tags = [
      "前端开发", "后端开发", "数据库", "DevOps", "人工智能",
      "开源项目", "架构设计", "算法", "面试互助", "工具资源",
      "移动开发", "安全", "云原生", "灌水乐园",
    ];

    var cloud = API.el("div", { class: "card tag-cloud" });
    tags.forEach(function (t) {
      var chip = API.el("button", {
        class: "tag-chip",
        attrs: { type: "button" },
        text: "# " + t,
      });
      chip.addEventListener("click", function () {
        showToast("标签体系即将上线，敬请期待", "info");
      });
      cloud.appendChild(chip);
    });

    dom.postList.appendChild(API.el("div", { class: "empty-state", style: "border:none;padding-top:8px;" }, [
      API.el("div", { class: "text", text: "按话题浏览帖子" }),
      API.el("div", { class: "hint", text: "点击标签查看相关讨论（功能筹备中）" }),
    ]));
    dom.postList.appendChild(cloud);
  }

  /** 侧栏：热门帖子 TOP5 */
  async function loadHotList() {
    var wrap = document.getElementById("hot-list");
    if (!wrap) return;
    try {
      var data = await API.getPosts(1, 50);
      var posts = (data.posts || []).slice().sort(function (a, b) {
        var rc = (b.reply_count || 0) - (a.reply_count || 0);
        if (rc !== 0) return rc;
        return (b.view_count || 0) - (a.view_count || 0);
      }).slice(0, 5);

      wrap.innerHTML = "";
      if (!posts.length) {
        wrap.appendChild(API.el("div", { class: "hot-empty", text: "暂无热门帖子" }));
        return;
      }
      posts.forEach(function (post, i) {
        wrap.appendChild(API.el("a", {
          class: "hot-item",
          attrs: { href: "post.html?id=" + encodeURIComponent(post.id) },
        }, [
          API.el("span", { class: "hot-rank", text: String(i + 1) }),
          API.el("span", { class: "hot-title", text: post.title || "（无标题）" }),
          API.el("span", { class: "hot-meta", text: "💬 " + (post.reply_count || 0) }),
        ]));
      });
    } catch (err) {
      wrap.innerHTML = "";
      wrap.appendChild(API.el("div", { class: "hot-empty", text: "热门榜加载失败" }));
    }
  }

  /** 侧栏：社区统计（/api/stats） */
  async function loadStats() {
    var ids = { posts: "stat-posts", users: "stat-users", replies: "stat-replies" };
    try {
      var s = await API.getStats();
      Object.keys(ids).forEach(function (k) {
        var el = document.getElementById(ids[k]);
        if (el) el.textContent = formatNum(s && s[k] !== undefined ? s[k] : 0);
      });
    } catch (err) {
      Object.keys(ids).forEach(function (k) {
        var el = document.getElementById(ids[k]);
        if (el) el.textContent = "–";
      });
    }
  }

  /* ------------------------------------------------------------------------
   * Tab 与搜索
   * ---------------------------------------------------------------------- */
  function setTab(tab) {
    if (["default", "latest", "hot", "tags"].indexOf(tab) === -1) tab = "default";
    currentTab = tab;

    // 同步桌面导航与移动抽屉中的 tab 状态
    var tabBtns = document.querySelectorAll(".nav-tab");
    Array.prototype.forEach.call(tabBtns, function (btn) {
      var active = btn.getAttribute("data-tab") === tab;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });

    if (tab === "hot") loadHotFeed();
    else if (tab === "tags") showTagPanel();
    else loadPosts();
  }

  function bindSearch(input) {
    if (!input) return;
    input.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        currentQuery = (input.value || "").trim().toLowerCase();
        // 同步桌面 / 抽屉两个搜索框
        syncSearchInputs(input);
        // 重新渲染当前数据源（不重新请求）
        if (currentTab === "tags") return;
        renderList(applyFilter(feedCache));
      }, 220);
    });
  }

  function syncSearchInputs(except) {
    ["header-search-input", "drawer-search-input"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el && el !== except) el.value = except ? except.value : "";
    });
  }

  /* ------------------------------------------------------------------------
   * 头部：内容导航折叠（移动端）
   * - 抽屉内容由桌面导航 tab / 搜索框 **克隆** 生成，DOM 里只保留一份"真身"，
   *   避免头部出现重复的"首页/最新/热门/标签"与多余搜索框
   * - 克隆节点与原节点在 setTab / syncSearchInputs 中按 `.nav-tab`、id 统一处理，
   *   无需为抽屉单独维护状态
   * ---------------------------------------------------------------------- */
  function buildNavDrawer() {
    var drawer = document.getElementById("nav-drawer");
    if (!drawer || drawer.getAttribute("data-built") === "1") return;
    drawer.setAttribute("data-built", "1");
    drawer.innerHTML = "";

    var srcTabs = document.querySelector(".header-inner > .nav-tabs");
    if (srcTabs) {
      var tabsClone = srcTabs.cloneNode(true);
      tabsClone.removeAttribute("id");
      drawer.appendChild(tabsClone);
    }

    var srcSearch = document.querySelector(".header-inner > .header-search");
    if (srcSearch) {
      var searchWrap = API.el("div", { class: "drawer-search" });
      var searchClone = srcSearch.cloneNode(true);
      // 克隆体会带上 `.header-search`，而该选择器在移动端是 display:none，
      // 必须去掉才在抽屉里可见
      searchClone.classList.remove("header-search");
      var clonedInput = searchClone.querySelector("input");
      if (clonedInput) {
        // 必须换 id：cloneNode 会连 id 一起复制，重复 id 会导致
        // getElementById 永远只命中桌面那个，抽屉搜索框静默失效
        clonedInput.id = "drawer-search-input";
      }
      searchWrap.appendChild(searchClone);
      drawer.appendChild(searchWrap);
    }

    // 抽屉内点击 tab：切页后收起抽屉
    drawer.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest(".nav-tab")) closeNavDrawer();
    });
  }

  function openNavDrawer() {
    document.documentElement.classList.add("nav-open");
    setNavToggleState(true);
  }

  function closeNavDrawer() {
    document.documentElement.classList.remove("nav-open");
    setNavToggleState(false);
  }

  function setNavToggleState(open) {
    var btn = document.getElementById("nav-toggle");
    if (btn) {
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", open ? "收起内容导航菜单" : "打开内容导航菜单");
    }
  }

  function bindNavToggle() {
    var btn = document.getElementById("nav-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        if (document.documentElement.classList.contains("nav-open")) closeNavDrawer();
        else openNavDrawer();
      });
    }

    // 视口回到桌面宽度时收起抽屉，避免状态残留
    if (window.matchMedia) {
      var mq = window.matchMedia("(min-width: 641px)");
      var onChange = function (e) { if (e.matches) closeNavDrawer(); };
      if (mq.addEventListener) mq.addEventListener("change", onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  }

  /* ------------------------------------------------------------------------
   * 发帖模态框
   * ---------------------------------------------------------------------- */
  function openPostModal() {
    if (!API.isLoggedIn()) {
      window.location.href = "auth.html?redirect=" + encodeURIComponent(window.location.href);
      return;
    }
    var backdrop = dom.postModal;
    if (!backdrop) return;
    // 清空表单
    dom.postTitle.value = "";
    dom.postContent.value = "";
    if (postUploader) postUploader.reset();
    clearFormErrors();
    backdrop.classList.remove("hidden");
    setTimeout(function () { dom.postTitle.focus(); }, 50);
  }

  function closePostModal() {
    if (dom.postModal) dom.postModal.classList.add("hidden");
    // 关闭弹窗即放弃未完成的上传与已选图片
    if (postUploader) postUploader.reset();
  }

  function clearFormErrors() {
    if (dom.postTitleGroup) dom.postTitleGroup.classList.remove("has-error");
    if (dom.postContentGroup) dom.postContentGroup.classList.remove("has-error");
  }

  async function submitPost() {
    var title = (dom.postTitle.value || "").trim();
    var content = (dom.postContent.value || "").trim();

    clearFormErrors();
    var hasError = false;
    if (!title) {
      dom.postTitleGroup.classList.add("has-error");
      hasError = true;
    }
    if (!content) {
      dom.postContentGroup.classList.add("has-error");
      hasError = true;
    }
    if (hasError) return;

    var btn = dom.postSubmitBtn;
    btn.disabled = true;
    btn.textContent = "发布中…";
    try {
      // 若有图片仍在上传，等待其完成（成功或失败）后再提交，
      // 确保正文中的图片链接完整；等待期间不阻塞页面其他交互
      if (postUploader && postUploader.hasPending()) {
        btn.textContent = "等待图片上传…";
        await postUploader.waitAll();
      }
      await API.createPost(title, dom.postContent.value || content);
      showToast("帖子发布成功！", "success");
      closePostModal();
      // 重置分页到第 1 页，确保能看到新帖
      currentPage = 1;
      await setTab(currentTab);
      loadHotList();
      loadStats();
    } catch (err) {
      showToast(err.message || "发布失败", "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "发布帖子";
    }
  }

  /* ------------------------------------------------------------------------
   * Toast
   * ---------------------------------------------------------------------- */
  var toastTimer = null;
  function showToast(msg, type) {
    var container = dom.toastContainer;
    if (!container) return;
    // 清空旧的
    container.innerHTML = "";
    var t = API.el("div", { class: "toast " + (type || "info"), text: msg });
    container.appendChild(t);
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      if (t.parentNode) t.parentNode.removeChild(t);
    }, 3000);
  }

  /* ------------------------------------------------------------------------
   * 初始化
   * ---------------------------------------------------------------------- */
  function init() {
    // 配置 Markdown 渲染（列表卡片图片展示依赖）
    setupMarked();

    // 绑定 DOM
    dom.navActions = document.getElementById("nav-actions");
    dom.postList = document.getElementById("post-list");
    dom.pagination = document.getElementById("pagination");
    dom.postModal = document.getElementById("post-modal");
    dom.postTitle = document.getElementById("post-title");
    dom.postContent = document.getElementById("post-content");
    dom.postTitleGroup = document.getElementById("post-title-group");
    dom.postContentGroup = document.getElementById("post-content-group");
    dom.postSubmitBtn = document.getElementById("post-submit-btn");
    dom.toastContainer = document.getElementById("toast-container");

    if (!dom.postList || !dom.pagination) {
      console.error("[index.js] 关键 DOM 节点缺失");
      return;
    }

    renderNav();

    // 模态框事件
    var openBtn = document.getElementById("open-post-btn");
    if (openBtn) openBtn.addEventListener("click", openPostModal);
    if (dom.postModal) {
      dom.postModal.addEventListener("click", function (e) {
        if (e.target === dom.postModal) closePostModal();
      });
      var closeBtn = document.getElementById("post-modal-close");
      if (closeBtn) closeBtn.addEventListener("click", closePostModal);
      var cancelBtn = document.getElementById("post-cancel-btn");
      if (cancelBtn) cancelBtn.addEventListener("click", closePostModal);
      if (dom.postSubmitBtn) dom.postSubmitBtn.addEventListener("click", submitPost);
      // ESC 关闭
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          if (document.documentElement.classList.contains("nav-open")) closeNavDrawer();
          if (!dom.postModal.classList.contains("hidden")) closePostModal();
        }
      });
      // Ctrl/Cmd + Enter 提交
      if (dom.postContent) {
        dom.postContent.addEventListener("keydown", function (e) {
          if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
            e.preventDefault();
            submitPost();
          }
        });
      }

      // 图片上传器（发帖）
      var postImagesEl = document.getElementById("post-images");
      if (postImagesEl && global.ForumImageUpload) {
        postUploader = global.ForumImageUpload.createUploader({
          container: postImagesEl,
          textarea: dom.postContent,
          onToast: showToast,
        });
      }
    }

    // 移动端折叠导航（抽屉内容由桌面节点克隆，保证头部无重复元素）
    // 必须在下方绑定 .nav-tab 之前执行，否则克隆出来的 tab 拿不到点击监听
    buildNavDrawer();
    bindNavToggle();

    // Tab 切换（桌面导航 + 移动抽屉：抽屉内为克隆节点，此处统一绑定）
    var tabBtns = document.querySelectorAll(".nav-tab");
    Array.prototype.forEach.call(tabBtns, function (btn) {
      btn.addEventListener("click", function () {
        setTab(btn.getAttribute("data-tab"));
      });
    });

    // 搜索（防抖，当前数据源内过滤）
    bindSearch(document.getElementById("header-search-input"));
    bindSearch(document.getElementById("drawer-search-input"));

    // 从 URL 读取初始 Tab / 页码（深链）
    var params = new URLSearchParams(window.location.search);
    var t = params.get("tab");
    var p = parseInt(params.get("page") || "1", 10);
    if (!isNaN(p) && p > 0) currentPage = p;

    // 首次加载（setTab 内部触发数据加载）
    setTab(t || "default");

    // 侧栏数据（并行）
    loadHotList();
    loadStats();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // 暴露给外部（调试用）
  global.ForumIndex = {
    reload: loadPosts,
    gotoPage: gotoPage,
    setTab: setTab,
  };
})(typeof globalThis !== "undefined" ? globalThis : (typeof window !== "undefined" ? window : this));
