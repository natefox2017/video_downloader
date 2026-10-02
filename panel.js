/**
 * panel.js —— 浮层面板的界面逻辑（纯原生 JS，无框架）
 *
 * 面板只负责“展示列表 + 收集选择 + 下发指令”，真正的下载由父页面里的 content.js 执行；
 * 双方通过 postMessage 通信（面板在 iframe 中，与页面跨源）：
 *   content → panel : media_list / item_status / batch_* / toast
 *   panel → content : panel_ready / start_download / stop_download
 *
 * 列表条目额外携带 platform（平台名）与 source（页面解析 / DOM嗅探 / 网络嗅探），
 * 渲染为每行的小标签，方便分辨每条视频的来源。
 */

(() => {
  "use strict";

  /* ---------------- 状态 ---------------- */

  const state = {
    /** 媒体列表（顺序即展示顺序，最新在最前） */
    items: [],
    /** 已勾选的 shareUrl 集合（已完成的条目不会被勾选） */
    selected: new Set(),
    /** 是否正在批量下载 */
    downloading: false,
    /** 批量下载的整体进度 + 线程数算式说明（下载中时用于工具栏显示） */
    batch: { completed: 0, total: 0, tooltip: "" },
  };

  /* ---------------- DOM ---------------- */

  const listEl = document.getElementById("list");
  const emptyEl = document.getElementById("empty");
  const countEl = document.getElementById("count");
  const selectAllEl = document.getElementById("select-all");
  const invertEl = document.getElementById("invert");
  const downloadEl = document.getElementById("download");
  const toastEl = document.getElementById("toast");

  /* ---------------- 工具 ---------------- */

  /** 字节数格式化 */
  function formatSize(bytes) {
    if (!bytes || bytes <= 0) return "未知大小";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    return (bytes / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 2) + " " + units[index];
  }

  /** HTML 转义，避免标题里的特殊字符破坏结构 */
  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** 底部轻提示 */
  let toastTimer = null;
  function toast(message) {
    if (!message) return;
    toastEl.textContent = message;
    toastEl.classList.add("is-show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("is-show"), 2200);
  }

  /** 向父页面（content.js）发送指令 */
  function post(message) {
    window.parent.postMessage({ source: "vd-panel", ...message }, "*");
  }

  /* ---------------- 渲染 ---------------- */

  /** 单行的状态文案 */
  function stateText(item) {
    if (item.status === "downloading") return item.progress > 0 ? item.progress + "%" : "下载中";
    if (item.status === "done") return "已完成";
    if (item.status === "error") return "失败";
    return formatSize(item.size);
  }

  /** 该条是否已下载完成（已完成的不再参与全选 / 反选） */
  function isDone(item) {
    return item.status === "done";
  }

  /** 可以参与勾选的条目（排除已完成的） */
  function selectableItems() {
    return state.items.filter((item) => !isDone(item));
  }

  /**
   * 批量下载开始时的提示文案。
   * 线程数是按浏览器内存预算算出来的，这里把算式一并亮出来，
   * 免得「这次 16 线程、那次 5 线程」看着莫名其妙。
   */
  function startText(message) {
    const threads = message.concurrency || 1;
    const head = `开始下载 ${message.total} 条 · ${threads} 线程并发`;

    const plan = message.memory;
    if (!plan || !plan.usableBytes) return head;

    // 例：开始下载 20 条 · 16 线程（2.8 GB ÷ 单条 113 MB）
    const sampled = plan.sizeSampled ? "" : "，视频体积未知按 80 MB 估";
    return `${head}（${formatSize(plan.usableBytes)} ÷ 单条 ${formatSize(plan.perVideo)}${sampled}）`;
  }

  /** 线程数算式的完整说明，挂在计数文字上做 tooltip */
  function startTooltip(message) {
    const plan = message.memory;
    if (!plan) return "";
    return [
      `线程数 ${message.concurrency || 1} = ${formatSize(plan.usableBytes)} ÷ 单条 ${formatSize(plan.perVideo)}`,
      `内存预算来源：${plan.source}，只取 70%`,
      plan.sizeSampled ? "单条体积取自接口返回的真实文件大小" : "接口没给文件大小，按 80 MB 估算",
      "下载过程中浏览器若内存吃紧会自动减少并发",
    ].join("\n");
  }

  /** 批量下载结束时的提示文案 */
  function finishText(message) {
    const succeeded = message.succeeded == null ? message.completed || 0 : message.succeeded;
    const failed = message.failed || 0;
    if (message.stopped) return `已停止，完成 ${succeeded} 条${failed ? `，失败 ${failed} 条` : ""}`;
    if (failed > 0) return `下载结束，成功 ${succeeded} 条，失败 ${failed} 条`;
    return `全部完成，共 ${succeeded} 条`;
  }

  /** 整体刷新列表（数据量小，直接重建） */
  function render() {
    const items = state.items;

    emptyEl.classList.toggle("hidden", items.length > 0);

    listEl.innerHTML = items
      .map((item) => {
        const checked = state.selected.has(item.shareUrl) ? "checked" : "";
        const tagClass = item.type === "图集" ? "tag tag--gallery" : "tag";
        const stateClass =
          item.status === "done" ? "row__state is-done" : item.status === "error" ? "row__state is-error" : "row__state";
        const rowClass = [
          "row",
          item.status === "downloading" ? "is-downloading" : "",
          isDone(item) ? "is-done" : "",
        ]
          .filter(Boolean)
          .join(" ");
        const cover = item.cover
          ? `<img class="row__cover" src="${escapeHtml(item.cover)}" alt="" loading="lazy" />`
          : `<span class="row__cover"></span>`;

        return `
          <li class="${rowClass}" data-url="${escapeHtml(item.shareUrl)}">
            <input type="checkbox" class="row__check" ${checked} />
            ${cover}
            <div class="row__main">
              <div class="row__title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
              <div class="row__meta">
                <span class="${tagClass}">${escapeHtml(item.type || "视频")}</span>
                <span class="tag tag--src">${escapeHtml(item.platform || "")} · ${escapeHtml(item.source || "")}</span>
                <span>${escapeHtml(item.author || "")}</span>
              </div>
              <div class="row__bar"><div class="row__bar-inner" style="width:${item.progress || 0}%"></div></div>
            </div>
            <div class="${stateClass}">${escapeHtml(stateText(item))}</div>
          </li>
        `;
      })
      .join("");

    renderToolbar();
  }

  /** 只刷新某一行的下载状态（避免整表重建导致进度闪烁） */
  function updateRow(item) {
    const row = listEl.querySelector(`.row[data-url="${CSS.escape(item.shareUrl)}"]`);
    if (!row) return;

    row.classList.toggle("is-downloading", item.status === "downloading");
    row.classList.toggle("is-done", isDone(item));

    // 勾选框始终以 state.selected 为准：下载完成后已被移出选择集，
    // 因此这里会自动把对勾去掉，下次批量下载不会重复下载。
    const check = row.querySelector(".row__check");
    if (check) check.checked = state.selected.has(item.shareUrl);

    const stateCell = row.querySelector(".row__state");
    if (stateCell) {
      stateCell.textContent = stateText(item);
      stateCell.classList.toggle("is-done", item.status === "done");
      stateCell.classList.toggle("is-error", item.status === "error");
    }

    const bar = row.querySelector(".row__bar-inner");
    if (bar) bar.style.width = (item.progress || 0) + "%";
  }

  /** 刷新工具栏（数量、全选三态、按钮文案） */
  function renderToolbar() {
    const total = state.items.length;
    const doneCount = state.items.filter(isDone).length;
    const selectedCount = state.items.filter((item) => state.selected.has(item.shareUrl)).length;
    const selectable = selectableItems();
    const selectableSelected = selectable.filter((item) => state.selected.has(item.shareUrl)).length;

    if (state.downloading) {
      // 下载中优先显示进度；列表刷新时不覆盖
      countEl.textContent = `下载中 ${state.batch.completed}/${state.batch.total}`;
      countEl.title = state.batch.tooltip || "";
    } else {
      countEl.textContent = `共 ${total} 条 · 已选 ${selectedCount}`;
      countEl.title = doneCount ? `共 ${total} 条，其中已完成 ${doneCount} 条（已完成的不参与全选/反选）` : `共 ${total} 条`;
    }

    // 全选三态只统计「未完成」的条目，已完成的不计入
    selectAllEl.checked = selectable.length > 0 && selectableSelected === selectable.length;
    selectAllEl.indeterminate = selectableSelected > 0 && selectableSelected < selectable.length;

    downloadEl.textContent = state.downloading ? "停止下载" : "批量下载";
    downloadEl.classList.toggle("btn--danger", state.downloading);
    downloadEl.classList.toggle("btn--primary", !state.downloading);
    downloadEl.disabled = !state.downloading && selectedCount === 0;
  }

  /* ---------------- 列表交互 ---------------- */

  // 点击整行 = 切换勾选
  listEl.addEventListener("click", (event) => {
    const row = event.target.closest(".row");
    if (!row) return;
    const shareUrl = row.dataset.url;
    if (!shareUrl) return;

    if (event.target.classList.contains("row__check")) {
      // 复选框本身由 change 事件处理
      return;
    }
    toggleOne(shareUrl);
  });

  listEl.addEventListener("change", (event) => {
    if (!event.target.classList.contains("row__check")) return;
    const row = event.target.closest(".row");
    if (!row) return;
    toggleOne(row.dataset.url, event.target.checked);
  });

  /** 切换单条勾选状态 */
  function toggleOne(shareUrl, force) {
    const shouldSelect = typeof force === "boolean" ? force : !state.selected.has(shareUrl);
    if (shouldSelect) state.selected.add(shareUrl);
    else state.selected.delete(shareUrl);
    render();
  }

  // 全选：默认跳过已完成的视频，只勾选还没下载过的
  selectAllEl.addEventListener("change", () => {
    if (selectAllEl.checked) {
      selectableItems().forEach((item) => state.selected.add(item.shareUrl));
    } else {
      state.selected.clear();
    }
    render();
  });

  // 反选：同样跳过已完成的，避免把下载过的又勾回来
  invertEl.addEventListener("click", () => {
    selectableItems().forEach((item) => {
      if (state.selected.has(item.shareUrl)) state.selected.delete(item.shareUrl);
      else state.selected.add(item.shareUrl);
    });
    render();
  });

  downloadEl.addEventListener("click", () => {
    if (state.downloading) {
      post({ type: "stop_download" });
      toast("正在停止，已发起的下载完成后结束");
      return;
    }
    // 已完成的即使被手动勾上也过滤掉，避免重复下载
    const shareUrls = state.items
      .filter((item) => state.selected.has(item.shareUrl) && !isDone(item))
      .map((item) => item.shareUrl);
    if (!shareUrls.length) {
      toast("请先勾选需要下载的视频");
      return;
    }
    post({ type: "start_download", shareUrls });
  });

  /* ---------------- 与 content.js 通信 ---------------- */

  window.addEventListener("message", (event) => {
    const message = event.data;
    if (!message || message.source !== "vd-content") return;

    switch (message.type) {
      case "media_list":
      case "panel_init": {
        const previous = new Set(state.items.map((item) => item.shareUrl));
        state.items = message.items || [];

        // 已完成的条目一律移出选择集（自动去勾）
        state.items.forEach((item) => {
          if (isDone(item)) state.selected.delete(item.shareUrl);
        });
        // 新出现的条目默认勾选，但已完成的除外
        state.items.forEach((item) => {
          if (!previous.has(item.shareUrl) && !isDone(item)) state.selected.add(item.shareUrl);
        });
        // 已消失的条目从选择集中移除
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
          // 刚下载完成 → 立刻取消勾选，防止下次批量下载重复下载
          if (isDone(item)) state.selected.delete(item.shareUrl);
          updateRow(item);
          renderToolbar();
        }
        break;
      }

      case "batch_started":
        state.downloading = true;
        state.batch = { completed: 0, total: message.total || 0, tooltip: startTooltip(message) };
        renderToolbar();
        toast(startText(message));
        break;

      case "batch_progress":
        state.batch = Object.assign({}, state.batch, {
          completed: message.completed || 0,
          total: message.total || 0,
        });
        renderToolbar();
        break;

      case "batch_finished":
        state.downloading = false;
        state.batch = { completed: 0, total: 0, tooltip: "" };
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

  /* ---------------- 启动 ---------------- */

  render();
  post({ type: "panel_ready" });
})();
