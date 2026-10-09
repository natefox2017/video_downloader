/**
 * monitor.js — Live list of detected videos across open tabs and batch-download dispatcher.
 * Video downloads remain in each tab's content.js; this page only selects and sends requests.
 */

(() => {
  "use strict";

  const REGISTRY_KEY = "vd_media_registry";
  const registryEl = document.getElementById("registry");
  const emptyEl = document.getElementById("registry-empty");
  const summaryEl = document.getElementById("registry-summary");
  const selectAllEl = document.getElementById("select-all");
  const downloadButton = document.getElementById("download-selected");
  const downloadStatusEl = document.getElementById("download-status");

  // New rows are selected by default. Explicitly deselected rows stay deselected on updates.
  const excluded = new Set();
  const collapsedTabs = new Set();
  let renderRequest = 0;

  /**
   * Use both tab and media key to keep same-named videos from different websites independent.
   * @param {number|string} tabId Browser tab identifier.
   * @param {string} shareUrl Per-tab media identifier.
   * @returns {string} Stable selection key.
   */
  function videoKey(tabId, shareUrl) {
    return JSON.stringify([String(tabId), String(shareUrl)]);
  }

  /**
   * Escape untrusted webpage titles and media identifiers before HTML insertion.
   * @param {*} value Text from a browser tab or extractor.
   * @returns {string} Escaped value.
   */
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * Format a known byte count without inventing a size for unavailable metadata.
   * @param {number} bytes File size.
   * @returns {string} File size or unknown.
   */
  function formatSize(bytes) {
    const size = Number(bytes);
    if (!Number.isFinite(size) || size <= 0) return "大小未知";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(units.length - 1, Math.floor(Math.log(size) / Math.log(1024)));
    const value = size / Math.pow(1024, index);
    return value.toFixed(index === 0 ? 0 : value >= 100 ? 0 : 1) + " " + units[index];
  }

  /**
   * Return the public host without showing potentially sensitive URL parameters.
   * @param {string} url Tab URL.
   * @returns {string} Host or a generic label.
   */
  function hostLabel(url) {
    try {
      return new URL(url).hostname;
    } catch (error) {
      return "网页";
    }
  }

  /**
   * Build the set of selected items from the current rendered checkboxes.
   * @returns {{tabId:number,shareUrl:string}[]} Requested media identifiers.
   */
  function selectedItems() {
    return Array.from(registryEl.querySelectorAll("input[data-video]:checked")).map((input) => ({
      tabId: Number(input.dataset.tabId),
      shareUrl: input.dataset.shareUrl,
    }));
  }

  /**
   * Update the aggregate checkbox state and download action without rerendering rows.
   * @returns {void}
   */
  function updateSelection() {
    const eligible = Array.from(registryEl.querySelectorAll("input[data-video]:not(:disabled)"));
    const selectedCount = eligible.filter((input) => input.checked).length;
    selectAllEl.disabled = eligible.length === 0;
    selectAllEl.checked = eligible.length > 0 && selectedCount === eligible.length;
    selectAllEl.indeterminate = selectedCount > 0 && selectedCount < eligible.length;
    downloadButton.disabled = selectedCount === 0;
    downloadButton.textContent = selectedCount ? `下载选中的 ${selectedCount} 个视频` : "下载选中的视频";
  }

  /**
   * Refresh the registry without losing selections or collapsed groups during live updates.
   * @returns {Promise<void>}
   */
  async function renderRegistry() {
    const request = ++renderRequest;
    let response;
    try {
      response = await chrome.runtime.sendMessage({ type: "get_media_registry" });
    } catch (error) {
      if (request !== renderRequest) return;
      summaryEl.textContent = "读取失败";
      emptyEl.hidden = false;
      emptyEl.textContent = "读取失败，请点击刷新重试。";
      return;
    }
    if (request !== renderRequest) return;
    if (!response?.ok) {
      summaryEl.textContent = "读取失败";
      emptyEl.hidden = false;
      emptyEl.textContent = "读取失败，请点击刷新重试。";
      return;
    }

    const registry = response.registry || {};
    const tabs = Object.values(registry)
      .filter((entry) => Number.isInteger(Number(entry?.tabId)) && Array.isArray(entry.items) && entry.items.length > 0)
      .sort((a, b) => Number(a.tabId) - Number(b.tabId));
    const currentKeys = new Set();
    let totalVideos = 0;

    const groups = tabs.map((entry) => {
      const items = entry.items.filter((item) => item && typeof item.shareUrl === "string" && item.shareUrl);
      if (!items.length) return "";
      const tabId = Number(entry.tabId);
      const rows = items.map((item) => {
        const key = videoKey(tabId, item.shareUrl);
        currentKeys.add(key);
        const unavailable = item.status === "done" || item.status === "downloading";
        const checked = !unavailable && !excluded.has(key);
        const stateLabel = item.status === "done" ? "已完成" : item.status === "downloading" ? "下载中" : item.status === "error" ? "失败" : "";
        const meta = [formatSize(item.size), item.quality || "", stateLabel].filter(Boolean).join(" · ");
        return `<label class="video-row ${unavailable ? "is-complete" : ""}">
          <input type="checkbox" data-video data-tab-id="${tabId}" data-share-url="${escapeHtml(item.shareUrl)}"
            ${checked ? "checked" : ""} ${unavailable ? "disabled" : ""} />
          <span class="video-title" title="${escapeHtml(item.title || "视频")}">${escapeHtml(item.title || "视频")}</span>
          <span class="video-meta">${escapeHtml(meta)}</span>
        </label>`;
      }).join("");
      totalVideos += items.length;
      const tabTitle = escapeHtml(entry.pageTitle || entry.platform || hostLabel(entry.pageUrl));
      return `<details class="tab-group" data-tab-id="${tabId}" ${collapsedTabs.has(String(tabId)) ? "" : "open"}>
        <summary><strong title="${tabTitle}">${tabTitle}</strong><small>${escapeHtml(hostLabel(entry.pageUrl))} · ${items.length} 个视频</small></summary>
        <div class="tab-rows">${rows}</div>
      </details>`;
    });

    for (const key of excluded) {
      if (!currentKeys.has(key)) excluded.delete(key);
    }
    emptyEl.hidden = totalVideos > 0;
    emptyEl.textContent = "暂时没有检测到视频。请先打开视频网页并播放视频，再回来刷新。";
    summaryEl.textContent = `${tabs.length} 个网页 · ${totalVideos} 个视频`;
    registryEl.innerHTML = groups.join("");
    updateSelection();
  }

  registryEl.addEventListener("change", (event) => {
    const input = event.target.closest("input[data-video]");
    if (!input) return;
    const key = videoKey(input.dataset.tabId, input.dataset.shareUrl);
    if (input.checked) excluded.delete(key);
    else excluded.add(key);
    updateSelection();
  });

  registryEl.addEventListener("toggle", (event) => {
    const group = event.target;
    if (!group.matches("details[data-tab-id]")) return;
    const key = String(group.dataset.tabId);
    if (group.open) collapsedTabs.delete(key);
    else collapsedTabs.add(key);
  }, true);

  selectAllEl.addEventListener("change", () => {
    registryEl.querySelectorAll("input[data-video]:not(:disabled)").forEach((input) => {
      input.checked = selectAllEl.checked;
      const key = videoKey(input.dataset.tabId, input.dataset.shareUrl);
      if (input.checked) excluded.delete(key);
      else excluded.add(key);
    });
    updateSelection();
  });

  document.getElementById("refresh-registry").addEventListener("click", () => {
    renderRegistry().catch((error) => console.error("[视频下载] 读取检测结果失败：", error));
  });

  downloadButton.addEventListener("click", async () => {
    const items = selectedItems();
    if (!items.length) return;
    downloadButton.disabled = true;
    downloadStatusEl.textContent = "正在向对应网页发送下载任务…";
    try {
      const response = await chrome.runtime.sendMessage({ type: "start_multi_tab_download", items });
      if (response?.ok && response.tabsStarted > 0) {
        const totalTabs = new Set(items.map((item) => item.tabId)).size;
        const failedTabs = totalTabs - response.tabsStarted;
        downloadStatusEl.textContent = failedTabs
          ? `已发送 ${response.videosRequested} 个视频，另有 ${failedTabs} 个网页未成功接收任务，请检查标签页。`
          : `已向 ${response.tabsStarted} 个网页发送 ${response.videosRequested} 个下载任务。`;
      } else {
        downloadStatusEl.textContent = "未能发送下载任务，请确认对应网页还在打开。";
      }
    } catch (error) {
      downloadStatusEl.textContent = "发送失败，请刷新后重试。";
    } finally {
      updateSelection();
    }
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "session" && changes[REGISTRY_KEY]) {
      renderRegistry().catch((error) => console.error("[视频下载] 更新检测列表失败：", error));
    }
  });

  document.getElementById("version").textContent = "v" + chrome.runtime.getManifest().version;
  renderRegistry().catch((error) => console.error("[视频下载] 页面初始化失败：", error));
})();
