/* ==========================================================================
   api.js - API 请求封装
   - 统一处理 fetch、Bearer Token 注入、错误响应
   - 401 自动跳转 auth.html
   - 暴露全局 ForumAPI 命名空间，供 index.js / post.js / auth.js 调用
   ========================================================================== */

(function (global) {
  "use strict";

  // 后端 BaseURL：
  //   Flask 直接提供前端文件（本地开发 + 生产部署均适用），留空即可
  //   如使用独立的前端服务器，可设置后端地址，例如 'http://127.0.0.1:5000'
  var BASE_URL = "";

  // Token 在 localStorage 中的 key
  var TOKEN_KEY = "token";

  var API = {};

  /* ------------------------------------------------------------------------
   * Token 管理
   * ---------------------------------------------------------------------- */
  API.getToken = function () {
    try {
      return localStorage.getItem(TOKEN_KEY) || "";
    } catch (e) {
      return "";
    }
  };

  API.setToken = function (token) {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (e) {
      // ignore
    }
  };

  API.clearToken = function () {
    API.setToken("");
  };

  API.isLoggedIn = function () {
    return !!API.getToken();
  };

  /* ------------------------------------------------------------------------
   * 工具函数
   * ---------------------------------------------------------------------- */

  /**
   * 构造带 query 的 URL
   * @param {string} path
   * @param {Object} params 键值对，值为 null/undefined 的会被忽略
   */
  function buildUrl(path, params) {
    var url = BASE_URL + path;
    if (!params || typeof params !== "object") return url;
    var qs = [];
    Object.keys(params).forEach(function (k) {
      var v = params[k];
      if (v === null || v === undefined || v === "") return;
      qs.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
    });
    if (qs.length) url += (url.indexOf("?") === -1 ? "?" : "&") + qs.join("&");
    return url;
  }

  /**
   * 解析响应体：优先 JSON，失败时返回原始文本
   */
  async function parseBody(response) {
    if (!response.bodyUsed) {
      var text = await response.text();
      if (!text) return {};
      try {
        return JSON.parse(text);
      } catch (e) {
        return { _raw: text };
      }
    }
    return {};
  }

  /**
   * 抽取人类可读的错误信息
   */
  function extractError(data, fallback) {
    if (!data) return fallback;
    if (typeof data === "string") return data;
    if (data.message) return data.message;
    if (data.error) {
      return typeof data.error === "object" ? JSON.stringify(data.error) : data.error;
    }
    if (data.detail) return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    if (data._raw) return data._raw;
    return fallback;
  }

  /**
   * 401 时跳转登录页（带 redirect 参数方便登录后回跳）
   */
  function handleUnauthorized() {
    API.clearToken();
    var redirect = encodeURIComponent(
      window.location.pathname + window.location.search
    );
    var authUrl = "auth.html?redirect=" + redirect;
    if (window.location.pathname !== "auth.html") {
      window.location.replace(authUrl);
    }
  }

  /* ------------------------------------------------------------------------
   * 核心请求方法
   * ---------------------------------------------------------------------- */

  /**
   * 通用 fetch 封装
   * @param {string} path 例如 '/api/posts'
   * @param {Object} [opts]
   * @param {'GET'|'POST'|'PUT'|'DELETE'|'PATCH'} [opts.method='GET']
   * @param {Object} [opts.body]   POST/PUT 请求体（对象，将被 JSON 序列化）
   * @param {Object} [opts.params] URL query 参数
   * @param {boolean} [opts.auth=true] 是否携带 Bearer Token
   * @param {Object} [opts.headers] 额外 headers
   * @param {boolean} [opts.redirectOn401=true] 401 时是否自动跳转
   * @returns {Promise<any>} 解析后的响应 JSON（或原始文本）
   * @throws {Error} 网络错误 / 非 2xx 响应（错误对象带 .status / .data）
   */
  async function request(path, opts) {
    opts = opts || {};
    var method = (opts.method || "GET").toUpperCase();
    var needsAuth = opts.auth !== false;
    var redirectOn401 = opts.redirectOn401 !== false;
    var token = API.getToken();

    var headers = Object.assign(
      { Accept: "application/json" },
      opts.headers || {}
    );

    // 携带 Bearer Token
    if (needsAuth && token) {
      headers["Authorization"] = "Bearer " + token;
    }

    // JSON body
    var body = undefined;
    if (opts.body !== undefined && opts.body !== null) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }

    var url = buildUrl(path, opts.params);

    var response;
    var controller = new AbortController();
    var timeout = setTimeout(function () {
      controller.abort();
    }, 15000); // 15 秒超时

    try {
      response = await fetch(url, {
        method: method,
        headers: headers,
        body: body,
        credentials: "same-origin",
        signal: controller.signal,
      });
    } catch (netErr) {
      clearTimeout(timeout);
      if (netErr.name === "AbortError") {
        throw new Error("请求超时，请检查网络连接或后端服务是否启动");
      }
      var e = new Error("网络请求失败，请检查网络连接或后端服务是否启动");
      e.cause = netErr;
      e.network = true;
      throw e;
    }
    clearTimeout(timeout);

    var data = await parseBody(response);

    if (response.status === 401) {
      if (redirectOn401) {
        handleUnauthorized();
        // 抛出一个标记错误，阻止后续逻辑继续执行
        var ue = new Error("登录已过期，请重新登录");
        ue.status = 401;
        ue.data = data;
        throw ue;
      }
    }

    if (!response.ok) {
      var msg = extractError(
        data,
        "请求失败（HTTP " + response.status + "）"
      );
      var err = new Error(msg);
      err.status = response.status;
      err.data = data;
      throw err;
    }

    return data;
  }

  API.request = request;

  /* ------------------------------------------------------------------------
   * 鉴权接口
   * ---------------------------------------------------------------------- */

  /**
   * 登录
   * @param {string} username
   * @param {string} password
   * @returns {Promise<{token:string}>}
   */
  API.login = function (username, password) {
    return request("/api/auth/login", {
      method: "POST",
      body: { username: username, password: password },
      auth: false, // 登录接口无需 Token
      redirectOn401: false, // 登录接口 401 表示凭据错误，不应跳转
    });
  };

  /**
   * 注册
   * @param {string} username
   * @param {string} password
   */
  API.register = function (username, password) {
    return request("/api/auth/register", {
      method: "POST",
      body: { username: username, password: password },
      auth: false,
      redirectOn401: false,
    });
  };

  /* ------------------------------------------------------------------------
   * 帖子接口
   * ---------------------------------------------------------------------- */

  /**
   * 获取主贴列表（分页）
   * @param {number} [page=1]
   * @param {number} [limit=20]
   * @returns {Promise<{posts:Array, total:number, page:number, limit:number, pages:number}>}
   */
  API.getPosts = function (page, limit) {
    return request("/api/posts", {
      method: "GET",
      params: { page: page || 1, limit: limit || 20 },
      auth: false, // 列表对游客开放
    });
  };

  /**
   * 获取帖子详情
   * @param {number|string} id
   * @returns {Promise<{post:Object, replies:Array, reply_tree:Array, reply_count:number}>}
   *        V3 起额外返回 reply_tree（嵌套树）与 reply_count
   */
  API.getPost = function (id) {
    return request("/api/posts/" + encodeURIComponent(id), {
      method: "GET",
      auth: false,
    });
  };

  /**
   * 发布新贴
   * @param {string} title
   * @param {string} content
   */
  API.createPost = function (title, content) {
    return request("/api/posts", {
      method: "POST",
      body: { title: title, content: content },
      auth: true,
    });
  };

  /**
   * 发布回复（V3：支持楼中楼）
   * @param {number|string} postId
   * @param {string} content
   * @param {number|string} [parentId] 可选；传入则作为对该回复的子回复，
   *        省略或传 null 表示一级回复
   * @returns {Promise<Object>} 新回复节点（含 depth/root_id/parent_id 等层级字段）
   */
  API.createReply = function (postId, content, parentId) {
    var body = { content: content };
    if (parentId !== undefined && parentId !== null && parentId !== "") {
      body.parent_id = parentId;
    }
    return request("/api/posts/" + encodeURIComponent(postId) + "/replies", {
      method: "POST",
      body: body,
      auth: true,
    });
  };

  /**
   * 编辑帖子（模块 1 新增）
   * 仅帖子作者或管理员可调用；成功后服务端写入 updated_at。
   * @param {number|string} id
   * @param {string} title
   * @param {string} content
   * @returns {Promise<Object>} 更新后的完整帖子信息
   */
  API.updatePost = function (id, title, content) {
    return request("/api/posts/" + encodeURIComponent(id), {
      method: "PUT",
      body: { title: title, content: content },
      auth: true,
    });
  };

  /**
   * 编辑回复（模块 1 新增）
   * 仅回复作者或管理员可调用；成功后服务端写入 updated_at。
   * @param {number|string} replyId
   * @param {string} content
   * @returns {Promise<Object>} 更新后的完整回复节点
   */
  API.updateReply = function (replyId, content) {
    return request("/api/replies/" + encodeURIComponent(replyId), {
      method: "PUT",
      body: { content: content },
      auth: true,
    });
  };

  /**
   * 删除回复（V3 新增）
   * 仅回复作者或管理员可调用；服务端会级联删除其所有子孙回复。
   * @param {number|string} replyId
   * @returns {Promise<{message:string, deleted_count:number}>}
   */
  API.deleteReply = function (replyId) {
    return request("/api/replies/" + encodeURIComponent(replyId), {
      method: "DELETE",
      auth: true,
    });
  };

  /**
   * 删除帖子
   * @param {number|string} id
   */
  API.deletePost = function (id) {
    return request("/api/posts/" + encodeURIComponent(id), {
      method: "DELETE",
      auth: true,
    });
  };

  /* ------------------------------------------------------------------------
   * 时间格式化（友好格式）
   * @param {string|number|Date} input
   * @returns {string} 例如 "3天前" / "刚刚" / "2024-01-01 12:00"
   */
  /**
   * 将任意输入转为 Date 对象（兼容字符串、时间戳、Date）
   */
  API._toDate = function (input) {
    if (!input) return null;
    if (input instanceof Date) return input;
    if (typeof input === "number") return new Date(input);
    var s = String(input).replace(" ", "T");
    var date = new Date(s);
    if (isNaN(date.getTime())) date = new Date(String(input));
    return isNaN(date.getTime()) ? null : date;
  };

  API.formatTime = function (input) {
    if (!input) return "";
    var date = API._toDate(input);
    if (!date) return String(input);

    var now = Date.now();
    var diff = Math.floor((now - date.getTime()) / 1000);

    if (diff < 0) {
      // 未来时间，直接显示
      return API._formatDateTime(date);
    }

    var sec = 60;
    var min = 60;
    var hour = 24;
    var day = 30;

    if (diff < 10) return "刚刚";
    if (diff < 60) return diff + "秒前";
    if (diff < 60 * min) return Math.floor(diff / 60) + "分钟前";
    if (diff < 60 * 60 * hour) return Math.floor(diff / (60 * 60)) + "小时前";
    if (diff < 60 * 60 * 24 * 7) return Math.floor(diff / (60 * 60 * 24)) + "天前";
    if (diff < 60 * 60 * 24 * day) {
      var weeks = Math.floor(diff / (60 * 60 * 24 * 7));
      return weeks + "周前";
    }
    return API._formatDateTime(date);
  };

  API._formatDateTime = function (date) {
    function pad(n) {
      return n < 10 ? "0" + n : "" + n;
    }
    return (
      date.getFullYear() +
      "-" +
      pad(date.getMonth() + 1) +
      "-" +
      pad(date.getDate()) +
      " " +
      pad(date.getHours()) +
      ":" +
      pad(date.getMinutes())
    );
  };

  /**
   * 获取完整日期时间字符串（用于 tooltip / 详情）
   */
  API.formatDateTime = function (input) {
    var date = API._toDate(input);
    if (!date) return String(input);
    return API._formatDateTime(date);
  };

  /* ------------------------------------------------------------------------
   * XSS 防护
   *
   * 采用白名单方式清洗标记语言生成后的 HTML：
   * - 移除危险标签：script/style/iframe/object/embed/form/link/meta/base
   * - 移除危险属性：on*（事件）、style（含不安全 CSS）、formaction、srcdoc
   * - 校验 href/src 协议：仅允许 http(s)/mailto/tel/#/相对路径
   * - 保留 markdown 常用标签
   * ---------------------------------------------------------------------- */

  var ALLOWED_TAGS = {
    p: 1, br: 1, hr: 1,
    h1: 1, h2: 1, h3: 1, h4: 1, h5: 1, h6: 1,
    ul: 1, ol: 1, li: 1,
    blockquote: 1,
    code: 1, pre: 1,
    a: 1,
    em: 1, i: 1, strong: 1, b: 1,
    del: 1, ins: 1, s: 1, strike: 1,
    sup: 1, sub: 1,
    table: 1, thead: 1, tbody: 1, tfoot: 1, tr: 1, th: 1, td: 1,
    span: 1,
    img: 1,
    u: 1,
  };

  var ALLOWED_ATTRS = {
    a: ["href", "title", "target", "rel"],
    img: ["src", "alt", "title"],
    code: ["class"],
    span: ["class"],
    td: ["align"],
    th: ["align"],
  };

  var SAFE_PROTOCOLS = ["http:", "https:", "mailto:", "tel:", "#"];

  /**
   * 校验 URL 是否安全（阻止 javascript: / data: 等危险协议）
   */
  function isSafeUrl(url) {
    if (!url) return false;
    var u = url.trim().toLowerCase();
    // 阻止控制字符
    if (/[\u0000-\u001f\u007f]/.test(u)) return false;
    // 阻止 javascript: data: vbscript: 等
    if (/^(javascript|data|vbscript|file):/i.test(u)) return false;
    // 允许相对路径与锚点
    if (u.charAt(0) === "/" || u.charAt(0) === "." || u.charAt(0) === "#" || u.charAt(0) === "?") {
      return true;
    }
    // 必须以受信任协议开头
    for (var i = 0; i < SAFE_PROTOCOLS.length; i++) {
      if (u.indexOf(SAFE_PROTOCOLS[i]) === 0) return true;
    }
    return false;
  }

  /**
   * 解析属性字符串，过滤危险属性
   * @param {string} attrString
   * @param {string} tagName
   * @returns {string}
   */
  function filterAttrs(attrString, tagName) {
    if (!attrString) return "";
    var allowed = ALLOWED_ATTRS[tagName] || [];
    var result = [];
    var regex = /([a-zA-Z_:][\w:.-]*)\s*(?:=\s*("([^"]*)"|'([^']*)'|[^\s"'>]+))?/g;
    var m;
    while ((m = regex.exec(attrString)) !== null) {
      var name = m[1].toLowerCase();
      var value = m[3] !== undefined
        ? m[3]
        : m[4] !== undefined
          ? m[4]
          : m[5] !== undefined
            ? m[5]
            : "";

      // 跳过危险属性
      if (name.indexOf("on") === 0) continue;
      if (name === "style") continue;
      if (name === "formaction" || name === "srcdoc" || name === "xlink:href") continue;
      if (allowed.indexOf(name) === -1) continue;

      // 对 URL 属性做协议校验
      if (name === "href" || name === "src") {
        if (!isSafeUrl(value)) continue;
      }

      result.push(name + '="' + escapeAttr(value) + '"');
    }
    return result.join(" ");
  }

  function escapeAttr(v) {
    return String(v)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /**
   * 清洗 HTML：白名单标签 + 过滤危险属性 + 阻止危险协议
   * @param {string} html
   * @returns {string}
   */
  API.sanitize = function (html) {
    if (!html) return "";
    try {
      return sanitizeInner(html);
    } catch (e) {
      // 兜底：DOM 清洗异常时降级为纯文本转义，保证页面不白屏
      console.warn("[sanitize] fallback to plain text:", e && e.message);
      return String(html)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }
  };

  function sanitizeInner(html) {
    // 移除隐藏 HTML 注释（防止条件注释攻击等）
    html = html.replace(/<!--[\s\S]*?-->/g, "");
    // 移除 <?...?> 处理指令
    html = html.replace(/<\?[\s\S]*?\?>/g, "");

    var doc = new DOMParser().parseFromString("<body>" + html + "</body>", "text/html");
    var body = doc.body;

    // 先收集所有元素节点（避免遍历中修改树导致迭代状态错乱）。
    // 注意：body 属于 DOMParser 解析出的独立 document，
    // 不能用主文档的 Range.createNodeIterator 跨文档遍历，
    // 这里改用纯 DOM 递归遍历，兼容性最好。
    var allElements = [];
    (function collect(node) {
      var child = node.firstChild;
      while (child) {
        if (child.nodeType === 1) {
          allElements.push(child);
          collect(child);
        }
        child = child.nextSibling;
      }
    })(body);

    var DANGEROUS_TAGS = ["script", "style", "iframe", "object", "embed", "link", "meta", "base", "form", "input", "button", "select", "textarea"];

    allElements.forEach(function (node) {
      // 节点可能已被父节点替换/移除，跳过
      if (!node.parentNode) return;

      var tag = node.tagName.toLowerCase();
      if (!ALLOWED_TAGS[tag]) {
        if (DANGEROUS_TAGS.indexOf(tag) !== -1) {
          // 危险标签：整体删除（含子节点）
          node.parentNode.removeChild(node);
        } else {
          // 非危险标签：解包，保留文本内容
          // 用 body 所属文档创建片段，避免跨文档 Fragment 兼容性问题
          var frag = body.ownerDocument.createDocumentFragment();
          var child = node.firstChild;
          while (child) {
            frag.appendChild(child);
            child = node.firstChild;
          }
          node.parentNode.replaceChild(frag, node);
        }
        return;
      }

      // 对白名单标签做属性过滤
      var tagLower = tag;
      var allowed = ALLOWED_ATTRS[tagLower] || [];
      // 先读取属性，再逐个移除/重建
      var existing = [];
      for (var i = 0; i < node.attributes.length; i++) {
        var attrName = node.attributes[i].name;
        var attrVal = node.attributes[i].value;
        existing.push({ name: attrName, value: attrVal });
      }
      // 清空属性
      while (node.attributes.length) {
        node.removeAttribute(node.attributes[0].name);
      }
      // 重建安全属性
      existing.forEach(function (attr) {
        var name = attr.name.toLowerCase();
        var val = attr.value;
        if (name.indexOf("on") === 0) return;
        if (name === "style" || name === "formaction" || name === "srcdoc" || name === "xlink:href") return;
        if (allowed.indexOf(name) === -1) return;
        if ((name === "href" || name === "src") && !isSafeUrl(val)) return;
        try {
          node.setAttribute(name, val);
        } catch (e) { /* ignore */ }
      });

      // a 标签统一加 rel="noopener" 防反向 tabnabbing
      if (tag === "a") {
        node.setAttribute("rel", "noopener noreferrer");
        if (!node.getAttribute("target")) node.setAttribute("target", "_blank");
      }
    });

    return body.innerHTML;
  }

  /* ------------------------------------------------------------------------
   * DOM 工具
   * ---------------------------------------------------------------------- */

  /**
   * 创建元素
   * @param {string} tag
   * @param {Object} [props] 例如 {class:'btn', text:'hi', attrs:{href:'#'}}
   * @param {Array|Node|string} [children]
   */
  API.el = function (tag, props, children) {
    var node = document.createElement(tag);
    props = props || {};
    if (props.class) node.className = props.class;
    if (props.id) node.id = props.id;
    if (props.text !== undefined) node.textContent = props.text;
    if (props.attrs) {
      Object.keys(props.attrs).forEach(function (k) {
        node.setAttribute(k, props.attrs[k]);
      });
    }
    if (props.dataset) {
      Object.keys(props.dataset).forEach(function (k) {
        node.dataset[k] = props.dataset[k];
      });
    }
    if (children) {
      var list = Array.isArray(children) ? children : [children];
      list.forEach(function (c) {
        if (c == null) return;
        if (typeof c === "string" || typeof c === "number") {
          node.appendChild(document.createTextNode(String(c)));
        } else {
          node.appendChild(c);
        }
      });
    }
    return node;
  };

  /**
   * 文本摘要：去掉 Markdown 标记后截取前 N 字
   */
  API.excerpt = function (text, max) {
    if (!text) return "";
    max = max || 100;
    var plain = String(text)
      .replace(/```[\s\S]*?```/g, " ")          // 代码块
      .replace(/`([^`]+)`/g, "$1")              // 行内代码
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, " ") // 图片
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // 链接
      .replace(/[#*_~>]/g, "")                  // 标记符号
      .replace(/\s+/g, " ")
      .trim();
    if (plain.length <= max) return plain;
    return plain.slice(0, max) + "…";
  };

  /**
   * 头像字母：取用户名首字母（中文取首字）
   * 如果本地缓存有头像 URL，优先返回图片标记
   */
  API.avatarChar = function (name) {
    if (!name) return "?";
    var s = String(name).trim();
    if (!s) return "?";
    // 中文/日文/韩文取首字符；英文取首字母
    var c = s.charAt(0);
    return /[一-龥぀-ゟ゠-ヿ가-힯]/.test(c) ? c : c.toUpperCase();
  };

  /* ------------------------------------------------------------------------
   * 用户中心接口（V2）
   * ---------------------------------------------------------------------- */

  /**
   * 获取当前用户资料
   * @returns {Promise<{id,username,nickname,bio,avatar_url,role,created_at}>}
   */
  API.getProfile = function () {
    return request("/api/user/profile", {
      method: "GET",
      auth: true,
    });
  };

  /**
   * 更新昵称与简介
   * @param {Object} data  {nickname, bio}
   */
  API.updateProfile = function (data) {
    return request("/api/user/profile", {
      method: "PUT",
      body: data,
      auth: true,
    });
  };

  /**
   * 修改密码
   * @param {string} oldPassword
   * @param {string} newPassword
   */
  API.changePassword = function (oldPassword, newPassword) {
    return request("/api/user/password", {
      method: "POST",
      body: { old_password: oldPassword, new_password: newPassword },
      auth: true,
    });
  };

  /**
   * 上传头像
   * @param {File} file 图片文件对象
   */
  API.uploadAvatar = function (file) {
    var token = API.getToken();
    var formData = new FormData();
    formData.append("file", file);

    var url = buildUrl("/api/user/avatar");
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 30000);

    return fetch(url, {
      method: "POST",
      headers: token ? { "Authorization": "Bearer " + token } : {},
      body: formData,
      credentials: "same-origin",
      signal: controller.signal,
    }).then(function (response) {
      clearTimeout(timeout);
      var ct = response.headers.get("Content-Type") || "";
      var isJson = ct.indexOf("application/json") !== -1;
      return response.text().then(function (text) {
        var data;
        try { data = isJson ? JSON.parse(text) : { _raw: text }; }
        catch (e) { data = { _raw: text }; }

        if (response.status === 401) {
          handleUnauthorized();
          var ue = new Error("登录已过期，请重新登录");
          ue.status = 401;
          ue.data = data;
          throw ue;
        }

        if (!response.ok) {
          var msg = extractError(data, "请求失败（HTTP " + response.status + "）");
          var err = new Error(msg);
          err.status = response.status;
          err.data = data;
          throw err;
        }

        return data;
      });
    }).catch(function (err) {
      if (err && err.name === "AbortError") {
        throw new Error("请求超时，请检查网络连接或后端服务是否启动");
      }
      throw err;
    });
  };

  /* ------------------------------------------------------------------------
   * 账号下拉悬停交互
   * ---------------------------------------------------------------------- */
  API.setupBadgeDropdown = function (wrap) {
    if (!wrap || !wrap.classList) return;
    var MENU_HIDE_DELAY = 250; // 毫秒，鼠标离开后延迟隐藏，容忍小抖动
    var pending = null;
    var menu = wrap.querySelector(".user-badge-menu");
    if (!menu) return;

    function show() {
      if (pending) { clearTimeout(pending); pending = null; }
      menu.classList.add("is-open");
    }
    function hideSoon() {
      if (pending) return;
      pending = setTimeout(function () {
        pending = null;
        menu.classList.remove("is-open");
      }, MENU_HIDE_DELAY);
    }

    wrap.addEventListener("mouseenter", show);
    wrap.addEventListener("mouseleave", hideSoon);
    // 点击下拉项（登出）时，可能在处理完后需要主动关闭；点其他 item 自然跳转
    menu.addEventListener("click", function (e) {
      var target = e.target && e.target.closest ? e.target.closest("a") : null;
      if (target && target.getAttribute("href") === "javascript:void(0)") {
        // 登出按钮：等 handleLogout 执行完再关闭，视觉更自然
        setTimeout(function () { menu.classList.remove("is-open"); }, 150);
      }
    });
  };

  /* ------------------------------------------------------------------------
   * 暴露到全局
   * ---------------------------------------------------------------------- */
  global.ForumAPI = API;
  // 同时挂载到 window 便于旧代码访问
  if (typeof window !== "undefined") {
    window.ForumAPI = API;
  }
})(typeof globalThis !== "undefined" ? globalThis : (typeof window !== "undefined" ? window : this));
