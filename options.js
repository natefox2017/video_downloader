/**
 * options.js — Settings page and cross-tab detected-media queue.
 */

(() => {
  "use strict";

  const SETTINGS_KEY = "vd_settings";
  const REGISTRY_KEY = "vd_media_registry";
  const formatEl = document.getElementById("format");
  const qualityEl = document.getElementById("quality");
  const rememberEl = document.getElementById("remember-position");
  const strategiesEl = document.getElementById("platform-strategies");
  const registryEl = document.getElementById("registry");
  const registryEmptyEl = document.getElementById("registry-empty");
  const downloadButton = document.getElementById("download-selected");
  const downloadStatus = document.getElementById("download-status");

  let settings = {
    ...DEFAULT_SETTINGS,
    platformStrategies: { ...DEFAULT_SETTINGS.platformStrategies },
  };

  function formatSize(bytes) {
    if (!bytes || bytes <= 0) return "大小未知";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
    const value = bytes / Math.pow(1024, index);
    return value.toFixed(index === 0 ? 0 : value >= 100 ? 0 : 1) + " " + units[index];
  }

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function saveSettings() {
    settings.format = formatEl.value;
    settings.quality = qualityEl.value;
    settings.rememberPanelPosition = rememberEl.checked;
    settings.platformStrategies = {};
    strategiesEl.querySelectorAll("select[data-platform]").forEach((select) => {
      if (select.value !== "auto") settings.platformStrategies[select.dataset.platform] = select.value;
    });
    await chrome.storage.local.set({ [SETTINGS_KEY]: settings });
  }

  function renderSettings() {
    document.getElementById("version").textContent = "v" + chrome.runtime.getManifest().version;
    qualityEl.innerHTML = Object.entries(QUALITY_LABELS)
      .map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`)
      .join("");
    formatEl.value = settings.format || DEFAULT_SETTINGS.format;
    qualityEl.value = settings.quality || DEFAULT_SETTINGS.quality;
    rememberEl.checked = settings.rememberPanelPosition !== false;

    strategiesEl.innerHTML = [...PLATFORMS, GENERIC_PLATFORM].map((platform) => {
      const selected = settings.platformStrategies?.[platform.id] || "auto";
      const defaultText = platform.defaultStrategy === "sniff" ? "网络嗅探优先" : "页面解析优先";
      const options = Object.entries(DOWNLOAD_STRATEGY_LABELS)
        .map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${escapeHtml(label)}</option>`)
        .join("");
      return `<tr>
        <td>${escapeHtml(platform.name)}</td>
        <td><small>${defaultText}</small></td>
        <td><select data-platform="${escapeHtml(platform.id)}" aria-label="${escapeHtml(platform.name)}解析方式">${options}</select></td>
      </tr>`;
    }).join("");
  }

  function selectedItems() {
    return Array.from(registryEl.querySelectorAll('input[data-video]:checked')).map((input) => ({
      tabId: Number(input.dataset.tabId),
      shareUrl: input.dataset.shareUrl,
    }));
  }

  function updateDownloadButton() {
    const count = selectedItems().length;
    downloadButton.disabled = count === 0;
    downloadButton.textContent = count > 0 ? `下载选中的 ${count} 个视频` : "下载选中的视频";
  }

  async function renderRegistry() {
    const response = await chrome.runtime.sendMessage({ type: "get_media_registry" });
    const registry = response?.ok ? response.registry || {} : {};
    const tabs = Object.values(registry).filter((entry) => Array.isArray(entry.items) && entry.items.length > 0);
    registryEmptyEl.hidden = tabs.length > 0;

    registryEl.innerHTML = tabs.map((entry) => {
      const rows = entry.items.map((item) => {
        const quality = item.quality ? ` · ${escapeHtml(item.quality)}` : "";
        return `<label class="video-row">
          <input type="checkbox" data-video data-tab-id="${entry.tabId}" data-share-url="${escapeHtml(item.shareUrl)}" ${item.status === "done" ? "disabled" : "checked"} />
          <span class="video-title" title="${escapeHtml(item.title || "视频")}">${escapeHtml(item.title || "视频")}</span>
          <span class="video-meta">${formatSize(item.size)}${quality}</span>
        </label>`;
      }).join("");
      return `<article class="tab-group">
        <header class="tab-head">
          <strong>${escapeHtml(entry.pageTitle || entry.platform || "网页")}</strong>
          <small>${entry.items.length} 个视频</small>
        </header>
        ${rows}
      </article>`;
    }).join("");
    updateDownloadButton();
  }

  async function init() {
    const data = await chrome.storage.local.get(SETTINGS_KEY);
    if (data[SETTINGS_KEY]) {
      settings = {
        ...DEFAULT_SETTINGS,
        ...data[SETTINGS_KEY],
        platformStrategies: {
          ...DEFAULT_SETTINGS.platformStrategies,
          ...(data[SETTINGS_KEY].platformStrategies || {}),
        },
      };
    }
    renderSettings();
    await renderRegistry();
  }

  document.addEventListener("change", (event) => {
    if (event.target.matches("#format, #quality, #remember-position, select[data-platform]")) {
      saveSettings().catch(() => {});
    }
    if (event.target.matches("input[data-video]")) updateDownloadButton();
  });

  document.getElementById("refresh-registry").addEventListener("click", () => renderRegistry());
  downloadButton.addEventListener("click", async () => {
    const items = selectedItems();
    if (!items.length) return;
    downloadButton.disabled = true;
    downloadStatus.textContent = "正在把任务发送到对应网页…";
    const response = await chrome.runtime.sendMessage({ type: "start_multi_tab_download", items });
    if (response?.ok) {
      downloadStatus.textContent = `已向 ${response.tabsStarted} 个网页发送 ${response.videosRequested} 个下载任务。`;
    } else {
      downloadStatus.textContent = "下发任务失败，请确认对应网页仍然打开。";
    }
    updateDownloadButton();
  });

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "session" && changes[REGISTRY_KEY]) renderRegistry();
  });

  init().catch((error) => {
    console.error("[视频下载] 设置页初始化失败：", error);
  });
})();
