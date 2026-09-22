/* ==========================================================================
   index.js - 首页逻辑
   - 加载时拉取主贴列表并渲染
   - 根据 localStorage 中的 token 决定显示"发帖"或"登录"入口
   - 支持分页
   - 发帖使用模态框
   - 删除需要确认（首页不直接删除，仅详情页支持）
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

  // DOM 引用（在 DOMContentLoaded 后绑定）
  var dom = {};

  // 发帖图片上传器（image-upload.js 工厂创建）
  var postUploader = null;

  /* ------------------------------------------------------------------------
   * 头部导航渲染
   * ---------------------------------------------------------------------- */
  function renderNav() {
    var actions = dom.navActions;
    if (!actions) return;
    actions.innerHTML = "";

    // 同步 page-header 中的"发布新帖"按钮可见性
    var pagePostBtn = document.getElementById("open-post-btn");

    if (API.isLoggedIn()) {
      // 读取缓存的用户信息
      var username = "";
      var nickname = "";
      var avatarUrl = "";
      var role = "";
      try {
        username = localStorage.getItem("username") || "";
        nickname = localStorage.getItem("nickname") || "";
        avatarUrl = localStorage.getItem("avatar_url") || "";
        role = localStorage.getItem("role") || "";
      } catch (e) { /* ignore */ }
      var displayName = nickname || username;

      // 账号 + 下拉菜单（hover 显示）。导航区仅显示头像，用户名收入下拉菜单头部
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
          '<img class="avatar-img" src="' + avatarUrl + '" alt="用户头像" />' +
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
      }, ["🚪", " 登出"]);
      logoutItem.addEventListener("click", handleLogout);
      menu.appendChild(logoutItem);

      wrap.appendChild(trigger);
      wrap.appendChild(menu);
      API.setupBadgeDropdown(wrap);
      actions.appendChild(wrap);

      var postBtn = API.el("a", {
        class: "btn btn-primary btn-sm",
        attrs: { href: "javascript:void(0)" },
      }, ["✍ ", API.el("span", { class: "btn-text" }, ["发帖"])]);
      postBtn.addEventListener("click", openPostModal);
      actions.appendChild(postBtn);

      if (pagePostBtn) pagePostBtn.classList.remove("hidden");
    } else {
      var loginBtn = API.el("a", {
        class: "btn btn-primary btn-sm",
        attrs: { href: "auth.html" },
      }, ["登录 / 注册"]);
      actions.appendChild(loginBtn);

      if (pagePostBtn) pagePostBtn.classList.add("hidden");
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
   * 列表渲染
   * ---------------------------------------------------------------------- */
  function renderList(posts, state) {
    var list = dom.postList;
    if (!list) return;
    list.innerHTML = "";

    if (!posts || posts.length === 0) {
      list.appendChild(
        API.el("div", { class: "empty-state" }, [
          API.el("div", { class: "icon", text: "📭" }),
          API.el("div", { class: "text", text: "还没有任何帖子" }),
          API.el("div", { class: "hint", text: "登录并创建第一个帖子，开启技术交流之旅" }),
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

    // 头像
    var avatarUrl = post.avatar_url || "";
    var voteCol;
    if (avatarUrl) {
      voteCol = API.el("div", { class: "post-vote" });
      voteCol.innerHTML = '<div class="post-avatar post-avatar-img-wrap"><img class="avatar-img" src="' + avatarUrl + '" alt="" /></div>' +
        '<div class="vote-count" title="回复数">' + (replyCount > 0 ? replyCount : "") + '</div>';
    } else {
      voteCol = API.el("div", { class: "post-vote" }, [
        API.el("div", { class: "post-avatar", text: API.avatarChar(author) }),
        API.el("div", { class: "vote-count", title: "回复数" }, [String(replyCount)]),
      ]);
    }

    var metaItems = [
      API.el("span", { class: "meta-item" }, [
        "by ",
        API.el("span", { class: "author", text: author }),
      ]),
      API.el("span", { class: "meta-item", attrs: { title: fullTime }, text: time }),
      API.el("a", {
        class: "meta-item replies-count reply-link",
        attrs: {
          href: "post.html?id=" + encodeURIComponent(id) + "#reply",
          title: "跳转到回复区并回复该帖",
        },
      }, [
        "💬", " ", String(replyCount), " 回复",
      ]),
    ];

    var body = API.el("div", { class: "post-body" }, [
      API.el("a", {
        class: "post-title-link",
        attrs: { href: "post.html?id=" + encodeURIComponent(id) },
        text: title,
      }),
      excerpt
        ? API.el("div", { class: "post-excerpt", text: excerpt })
        : API.el("div", { class: "post-excerpt text-light", text: "（无内容）" }),
      API.el("div", { class: "post-meta" }, metaItems),
    ]);

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
      text: "第 " + page + " / " + pages + " 页 · 共 " + totalCount + " 篇",
    });

    // 上一页
    var prevBtn = API.el("button", {
      class: "page-btn",
      text: "«",
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
          text: String(item),
        });
        btn.addEventListener("click", function () { gotoPage(item); });
        pg.appendChild(btn);
      }
    });

    // 下一页
    var nextBtn = API.el("button", {
      class: "page-btn",
      text: "»",
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
    loadPosts();
    // 滚动到列表顶部
    if (dom.postList) {
      dom.postList.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* ------------------------------------------------------------------------
   * 数据加载
   * ---------------------------------------------------------------------- */
  async function loadPosts() {
    dom.postList.innerHTML = "";
    dom.postList.appendChild(API.el("div", { class: "loading", text: "加载中" }));
    dom.pagination.innerHTML = "";

    try {
      var data = await API.getPosts(currentPage, PAGE_SIZE);
      var posts = data.posts || [];
      totalCount = data.total || 0;
      totalPages = data.pages || Math.ceil(totalCount / PAGE_SIZE) || 1;
      renderList(posts);
      renderPagination(data);
    } catch (err) {
      dom.postList.innerHTML = "";
      var banner = API.el("div", { class: "error-banner" }, [
        "⚠ ", " ", err.message || "加载失败",
      ]);
      dom.postList.appendChild(banner);
      dom.postList.appendChild(API.el("div", { class: "empty-state" }, [
        API.el("div", { class: "icon", text: "🔌" }),
        API.el("div", { class: "text", text: "无法连接到后端服务" }),
        API.el("div", { class: "hint", text: "请确认后端 API 已启动，并刷新页面重试" }),
      ]));
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
      await loadPosts();
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

    // 未登录：直接跳登录页，登录后带 redirect 回到当前页
    if (!API.isLoggedIn()) {
      var currentUrl = window.location.pathname + window.location.search + window.location.hash;
      window.location.replace("auth.html?redirect=" + encodeURIComponent(currentUrl));
      return;
    }

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
        if (e.key === "Escape" && !dom.postModal.classList.contains("hidden")) {
          closePostModal();
        }
      });
      // 阻止提交时换行触发
      if (dom.postContent) {
        dom.postContent.addEventListener("keydown", function (e) {
          // Ctrl/Cmd + Enter 提交
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

    // 从 URL query 读取 page（便于深链）
    var params = new URLSearchParams(window.location.search);
    var p = parseInt(params.get("page") || "1", 10);
    if (!isNaN(p) && p > 0) currentPage = p;

    loadPosts();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // 暴露给外部（调试用）
  if (typeof global.ForumIndex !== "undefined" || true) {
    global.ForumIndex = {
      reload: loadPosts,
      gotoPage: gotoPage,
    };
  }
})(typeof globalThis !== "undefined" ? globalThis : (typeof window !== "undefined" ? window : this));
