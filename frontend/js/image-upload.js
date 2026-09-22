/* ==========================================================================
   image-upload.js - 图片上传模块
   - 图片选择（相册 / 拍摄）+ 拖拽添加
   - 多图预览：单图删除、左右移动排序、拖拽排序
   - 分片上传：进度显示、失败重试、断点续传（/api/upload/status 跳过已传分片）
   - 上传不阻塞编辑：后台并发上传，文本框可继续输入
   - 状态反馈：格式错误 / 大小超限 / 成功 / 离线提示（Toast）
   - Lightbox：内容区图片点击查看大图
   依赖：js/api.js（ForumAPI）
   ========================================================================== */

(function (global) {
  "use strict";

  var API = global.ForumAPI;
  if (!API) {
    console.error("[image-upload.js] ForumAPI 未加载，请先引入 js/api.js");
    return;
  }

  var IMG = {};

  // 与后端 config 保持一致
  var MAX_IMAGES = 9;
  var MAX_SIZE = 10 * 1024 * 1024; // 10MB
  var ALLOWED_MIMES = ["image/jpeg", "image/png", "image/webp", "image/gif"];
  var CONCURRENCY = 2; // 并发上传数，避免占满带宽阻塞其他操作

  function isOffline() {
    return typeof navigator !== "undefined" && navigator.onLine === false;
  }

  function formatSize(bytes) {
    if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + "MB";
    if (bytes >= 1024) return Math.round(bytes / 1024) + "KB";
    return bytes + "B";
  }

  /* ------------------------------------------------------------------------
   * Lightbox：内容区图片点击查看大图
   * ---------------------------------------------------------------------- */
  var lightboxEl = null;

  function ensureLightbox() {
    if (lightboxEl && document.body.contains(lightboxEl)) return lightboxEl;
    lightboxEl = API.el("div", {
      class: "lightbox hidden",
      attrs: { role: "dialog", "aria-label": "图片预览" },
    });
    var img = API.el("img", { class: "lightbox-img", attrs: { alt: "大图预览" } });
    var closeBtn = API.el("button", {
      class: "lightbox-close",
      attrs: { type: "button", "aria-label": "关闭预览" },
    }, ["×"]);
    var hint = API.el("div", { class: "lightbox-hint", text: "点击图片或按 Esc 关闭" });
    lightboxEl.appendChild(img);
    lightboxEl.appendChild(closeBtn);
    lightboxEl.appendChild(hint);
    document.body.appendChild(lightboxEl);

    function close() {
      lightboxEl.classList.add("hidden");
      img.src = "";
    }
    lightboxEl.addEventListener("click", close);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !lightboxEl.classList.contains("hidden")) close();
    });
    return lightboxEl;
  }

  /**
   * 开启全局 Lightbox 委托：.md-content 内的 <img> 点击放大。
   * 幂等，重复调用不会重复绑定。
   */
  IMG.setupLightbox = function () {
    if (IMG._lightboxBound) return;
    IMG._lightboxBound = true;
    document.addEventListener("click", function (e) {
      var target = e.target;
      if (!target || target.tagName !== "IMG") return;
      // 仅处理正文内容区图片（排除头像、预览缩略图）
      if (!target.closest || !target.closest(".md-content")) return;
      var box = ensureLightbox();
      var img = box.querySelector(".lightbox-img");
      img.src = target.currentSrc || target.src;
      box.classList.remove("hidden");
    });
  };

  /* ------------------------------------------------------------------------
   * 上传器工厂
   *
   * opts:
   *   container : HTMLElement  渲染上传界面的容器
   *   textarea  : HTMLTextAreaElement  上传完成后插入 Markdown 图片语法的目标
   *   onToast   : function(msg, type)  提示回调
   *   compact   : boolean  回复区紧凑模式
   * 返回:
   *   { reset, waitAll, hasPending, destroy, addFiles }
   * ---------------------------------------------------------------------- */
  IMG.createUploader = function (opts) {
    var container = opts.container;
    var textarea = opts.textarea;
    var toast = typeof opts.onToast === "function"
      ? opts.onToast
      : function () {};

    var items = [];          // 所有图片项
    var uploading = 0;       // 正在上传的数量
    var destroyed = false;

    /* ---------- 界面骨架 ---------- */
    var toolbar = API.el("div", { class: "img-up-toolbar" });
    var albumBtn = API.el("button", {
      class: "btn btn-ghost btn-sm img-up-btn",
      attrs: { type: "button" },
    }, ["🖼", " 添加图片"]);
    var hint = API.el("span", {
      class: "img-up-hint",
      text: "最多 " + MAX_IMAGES + " 张 · 单张 ≤ 10MB · JPG/PNG/WebP/GIF",
    });
    toolbar.appendChild(albumBtn);
    toolbar.appendChild(hint);

    // 图片选择（不带 capture，移动端弹出相册/拍照选择）
    var albumInput = API.el("input", {
      attrs: { type: "file", accept: "image/*", multiple: "multiple" },
      class: "img-up-input",
    });

    var grid = API.el("div", { class: "img-up-grid hidden" });

    container.classList.add("img-up");
    if (opts.compact) container.classList.add("img-up-compact");
    container.appendChild(toolbar);
    container.appendChild(albumInput);
    container.appendChild(grid);

    albumBtn.addEventListener("click", function () { albumInput.click(); });
    albumInput.addEventListener("change", function () {
      addFiles(albumInput.files);
      albumInput.value = "";
    });

    // 拖拽文件到容器添加
    container.addEventListener("dragover", function (e) {
      e.preventDefault();
      container.classList.add("img-up-dragover");
    });
    container.addEventListener("dragleave", function () {
      container.classList.remove("img-up-dragover");
    });
    container.addEventListener("drop", function (e) {
      e.preventDefault();
      container.classList.remove("img-up-dragover");
      if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
    });

    /* ---------- 文件添加与校验 ---------- */
    function addFiles(fileList) {
      if (destroyed) return;
      if (!fileList || !fileList.length) return;

      if (isOffline()) {
        toast("当前无网络连接，图片暂无法上传，请检查网络后重试", "error");
        return;
      }

      Array.prototype.forEach.call(fileList, function (file) {
        if (items.length >= MAX_IMAGES) {
          toast("最多上传 " + MAX_IMAGES + " 张图片", "error");
          return;
        }
        // 格式校验（MIME 优先，扩展名兜底）
        var okType = ALLOWED_MIMES.indexOf(file.type) !== -1;
        if (!okType) {
          var ext = (file.name || "").toLowerCase().split(".").pop();
          okType = ["jpg", "jpeg", "png", "webp", "gif"].indexOf(ext) !== -1;
        }
        if (!okType) {
          toast("「" + file.name + "」格式不支持，请上传 JPG/PNG/WebP/GIF 图片", "error");
          return;
        }
        if (file.size > MAX_SIZE) {
          toast("「" + file.name + "」超过 10MB 限制（当前 " + formatSize(file.size) + "）", "error");
          return;
        }
        if (file.size === 0) {
          toast("「" + file.name + "」是空文件，已跳过", "error");
          return;
        }
        createItem(file);
      });
      pump();
    }

    /* ---------- 预览项 ---------- */
    function createItem(file) {
      var item = {
        file: file,
        status: "queued",   // queued | uploading | done | error
        uploadId: null,
        received: {},       // 已上传分片索引集合（断点续传）
        chunkSize: 512 * 1024,
        url: null,
        inserted: false,
      };

      var el = API.el("div", { class: "img-up-item" });
      var img = API.el("img", {
        class: "img-up-thumb",
        attrs: { alt: file.name || "待上传图片" },
      });
      try {
        img.src = URL.createObjectURL(file);
        item.previewUrl = img.src;
      } catch (e) { /* 忽略预览失败 */ }

      var overlay = API.el("div", { class: "img-up-overlay" });
      var progressBar = API.el("div", { class: "img-up-progress" });
      var progressFill = API.el("div", { class: "img-up-progress-fill" });
      progressBar.appendChild(progressFill);
      var statusText = API.el("div", { class: "img-up-status", text: "等待上传" });
      overlay.appendChild(progressBar);
      overlay.appendChild(statusText);

      var actions = API.el("div", { class: "img-up-actions" });
      var moveLeftBtn = API.el("button", {
        class: "img-up-act", attrs: { type: "button", title: "前移", "aria-label": "前移" },
      }, ["◀"]);
      var moveRightBtn = API.el("button", {
        class: "img-up-act", attrs: { type: "button", title: "后移", "aria-label": "后移" },
      }, ["▶"]);
      var removeBtn = API.el("button", {
        class: "img-up-act img-up-act-danger",
        attrs: { type: "button", title: "删除", "aria-label": "删除图片" },
      }, ["×"]);
      actions.appendChild(moveLeftBtn);
      actions.appendChild(moveRightBtn);
      actions.appendChild(removeBtn);

      var retryBtn = API.el("button", {
        class: "img-up-retry hidden",
        attrs: { type: "button" },
      }, ["上传失败，点击重试"]);

      el.appendChild(img);
      el.appendChild(overlay);
      el.appendChild(actions);
      el.appendChild(retryBtn);
      grid.appendChild(el);
      grid.classList.remove("hidden");

      item.el = el;
      item.progressFill = progressFill;
      item.statusText = statusText;
      item.retryBtn = retryBtn;
      items.push(item);

      removeBtn.addEventListener("click", function () { removeItem(item); });
      moveLeftBtn.addEventListener("click", function () { moveItem(item, -1); });
      moveRightBtn.addEventListener("click", function () { moveItem(item, 1); });
      retryBtn.addEventListener("click", function () {
        item.status = "queued";
        retryBtn.classList.add("hidden");
        setStatus(item, "等待上传", 0);
        pump();
      });

      // 拖拽排序（桌面端）
      el.draggable = true;
      el.addEventListener("dragstart", function (e) {
        el.classList.add("img-up-dragging");
        try { e.dataTransfer.setData("text/plain", ""); } catch (err) { /* ignore */ }
        e.dataTransfer.effectAllowed = "move";
      });
      el.addEventListener("dragend", function () {
        el.classList.remove("img-up-dragging");
      });
      el.addEventListener("dragover", function (e) {
        e.preventDefault();
        var dragging = grid.querySelector(".img-up-dragging");
        if (!dragging || dragging === el) return;
        var rect = el.getBoundingClientRect();
        var before = (e.clientX - rect.left) < rect.width / 2;
        grid.insertBefore(dragging, before ? el : el.nextSibling);
      });
    }

    function removeItem(item) {
      var idx = items.indexOf(item);
      if (idx === -1) return;
      items.splice(idx, 1);
      if (item.previewUrl) {
        try { URL.revokeObjectURL(item.previewUrl); } catch (e) { /* ignore */ }
      }
      if (item.el && item.el.parentNode) item.el.parentNode.removeChild(item.el);
      if (!items.length) grid.classList.add("hidden");
    }

    function moveItem(item, delta) {
      var idx = items.indexOf(item);
      var target = idx + delta;
      if (idx === -1 || target < 0 || target >= items.length) return;
      items.splice(idx, 1);
      items.splice(target, 0, item);
      // 同步 DOM 顺序
      if (delta < 0) {
        grid.insertBefore(item.el, items[target + 1] ? items[target + 1].el : null);
      } else {
        var next = items[target + 1];
        grid.insertBefore(item.el, next ? next.el : null);
      }
    }

    function setStatus(item, text, percent) {
      if (item.statusText) item.statusText.textContent = text;
      if (item.progressFill) {
        item.progressFill.style.width = Math.max(0, Math.min(100, percent)) + "%";
      }
    }

    /* ---------- 上传调度（不阻塞 UI） ---------- */
    function pump() {
      if (destroyed) return;
      while (uploading < CONCURRENCY) {
        var next = null;
        for (var i = 0; i < items.length; i++) {
          if (items[i].status === "queued") { next = items[i]; break; }
        }
        if (!next) break;
        next.status = "uploading";
        uploading++;
        uploadItem(next).then(function () {
          uploading--;
          pump();
        });
      }
    }

    function xhrPost(url, formData, onProgress) {
      return new Promise(function (resolve, reject) {
        var xhr = new XMLHttpRequest();
        xhr.open("POST", url);
        var token = API.getToken();
        if (token) xhr.setRequestHeader("Authorization", "Bearer " + token);
        xhr.upload.addEventListener("progress", function (e) {
          if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
        });
        xhr.addEventListener("load", function () {
          var data = {};
          try { data = JSON.parse(xhr.responseText || "{}"); } catch (e) { /* ignore */ }
          if (xhr.status >= 200 && xhr.status < 300) resolve(data);
          else {
            var err = new Error(data.error || "上传失败（HTTP " + xhr.status + "）");
            err.status = xhr.status;
            reject(err);
          }
        });
        xhr.addEventListener("error", function () {
          var err = new Error(isOffline()
            ? "网络连接已断开，请检查网络后重试"
            : "网络请求失败，请检查网络连接");
          err.network = true;
          reject(err);
        });
        xhr.addEventListener("abort", function () {
          reject(new Error("上传已中断"));
        });
        xhr.send(formData);
      });
    }

    async function uploadItem(item) {
      var file = item.file;
      try {
        if (isOffline()) throw Object.assign(new Error("网络连接已断开，请检查网络后重试"), { network: true });

        // 1) 初始化会话（已有 upload_id 则复用，实现断点续传）
        if (!item.uploadId) {
          setStatus(item, "初始化…", 0);
          var totalChunks = Math.ceil(file.size / item.chunkSize);
          var init = await API.request("/api/upload/init", {
            method: "POST",
            body: {
              filename: file.name || "image.jpg",
              size: file.size,
              total_chunks: totalChunks,
              mime_type: file.type || "",
            },
          });
          item.uploadId = init.upload_id;
          item.chunkSize = init.chunk_size || item.chunkSize;
        }

        // 2) 查询已接收分片（断点续传：跳过已上传部分）
        var st = await API.request("/api/upload/status", {
          method: "GET",
          params: { upload_id: item.uploadId },
        });
        item.received = {};
        (st.received_chunks || []).forEach(function (i) { item.received[i] = true; });

        // 3) 逐片上传
        var chunkCount = st.total_chunks;
        var doneBytes = 0;
        Object.keys(item.received).forEach(function (k) {
          doneBytes += Math.min(item.chunkSize, file.size - k * item.chunkSize);
        });

        for (var i = 0; i < chunkCount; i++) {
          if (destroyed) return;
          if (item.received[i]) continue; // 已上传，跳过
          var start = i * item.chunkSize;
          var blob = file.slice(start, Math.min(start + item.chunkSize, file.size));
          var fd = new FormData();
          fd.append("upload_id", item.uploadId);
          fd.append("index", String(i));
          fd.append("chunk", blob, "chunk-" + i);

          var baseBytes = doneBytes;
          await xhrPost("/api/upload/chunk", fd, function (loaded) {
            var percent = ((baseBytes + loaded) / file.size) * 100;
            setStatus(item, "上传中 " + Math.floor(percent) + "%", percent);
          });
          doneBytes += blob.size;
          item.received[i] = true;
        }

        // 4) 合并完成
        setStatus(item, "处理中…", 100);
        var done = await API.request("/api/upload/complete", {
          method: "POST",
          body: { upload_id: item.uploadId },
        });

        item.status = "done";
        item.url = done.url;
        item.el.classList.add("img-up-done");
        setStatus(item, "✓ 已上传", 100);
        insertMarkdown(item);
        toast("图片上传成功", "success");
      } catch (err) {
        item.status = "error";
        item.el.classList.add("img-up-error");
        item.retryBtn.classList.remove("hidden");
        var msg = (err && err.message) || "上传失败";
        setStatus(item, err && err.network ? "网络中断" : "上传失败", 0);
        toast(err && err.network
          ? "网络异常，图片上传失败，恢复网络后可点击重试"
          : "图片上传失败：" + msg, "error");
      }
    }

    /** 上传成功后把图片以 Markdown 语法插入正文（光标处或末尾） */
    function insertMarkdown(item) {
      if (!textarea || item.inserted || !item.url) return;
      item.inserted = true;
      var snippet = "\n![图片](" + item.url + ")\n";
      var pos = textarea.selectionEnd != null ? textarea.selectionEnd : textarea.value.length;
      textarea.value = textarea.value.slice(0, pos) + snippet + textarea.value.slice(pos);
      try {
        textarea.setSelectionRange(pos + snippet.length, pos + snippet.length);
      } catch (e) { /* ignore */ }
    }

    /* ---------- 对外接口 ---------- */

    /** 是否还有未完成（排队/上传中）的图片 */
    function hasPending() {
      return items.some(function (it) {
        return it.status === "queued" || it.status === "uploading";
      });
    }

    /** 等待所有进行中的上传结束（成功或失败），用于提交前收尾 */
    function waitAll() {
      return new Promise(function (resolve) {
        (function check() {
          if (destroyed || !hasPending()) return resolve();
          setTimeout(check, 200);
        })();
      });
    }

    /** 清空全部图片与界面（关闭弹窗 / 提交成功后调用） */
    function reset() {
      items.slice().forEach(removeItem);
      items = [];
      uploading = 0;
      grid.classList.add("hidden");
    }

    function destroy() {
      destroyed = true;
      reset();
      if (container && container.parentNode) {
        container.parentNode.removeChild(container);
      }
    }

    return {
      addFiles: addFiles,
      reset: reset,
      waitAll: waitAll,
      hasPending: hasPending,
      destroy: destroy,
    };
  };

  global.ForumImageUpload = IMG;
})(typeof globalThis !== "undefined" ? globalThis : (typeof window !== "undefined" ? window : this));
