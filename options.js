/**
 * options.js — Saved download preferences and per-platform extraction strategies.
 */

(() => {
  "use strict";

  const SETTINGS_KEY = "vd_settings";
  const formatEl = document.getElementById("format");
  const qualityEl = document.getElementById("quality");
  const concurrencyEl = document.getElementById("concurrency");
  const rememberEl = document.getElementById("remember-position");
  const strategiesEl = document.getElementById("platform-strategies");

  let settings = {
    ...DEFAULT_SETTINGS,
    platformStrategies: { ...DEFAULT_SETTINGS.platformStrategies },
  };

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
    settings.batchConcurrency = Number(concurrencyEl.value) || 0;
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
    const concurrency = Number(settings.batchConcurrency ?? DEFAULT_SETTINGS.batchConcurrency);
    concurrencyEl.value = concurrency > 0 ? String(concurrency) : "auto";
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
        <td><span class="ui-select-wrap"><select class="ui-select" data-platform="${escapeHtml(platform.id)}" aria-label="${escapeHtml(platform.name)}解析方式">${options}</select></span></td>
      </tr>`;
    }).join("");
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
  }

  document.addEventListener("change", (event) => {
    if (event.target.matches("#format, #quality, #concurrency, #remember-position, select[data-platform]")) {
      saveSettings().catch(() => {});
    }
  });

  init().catch((error) => {
    console.error("[视频下载] 设置页初始化失败：", error);
  });
})();
