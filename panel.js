/**
 * panel.js —— 浮层面板的界面逻辑（纯原生 JS，无框架）
 *
 * 面板只负责"展示列表 + 收集选择 + 下发指令"，真正的下载由父页面里的 content.js 执行；
 * 双方通过 postMessage 通信（面板在 iframe 中，与页面跨源）：
 *   content → panel : media_list / panel_init / item_status / batch_* / toast / item_error
 *   panel → content : panel_ready / start_download / stop_download
 */

(() => {
  "use strict";

  /* ---------------- 状态 ---------------- */

  const state = {
    items: [],
    selected: new Set(),
    downloading: false,
    batch: { completed: 0, total: 0, tooltip: "" },
    keyword: "",
    /** 平台筛选：platformId，"" = 全部 */
    platFilter: "",
    /** 排序：time（最新优先）/ size（体积最大优先） */
    sort: "time",
  };

  /* ---------------- DOM ---------------- */

  const listEl = document.getElementById("list");
  const emptyEl = document.getElementById("empty");
  const countEl = document.getElementById("count");
  const selectAllEl = document.getElementById("select-all");
  const invertEl = document.getElementById("invert");
  const sortEl = document.getElementById("sort");
  const filtersEl = document.getElementById("filters");
  const downloadEl = document.getElementById("download");
  const downloadLabel = document.getElementById("download-label");
  const toastEl = document.getElementById("toast");
  const searchEl = document.getElementById("search");
  const searchBox = searchEl.closest(".search");
  const searchClear = document.getElementById("search-clear");

  /* ---------------- 工具 ---------------- */

  function formatSize(bytes) {
    if (!bytes || bytes <= 0) return "未知大小";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    return (bytes / Math.pow(1024, index)).toFixed(index === 0 ? 0 : 2) + " " + units[index];
  }

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  let toastTimer = null;
  function toast(message) {
    if (!message) return;
    toastEl.textContent = message;
    toastEl.classList.add("is-show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("is-show"), 2200);
  }

  function post(message) {
    window.parent.postMessage({ source: "vd-panel", ...message }, "*");
  }

  /** 平台 id → 徽标样式类 */
  const PLAT_CLASS = {
    douyin: "plat--douyin",
    kuaishou: "plat--kuaishou",
    bilibili: "plat--bilibili",
    weibo: "plat--weibo",
    xiaohongshu: "plat--xiaohongshu",
    xigua: "plat--xigua",
    generic: "plat--generic",
  };

  function platClass(item) {
    return "plat " + (PLAT_CLASS[item.platformId] || "plat--generic");
  }

  function isDone(item) {
    return item.status === "done";
  }

  function selectableItems() {
    return state.items.filter((item) => !isDone(item));
  }

  /** 平台品牌色（用于筛选 chips 的圆点） */
  const PLAT_COLOR = {
    douyin: "#161823",
    kuaishou: "#ff4906",
    bilibili: "#00a1d6",
    weibo: "#e6162d",
    xiaohongshu: "#ff2442",
    xigua: "#ff6a00",
    generic: "#6366f1",
  };

  function platColor(item) {
    return PLAT_COLOR[item.platformId] || PLAT_COLOR.generic;
  }

  /** 搜索 + 平台筛选 + 排序后的可见条目 */
  function visibleItems() {
    let items = state.items;

    if (state.platFilter) {
      items = items.filter((item) => item.platformId === state.platFilter);
    }

    const kw = state.keyword.trim().toLowerCase();
    if (kw) {
      items = items.filter((item) =>
        (item.title || "").toLowerCase().includes(kw) ||
        (item.author || "").toLowerCase().includes(kw)
      );
    }

    items = items.slice();
    if (state.sort === "size") {
      items.sort((a, b) => (b.size || 0) - (a.size || 0));
    }
    // sort === "time" 时保持原顺序（最新在前）

    return items;
  }

  /** 渲染平台筛选 chips（只显示有视频的平台） */
  function renderFilters() {
    const counts = {};
    state.items.forEach((item) => {
      const pid = item.platformId || "generic";
      counts[pid] = counts[pid] || { name: item.platform || "通用", n: 0 };
      counts[pid].n += 1;
    });

    const pids = Object.keys(counts);
    if (pids.length <= 1) {
      filtersEl.innerHTML = "";
      if (state.platFilter) state.platFilter = "";
      return;
    }

    // 当前筛选的平台如果没视频了，回到全部
    if (state.platFilter && !counts[state.platFilter]) state.platFilter = "";

    const total = state.items.length;
    let html = `<button class="fchip ${state.platFilter === "" ? "is-active" : ""}" data-plat="">全部 <span class="fchip__n">${total}</span></button>`;
    // 按数量降序排列
    pids.sort((a, b) => counts[b].n - counts[a].n);
    pids.forEach((pid) => {
      const c = counts[pid];
      const color = PLAT_COLOR[pid] || PLAT_COLOR.generic;
      html += `<button class="fchip ${state.platFilter === pid ? "is-active" : ""}" data-plat="${escapeHtml(pid)}">` +
        `<span class="fchip__dot" style="background:${color}"></span>${escapeHtml(c.name)} <span class="fchip__n">${c.n}</span></button>`;
    });
    filtersEl.innerHTML = html;
  }

  /* ---------------- 渲染 ---------------- */

  function stateText(item) {
    if (item.status === "downloading") return item.progress > 0 ? item.progress + "%" : "下载中";
    if (item.status === "done") return "已完成";
    if (item.status === "error") return "失败";
    return "";
  }

  function stateClass(item) {
    if (item.status === "done") return "card__state is-done";
    if (item.status === "error") return "card__state is-error";
    if (item.status === "downloading") return "card__state is-downloading";
    return "card__state";
  }

  function render() {
    renderFilters();
    const items = visibleItems();
    const filtering = state.keyword.trim().length > 0 || state.platFilter !== "";

    emptyEl.classList.toggle("hidden", items.length > 0);
    if (filtering && items.length === 0) {
      emptyEl.querySelector(".empty__title").textContent = "没有匹配的结果";
      emptyEl.querySelector(".empty__desc").textContent = "换个关键词试试";
    } else {
      emptyEl.querySelector(".empty__title").textContent = "还没有识别到视频";
      emptyEl.querySelector(".empty__desc").textContent = "在页面里播放一个视频，这里会自动出现";
    }

    listEl.innerHTML = items.map((item) => {
      const selected = state.selected.has(item.shareUrl);
      const cardClass = [
        "card",
        selected ? "is-selected" : "",
        item.status === "downloading" ? "is-downloading" : "",
        isDone(item) ? "is-done" : "",
      ].filter(Boolean).join(" ");
      const cover = item.cover
        ? `<img class="card__cover" src="${escapeHtml(item.cover)}" alt="" loading="lazy" />`
        : `<span class="card__cover"></span>`;

      return `
        <li class="${cardClass}" data-url="${escapeHtml(item.shareUrl)}">
          <label class="check card__check" data-stop="1">
            <input type="checkbox" class="row__check" ${selected ? "checked" : ""} />
            <span class="check__box"><svg viewBox="0 0 12 12" width="10" height="10"><path d="M2 6.5 4.8 9 10 3" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
          </label>
          ${cover}
          <div class="card__main">
            <div class="card__title" title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</div>
            <div class="card__meta">
              <span class="${platClass(item)}">${escapeHtml(item.platform || "")}</span>
              <span class="tag">${escapeHtml(item.type || "视频")}</span>
              <span class="tag">${escapeHtml(item.source || "")}</span>
              ${item.author ? `<span class="card__author">${escapeHtml(item.author)}</span>` : ""}
            </div>
            <div class="card__foot">
              <span class="card__size">${escapeHtml(formatSize(item.size))}</span>
              <div class="card__bar"><div class="card__bar-inner" style="width:${item.progress || 0}%"></div></div>
              <span class="${stateClass(item)}">${escapeHtml(stateText(item))}</span>
            </div>
          </div>
          <button class="card__go" data-stop="1" title="直接下载这一条">
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M8 2v8m0 0 3.5-3.5M8 10 4.5 6.5M2.5 13.5h11" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
        </li>
      `;
    }).join("");

    renderToolbar();
  }

  /** 只刷新某一行的下载状态 */
  function updateRow(item) {
    const row = listEl.querySelector(`.card[data-url="${CSS.escape(item.shareUrl)}"]`);
    if (!row) return;

    row.classList.toggle("is-downloading", item.status === "downloading");
    row.classList.toggle("is-done", isDone(item));
    row.classList.toggle("is-selected", state.selected.has(item.shareUrl));

    const check = row.querySelector(".row__check");
    if (check) check.checked = state.selected.has(item.shareUrl);

    const stateCell = row.querySelector(".card__state");
    if (stateCell) {
      stateCell.textContent = stateText(item);
      stateCell.className = stateClass(item);
    }

    const bar = row.querySelector(".card__bar-inner");
    if (bar) bar.style.width = (item.progress || 0) + "%";
  }

  /** 刷新工具栏与底部按钮 */
  function renderToolbar() {
    const total = state.items.length;
    const doneCount = state.items.filter(isDone).length;
    const selectedCount = state.items.filter((item) => state.selected.has(item.shareUrl)).length;
    const selectable = selectableItems();
    const selectableSelected = selectable.filter((item) => state.selected.has(item.shareUrl)).length;

    if (state.downloading) {
      countEl.textContent = `下载中 ${state.batch.completed}/${state.batch.total}`;
      countEl.title = state.batch.tooltip || "";
    } else {
      const kw = state.keyword.trim();
      const matched = visibleItems().length;
      const totalSize = state.items.reduce((sum, item) => sum + (item.size || 0), 0);
      const sizeText = totalSize > 0 ? ` · ${formatSize(totalSize)}` : "";
      if (kw || state.platFilter) {
        countEl.textContent = `共 ${total} 条 · 匹配 ${matched} 条${sizeText}`;
      } else {
        countEl.textContent = `共 ${total} 条 · 已选 ${selectedCount}${sizeText}`;
      }
      countEl.title = doneCount ? `共 ${total} 条，其中已完成 ${doneCount} 条` : `共 ${total} 条`;
    }

    selectAllEl.checked = selectable.length > 0 && selectableSelected === selectable.length;
    selectAllEl.indeterminate = selectableSelected > 0 && selectableSelected < selectable.length;

    if (state.downloading) {
      downloadLabel.textContent = `停止下载 (${state.batch.completed}/${state.batch.total})`;
      downloadEl.classList.add("is-stop");
      downloadEl.disabled = false;
    } else {
      downloadLabel.textContent = selectedCount > 0 ? `下载选中 (${selectedCount})` : "下载选中";
      downloadEl.classList.remove("is-stop");
      downloadEl.disabled = selectedCount === 0;
    }
  }

  function startText(message) {
    const threads = message.concurrency || 1;
    const head = `开始下载 ${message.total} 条 · ${threads} 线程并发`;
    const plan = message.memory;
    if (!plan || !plan.usableBytes) return head;
    const sampled = plan.sizeSampled ? "" : "，视频体积未知按 80 MB 估";
    return `${head}（${formatSize(plan.usableBytes)} ÷ 单条 ${formatSize(plan.perVideo)}${sampled}）`;
  }

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

  function finishText(message) {
    const succeeded = message.succeeded == null ? message.completed || 0 : message.succeeded;
    const failed = message.failed || 0;
    if (message.stopped) return `已停止，完成 ${succeeded} 条${failed ? `，失败 ${failed} 条` : ""}`;
    if (failed > 0) return `下载结束，成功 ${succeeded} 条，失败 ${failed} 条`;
    return `全部完成，共 ${succeeded} 条`;
  }

  /* ---------------- 列表交互 ---------------- */

  function toggleOne(shareUrl, force) {
    const shouldSelect = typeof force === "boolean" ? force : !state.selected.has(shareUrl);
    if (shouldSelect) state.selected.add(shareUrl);
    else state.selected.delete(shareUrl);
    render();
  }

  // 点击卡片 = 切换勾选（点快捷按钮 / 复选框本身除外）
  listEl.addEventListener("click", (event) => {
    if (event.target.closest("[data-stop]")) return;
    const card = event.target.closest(".card");
    if (!card || !card.dataset.url) return;
    toggleOne(card.dataset.url);
  });

  listEl.addEventListener("change", (event) => {
    if (!event.target.classList.contains("row__check")) return;
    const card = event.target.closest(".card");
    if (!card) return;
    event.stopPropagation();
    toggleOne(card.dataset.url, event.target.checked);
  });

  // 单行快捷下载
  listEl.addEventListener("click", (event) => {
    const go = event.target.closest(".card__go");
    if (!go) return;
    const card = go.closest(".card");
    if (!card || !card.dataset.url) return;
    const item = state.items.find((row) => row.shareUrl === card.dataset.url);
    if (!item || isDone(item)) return;
    post({ type: "start_download", shareUrls: [item.shareUrl] });
  });

  selectAllEl.addEventListener("change", () => {
    if (selectAllEl.checked) {
      selectableItems().forEach((item) => state.selected.add(item.shareUrl));
    } else {
      state.selected.clear();
    }
    render();
  });

  invertEl.addEventListener("click", () => {
    selectableItems().forEach((item) => {
      if (state.selected.has(item.shareUrl)) state.selected.delete(item.shareUrl);
      else state.selected.add(item.shareUrl);
    });
    render();
  });

  // 平台筛选
  filtersEl.addEventListener("click", (event) => {
    const chip = event.target.closest(".fchip");
    if (!chip) return;
    state.platFilter = chip.dataset.plat || "";
    render();
  });

  // 排序切换：最新优先 ↔ 体积最大优先
  const SORT_LABEL = { time: "🕐 最新", size: "💾 最大" };
  sortEl.addEventListener("click", () => {
    state.sort = state.sort === "time" ? "size" : "time";
    sortEl.textContent = SORT_LABEL[state.sort];
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

  /* ---------------- 搜索 ---------------- */

  let searchDebounce = null;
  searchEl.addEventListener("input", () => {
    searchBox.classList.toggle("has-text", searchEl.value.length > 0);
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(() => {
      state.keyword = searchEl.value;
      render();
    }, 180);
  });

  searchClear.addEventListener("click", () => {
    searchEl.value = "";
    searchBox.classList.remove("has-text");
    state.keyword = "";
    render();
    searchEl.focus();
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
