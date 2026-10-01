/* ==========================================================================
   profile.js - 用户中心页面逻辑
   - 加载用户资料（头像、昵称、简介）
   - 头像上传（文件选择 + 预览）
   - 昵称与简介编辑 + 保存
   - 密码修改（旧密码验证 + 强度校验 + 二次确认）
   - 密码显示/隐藏切换
   ========================================================================== */

(function (global) {
  "use strict";

  var API = global.ForumAPI;
  if (!API) {
    console.error("[profile.js] ForumAPI 未加载，请先引入 js/api.js");
    return;
  }

  // DOM 引用
  var dom = {};

  /* ------------------------------------------------------------------------
   * 头部导航
   * ---------------------------------------------------------------------- */
  function renderNav() {
    var actions = dom.navActions;
    if (!actions) return;
    actions.innerHTML = "";

    if (!API.isLoggedIn()) {
      // 未登录则重定向
      window.location.replace("auth.html?redirect=" + encodeURIComponent("profile.html"));
      return;
    }

    var username = "";
    try { username = localStorage.getItem("username") || ""; } catch (e) { /* ignore */ }
    var nickname = "";
    try { nickname = localStorage.getItem("nickname") || ""; } catch (e) { /* ignore */ }
    var role = "";
    try { role = localStorage.getItem("role") || ""; } catch (e) { /* ignore */ }
    var displayName = nickname || username;

    // 头像 + 下拉菜单（与首页 / 详情页一致）
    var wrap = API.el("div", { class: "user-badge-wrap" });
    var trigger = API.el("div", {
      class: "user-badge user-badge-trigger",
      attrs: {
        title: displayName || "账号菜单",
        "aria-label": "账号菜单",
        "aria-haspopup": "true",
      },
    });
    trigger.innerHTML = buildAvatarHTML(displayName || "U");

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
  }

  /**
   * 构建头像 HTML（优先使用头像图片，回退到首字母）
   */
  function buildAvatarHTML(displayName, sizeClass) {
    var avatarUrl = "";
    try { avatarUrl = localStorage.getItem("avatar_url") || ""; } catch (e) { /* ignore */ }
    var role = "";
    try { role = localStorage.getItem("role") || ""; } catch (e) { /* ignore */ }
    var cls = sizeClass || "avatar";
    if (role === "admin") cls += " avatar-admin";
    if (avatarUrl) {
      return '<span class="' + cls + ' avatar-img-wrap"><img class="avatar-img" src="' + escapeAttr(avatarUrl) + '" alt="" /></span>';
    }
    return '<span class="' + cls + '">' + API.avatarChar(displayName || "?") + '</span>';
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttr(s) {
    return escapeHtml(s);
  }

  function handleLogout() {
    API.clearToken();
    try {
      localStorage.removeItem("username");
      localStorage.removeItem("nickname");
      localStorage.removeItem("avatar_url");
    } catch (e) { /* ignore */ }
    window.location.href = "index.html";
  }

  /* ------------------------------------------------------------------------
   * 资料加载
   * ---------------------------------------------------------------------- */
  async function loadProfile() {
    dom.profileLoading.classList.remove("hidden");
    dom.profileWrapper.classList.add("hidden");

    try {
      var profile = await API.getProfile();

      // 更新本地缓存
      try {
        localStorage.setItem("username", profile.username || "");
        if (profile.nickname) localStorage.setItem("nickname", profile.nickname);
        else localStorage.removeItem("nickname");
        if (profile.avatar_url) localStorage.setItem("avatar_url", profile.avatar_url);
        else localStorage.removeItem("avatar_url");
        if (profile.role) localStorage.setItem("role", profile.role);
        else localStorage.removeItem("role");
      } catch (e) { /* ignore */ }

      // 渲染头像
      renderAvatar(profile);

      // 渲染基本信息
      dom.displayName.textContent = profile.display_name || profile.nickname || profile.username;
      dom.username.textContent = profile.username;
      dom.role.textContent = profile.role === "admin" ? "管理员" : "普通用户";
      dom.createdAt.textContent = profile.created_at
        ? API.formatDateTime(profile.created_at)
        : "未知";

      // 填充表单
      dom.nicknameInput.value = profile.nickname || "";
      dom.bioInput.value = profile.bio || "";
      updateCharCount();

      dom.profileLoading.classList.add("hidden");
      dom.profileWrapper.classList.remove("hidden");
    } catch (err) {
      dom.profileLoading.classList.add("hidden");
      if (err.status === 401) {
        // Token 过期，跳转登录
        window.location.href = "auth.html?redirect=" + encodeURIComponent("profile.html");
      } else {
        dom.profileError.classList.remove("hidden");
        dom.profileError.innerHTML = "";
        dom.profileError.appendChild(API.el("div", { class: "icon", text: "⚠️" }));
        dom.profileError.appendChild(API.el("div", { class: "text", text: err.message || "加载失败" }));
        dom.profileError.appendChild(API.el("a", {
          class: "btn btn-primary",
          attrs: { href: "index.html" },
          text: "返回首页",
        }));
      }
    }
  }

  /* ------------------------------------------------------------------------
   * 头像渲染与上传
   * ---------------------------------------------------------------------- */
  function renderAvatar(profile) {
    var avatarEl = dom.profileAvatar;
    var infoEl = dom.avatarUrlInfo;
    if (!avatarEl) return;

    if (profile.avatar_url) {
      avatarEl.classList.remove("avatar-letter");
      avatarEl.classList.add("avatar-img-wrap");
      avatarEl.innerHTML = '<img class="avatar-img avatar-lg-img" src="' + escapeAttr(profile.avatar_url) + '" alt="头像" />';
      if (infoEl) {
        infoEl.classList.remove("hidden");
        infoEl.textContent = "当前头像：" + profile.avatar_url;
      }
    } else {
      var displayName = profile.display_name || profile.username || "?";
      avatarEl.classList.add("avatar-letter");
      avatarEl.classList.remove("avatar-img-wrap");
      avatarEl.textContent = API.avatarChar(displayName);
      if (infoEl) infoEl.classList.add("hidden");
    }
  }

  async function handleAvatarUpload(e) {
    var file = e.target.files && e.target.files[0];
    if (!file) return;

    // 校验类型
    var allowedTypes = ["image/jpeg", "image/png", "image/webp", "image/gif"];
    if (allowedTypes.indexOf(file.type) === -1) {
      showToast("请选择 JPG / PNG / WebP / GIF 格式的图片", "error");
      e.target.value = "";
      return;
    }

    // 校验大小（2MB）
    if (file.size > 2 * 1024 * 1024) {
      showToast("图片大小不能超过 2MB", "error");
      e.target.value = "";
      return;
    }

    try {
      var data = await API.uploadAvatar(file);
      showToast("头像上传成功！", "success");

      // 更新本地缓存
      try { localStorage.setItem("avatar_url", data.avatar_url); } catch (ex) { /* ignore */ }

      // 更新头像显示
      renderAvatar({ avatar_url: data.avatar_url });
      // 更新导航栏头像
      renderNav();
    } catch (err) {
      showToast(err.message || "头像上传失败", "error");
    } finally {
      e.target.value = ""; // 重置文件输入
    }
  }

  /* ------------------------------------------------------------------------
   * 昵称与简介
   * ---------------------------------------------------------------------- */
  function updateCharCount() {
    var len = (dom.bioInput.value || "").length;
    var countEl = dom.bioCharCount;
    if (countEl) countEl.textContent = String(len);
  }

  async function saveProfile() {
    var nickname = (dom.nicknameInput.value || "").trim();
    var bio = (dom.bioInput.value || "").trim();

    // 清除错误
    clearErrors(["nickname-group", "bio-group"]);

    var hasError = false;

    // 昵称校验
    if (nickname.length > 50) {
      setError("nickname-group", "昵称不能超过 50 个字符");
      hasError = true;
    }

    // 简介校验
    if (bio.length > 200) {
      setError("bio-group", "简介不能超过 200 个字符");
      hasError = true;
    }

    if (hasError) return;

    var btn = dom.saveProfileBtn;
    btn.disabled = true;
    btn.textContent = "保存中…";

    try {
      await API.updateProfile({
        nickname: nickname || null,
        bio: bio || null,
      });

      // 更新本地缓存
      try {
        if (nickname) localStorage.setItem("nickname", nickname);
        else localStorage.removeItem("nickname");
      } catch (e) { /* ignore */ }

      // 更新显示名
      dom.displayName.textContent = nickname || dom.username.textContent;
      showToast("资料保存成功！", "success");

      // 更新导航栏
      renderNav();
    } catch (err) {
      showToast(err.message || "保存失败", "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "保存资料";
    }
  }

  /* ------------------------------------------------------------------------
   * 密码修改
   * ---------------------------------------------------------------------- */
  async function changePassword() {
    var oldPwd = dom.oldPasswordInput.value || "";
    var newPwd = dom.newPasswordInput.value || "";
    var confirmPwd = dom.confirmPasswordInput.value || "";

    clearErrors(["old-password-group", "new-password-group", "confirm-password-group"]);

    var hasError = false;

    // 校验
    if (!oldPwd) {
      setError("old-password-group", "请输入当前密码");
      hasError = true;
    }
    if (!newPwd) {
      setError("new-password-group", "请输入新密码");
      hasError = true;
    } else {
      var strengthErr = validatePasswordStrength(newPwd);
      if (strengthErr) {
        setError("new-password-group", strengthErr);
        hasError = true;
      }
    }
    if (!confirmPwd) {
      setError("confirm-password-group", "请确认新密码");
      hasError = true;
    } else if (newPwd && newPwd !== confirmPwd) {
      setError("confirm-password-group", "两次输入的新密码不一致");
      hasError = true;
    }

    if (hasError) return;

    // 二次确认：修改密码后需重新登录，弹窗确认后再提交
    openPasswordConfirm();
  }

  /* ------------------------------------------------------------------------
   * 修改密码二次确认弹窗
   * ---------------------------------------------------------------------- */
  var pendingChangePassword = false;

  function openPasswordConfirm() {
    var backdrop = dom.passwordConfirmBackdrop;
    if (!backdrop) {
      doChangePassword();
      return;
    }
    pendingChangePassword = true;
    backdrop.classList.remove("hidden");
    var confirmBtn = document.getElementById("password-confirm-btn");
    if (confirmBtn) confirmBtn.focus();
  }

  function closePasswordConfirm() {
    pendingChangePassword = false;
    if (dom.passwordConfirmBackdrop) {
      dom.passwordConfirmBackdrop.classList.add("hidden");
    }
  }

  async function doChangePassword() {
    var oldPwd = dom.oldPasswordInput.value || "";
    var newPwd = dom.newPasswordInput.value || "";

    var btn = dom.changePasswordBtn;
    btn.disabled = true;
    btn.textContent = "修改中…";

    try {
      await API.changePassword(oldPwd, newPwd);
      showToast("密码修改成功！", "success");

      // 清空密码输入框
      dom.oldPasswordInput.value = "";
      dom.newPasswordInput.value = "";
      dom.confirmPasswordInput.value = "";
      updatePasswordStrength("");
    } catch (err) {
      showToast(err.message || "密码修改失败", "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "修改密码";
    }
  }

  /**
   * 前端密码强度校验（与后端一致）
   */
  function validatePasswordStrength(password) {
    if (!password) return "请输入密码";
    if (password.length < 8) return "密码长度至少 8 个字符";
    if (password.length > 72) return "密码长度不能超过 72 个字符";
    if (!/[a-zA-Z]/.test(password)) return "密码必须包含至少一个字母";
    if (!/\d/.test(password)) return "密码必须包含至少一个数字";
    if (!/[^\w]/.test(password)) return "密码必须包含至少一个特殊字符（如 . @ # $ 等）";
    return "";
  }

  /**
   * 密码强度可视化
   */
  function updatePasswordStrength(password) {
    var strengthEl = dom.passwordStrength;
    if (!strengthEl) return;

    var bar = strengthEl.querySelector(".strength-bar");
    var text = strengthEl.querySelector(".strength-text");

    if (!password) {
      strengthEl.hidden = true;
      bar.className = "strength-bar";
      bar.style.width = "0%";
      text.textContent = "";
      return;
    }
    strengthEl.hidden = false;

    var score = 0;
    if (password.length >= 8) score++;
    if (password.length >= 12) score++;
    if (/[a-zA-Z]/.test(password)) score++;
    if (/\d/.test(password)) score++;
    if (/[^a-zA-Z0-9]/.test(password)) score++;
    if (password.length >= 16) score++;

    var levels = [
      { width: "20%", color: "#ef4444", text: "弱" },
      { width: "40%", color: "#f59e0b", text: "较弱" },
      { width: "60%", color: "#eab308", text: "一般" },
      { width: "80%", color: "#22c55e", text: "强" },
      { width: "100%", color: "#10b981", text: "极强" },
    ];

    var idx = Math.min(score - 1, levels.length - 1);
    if (idx < 0) idx = 0;

    bar.className = "strength-bar";
    bar.style.width = levels[idx].width;
    bar.style.background = levels[idx].color;
    text.textContent = levels[idx].text;
  }

  /**
   * 密码显示/隐藏切换（眼睛按钮，双态图标）
   */
  function togglePassword(btn) {
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
  }

  /* ------------------------------------------------------------------------
   * 表单错误处理
   * ---------------------------------------------------------------------- */
  function setError(groupId, message) {
    var group = document.getElementById(groupId);
    if (!group) return;
    group.classList.add("has-error");
    var errEl = group.querySelector(".form-error");
    if (errEl) errEl.textContent = message;
  }

  function clearErrors(groupIds) {
    groupIds.forEach(function (gid) {
      var group = document.getElementById(gid);
      if (!group) return;
      group.classList.remove("has-error");
      var errEl = group.querySelector(".form-error");
      if (errEl) errEl.textContent = "";
    });
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
    // DOM 绑定
    dom.navActions = document.getElementById("nav-actions");
    dom.profileLoading = document.getElementById("profile-loading");
    dom.profileWrapper = document.getElementById("profile-wrapper");
    dom.profileError = document.getElementById("profile-error");
    dom.profileAvatar = document.getElementById("profile-avatar");
    dom.avatarUrlInfo = document.getElementById("profile-avatar-url");
    dom.displayName = document.getElementById("profile-display-name");
    dom.username = document.getElementById("profile-username");
    dom.role = document.getElementById("profile-role");
    dom.createdAt = document.getElementById("profile-created-at");
    dom.nicknameInput = document.getElementById("nickname-input");
    dom.bioInput = document.getElementById("bio-input");
    dom.bioCharCount = document.getElementById("bio-char-count");
    dom.saveProfileBtn = document.getElementById("save-profile-btn");
    dom.oldPasswordInput = document.getElementById("old-password-input");
    dom.newPasswordInput = document.getElementById("new-password-input");
    dom.confirmPasswordInput = document.getElementById("confirm-password-input");
    dom.changePasswordBtn = document.getElementById("change-password-btn");
    dom.passwordStrength = document.getElementById("password-strength");
    dom.toastContainer = document.getElementById("toast-container");

    // 检查登录状态
    if (!API.isLoggedIn()) {
      window.location.replace("auth.html?redirect=" + encodeURIComponent("profile.html"));
      return;
    }

    renderNav();

    // 头像上传事件
    var avatarInput = document.getElementById("avatar-upload-input");
    if (avatarInput) {
      avatarInput.addEventListener("change", handleAvatarUpload);
    }

    // 简介字数统计
    if (dom.bioInput) {
      dom.bioInput.addEventListener("input", updateCharCount);
    }

    // 保存资料
    if (dom.saveProfileBtn) {
      dom.saveProfileBtn.addEventListener("click", saveProfile);
    }

    // 修改密码
    if (dom.changePasswordBtn) {
      dom.changePasswordBtn.addEventListener("click", changePassword);
    }

    // 修改密码二次确认弹窗
    dom.passwordConfirmBackdrop = document.getElementById("password-confirm-backdrop");
    var pwdConfirmBtn = document.getElementById("password-confirm-btn");
    var pwdConfirmCancelBtn = document.getElementById("password-confirm-cancel-btn");
    if (pwdConfirmBtn) {
      pwdConfirmBtn.addEventListener("click", function () {
        closePasswordConfirm();
        doChangePassword();
      });
    }
    if (pwdConfirmCancelBtn) {
      pwdConfirmCancelBtn.addEventListener("click", closePasswordConfirm);
    }
    if (dom.passwordConfirmBackdrop) {
      dom.passwordConfirmBackdrop.addEventListener("click", function (e) {
        if (e.target === dom.passwordConfirmBackdrop) closePasswordConfirm();
      });
    }
    // ESC 关闭弹窗
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && dom.passwordConfirmBackdrop &&
          !dom.passwordConfirmBackdrop.classList.contains("hidden")) {
        closePasswordConfirm();
      }
    });

    // 密码强度实时反馈
    if (dom.newPasswordInput) {
      dom.newPasswordInput.addEventListener("input", function () {
        updatePasswordStrength(dom.newPasswordInput.value);
      });
    }

    // 密码显示/隐藏切换
    var toggles = document.querySelectorAll(".password-toggle");
    toggles.forEach(function (btn) {
      btn.addEventListener("click", function () {
        togglePassword(btn);
      });
    });

    // 加载资料
    loadProfile();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // 暴露给外部（调试用）
  global.ForumProfile = {
    reload: loadProfile,
  };
})(typeof globalThis !== "undefined" ? globalThis : (typeof window !== "undefined" ? window : this));
