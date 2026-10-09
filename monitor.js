/**
 * monitor.js — Accessible, component-based cross-tab media list and preview dialog.
 * Selection keys contain both tab ID and media ID; downloads remain inside content.js.
 */

(() => {
  "use strict";

  const REGISTRY_KEY = "vd_media_registry";
  const SETTINGS_KEY = "vd_settings";
  const registryEl = document.getElementById("registry");
  const emptyEl = document.getElementById("registry-empty");
  const emptyHelpEl = document.getElementById("empty-help");
  const summaryEl = document.getElementById("registry-summary");
  const selectAllEl = document.getElementById("select-all");
  const downloadButton = document.getElementById("download-selected");
  const downloadLabelEl = document.getElementById("download-button-label");
  const selectionSummaryEl = document.getElementById("selection-summary");
  const downloadStatusEl = document.getElementById("download-status");
  const searchEl = document.getElementById("search-videos");
  const concurrencyEl = document.getElementById("concurrency");
  const previewEl = document.getElementById("preview-modal");
  const previewTitleEl = document.getElementById("preview-title");
  const previewVideoEl = document.getElementById("preview-video");
  const previewMessageEl = document.getElementById("preview-message");
  const previewCloseEl = document.getElementById("preview-close");

  const excluded = new Set();
  const collapsedTabs = new Set();
  let registry = {};
  let renderRequest = 0;
  let submitting = false;
  let lastFocused = null;

  /** Preserve identities even when two tabs expose the same media URL. */
  function videoKey(tabId, shareUrl) {
    return JSON.stringify([String(tabId), String(shareUrl)]);
  }

  /** Escape metadata originating in untrusted websites before rendering markup. */
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /** Restrict media and cover attributes to HTTP(S) URLs. */
  function safeMediaUrl(value) {
    try {
      const url = new URL(String(value || ""));
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
    } catch (error) {
      return "";
    }
  }

  /** Format only real sizes; unknown lengths must not be guessed. */
  function formatSize(bytes) {
    const size = Number(bytes);
    if (!Number.isFinite(size) || size <= 0) return "大小未知";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(units.length - 1, Math.floor(Math.log(size) / Math.log(1024)));
    const value = size / 1024 ** index;
    return value.toFixed(index === 0 || value >= 100 ? 0 : 1) + " " + units[index];
  }

  /** Display only the hostname so signed URLs are not exposed in the UI. */
  function hostLabel(url) {
    try { return new URL(url).hostname; } catch (error) { return "网页"; }
  }

  /** A completed or running entry must never be dispatched twice. */
  function isEligible(item) {
    return item.status !== "done" && item.status !== "downloading";
  }

  /** Return valid tab groups using snapshot data rather than DOM state. */
  function allGroups() {
    return Object.values(registry)
      .filter((entry) => Number.isInteger(Number(entry?.tabId)) && Array.isArray(entry.items))
      .map((entry) => ({
        ...entry,
        tabId: Number(entry.tabId),
        items: entry.items.filter((item) => item && typeof item.shareUrl === "string" && item.shareUrl),
      }))
      .filter((entry) => entry.items.length > 0)
      .sort((a, b) => a.tabId - b.tabId);
  }

  /** Search narrows visible rows without dropping selections hidden by the filter. */
  function visibleGroups() {
    const query = searchEl.value.trim().toLocaleLowerCase();
    return allGroups().map((entry) => {
      const matchesSite = [entry.pageTitle, entry.platform, hostLabel(entry.pageUrl)]
        .some((value) => String(value || "").toLocaleLowerCase().includes(query));
      return {
        ...entry,
        items: query && !matchesSite
          ? entry.items.filter((item) => String(item.title || "").toLocaleLowerCase().includes(query))
          : entry.items,
      };
    }).filter((entry) => entry.items.length > 0);
  }

  /** Return selections from the registry, including collapsed and filtered rows. */
  function selectedItems() {
    return allGroups().flatMap((entry) => entry.items
      .filter((item) => isEligible(item) && !excluded.has(videoKey(entry.tabId, item.shareUrl)))
      .map((item) => ({ tabId: entry.tabId, shareUrl: item.shareUrl })));
  }

  /** UI icon is shared by thumbnail fallbacks and website headers. */
  function videoIcon() {
    return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></svg>';
  }

  /** Component: status badge with optional download percentage. */
  function renderStatus(item) {
    if (item.status === "done") return '<span class="ui-badge ui-badge--done">已完成</span>';
    if (item.status === "error") return '<span class="ui-badge ui-badge--error">下载失败</span>';
    if (item.status === "downloading") {
      const progress = Math.max(0, Math.min(100, Number(item.progress) || 0));
      return '<span class="ui-badge ui-badge--progress">下载中 ' + Math.floor(progress) + '%</span>';
    }
    return '<span class="ui-badge ui-badge--neutral">待下载</span>';
  }

  /** Component: selectable video row with a separately actionable thumbnail. */
  function renderVideo(entry, item, index) {
    const tabId = entry.tabId;
    const key = videoKey(tabId, item.shareUrl);
    const checkId = "video-" + tabId + "-" + index;
    const checked = isEligible(item) && !excluded.has(key);
    const cover = safeMediaUrl(item.cover);
    const title = escapeHtml(item.title || "未命名视频");
    const attrs = 'data-tab-id="' + tabId + '" data-share-url="' + escapeHtml(item.shareUrl) + '"';
    const size = escapeHtml(formatSize(item.size));
    const quality = item.quality ? '<span>' + escapeHtml(item.quality) + '</span>' : '';
    const progress = Math.max(0, Math.min(100, Number(item.progress) || 0));
    const progressMarkup = item.status === "downloading"
      ? '<div class="ui-progress video-row__progress" role="progressbar" aria-valuenow="' + Math.floor(progress) + '" aria-valuemin="0" aria-valuemax="100" aria-label="下载进度"><span class="ui-progress__bar" style="width:' + progress + '%"></span></div>'
      : '';
    return `<div class="video-row ${item.status === "done" ? "is-done" : ""}">
      <input type="checkbox" class="ui-checkbox video-row__select" id="${checkId}" data-video ${attrs}
        ${checked ? "checked" : ""} ${isEligible(item) ? "" : "disabled"} aria-label="选择：${title}" />
      <button type="button" class="video-thumb" data-preview ${attrs} title="预览视频" aria-label="预览：${title}">
        ${cover ? `<img src="${escapeHtml(cover)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : ""}
        <span class="video-thumb__placeholder">${videoIcon()}</span>
        <span class="video-thumb__play"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7z"/></svg></span>
      </button>
      <div class="video-row__main">
        <label class="video-title" for="${checkId}" title="${title}">${title}</label>
        <div class="video-row__meta"><span>${size}</span>${quality}</div>
        ${progressMarkup}
      </div>
      <div class="video-row__status">${renderStatus(item)}</div>
    </div>`;
  }

  /** Component: independent website group with its own selection and collapse. */
  function renderGroup(entry) {
    const tabId = entry.tabId;
    const title = escapeHtml(entry.pageTitle || entry.platform || hostLabel(entry.pageUrl));
    const host = escapeHtml(hostLabel(entry.pageUrl));
    const collapsed = collapsedTabs.has(String(tabId));
    return `<section class="site-card ${collapsed ? "is-collapsed" : ""}" data-tab-id="${tabId}">
      <header class="site-card__header">
        <input type="checkbox" class="ui-checkbox site-card__check" data-group="${tabId}" aria-label="选择该网页中的可下载视频" />
        <button class="site-card__title" type="button" data-collapse="${tabId}"
          aria-expanded="${!collapsed}" aria-controls="site-rows-${tabId}">
          <span class="site-card__favicon">${videoIcon()}</span>
          <span class="site-card__name"><strong title="${title}">${title}</strong><small>${host}</small></span>
          <span class="ui-badge site-card__count">${entry.items.length} 个视频</span>
          <svg class="ui-icon site-card__chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        </button>
      </header>
      <div class="site-card__rows" id="site-rows-${tabId}" ${collapsed ? "hidden" : ""}>
        ${entry.items.map((item, index) => renderVideo(entry, item, index)).join("")}
      </div>
    </section>`;
  }

  /**
   * Synchronize the global and per-site tri-state checkboxes with model selection.
   * The global checkbox applies only to the current search, unlike Download.
   */
  function updateSelection() {
    const visible = visibleGroups();
    const visibleEligible = visible.flatMap((group) => group.items.filter(isEligible).map((item) => ({
      tabId: group.tabId, shareUrl: item.shareUrl,
    })));
    const visibleSelected = visibleEligible.filter(({ tabId, shareUrl }) => !excluded.has(videoKey(tabId, shareUrl)));
    selectAllEl.disabled = visibleEligible.length === 0;
    selectAllEl.checked = visibleEligible.length > 0 && visibleSelected.length === visibleEligible.length;
    selectAllEl.indeterminate = visibleSelected.length > 0 && visibleSelected.length < visibleEligible.length;

    for (const checkbox of registryEl.querySelectorAll("input[data-group]")) {
      const group = visible.find((entry) => entry.tabId === Number(checkbox.dataset.group));
      const selectable = group ? group.items.filter(isEligible) : [];
      const checked = selectable.filter((item) => !excluded.has(videoKey(group.tabId, item.shareUrl))).length;
      checkbox.disabled = selectable.length === 0;
      checkbox.checked = selectable.length > 0 && selectable.length === checked;
      checkbox.indeterminate = checked > 0 && checked < selectable.length;
    }

    const count = selectedItems().length;
    selectionSummaryEl.textContent = count ? "已选择 " + count + " 个视频" : "尚未选择视频";
    downloadLabelEl.textContent = count ? "下载选中的 " + count + " 个视频" : "下载选中的视频";
    downloadButton.disabled = count === 0 || submitting;
  }

  /** Render the latest stored snapshot, retaining state across live updates. */
  function renderView() {
    const all = allGroups();
    const groups = visibleGroups();
    const total = all.reduce((sum, group) => sum + group.items.length, 0);
    const available = all.reduce((sum, group) => sum + group.items.filter(isEligible).length, 0);
    const visibleCount = groups.reduce((sum, group) => sum + group.items.length, 0);

    document.getElementById("stat-sites").textContent = String(all.length);
    document.getElementById("stat-total").textContent = String(total);
    document.getElementById("stat-available").textContent = String(available);
    summaryEl.textContent = all.length + " 个网页 · " + total + " 个视频" +
      (searchEl.value.trim() ? " · 筛选到 " + visibleCount + " 个" : "");

    registryEl.innerHTML = groups.map(renderGroup).join("");
    emptyEl.hidden = visibleCount > 0;
    emptyHelpEl.textContent = total === 0
      ? "请先打开视频网页并播放视频，再回来刷新。"
      : "没有匹配的视频，请调整搜索关键词。";
    updateSelection();
  }

  /** Load registry from service worker, discarding out-of-order responses. */
  async function refreshRegistry() {
    const request = ++renderRequest;
    try {
      const response = await chrome.runtime.sendMessage({ type: "get_media_registry" });
      if (request !== renderRequest) return;
      if (!response?.ok) throw new Error("Registry not available");
      registry = response.registry || {};
      const currentKeys = new Set(allGroups().flatMap((entry) =>
        entry.items.map((item) => videoKey(entry.tabId, item.shareUrl))));
      for (const key of excluded) if (!currentKeys.has(key)) excluded.delete(key);
      renderView();
    } catch (error) {
      if (request !== renderRequest) return;
      summaryEl.textContent = "读取失败";
      downloadStatusEl.textContent = "读取检测列表失败，请点击刷新重试。";
    }
  }

  /** Apply bulk selection to visible eligible entries only. */
  function setVisibleSelection(groups, selected) {
    for (const entry of groups) {
      for (const item of entry.items) {
        if (!isEligible(item)) continue;
        const key = videoKey(entry.tabId, item.shareUrl);
        if (selected) excluded.delete(key);
        else excluded.add(key);
      }
    }
    renderView();
  }

  /** Find a media item using both parts of its stable identity. */
  function findItem(tabId, shareUrl) {
    return allGroups().find((entry) => entry.tabId === Number(tabId))
      ?.items.find((item) => item.shareUrl === shareUrl);
  }

  /** Ensure modal video and media references are released when closed. */
  function closePreview() {
    previewVideoEl.pause();
    previewVideoEl.removeAttribute("src");
    previewVideoEl.removeAttribute("poster");
    previewVideoEl.load();
    previewEl.hidden = true;
    document.body.classList.remove("is-preview-open");
    if (lastFocused?.isConnected) lastFocused.focus();
    lastFocused = null;
  }

  /** Display a meaningful fallback for missing or unsupported playback sources. */
  function showPreviewMessage(message) {
    previewVideoEl.pause();
    previewVideoEl.hidden = true;
    previewMessageEl.textContent = message;
    previewMessageEl.hidden = false;
  }

  /** Open a video preview without allowing webpage HTML or script URLs. */
  function openPreview(tabId, shareUrl) {
    const item = findItem(tabId, shareUrl);
    if (!item) return;
    lastFocused = document.activeElement;
    previewTitleEl.textContent = item.title || "未命名视频";
    previewMessageEl.hidden = true;
    previewVideoEl.hidden = false;
    previewVideoEl.removeAttribute("src");
    const poster = safeMediaUrl(item.cover);
    if (poster) previewVideoEl.poster = poster;
    else previewVideoEl.removeAttribute("poster");
    previewEl.hidden = false;
    document.body.classList.add("is-preview-open");
    previewCloseEl.focus();

    const url = safeMediaUrl(item.previewUrl);
    if (!url) {
      showPreviewMessage("当前视频没有可用于浏览器预览的播放地址。");
      return;
    }
    if (/\.m3u8(?:[?#]|$)/i.test(url) && !previewVideoEl.canPlayType("application/vnd.apple.mpegurl")) {
      showPreviewMessage("此浏览器无法直接预览 HLS (m3u8) 流，请下载后播放。");
      return;
    }
    previewVideoEl.src = url;
    previewVideoEl.load();
    previewVideoEl.play().catch(() => {});
  }

  registryEl.addEventListener("change", (event) => {
    const input = event.target;
    if (input.matches("input[data-video]")) {
      const key = videoKey(input.dataset.tabId, input.dataset.shareUrl);
      if (input.checked) excluded.delete(key);
      else excluded.add(key);
      updateSelection();
    } else if (input.matches("input[data-group]")) {
      const group = visibleGroups().find((entry) => entry.tabId === Number(input.dataset.group));
      if (group) setVisibleSelection([group], input.checked);
    }
  });

  registryEl.addEventListener("click", (event) => {
    const previewButton = event.target.closest("button[data-preview]");
    if (previewButton) {
      openPreview(previewButton.dataset.tabId, previewButton.dataset.shareUrl);
      return;
    }
    const collapse = event.target.closest("button[data-collapse]");
    if (!collapse) return;
    const tabId = String(collapse.dataset.collapse);
    if (collapsedTabs.has(tabId)) collapsedTabs.delete(tabId);
    else collapsedTabs.add(tabId);
    renderView();
    const nextButton = registryEl.querySelector('button[data-collapse="' + tabId + '"]');
    nextButton?.focus();
  });

  // A failed cover should restore the placeholder instead of a broken-image glyph.
  registryEl.addEventListener("error", (event) => {
    if (event.target.matches(".video-thumb > img")) event.target.remove();
  }, true);

  selectAllEl.addEventListener("change", () => setVisibleSelection(visibleGroups(), selectAllEl.checked));
  searchEl.addEventListener("input", renderView);
  document.getElementById("refresh-registry").addEventListener("click", refreshRegistry);

  concurrencyEl.addEventListener("change", async () => {
    try {
      const raw = await chrome.storage.local.get(SETTINGS_KEY);
      const settings = raw[SETTINGS_KEY] || {};
      const count = Number(concurrencyEl.value) || 0;
      await chrome.storage.local.set({ [SETTINGS_KEY]: { ...settings, batchConcurrency: count } });
    } catch (error) {
      downloadStatusEl.textContent = "并发设置保存失败，请重试。";
    }
  });

  downloadButton.addEventListener("click", async () => {
    const items = selectedItems();
    if (!items.length || submitting) return;
    submitting = true;
    updateSelection();
    downloadStatusEl.textContent = "正在向各视频所在的网页提交下载任务…";
    try {
      const response = await chrome.runtime.sendMessage({
        type: "start_multi_tab_download",
        items,
        concurrency: Number(concurrencyEl.value) || 0,
      });
      if (response?.ok && response.tabsStarted > 0) {
        const expectedTabs = new Set(items.map((item) => item.tabId)).size;
        const failedTabs = expectedTabs - response.tabsStarted;
        downloadStatusEl.textContent = failedTabs
          ? "已提交 " + response.videosRequested + " 个任务，" + failedTabs + " 个网页未接收成功。"
          : "已向 " + response.tabsStarted + " 个网页提交 " + response.videosRequested + " 个下载任务；进度将在列表中更新。";
      } else {
        downloadStatusEl.textContent = "下载任务没有成功提交，请检查原网页是否仍然打开。";
      }
    } catch (error) {
      downloadStatusEl.textContent = "任务提交失败，请刷新后重试。";
    } finally {
      submitting = false;
      updateSelection();
      refreshRegistry();
    }
  });

  document.querySelector("[data-close-preview]").addEventListener("click", closePreview);
  previewCloseEl.addEventListener("click", closePreview);
  previewVideoEl.addEventListener("error", () => {
    if (!previewEl.hidden) showPreviewMessage("视频源无法播放，可能已失效、受网站限制或格式不受支持。");
  });
  document.addEventListener("keydown", (event) => {
    if (previewEl.hidden) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closePreview();
    } else if (event.key === "Tab" && !previewEl.contains(document.activeElement)) {
      event.preventDefault();
      previewCloseEl.focus();
    }
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "session" && changes[REGISTRY_KEY]) refreshRegistry();
    if (area === "local" && changes[SETTINGS_KEY]) {
      const next = Number(changes[SETTINGS_KEY].newValue?.batchConcurrency);
      concurrencyEl.value = next > 0 ? String(next) : "auto";
    }
  });

  async function init() {
    document.getElementById("version").textContent = "v" + chrome.runtime.getManifest().version;
    try {
      const data = await chrome.storage.local.get(SETTINGS_KEY);
      const concurrency = Number(data[SETTINGS_KEY]?.batchConcurrency ?? 4);
      concurrencyEl.value = concurrency > 0 ? String(concurrency) : "auto";
    } catch (error) {
      concurrencyEl.value = "4";
    }
    await refreshRegistry();
  }

  init().catch((error) => console.error("[视频下载] 批量下载页面初始化失败：", error));
})();
