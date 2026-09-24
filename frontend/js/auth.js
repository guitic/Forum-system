/* ==========================================================================
   auth.js - 鉴权页逻辑
   - 登录 / 注册 Tab 切换
   - 表单校验（用户名长度、密码长度、二次确认）
   - 成功后 localStorage.setItem('token', token) 并跳回首页
   - 已登录状态自动重定向到首页
   - 支持 ?redirect= 参数回跳
   ========================================================================== */

(function (global) {
  "use strict";

  var API = global.ForumAPI;
  if (!API) {
    console.error("[auth.js] ForumAPI 未加载，请先引入 js/api.js");
    return;
  }

  // DOM 引用
  var dom = {};

  // 当前模式：'login' | 'register'
  var currentMode = "login";

  // 默认跳转地址
  var defaultRedirect = "index.html";

  /* ------------------------------------------------------------------------
   * 头部导航
   * ---------------------------------------------------------------------- */
  function renderNav() {
    var actions = dom.navActions;
    if (!actions) return;
    actions.innerHTML = "";

    if (API.isLoggedIn()) {
      var logoutBtn = API.el("a", {
        class: "btn btn-ghost btn-sm",
        attrs: { href: "javascript:void(0)" },
      }, ["登出"]);
      logoutBtn.addEventListener("click", handleLogout);
      actions.appendChild(logoutBtn);
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
    switchMode("login");
    clearFormErrors();
    dom.loginUsername.value = "";
    dom.loginPassword.value = "";
    showToast("已退出登录", "info");
  }

  /* ------------------------------------------------------------------------
   * 模式切换
   * ---------------------------------------------------------------------- */
  function switchMode(mode) {
    currentMode = mode;
    // Tab 高亮
    var tabs = document.querySelectorAll(".auth-tab");
    tabs.forEach(function (t) {
      t.classList.toggle("active", t.dataset.mode === mode);
    });
    // 切换面板
    dom.loginPanel.classList.toggle("hidden", mode !== "login");
    dom.registerPanel.classList.toggle("hidden", mode !== "register");
    clearFormErrors();
    // 强度条随面板切换重新计算（密码为空时自动隐藏）
    updatePasswordStrength();
  }

  /* ------------------------------------------------------------------------
   * 校验
   * ---------------------------------------------------------------------- */

  function validateUsername(v) {
    var s = (v || "").trim();
    if (!s) return "请输入用户名";
    if (s.length < 8 || s.length > 14) return "用户名长度须在 8-14 位之间";
    // 首位必须是小写英文字母
    if (!/^[a-z]/.test(s)) return "用户名首位必须是小写英文字母";
    // 仅允许字母和数字
    if (!/^[a-zA-Z0-9]+$/.test(s)) return "用户名只能包含字母和数字";
    // 必须同时包含字母和数字
    if (!/\d/.test(s)) return "用户名必须包含至少一个数字";
    if (!/[a-zA-Z]/.test(s)) return "用户名必须包含至少一个字母";
    return "";
  }

  function validatePassword(v) {
    var s = v || "";
    if (!s) return "请输入密码";
    if (s.length < 8) return "密码长度至少 8 个字符";
    if (s.length > 72) return "密码长度不能超过 72 个字符";
    if (!/[a-zA-Z]/.test(s)) return "密码必须包含至少一个字母";
    if (!/\d/.test(s)) return "密码必须包含至少一个数字";
    if (!/[^\w]/.test(s)) return "密码必须包含至少一个特殊字符（如 . @ # $ 等）";
    return "";
  }

  function clearFormErrors() {
    [dom.loginUsernameGroup, dom.loginPasswordGroup,
     dom.regUsernameGroup, dom.regPasswordGroup, dom.regConfirmGroup].forEach(function (g) {
      if (g) g.classList.remove("has-error");
    });
  }

  function setGroupError(group, message) {
    if (!group) return;
    group.classList.add("has-error");
    var errEl = group.querySelector(".form-error");
    if (errEl) errEl.textContent = message;
  }

  /* ------------------------------------------------------------------------
   * 密码可见性切换（眼睛按钮）
   * ---------------------------------------------------------------------- */
  function bindPasswordToggles() {
    var toggles = document.querySelectorAll(".password-toggle");
    Array.prototype.forEach.call(toggles, function (btn) {
      btn.addEventListener("click", function () {
        var targetId = btn.getAttribute("data-target");
        var input = document.getElementById(targetId);
        if (!input) return;
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.setAttribute("aria-pressed", show ? "true" : "false");
        var eyeOpen = btn.querySelector(".eye-open");
        var eyeClosed = btn.querySelector(".eye-closed");
        if (eyeOpen) eyeOpen.hidden = show;
        if (eyeClosed) eyeClosed.hidden = !show;
        btn.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
        input.focus();
      });
    });
  }

  /* ------------------------------------------------------------------------
   * 注册密码强度条
   * ---------------------------------------------------------------------- */
  function scorePassword(v) {
    var s = v || "";
    var score = 0;
    if (s.length >= 8) score += 1;
    if (s.length >= 12) score += 1;
    if (/[a-zA-Z]/.test(s) && /\d/.test(s)) score += 1;
    if (/[^\w]/.test(s)) score += 1;
    return score; // 0-4
  }

  function updatePasswordStrength() {
    var wrap = document.getElementById("register-password-strength");
    var input = dom.regPassword;
    if (!wrap || !input) return;

    var value = input.value || "";
    if (!value) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;

    var score = scorePassword(value);
    var bar = wrap.querySelector(".strength-bar");
    var text = wrap.querySelector(".strength-text");
    var levels = [
      { w: "20%", c: "var(--danger)", t: "弱" },
      { w: "45%", c: "var(--warning)", t: "中" },
      { w: "70%", c: "var(--warning)", t: "良" },
      { w: "85%", c: "var(--success)", t: "强" },
      { w: "100%", c: "var(--success)", t: "很强" },
    ];
    var lv = levels[Math.max(0, Math.min(4, score))];
    if (bar) {
      bar.style.width = lv.w;
      bar.style.background = lv.c;
    }
    if (text) {
      text.textContent = lv.t;
      text.style.color = lv.c;
    }
  }

  /* ------------------------------------------------------------------------
   * 登录
   * ---------------------------------------------------------------------- */
  async function doLogin() {
    clearFormErrors();
    var username = dom.loginUsername.value.trim();
    var password = dom.loginPassword.value;

    var uErr = username ? "" : "请输入用户名";
    var pErr = password ? "" : "请输入密码";
    if (uErr) setGroupError(dom.loginUsernameGroup, uErr);
    if (pErr) setGroupError(dom.loginPasswordGroup, pErr);
    if (uErr || pErr) return;

    var btn = dom.loginSubmitBtn;
    btn.disabled = true;
    btn.textContent = "登录中…";
    try {
      var data = await API.login(username, password);
      var token = data.token || (data && data.data && data.data.token);
      if (!token) {
        throw new Error("服务器未返回有效的 Token");
      }
      API.setToken(token);
      // 缓存用户名和用户资料信息
      var uname = data.username || (data && data.data && data.data.username) || username;
      try { localStorage.setItem("username", uname); } catch (e) { /* ignore */ }
      // V2: 缓存昵称和头像
      if (data.nickname) try { localStorage.setItem("nickname", data.nickname); } catch (e) { /* ignore */ }
      else try { localStorage.removeItem("nickname"); } catch (e) { /* ignore */ }
      if (data.avatar_url) try { localStorage.setItem("avatar_url", data.avatar_url); } catch (e) { /* ignore */ }
      else try { localStorage.removeItem("avatar_url"); } catch (e) { /* ignore */ }
      // V3: 缓存角色（用于前端判断是否显示管理员特权操作）
      if (data.role) try { localStorage.setItem("role", data.role); } catch (e) { /* ignore */ }
      else try { localStorage.removeItem("role"); } catch (e) { /* ignore */ }
      showToast("登录成功，正在跳转…", "success");
      setTimeout(function () {
        window.location.href = getRedirect();
      }, 400);
    } catch (err) {
      var msg = err.message || "登录失败";
      // 后端返回的用户名/密码错误友好提示
      if (msg.indexOf("密码") !== -1 || msg.indexOf("不存在") !== -1 || msg.indexOf("invalid") !== -1) {
        msg = "用户名或密码错误";
      }
      showToast(msg, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "登 录";
    }
  }

  /* ------------------------------------------------------------------------
   * 注册
   * ---------------------------------------------------------------------- */
  async function doRegister() {
    clearFormErrors();
    var username = dom.regUsername.value.trim();
    var password = dom.regPassword.value;
    var confirm = dom.regConfirm.value;

    var uErr = validateUsername(username);
    var pErr = validatePassword(password);
    var cErr = "";
    if (!confirm) cErr = "请再次输入密码";
    else if (password !== confirm) cErr = "两次输入的密码不一致";

    if (uErr) setGroupError(dom.regUsernameGroup, uErr);
    if (pErr) setGroupError(dom.regPasswordGroup, pErr);
    if (cErr) setGroupError(dom.regConfirmGroup, cErr);
    if (uErr || pErr || cErr) return;

    var btn = dom.registerSubmitBtn;
    btn.disabled = true;
    btn.textContent = "注册中…";
    try {
      var data = await API.register(username, password);
      // 注册后通常直接返回 token 或需要再登录
      var token = data.token || (data && data.data && data.data.token);
      if (token) {
        API.setToken(token);
        try { localStorage.setItem("username", username); } catch (e) { /* ignore */ }
        try { localStorage.removeItem("nickname"); } catch (e) { /* ignore */ }
        try { localStorage.removeItem("avatar_url"); } catch (e) { /* ignore */ }
        showToast("注册成功，已自动登录", "success");
        setTimeout(function () {
          window.location.href = getRedirect();
        }, 400);
      } else {
        // 注册成功但未自动登录 → 提示并切换到登录
        showToast("注册成功，请登录", "success");
        switchMode("login");
        dom.loginUsername.value = username;
        dom.loginPassword.value = "";
        dom.loginPassword.focus();
      }
    } catch (err) {
      var msg = err.message || "注册失败";
      if (msg.indexOf("已存在") !== -1 || msg.indexOf("exist") !== -1 || msg.indexOf("duplicate") !== -1) {
        msg = "该用户名已被注册";
      }
      showToast(msg, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "注 册";
    }
  }

  /* ------------------------------------------------------------------------
   * 跳转地址
   * ---------------------------------------------------------------------- */
  function getRedirect() {
    var params = new URLSearchParams(window.location.search);
    var r = params.get("redirect");
    if (!r) return defaultRedirect;
    // 仅允许相对路径，避免开放重定向
    try {
      var u = new URL(r, window.location.origin);
      if (u.origin !== window.location.origin) return defaultRedirect;
      if (!/^(index|post|auth|profile)\.html/.test(u.pathname.split("/").pop())) return defaultRedirect;
      // 保留 hash（如 post.html?id=1#reply），登录回跳后详情页可据此聚焦回复框
      return u.pathname + u.search + u.hash;
    } catch (e) {
      return defaultRedirect;
    }
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
    }, 3200);
  }

  /* ------------------------------------------------------------------------
   * 初始化
   * ---------------------------------------------------------------------- */
  function init() {
    dom.navActions = document.getElementById("nav-actions");
    dom.loginPanel = document.getElementById("login-panel");
    dom.registerPanel = document.getElementById("register-panel");
    dom.loginUsername = document.getElementById("login-username");
    dom.loginPassword = document.getElementById("login-password");
    dom.loginUsernameGroup = document.getElementById("login-username-group");
    dom.loginPasswordGroup = document.getElementById("login-password-group");
    dom.loginSubmitBtn = document.getElementById("login-submit-btn");
    dom.regUsername = document.getElementById("register-username");
    dom.regPassword = document.getElementById("register-password");
    dom.regConfirm = document.getElementById("register-confirm");
    dom.regUsernameGroup = document.getElementById("register-username-group");
    dom.regPasswordGroup = document.getElementById("register-password-group");
    dom.regConfirmGroup = document.getElementById("register-confirm-group");
    dom.registerSubmitBtn = document.getElementById("register-submit-btn");
    dom.toastContainer = document.getElementById("toast-container");

    // 若已登录则重定向
    if (API.isLoggedIn()) {
      window.location.replace(getRedirect());
      return;
    }

    renderNav();

    // Tab 切换
    var tabs = document.querySelectorAll(".auth-tab");
    tabs.forEach(function (t) {
      t.addEventListener("click", function () {
        switchMode(t.dataset.mode);
      });
    });

    // 表单提交
    var loginForm = document.getElementById("login-form");
    if (loginForm) {
      loginForm.addEventListener("submit", function (e) {
        e.preventDefault();
        doLogin();
      });
    }
    if (dom.loginSubmitBtn) dom.loginSubmitBtn.addEventListener("click", doLogin);

    var regForm = document.getElementById("register-form");
    if (regForm) {
      regForm.addEventListener("submit", function (e) {
        e.preventDefault();
        doRegister();
      });
    }
    if (dom.registerSubmitBtn) dom.registerSubmitBtn.addEventListener("click", doRegister);

    // 密码可见性切换（登录 / 注册 / 确认密码）
    bindPasswordToggles();

    // 注册密码实时强度
    if (dom.regPassword) {
      dom.regPassword.addEventListener("input", updatePasswordStrength);
    }

    // 回车提交
    [dom.loginUsername, dom.loginPassword, dom.regUsername, dom.regPassword, dom.regConfirm].forEach(function (inp) {
      if (!inp) return;
      inp.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          if (currentMode === "login") doLogin();
          else doRegister();
        }
      });
    });

    // 默认模式：若 URL 带 ?mode=register 则切到注册，否则显示登录
    var params = new URLSearchParams(window.location.search);
    if (params.get("mode") === "register") switchMode("register");
    else switchMode("login");

    // 焦点
    setTimeout(function () {
      if (dom.loginUsername) dom.loginUsername.focus();
    }, 100);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  if (typeof global.ForumAuth !== "undefined" || true) {
    global.ForumAuth = {
      switchMode: switchMode,
    };
  }
})(typeof globalThis !== "undefined" ? globalThis : (typeof window !== "undefined" ? window : this));
