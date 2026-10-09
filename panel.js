/**
 * panel.js — Compact floating-panel UI.
 *
 * The panel only displays detected media, collects the current selection, and sends commands.
 * Downloading and media handling stay in content.js.
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
  const countEl = document.getElementById("count");
  const selectAllEl = document.getElementById("select-all");
  const downloadEl = document.getElementById("download");
  const downloadLabel = document.getElementById("download-label");
  const toastEl = document.getElementById("toast");

  /**
   * Format a byte count for compact display.
   * @param {number} bytes Raw byte count.
   * @returns {string} Human-readable size, or an empty string when unknown.
   */
  function formatSize(bytes) {
    if (!bytes || bytes <= 0) return "";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    const value = bytes / Math.pow(1024, index);
    return value.toFixed(index === 0 ? 0 : value >= 10 ? 1 : 2) + " " + units[index];
  }

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
    toastTimer = setTimeout(() => toastEl.classList.remove("is-show"), 2000);
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
   * Return items that may still be selected for download.
   * @returns {Object[]} Selectable media rows.
   */
  function selectableItems() {
    return state.items.filter((item) => !isDone(item));
  }

  /**
   * Build the compact state label for one item.
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
   * Return the CSS class for one item state label.
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
   * Render the current media list.
   * @returns {void}
   */
  function render() {
    emptyEl.classList.toggle("hidden", state.items.length > 0);

    listEl.innerHTML = state.items.map((item) => {
      const selected = state.selected.has(item.shareUrl);
      const status = stateText(item);
      const info = [
        item.platform || "网页视频",
        formatSize(item.size),
      ].filter(Boolean).join(" · ");
      const classes = [
        "row",
        selected ? "is-selected" : "",
        item.status === "downloading" ? "is-downloading" : "",
        isDone(item) ? "is-done" : "",
      ].filter(Boolean).join(" ");

      return `
        <li class="${classes}" data-url="${escapeHtml(item.shareUrl)}">
          <label class="check row__check${isDone(item) ? " is-disabled" : ""}" data-stop="1">
            <input type="checkbox" class="row__check-input" ${selected ? "checked" : ""} ${isDone(item) ? "disabled" : ""} />
            <span class="check__box">
              <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
                <path d="M2 6.5 4.8 9 10 3" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </span>
          </label>
          <div class="row__main">
            <div class="row__title" title="${escapeHtml(item.title || "未命名视频")}">${escapeHtml(item.title || "未命名视频")}</div>
            <div class="row__meta">
              <span class="row__info">${escapeHtml(info)}</span>
              ${status ? `<span class="${stateClass(item)}">${escapeHtml(status)}</span>` : ""}
            </div>
            <div class="row__progress">
              <div class="row__progress-inner" style="width:${item.progress || 0}%"></div>
            </div>
          </div>
        </li>
      `;
    }).join("");

    renderToolbar();
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
      const topbar = document.querySelector(".topbar")?.offsetHeight || 0;
      const actionbar = document.querySelector(".actionbar")?.offsetHeight || 0;
      const contentHeight = state.items.length > 0 ? listEl.scrollHeight : 120;
      post({ type: "panel_resize", height: topbar + contentHeight + actionbar + 8 });
    }, 40);
  }

  /**
   * Update only the mutable download state of one rendered row.
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

    const status = row.querySelector(".row__state");
    const nextText = stateText(item);
    if (status) {
      status.textContent = nextText;
      status.className = stateClass(item);
    } else if (nextText) {
      const meta = row.querySelector(".row__meta");
      if (meta) {
        const node = document.createElement("span");
        node.className = stateClass(item);
        node.textContent = nextText;
        meta.appendChild(node);
      }
    }

    const progress = row.querySelector(".row__progress-inner");
    if (progress) progress.style.width = (item.progress || 0) + "%";
  }

  /**
   * Refresh selection controls and the primary action.
   * @returns {void}
   */
  function renderToolbar() {
    const selectedCount = state.items.filter((item) => state.selected.has(item.shareUrl)).length;
    const selectable = selectableItems();
    const selectableSelected = selectable.filter((item) => state.selected.has(item.shareUrl)).length;
    const selectAllLabel = selectAllEl.closest(".check");

    if (state.downloading) {
      countEl.textContent = `下载中 ${state.batch.completed}/${state.batch.total}`;
    } else {
      countEl.textContent = selectedCount > 0 ? `已选 ${selectedCount}` : "";
    }

    selectAllEl.checked = selectable.length > 0 && selectableSelected === selectable.length;
    selectAllEl.indeterminate = selectableSelected > 0 && selectableSelected < selectable.length;
    selectAllEl.disabled = selectable.length === 0;
    if (selectAllLabel) selectAllLabel.classList.toggle("is-disabled", selectable.length === 0);

    if (state.downloading) {
      downloadLabel.textContent = `停止下载 (${state.batch.completed}/${state.batch.total})`;
      downloadEl.classList.add("is-stop");
      downloadEl.disabled = false;
    } else {
      downloadLabel.textContent = selectedCount > 0 ? `下载 ${selectedCount} 项` : "下载选中";
      downloadEl.classList.remove("is-stop");
      downloadEl.disabled = selectedCount === 0;
    }
  }

  /**
   * Build the completion toast.
   * @param {Object} message Batch result payload.
   * @returns {string} Completion text.
   */
  function finishText(message) {
    const succeeded = message.succeeded == null ? message.completed || 0 : message.succeeded;
    const failed = message.failed || 0;
    if (message.stopped) return `已停止，完成 ${succeeded} 条${failed ? `，失败 ${failed} 条` : ""}`;
    if (failed > 0) return `下载结束，成功 ${succeeded} 条，失败 ${failed} 条`;
    return `全部完成，共 ${succeeded} 条`;
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

  selectAllEl.addEventListener("change", () => {
    if (selectAllEl.checked) {
      selectableItems().forEach((item) => state.selected.add(item.shareUrl));
    } else {
      state.selected.clear();
    }
    render();
  });

  downloadEl.addEventListener("click", () => {
    if (state.downloading) {
      post({ type: "stop_download" });
      toast("正在停止，已发起的下载完成后结束");
      return;
    }

    const shareUrls = state.items
      .filter((item) => state.selected.has(item.shareUrl) && !isDone(item))
      .map((item) => item.shareUrl);

    if (!shareUrls.length) {
      toast("请先勾选需要下载的视频");
      return;
    }

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
          renderToolbar();
        }
        break;
      }

      case "batch_started":
        state.downloading = true;
        state.batch = { completed: 0, total: message.total || 0 };
        renderToolbar();
        toast(`开始下载 ${message.total || 0} 条`);
        break;

      case "batch_progress":
        state.batch = {
          completed: message.completed || 0,
          total: message.total || 0,
        };
        renderToolbar();
        break;

      case "batch_finished":
        state.downloading = false;
        state.batch = { completed: 0, total: 0 };
        render();
        toast(finishText(message));
        break;

      case "item_error":
        toast(`下载失败：${message.message || "未知错误"}`);
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
