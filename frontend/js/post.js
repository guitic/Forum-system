/* ==========================================================================
   post.js - 帖子详情页逻辑
   - 解析 URL 参数 ?id=XXX
   - 拉取帖子详情 + 层级回复树
   - 使用 marked.js 渲染 Markdown，highlight.js 高亮代码
   - XSS 防护（白名单清洗）
   - V3：递归渲染回复树（楼中楼，展示期最多 3 层）
   - V3：内联回复表单（回复某条回复）
   - V3：删除回复（作者 / 管理员，级联删除子孙）
   - V3：角色徽标 —— 楼主（帖子作者）/ ADMIN
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
  var currentPost = null;   // 帖子对象
  var currentTree = [];     // V3：回复树（嵌套）
  var replyCount = 0;       // V3：回复总数
  var pendingConfirm = null;// 确认弹窗待执行回调

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
    // 直接操作 DOM 片段，避免 sanitize 误伤代码块 class
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
   * 当前用户（从 localStorage 缓存读取）
   * ---------------------------------------------------------------------- */
  function getCurrentUser() {
    var me = {
      username: "", nickname: "", avatarUrl: "", role: "", displayName: "",
    };
    try {
      me.username = localStorage.getItem("username") || "";
      me.nickname = localStorage.getItem("nickname") || "";
      me.avatarUrl = localStorage.getItem("avatar_url") || "";
      me.role = localStorage.getItem("role") || "";
    } catch (e) { /* ignore */ }
    me.displayName = me.nickname || me.username;
    return me;
  }

  function isCurrentUserAdmin() {
    return getCurrentUser().role === "admin";
  }

  function isCurrentUser(username) {
    if (!username) return false;
    var me = getCurrentUser();
    return String(me.username).toLowerCase() === String(username).toLowerCase();
  }

  function isPostAuthor(username) {
    return !!(currentPost && username &&
      String(currentPost.username || "").toLowerCase() === String(username).toLowerCase());
  }

  /** 跳转到登录页并带回跳地址 */
  function goLogin() {
    window.location.href = "auth.html?redirect=" + encodeURIComponent(window.location.href);
  }

  /**
   * 渲染头像（按身份区分配色）
   * @param {Object} who {avatar_url, display_name}
   * @param {Object} opts
   *   base  : 基础类名，如 'avatar' / 'avatar-lg' / 'reply-avatar'
   *   size  : 'lg' 时给 <img> 追加 avatar-lg-img
   *   op    : 楼主 → avatar-op
   *   admin : 管理员 → avatar-admin
   * @returns {HTMLElement}
   */
  function renderAvatar(who, opts) {
    opts = opts || {};
    var cls = (opts.base || "") +
      (opts.size === "lg" ? " avatar-lg" : "") +
      (opts.op ? " avatar-op" : "") +
      (opts.admin ? " avatar-admin" : "");
    cls = cls.trim();

    if (who.avatar_url) {
      var node = API.el("span", { class: cls + " avatar-img-wrap" });
      node.innerHTML = '<img class="avatar-img"' +
        (opts.size === "lg" ? " avatar-lg-img" : "") +
        '" src="' + escapeHtml(who.avatar_url) + '" alt="" />';
      return node;
    }
    return API.el("span", {
      class: cls,
      text: API.avatarChar(who.display_name),
    });
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
      var me = getCurrentUser();
      if (me.displayName) {
        var badge = API.el("span", { class: "user-badge" });
        badge.addEventListener("click", function () {
          window.location.href = "profile.html";
        });
        badge.style.cursor = "pointer";
        badge.appendChild(renderAvatar(me, { base: "avatar", admin: me.role === "admin" }));
        badge.appendChild(document.createTextNode(" " + me.displayName));
        actions.appendChild(badge);
      }

      var profileBtn = API.el("a", {
        class: "btn btn-ghost btn-sm",
        attrs: { href: "profile.html" },
      }, ["⚙", " 我的"]);
      actions.appendChild(profileBtn);

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
    try {
      localStorage.removeItem("username");
      localStorage.removeItem("nickname");
      localStorage.removeItem("avatar_url");
      localStorage.removeItem("role");
    } catch (e) { /* ignore */ }
    renderNav();
    renderReplyForm();
    if (currentPost) renderPost(currentPost);
    showToast("已退出登录", "info");
  }

  /* ------------------------------------------------------------------------
   * 帖子详情渲染（V3：凸显楼主）
   * ---------------------------------------------------------------------- */
  function renderPost(post) {
    var titleEl = dom.postTitle;
    var metaEl = dom.postMeta;
    var contentEl = dom.postContent;
    var actionsEl = dom.postActions;
    if (!titleEl || !contentEl) return;

    currentPost = post;
    var author = post.display_name || post.nickname || post.username || "匿名";
    var time = API.formatTime(post.created_at);
    var fullTime = API.formatDateTime(post.created_at);
    var isAdmin = post.role === "admin";

    titleEl.textContent = post.title || "（无标题）";
    document.title = (post.title || "帖子详情") + " - 技术论坛";

    // 主贴卡片：作者即楼主，佩戴主色左边框
    var wrapper = dom.postDetailWrapper;
    if (wrapper) wrapper.classList.add("post-op");
    if (metaEl) {
      metaEl.classList.remove("post-detail-admin");
      if (isAdmin) metaEl.classList.add("post-detail-admin");
    }

    // meta
    metaEl.innerHTML = "";
    metaEl.appendChild(renderAvatar(
      { avatar_url: post.avatar_url, display_name: author },
      { base: "avatar-lg", op: true, admin: isAdmin }
    ));
    metaEl.appendChild(API.el("span", { class: "author-name", text: author }));
    // 帖子作者永远是楼主
    metaEl.appendChild(API.el("span", { class: "tag-op", text: "楼主" }));
    if (isAdmin) {
      metaEl.appendChild(API.el("span", { class: "tag-admin", text: "ADMIN" }));
    }
    metaEl.appendChild(API.el("span", { class: "meta-dot", text: "·" }));
    metaEl.appendChild(API.el("span", { attrs: { title: fullTime }, text: time }));
    metaEl.appendChild(API.el("span", { class: "meta-dot", text: "·" }));
    metaEl.appendChild(API.el("span", { text: replyCount + " 条回复" }));

    // actions：楼主或管理员可删除
    actionsEl.innerHTML = "";
    var canDelete = API.isLoggedIn() &&
      (isCurrentUser(post.username) || isCurrentUserAdmin());
    if (canDelete) {
      var delBtn = API.el("button", { class: "btn btn-danger btn-sm" }, ["🗑", " 删除"]);
      delBtn.addEventListener("click", confirmDeletePost);
      actionsEl.appendChild(delBtn);
    }

    // 渲染内容
    contentEl.innerHTML = renderMarkdown(post.content || "");
  }

  /* ------------------------------------------------------------------------
   * 回复树渲染（V3）
   * ---------------------------------------------------------------------- */
  function renderReplies(tree) {
    currentTree = tree || [];
    var listEl = dom.replyList;
    var countEl = dom.replyCount;
    if (!listEl) return;
    listEl.innerHTML = "";

    if (countEl) countEl.textContent = replyCount;

    if (currentTree.length === 0) {
      listEl.appendChild(API.el("div", { class: "empty-state" }, [
        API.el("div", { class: "icon", text: "💬" }),
        API.el("div", { class: "text", text: "还没有回复" }),
        API.el("div", { class: "hint", text: "登录后发表你的第一条回复" }),
      ]));
      return;
    }

    var floor = 0;
    currentTree.forEach(function (node) {
      floor += 1;
      listEl.appendChild(renderReplyNode(node, 0, floor));
    });
  }

  /**
   * 递归渲染单个回复节点
   * @param {Object} node 后端返回的回复节点
   * @param {number} depth 展示深度（0 = 一级回复）
   * @param {number} [floorNo] 楼层号，仅一级回复传入
   * @returns {HTMLElement}
   */
  function renderReplyNode(node, depth, floorNo) {
    if (!node) return document.createElement("div");

    var author = node.display_name || node.nickname || node.username || "匿名";
    var time = API.formatTime(node.created_at);
    var fullTime = API.formatDateTime(node.created_at);
    var isAdmin = node.role === "admin";
    var isOP = !!node.is_author || isPostAuthor(node.username);
    var who = { avatar_url: node.avatar_url, display_name: author };

    // 卡片容器（按身份与深度附加修饰类）
    var cardCls = "reply-item";
    if (isOP) cardCls += " reply-op";
    if (isAdmin) cardCls += " reply-admin";
    if (depth > 0) cardCls += " reply-nested";
    if (depth >= 2) cardCls += " reply-deep";

    var card = API.el("div", {
      class: cardCls,
      attrs: { "data-id": node.id, "data-depth": String(depth) },
      id: "reply-" + node.id,
    });

    // 头像
    card.appendChild(renderAvatar(who, { base: "reply-avatar", op: isOP, admin: isAdmin }));

    // ===== 主体 =====
    var body = API.el("div", { class: "reply-body" });

    // 头部：作者 + 楼主/ADMIN 徽标 + 楼层号 + 时间
    var headChildren = [
      API.el("span", { class: "reply-author", text: author }),
    ];
    if (isOP) headChildren.push(API.el("span", { class: "tag-op", text: "楼主" }));
    if (isAdmin) headChildren.push(API.el("span", { class: "tag-admin", text: "ADMIN" }));
    if (depth === 0 && floorNo) {
      headChildren.push(API.el("span", { class: "reply-floor", text: "#" + floorNo }));
    }
    headChildren.push(API.el("span", {
      class: "reply-time",
      attrs: { title: fullTime },
      text: time,
    }));
    body.appendChild(API.el("div", { class: "reply-head" }, headChildren));

    // 引用条：回复 @被回复人（仅子回复）
    if (node.reply_to_display_name) {
      body.appendChild(API.el("div", { class: "reply-quote" }, [
        API.el("span", { class: "reply-quote-label", text: "回复" }),
        API.el("span", { class: "reply-quote-target", text: "@" + node.reply_to_display_name }),
      ]));
    }

    // 内容（Markdown → 安全 HTML）
    var contentEl = API.el("div", { class: "reply-content md-content" });
    contentEl.innerHTML = renderMarkdown(node.content || "");
    body.appendChild(contentEl);

    // 操作栏：回复（所有人，未登录跳登录）/ 删除（作者或管理员）
    var actions = API.el("div", { class: "reply-actions" });
    var replyBtn = API.el("button", {
      class: "btn btn-ghost btn-sm reply-action-btn",
      attrs: { type: "button" },
    }, ["💬", " 回复"]);
    replyBtn.addEventListener("click", function () {
      if (!API.isLoggedIn()) { goLogin(); return; }
      openInlineReply(node);
    });
    actions.appendChild(replyBtn);

    if (API.isLoggedIn() &&
        (isCurrentUser(node.username) || isCurrentUserAdmin())) {
      var delBtn = API.el("button", {
        class: "btn btn-ghost btn-sm reply-action-btn reply-action-delete",
        attrs: { type: "button" },
      }, ["🗑", " 删除"]);
      delBtn.addEventListener("click", function () { confirmDeleteReply(node); });
      actions.appendChild(delBtn);
    }
    body.appendChild(actions);

    // 子回复（递归）
    var children = node.children;
    if (children && children.length) {
      var childrenEl = API.el("div", { class: "reply-children" });
      children.forEach(function (child) {
        childrenEl.appendChild(renderReplyNode(child, depth + 1, null));
      });
      body.appendChild(childrenEl);
    }

    card.appendChild(body);
    return card;
  }

  /* ------------------------------------------------------------------------
   * 内联回复表单（V3：回复某条回复）
   * ---------------------------------------------------------------------- */
  /**
   * 打开内联回复表单
   * 同一时间只存在一个内联表单；插入到目标回复所在子树末尾并自动聚焦。
   */
  function openInlineReply(node) {
    closeInlineReply();
    var card = document.getElementById("reply-" + node.id);
    if (!card) return;
    var body = card.querySelector(".reply-body");
    if (!body) return;

    var form = buildInlineForm(node);
    body.appendChild(form);
    var ta = form.querySelector("textarea");
    if (ta) {
      ta.focus();
      try {
        var pos = ta.value.length;
        ta.setSelectionRange(pos, pos);
      } catch (e) { /* 旧浏览器不支持 */ }
    }
  }

  function buildInlineForm(node) {
    var target = node.display_name || node.nickname || node.username || "匿名";
    var parentId = node.id;

    var bar = API.el("div", { class: "inline-reply-bar" }, [
      API.el("span", { class: "inline-reply-label", text: "回复" }),
      API.el("span", { class: "inline-reply-target", text: "@" + target }),
    ]);
    var closeBtn = API.el("button", {
      class: "inline-reply-close",
      attrs: { type: "button", "aria-label": "取消回复" },
    }, ["×"]);
    closeBtn.addEventListener("click", closeInlineReply);
    bar.appendChild(closeBtn);

    var ta = API.el("textarea", {
      class: "form-control md-editor inline-reply-textarea",
      attrs: {
        maxlength: "5000",
        rows: "3",
        placeholder: "回复 @" + target + "，支持 Markdown 语法…",
      },
    });
    // 预填 @引用，便于被回复人感知上下文
    ta.value = "@" + target + " ";

    var btn = API.el("button", {
      class: "btn btn-primary btn-sm inline-reply-submit",
      attrs: { type: "button" },
    }, ["发布回复"]);
    var foot = API.el("div", { class: "inline-reply-foot" }, [
      API.el("span", {
        class: "inline-reply-hint",
        text: "<kbd>Ctrl/Cmd</kbd> + <kbd>Enter</kbd> 快速提交",
      }),
      btn,
    ]);

    btn.addEventListener("click", function () {
      submitInlineReply(parentId, ta, btn);
    });
    ta.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        submitInlineReply(parentId, ta, btn);
      }
    });

    return API.el("div", {
      class: "inline-reply-form",
      attrs: { "data-parent": String(parentId) },
    }, [bar, ta, foot]);
  }

  function closeInlineReply() {
    var existed = document.querySelector(".inline-reply-form");
    if (existed && existed.parentNode) existed.parentNode.removeChild(existed);
  }

  async function submitInlineReply(parentId, ta, btn) {
    if (!currentPost) return;
    if (!API.isLoggedIn()) { goLogin(); return; }
    var content = (ta.value || "").trim();
    if (!content) {
      if (ta) ta.focus();
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = "提交中…";
    }
    try {
      await API.createReply(currentPost.id, content, parentId);
      showToast("回复发布成功！", "success");
      loadPost(); // 整棵树刷新，内联表单随之移除
    } catch (err) {
      showToast(err.message || "回复失败", "error");
      if (btn) {
        btn.disabled = false;
        btn.textContent = "发布回复";
      }
    }
  }

  /* ------------------------------------------------------------------------
   * 回复表单（一级回复，底部固定）
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
        prompt.appendChild(API.el("span", { text: "登录后可参与讨论" }));
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
      goLogin();
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
      await API.createReply(currentPost.id, content); // 不传 parentId = 一级回复
      dom.replyContent.value = "";
      showToast("回复发布成功！", "success");
      loadPost();
    } catch (err) {
      showToast(err.message || "回复失败", "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "发布回复";
    }
  }

  /* ------------------------------------------------------------------------
   * 删除确认（通用弹窗：帖子 / 回复）
   * ---------------------------------------------------------------------- */
  function confirmAction(message, btnLabel, onConfirm) {
    pendingConfirm = typeof onConfirm === "function" ? onConfirm : null;
    if (!dom.confirmBackdrop) return;
    dom.confirmMessage.textContent = message;
    dom.confirmDeleteBtn.textContent = btnLabel || "确认";
    dom.confirmDeleteBtn.disabled = false;
    dom.confirmBackdrop.classList.remove("hidden");
  }

  function closeConfirm() {
    pendingConfirm = null;
    if (dom.confirmBackdrop) dom.confirmBackdrop.classList.add("hidden");
    if (dom.confirmDeleteBtn) {
      dom.confirmDeleteBtn.disabled = false;
      dom.confirmDeleteBtn.textContent = "确认";
    }
  }

  async function doConfirm() {
    var fn = pendingConfirm;
    pendingConfirm = null;
    closeConfirm();
    if (!fn) return;
    try {
      await fn();
    } catch (e) {
      console.error("[post.js] 确认操作失败:", e);
    }
  }

  function confirmDeletePost() {
    if (!currentPost) return;
    confirmAction(
      "确定要删除帖子「" + (currentPost.title || "") + "」吗？其下所有回复将一并删除，该操作不可恢复。",
      "删除帖子",
      doDeletePost
    );
  }

  async function doDeletePost() {
    if (!currentPost) return;
    try {
      await API.deletePost(currentPost.id);
      showToast("帖子已删除", "success");
      setTimeout(function () {
        window.location.href = "index.html";
      }, 500);
    } catch (err) {
      showToast(err.message || "删除失败", "error");
    }
  }

  function confirmDeleteReply(reply) {
    var msg = "确定要删除这条回复吗？";
    var sub = reply.reply_count || 0;
    if (sub > 0) {
      msg += "该回复下还有 " + sub + " 条子回复，将一并删除。";
    }
    msg += " 该操作不可恢复。";
    confirmAction(msg, "删除回复", function () {
      return doDeleteReply(reply.id);
    });
  }

  async function doDeleteReply(replyId) {
    try {
      var data = await API.deleteReply(replyId);
      var n = (data && data.deleted_count) || 1;
      showToast("已删除 " + n + " 条回复", "success");
      loadPost();
    } catch (err) {
      showToast(err.message || "删除失败", "error");
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

      replyCount = data.reply_count !== undefined
        ? data.reply_count
        : replies.length;

      var tree = data.reply_tree || buildTreeFromFlat(replies);

      dom.postLoading.classList.add("hidden");
      dom.postDetailWrapper.classList.remove("hidden");
      renderPost(post);
      renderReplies(tree);
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

  /**
   * 兼容旧后端：从扁平 replies 列表按 parent_id 组装为树。
   * 新后端会直接返回 reply_tree，此函数仅在缺失时兜底。
   */
  function buildTreeFromFlat(replies) {
    var byId = {};
    replies.forEach(function (r) { byId[r.id] = r; });
    var roots = [];
    replies.forEach(function (r) {
      r.children = r.children || [];
      var parent = r.parent_id ? byId[r.parent_id] : null;
      if (parent && parent.id !== r.id) {
        parent.children.push(r);
      } else {
        roots.push(r);
      }
    });
    return roots;
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

    // 回复表单事件（一级回复）
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

    // 删除确认弹窗事件
    if (dom.confirmCancelBtn) {
      dom.confirmCancelBtn.addEventListener("click", closeConfirm);
    }
    if (dom.confirmDeleteBtn) {
      dom.confirmDeleteBtn.addEventListener("click", doConfirm);
    }
    if (dom.confirmBackdrop) {
      dom.confirmBackdrop.addEventListener("click", function (e) {
        if (e.target === dom.confirmBackdrop) closeConfirm();
      });
    }
    // ESC 关闭确认弹窗
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && dom.confirmBackdrop &&
          !dom.confirmBackdrop.classList.contains("hidden")) {
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

  // 暴露给外部（调试用）
  global.ForumPost = {
    reload: loadPost,
    openInlineReply: openInlineReply,
    closeInlineReply: closeInlineReply,
  };
})(typeof globalThis !== "undefined" ? globalThis : (typeof window !== "undefined" ? window : this));
