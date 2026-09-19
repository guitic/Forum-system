/* ==========================================================================
   post.js - 帖子详情页逻辑
   - 解析 URL 参数 ?id=XXX
   - 拉取帖子详情 + 回复
   - 使用 marked.js 渲染 Markdown，highlight.js 高亮代码
   - XSS 防护（白名单清洗）
   - 渲染回复列表
   - 回复表单（需登录）
   - 删除帖子（需确认弹窗，仅发帖人或 admin）
   ========================================================================== */

(function (global) {
  "use strict";

  var API = global.ForumAPI;
  if (!API) {
    console.error("[post.js] ForumAPI 未加载，请先引入 js/api.js");
    return;
  }

  // marked.js 全局对象
  var marked = global.marked || (typeof window !== "undefined" ? window.marked : null);

  // DOM 引用
  var dom = {};

  // 当前帖子数据
  var currentPost = null;
  var currentReplies = [];

  /* ------------------------------------------------------------------------
   * Markdown 渲染管线
   * ---------------------------------------------------------------------- */

  /**
   * 配置 marked 选项（一次性）
   */
  function setupMarked() {
    if (!marked) return;
    try {
      if (marked.use) {
        // marked v4+ API
        marked.use({
          gfm: true,           // GitHub Flavored Markdown
          breaks: true,        // 单个换行视为 <br>
          pedantic: false,
          async: false,
        });
      } else {
        // 旧版 marked 选项
        marked.setOptions({
          gfm: true,
          breaks: true,
          pedantic: false,
          smartLists: true,
          mangle: false,
          headerIds: false,
          sanitize: false, // 关闭内置 sanitize（已弃用），改用自定义 sanitize
        });
      }
    } catch (e) {
      console.warn("[post.js] marked 配置失败:", e);
    }
  }

  /**
   * 渲染 Markdown 为安全 HTML
   * @param {string} md 原始 Markdown
   * @returns {string} 安全 HTML
   */
  function renderMarkdown(md) {
    if (!md) return "";
    var html;
    if (marked) {
      try {
        html = marked.parse(md);
      } catch (e) {
        console.warn("[post.js] marked 解析失败，回退为纯文本:", e);
        html = "<p>" + escapeHtml(md) + "</p>";
      }
    } else {
      // marked 未加载时，至少做纯文本转义
      html = "<p>" + escapeHtml(md) + "</p>";
    }

    // 代码块高亮
    html = highlightCodeBlocks(html);

    // XSS 清洗
    html = API.sanitize(html);

    return html;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * 对 <pre><code> 块执行 highlight.js 高亮
   * @param {string} html
   * @returns {string}
   */
  function highlightCodeBlocks(html) {
    var hljs = global.hljs || (typeof window !== "undefined" ? window.hljs : null);
    if (!hljs) return html;
    // 用占位符保护，避免 sanitize 误伤；这里我们直接操作 DOM 片段
    var tmp = document.createElement("div");
    tmp.innerHTML = html;
    var codeNodes = tmp.querySelectorAll("pre code");
    codeNodes.forEach(function (node) {
      try {
        hljs.highlightElement(node);
      } catch (e) {
        // 跳过单块错误
      }
    });
    return tmp.innerHTML;
  }

  /* ------------------------------------------------------------------------
   * URL 参数解析
   * ---------------------------------------------------------------------- */
  function getPostId() {
    var params = new URLSearchParams(window.location.search);
    var id = params.get("id") || params.get("postId") || params.get("p");
    return id ? decodeURIComponent(id) : null;
  }

  /* ------------------------------------------------------------------------
   * 头部导航
   * ---------------------------------------------------------------------- */
  function renderNav() {
    var actions = dom.navActions;
    if (!actions) return;
    actions.innerHTML = "";

    var homeLink = API.el("a", {
      class: "btn btn-ghost btn-sm",
      attrs: { href: "index.html" },
    }, ["←", " 首页"]);
    actions.appendChild(homeLink);

    if (API.isLoggedIn()) {
      var username = "";
      try { username = localStorage.getItem("username") || ""; } catch (e) { /* ignore */ }
      if (username) {
        var badge = API.el("span", { class: "user-badge" }, [
          API.el("span", { class: "avatar", text: API.avatarChar(username) }),
          " " + username,
        ]);
        actions.appendChild(badge);
      }
      var logoutBtn = API.el("a", {
        class: "btn btn-ghost btn-sm",
        attrs: { href: "javascript:void(0)" },
      }, ["登出"]);
      logoutBtn.addEventListener("click", handleLogout);
      actions.appendChild(logoutBtn);
    } else {
      var loginBtn = API.el("a", {
        class: "btn btn-primary btn-sm",
        attrs: { href: "auth.html?redirect=" + encodeURIComponent(window.location.href) },
      }, ["登录 / 注册"]);
      actions.appendChild(loginBtn);
    }
  }

  function handleLogout() {
    API.clearToken();
    try { localStorage.removeItem("username"); } catch (e) { /* ignore */ }
    renderNav();
    renderReplyForm(); // 重新渲染回复表单为登录提示
    showToast("已退出登录", "info");
  }

  /* ------------------------------------------------------------------------
   * 帖子详情渲染
   * ---------------------------------------------------------------------- */
  function renderPost(post) {
    var titleEl = dom.postTitle;
    var metaEl = dom.postMeta;
    var contentEl = dom.postContent;
    var actionsEl = dom.postActions;
    if (!titleEl || !contentEl) return;

    currentPost = post;
    var author = post.username || post.user || "匿名";
    var time = API.formatTime(post.created_at);
    var fullTime = API.formatDateTime(post.created_at);

    titleEl.textContent = post.title || "（无标题）";
    document.title = (post.title || "帖子详情") + " - 技术论坛";

    // meta
    metaEl.innerHTML = "";
    metaEl.appendChild(API.el("span", {
      class: "avatar-lg",
      text: API.avatarChar(author),
    }));
    metaEl.appendChild(API.el("span", {
      class: "author-name",
      text: author,
    }));
    if (post.role === "admin") {
      metaEl.appendChild(API.el("span", { class: "tag-admin", text: "ADMIN" }));
    }
    metaEl.appendChild(API.el("span", { class: "meta-dot", text: "·" }));
    metaEl.appendChild(API.el("span", {
      attrs: { title: fullTime },
      text: time,
    }));
    metaEl.appendChild(API.el("span", { class: "meta-dot", text: "·" }));
    metaEl.appendChild(API.el("span", {
      text: currentReplies.length + " 条回复",
    }));

    // actions
    actionsEl.innerHTML = "";
    var myUsername = "";
    try { myUsername = localStorage.getItem("username") || ""; } catch (e) { /* ignore */ }
    var canDelete = false;
    var isAuthor = myUsername && author && String(myUsername).toLowerCase() === String(author).toLowerCase();
    var isAdmin = post.role === "admin" && myUsername && myUsername === author;
    // 后端判定权限，前端仅根据"是否已登录 + 是否发帖人"显示按钮
    if (API.isLoggedIn() && isAuthor) canDelete = true;

    if (canDelete) {
      var delBtn = API.el("button", {
        class: "btn btn-danger btn-sm",
      }, ["🗑", " 删除"]);
      delBtn.addEventListener("click", confirmDelete);
      actionsEl.appendChild(delBtn);
    }

    // 渲染内容
    contentEl.innerHTML = renderMarkdown(post.content || "");
  }

  /* ------------------------------------------------------------------------
   * 回复列表渲染
   * ---------------------------------------------------------------------- */
  function renderReplies(replies) {
    currentReplies = replies || [];
    var listEl = dom.replyList;
    var countEl = dom.replyCount;
    if (!listEl) return;
    listEl.innerHTML = "";

    if (countEl) {
      countEl.textContent = currentReplies.length;
    }

    if (currentReplies.length === 0) {
      listEl.appendChild(API.el("div", { class: "empty-state" }, [
        API.el("div", { class: "icon", text: "💬" }),
        API.el("div", { class: "text", text: "还没有回复" }),
        API.el("div", { class: "hint", text: "登录后发表你的第一条回复" }),
      ]));
      return;
    }

    currentReplies.forEach(function (reply) {
      listEl.appendChild(renderReplyItem(reply));
    });
  }

  function renderReplyItem(reply) {
    var author = reply.username || reply.user || "匿名";
    var time = API.formatTime(reply.created_at);
    var fullTime = API.formatDateTime(reply.created_at);
    var html = renderMarkdown(reply.content || "");

    // 创建内容元素并直接设置 innerHTML（不能用 setAttribute）
    var contentEl = API.el("div", {
      class: "reply-content md-content",
    });
    contentEl.innerHTML = html;


    

// var head = API.el("div", { class: "reply-head" }, [
//   API.el("span", { class: "reply-author", text: author }),
//   // ✅ 三元表达式，去掉if关键字
//   reply.role === "admin" ? API.el("span", { class: "tag-admin", text: "ADMIN" }) : null,
//   API.el("span", { class: "reply-time", attrs: { title: fullTime }, text: time }),
// ]);

// - 三元语法格式：`条件 ? 满足条件的值 : 不满足的值`
// - 不要加`if`！`if`是语句，不能放在数组元素位置；三元是表达式，可以。
// 下面语法是上面注释语法的优化版
    
    return API.el("div", {
      class: "reply-item",
      attrs: { "data-id": reply.id },
    }, [
      API.el("div", {
        class: "reply-avatar",
        text: API.avatarChar(author),
      }),
      API.el("div", { class: "reply-body" }, [head, contentEl]),
    ]);
  }

  /* ------------------------------------------------------------------------
   * 回复表单渲染
   * ---------------------------------------------------------------------- */
  function renderReplyForm() {
    var form = dom.replyForm;
    var prompt = dom.replyLoginPrompt;
    if (!form && !prompt) return;

    if (!API.isLoggedIn()) {
      if (form) form.classList.add("hidden");
      if (prompt) {
        prompt.classList.remove("hidden");
        prompt.innerHTML = "";
        prompt.appendChild(API.el("span", {
          text: "登录后可参与讨论",
        }));
        prompt.appendChild(API.el("a", {
          class: "btn btn-primary btn-sm",
          attrs: { href: "auth.html?redirect=" + encodeURIComponent(window.location.href) },
          text: "去登录",
        }));
      }
      return;
    }

    if (prompt) prompt.classList.add("hidden");
    if (form) form.classList.remove("hidden");
  }

  async function submitReply() {
    if (!currentPost) return;
    if (!API.isLoggedIn()) {
      window.location.href = "auth.html?redirect=" + encodeURIComponent(window.location.href);
      return;
    }
    var content = (dom.replyContent.value || "").trim();
    if (!content) {
      dom.replyGroup.classList.add("has-error");
      return;
    }
    dom.replyGroup.classList.remove("has-error");

    var btn = dom.replySubmitBtn;
    btn.disabled = true;
    btn.textContent = "提交中…";
    try {
      await API.createReply(currentPost.id, content);
      dom.replyContent.value = "";
      showToast("回复发布成功！", "success");
      // 刷新详情
      loadPost();
    } catch (err) {
      showToast(err.message || "回复失败", "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "发布回复";
    }
  }

  /* ------------------------------------------------------------------------
   * 删除确认
   * ---------------------------------------------------------------------- */
  function confirmDelete() {
    if (!currentPost) return;
    var backdrop = dom.confirmBackdrop;
    if (!backdrop) return;
    dom.confirmMessage.textContent =
      "确定要删除帖子「" + (currentPost.title || "") + "」吗？该操作不可恢复。";
    backdrop.classList.remove("hidden");
  }

  function closeConfirm() {
    if (dom.confirmBackdrop) dom.confirmBackdrop.classList.add("hidden");
  }

  async function doDelete() {
    if (!currentPost) return;
    var btn = dom.confirmDeleteBtn;
    btn.disabled = true;
    btn.textContent = "删除中…";
    try {
      await API.deletePost(currentPost.id);
      showToast("帖子已删除", "success");
      // 跳转首页
      setTimeout(function () {
        window.location.href = "index.html";
      }, 500);
    } catch (err) {
      showToast(err.message || "删除失败", "error");
      btn.disabled = false;
      btn.textContent = "确认删除";
    }
  }

  /* ------------------------------------------------------------------------
   * 数据加载
   * ---------------------------------------------------------------------- */
  async function loadPost() {
    var id = getPostId();
    if (!id) {
      renderNotFound("缺少帖子 ID 参数");
      return;
    }

    dom.postDetailWrapper.classList.add("hidden");
    dom.postLoading.classList.remove("hidden");

    try {
      var data = await API.getPost(id);
      var post = data.post || data;
      var replies = data.replies || [];

      dom.postLoading.classList.add("hidden");
      dom.postDetailWrapper.classList.remove("hidden");
      renderPost(post);
      renderReplies(replies);
    } catch (err) {
      dom.postLoading.classList.add("hidden");
      if (err.status === 404) {
        renderNotFound("帖子不存在或已被删除");
      } else if (err.network) {
        renderNotFound("无法连接到后端服务，请确认 API 已启动");
      } else {
        renderNotFound(err.message || "加载帖子失败");
      }
    }
  }

  function renderNotFound(msg) {
    dom.postLoading.classList.add("hidden");
    dom.postDetailWrapper.classList.add("hidden");
    var empty = document.getElementById("post-not-found");
    if (!empty) return;
    empty.classList.remove("hidden");
    empty.innerHTML = "";
    empty.appendChild(API.el("div", { class: "icon", text: "🚫" }));
    empty.appendChild(API.el("div", { class: "text", text: msg }));
    empty.appendChild(API.el("a", {
      class: "btn btn-primary",
      attrs: { href: "index.html" },
      text: "返回首页",
    }));
  }

  /* ------------------------------------------------------------------------
   * Toast
   * ---------------------------------------------------------------------- */
  var toastTimer = null;
  function showToast(msg, type) {
    var container = dom.toastContainer;
    if (!container) return;
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
    dom.navActions = document.getElementById("nav-actions");
    dom.postLoading = document.getElementById("post-loading");
    dom.postDetailWrapper = document.getElementById("post-detail-wrapper");
    dom.postTitle = document.getElementById("post-title");
    dom.postMeta = document.getElementById("post-meta");
    dom.postActions = document.getElementById("post-actions");
    dom.postContent = document.getElementById("post-content");
    dom.replyList = document.getElementById("reply-list");
    dom.replyCount = document.getElementById("reply-count");
    dom.replyForm = document.getElementById("reply-form");
    dom.replyLoginPrompt = document.getElementById("reply-login-prompt");
    dom.replyContent = document.getElementById("reply-content");
    dom.replyGroup = document.getElementById("reply-group");
    dom.replySubmitBtn = document.getElementById("reply-submit-btn");
    dom.confirmBackdrop = document.getElementById("confirm-backdrop");
    dom.confirmMessage = document.getElementById("confirm-message");
    dom.confirmDeleteBtn = document.getElementById("confirm-delete-btn");
    dom.confirmCancelBtn = document.getElementById("confirm-cancel-btn");
    dom.toastContainer = document.getElementById("toast-container");

    setupMarked();

    renderNav();
    renderReplyForm();

    // 回复表单事件
    if (dom.replyContent) {
      dom.replyContent.addEventListener("keydown", function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
          e.preventDefault();
          submitReply();
        }
      });
    }
    if (dom.replySubmitBtn) {
      dom.replySubmitBtn.addEventListener("click", submitReply);
    }

    // 删除确认事件
    if (dom.confirmCancelBtn) {
      dom.confirmCancelBtn.addEventListener("click", closeConfirm);
    }
    if (dom.confirmDeleteBtn) {
      dom.confirmDeleteBtn.addEventListener("click", doDelete);
    }
    if (dom.confirmBackdrop) {
      dom.confirmBackdrop.addEventListener("click", function (e) {
        if (e.target === dom.confirmBackdrop) closeConfirm();
      });
    }
    // ESC 关闭确认
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && dom.confirmBackdrop && !dom.confirmBackdrop.classList.contains("hidden")) {
        closeConfirm();
      }
    });

    loadPost();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  if (typeof global.ForumPost !== "undefined" || true) {
    global.ForumPost = {
      reload: loadPost,
    };
  }
})(typeof globalThis !== "undefined" ? globalThis : (typeof window !== "undefined" ? window : this));
