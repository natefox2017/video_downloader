/**
 * panel.js — Minimal floating-panel UI.
 *
 * The panel only shows detected videos, lets the user choose which ones to download,
 * and reports essential download status. All download logic stays in content.js.
 */

(() => {
  "use strict";

  const state = {
    items: [],
    selected: new Set(),
    downloading: false,
    batch: { completed: 0, total: 0 },
  };

  const listEl = document.getElementById("list");
  const emptyEl = document.getElementById("empty");
  const downloadEl = document.getElementById("download");
  const downloadLabel = document.getElementById("download-label");
  const toastEl = document.getElementById("toast");

  /**
   * Escape text inserted into HTML.
   * @param {*} text Input value.
   * @returns {string} Escaped HTML text.
   */
  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  let toastTimer = null;

  /**
   * Show a short non-blocking message.
   * @param {string} message Message text.
   * @returns {void}
   */
  function toast(message) {
    if (!message) return;
    toastEl.textContent = message;
    toastEl.classList.add("is-show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("is-show"), 1800);
  }

  /**
   * Send a protocol message to content.js.
   * @param {Object} message Protocol payload.
   * @returns {void}
   */
  function post(message) {
    window.parent.postMessage({ source: "vd-panel", ...message }, "*");
  }

  /**
   * Test whether an item has already completed.
   * @param {Object} item Media row.
   * @returns {boolean} True when the item is complete.
   */
  function isDone(item) {
    return item.status === "done";
  }

  /**
   * Build the essential state label for one item.
   * @param {Object} item Media row.
   * @returns {string} State label.
   */
  function stateText(item) {
    if (item.status === "downloading") return item.progress > 0 ? item.progress + "%" : "下载中";
    if (item.status === "done") return "已完成";
    if (item.status === "error") return "失败";
    return "";
  }

  /**
   * Return the CSS class for one state label.
   * @param {Object} item Media row.
   * @returns {string} CSS class list.
   */
  function stateClass(item) {
    if (item.status === "done") return "row__state is-done";
    if (item.status === "error") return "row__state is-error";
    if (item.status === "downloading") return "row__state is-downloading";
    return "row__state";
  }

  /**
   * Render the video list.
   * @returns {void}
   */
  function render() {
    const single = state.items.length === 1;
    document.body.classList.toggle("is-single", single);
    emptyEl.classList.toggle("hidden", state.items.length > 0);

    listEl.innerHTML = state.items.map((item) => {
      const selected = state.selected.has(item.shareUrl);
      const status = stateText(item);
      const classes = [
        "row",
        selected ? "is-selected" : "",
        item.status === "downloading" ? "is-downloading" : "",
        isDone(item) ? "is-done" : "",
      ].filter(Boolean).join(" ");

      return `
        <li class="${classes}" data-url="${escapeHtml(item.shareUrl)}">
          <label class="row__check" data-stop="1">
            <input type="checkbox" class="row__check-input" ${selected ? "checked" : ""} ${isDone(item) ? "disabled" : ""} />
            <span class="row__box">
              <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
                <path d="M2 6.5 4.8 9 10 3" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </span>
          </label>
          <div class="row__main">
            <div class="row__line">
              <div class="row__title" title="${escapeHtml(item.title || "视频")}">${escapeHtml(item.title || "视频")}</div>
              ${status ? `<span class="${stateClass(item)}">${escapeHtml(status)}</span>` : ""}
            </div>
            <div class="row__progress">
              <div class="row__progress-inner" style="width:${item.progress || 0}%"></div>
            </div>
          </div>
        </li>
      `;
    }).join("");

    renderAction();
    reportHeight();
  }

  /**
   * Report the preferred iframe height to content.js.
   * @returns {void}
   */
  let resizeTimer = null;
  function reportHeight() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const actionbar = document.querySelector(".actionbar")?.offsetHeight || 0;
      const contentHeight = state.items.length > 0 ? listEl.scrollHeight : 88;
      post({ type: "panel_resize", height: contentHeight + actionbar + 4 });
    }, 40);
  }

  /**
   * Update only mutable download state in one row.
   * @param {Object} item Media row.
   * @returns {void}
   */
  function updateRow(item) {
    const row = listEl.querySelector(`.row[data-url="${CSS.escape(item.shareUrl)}"]`);
    if (!row) return;

    row.classList.toggle("is-downloading", item.status === "downloading");
    row.classList.toggle("is-done", isDone(item));
    row.classList.toggle("is-selected", state.selected.has(item.shareUrl));

    const checkbox = row.querySelector(".row__check-input");
    if (checkbox) {
      checkbox.checked = state.selected.has(item.shareUrl);
      checkbox.disabled = isDone(item);
    }

    const line = row.querySelector(".row__line");
    let status = row.querySelector(".row__state");
    const nextText = stateText(item);
    if (nextText) {
      if (!status && line) {
        status = document.createElement("span");
        line.appendChild(status);
      }
      if (status) {
        status.textContent = nextText;
        status.className = stateClass(item);
      }
    } else if (status) {
      status.remove();
    }

    const progress = row.querySelector(".row__progress-inner");
    if (progress) progress.style.width = (item.progress || 0) + "%";
  }

  /**
   * Refresh the only primary action.
   * @returns {void}
   */
  function renderAction() {
    const selectedCount = state.items.filter((item) => state.selected.has(item.shareUrl) && !isDone(item)).length;

    if (state.downloading) {
      downloadLabel.textContent = state.batch.total > 1
        ? `停止下载 ${state.batch.completed}/${state.batch.total}`
        : "停止下载";
      downloadEl.classList.add("is-stop");
      downloadEl.disabled = false;
      return;
    }

    downloadLabel.textContent = selectedCount > 1 ? `下载 ${selectedCount} 个视频` : "下载视频";
    downloadEl.classList.remove("is-stop");
    downloadEl.disabled = selectedCount === 0;
  }

  /**
   * Build the completion toast.
   * @param {Object} message Batch result payload.
   * @returns {string} Completion text.
   */
  function finishText(message) {
    const succeeded = message.succeeded == null ? message.completed || 0 : message.succeeded;
    const failed = message.failed || 0;
    if (message.stopped) return "已停止";
    if (failed > 0) return `完成 ${succeeded}，失败 ${failed}`;
    return "下载完成";
  }

  /**
   * Toggle one item in the selection.
   * @param {string} shareUrl Item key.
   * @param {boolean=} force Optional explicit target state.
   * @returns {void}
   */
  function toggleOne(shareUrl, force) {
    const item = state.items.find((row) => row.shareUrl === shareUrl);
    if (!item || isDone(item)) return;
    const shouldSelect = typeof force === "boolean" ? force : !state.selected.has(shareUrl);
    if (shouldSelect) state.selected.add(shareUrl);
    else state.selected.delete(shareUrl);
    render();
  }

  listEl.addEventListener("click", (event) => {
    if (event.target.closest("[data-stop]")) return;
    if (state.items.length === 1) return;
    const row = event.target.closest(".row");
    if (!row || !row.dataset.url) return;
    toggleOne(row.dataset.url);
  });

  listEl.addEventListener("change", (event) => {
    if (!event.target.classList.contains("row__check-input")) return;
    const row = event.target.closest(".row");
    if (!row) return;
    event.stopPropagation();
    toggleOne(row.dataset.url, event.target.checked);
  });

  downloadEl.addEventListener("click", () => {
    if (state.downloading) {
      post({ type: "stop_download" });
      toast("正在停止");
      return;
    }

    const shareUrls = state.items
      .filter((item) => state.selected.has(item.shareUrl) && !isDone(item))
      .map((item) => item.shareUrl);

    if (!shareUrls.length) return;
    post({ type: "start_download", shareUrls });
  });

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (!message || message.source !== "vd-content") return;

    switch (message.type) {
      case "media_list":
      case "panel_init": {
        const previous = new Set(state.items.map((item) => item.shareUrl));
        state.items = message.items || [];

        state.items.forEach((item) => {
          if (isDone(item)) state.selected.delete(item.shareUrl);
        });

        state.items.forEach((item) => {
          if (!previous.has(item.shareUrl) && !isDone(item)) state.selected.add(item.shareUrl);
        });

        [...state.selected].forEach((url) => {
          if (!state.items.some((item) => item.shareUrl === url)) state.selected.delete(url);
        });

        render();
        break;
      }

      case "item_status": {
        const item = state.items.find((row) => row.shareUrl === message.shareUrl);
        if (item) {
          item.status = message.status;
          item.progress = message.progress || 0;
          if (isDone(item)) state.selected.delete(item.shareUrl);
          updateRow(item);
          renderAction();
        }
        break;
      }

      case "batch_started":
        state.downloading = true;
        state.batch = { completed: 0, total: message.total || 0 };
        renderAction();
        break;

      case "batch_progress":
        state.batch = {
          completed: message.completed || 0,
          total: message.total || 0,
        };
        renderAction();
        break;

      case "batch_finished":
        state.downloading = false;
        state.batch = { completed: 0, total: 0 };
        render();
        toast(finishText(message));
        break;

      case "item_error":
        toast(message.message ? `下载失败：${message.message}` : "下载失败");
        break;

      case "toast":
        toast(message.message);
        break;

      default:
        break;
    }
  });

  render();
  post({ type: "panel_ready" });
})();
