/**
 * background.js — Extension service worker.
 *
 * Owns action clicks, per-tab badge counts, the cross-tab media registry, options-page opening,
 * and dispatching batch download requests back to the tab that detected each video.
 */


/* Pure validation rules are shared with content.js, not reimplemented here. */
if (typeof importScripts === "function") importScripts("rules.js");

/**
 * Only send page-nominated HTTP(S) URLs through the privileged probe.
 * Prevent direct access to loopback, link-local and obvious private ranges.
 * @param {string} value Untrusted URL supplied by the current tab.
 * @returns {string} Normalized remote URL or empty string.
 */
function safeProbeUrl(value) {
  try {
    const url = new URL(String(value || ""));
    if (url.protocol !== "https:" && url.protocol !== "http:") return "";
    if (url.username || url.password) return "";
    const host = url.hostname.toLowerCase();
    if (/^(localhost|.*\.localhost|.*\.local|0\.0\.0\.0|127\..*|10\..*|192\.168\..*|169\.254\..*|\[?::1\]?)$/.test(host)) return "";
    if (/^172\.(1[6-9]|2\d|3[01])\./.test(host)) return "";
    return url.href;
  } catch (error) {
    return "";
  }
}


/**
 * Read a bounded prefix even when a CDN ignores Range and responds with
 * HTTP 200 for the entire file. Never call response.blob() for a probe.
 * @param {Response} response Partial (or full) HTTP response.
 * @param {number} limit Maximum bytes to retain.
 * @returns {Promise<Uint8Array>} Leading payload bytes.
 */
async function readProbeBytes(response, limit) {
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (length < limit) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunk = value.subarray(0, limit - length);
      chunks.push(chunk);
      length += chunk.length;
    }
  } finally {
    reader.cancel().catch(() => {}); // Reject the rest of an ignored Range response.
  }
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

/**
 * Fetch at most 16 KiB to confirm actual video bytes/MIME or a video HLS
 * manifest. A missing/incorrect Content-Type must be backed by a signature.
 * @param {string} url Candidate HTTP(S) source.
 * @returns {Promise<object|null>} Validated source metadata or null.
 */
async function probeMediaUrl(url) {
  const source = safeProbeUrl(url);
  if (!source || shouldIgnoreUrl(source)) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DOWNLOAD_RULES.MEDIA_PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(source, {
      credentials: "omit",
      headers: { Range: "bytes=0-" + (DOWNLOAD_RULES.MEDIA_PROBE_MAX_BYTES - 1) },
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const mime = response.headers.get("content-type") || "";
    const playlist = isPlaylistUrl(source) || /mpegurl/i.test(mime);
    if (!playlist && isRejectedMediaMimeType(mime)) return null;

    const bytes = await readProbeBytes(response, DOWNLOAD_RULES.MEDIA_PROBE_MAX_BYTES);
    if (!bytes.length) return null;
    if (playlist) {
      const text = new TextDecoder().decode(bytes);
      if (!isHlsVideoPlaylist(text)) return null;
    } else {
      const signature = sniffVideoSignature(bytes);
      if (signature === "invalid") return null;
      if (signature !== "video" && !isVideoMimeType(mime)) return null;
    }

    const rangeTotal = Number((response.headers.get("content-range") || "").match(/\/(\d+)$/)?.[1] || 0);
    const contentLength = Number(response.headers.get("content-length")) || 0;
    return {
      url: source,
      size: rangeTotal || (response.status === 200 && !playlist ? contentLength : 0),
      kind: playlist ? "hls" : "video",
    };
  } catch (error) {
    // A temporary failure remains invisible rather than being listed as a video.
    return null;
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}


const CONTENT_SCRIPT_FILES = ["rules.js", "content.js"];
const REGISTRY_KEY = "vd_media_registry";

function isInjectableUrl(url) {
  return /^https?:\/\//.test(url || "");
}

async function findTargetTab() {
  try {
    const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (activeTab && isInjectableUrl(activeTab.url)) return activeTab.id;
  } catch (error) {
    /* Continue to the fallback query. */
  }

  try {
    const tabs = await chrome.tabs.query({ url: ["http://*/*", "https://*/*"] });
    if (tabs.length) return tabs[tabs.length - 1].id;
  } catch (error) {
    /* Ignore. */
  }
  return null;
}

async function showPanelOnTab(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "show_panel" });
  } catch (error) {
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: CONTENT_SCRIPT_FILES });
      await chrome.tabs.sendMessage(tabId, { type: "show_panel" });
    } catch (retryError) {
      console.warn("[视频下载] 无法在该标签页显示面板，请刷新页面后重试。", retryError);
    }
  }
}

async function readRegistry() {
  const data = await chrome.storage.session.get(REGISTRY_KEY);
  const registry = data[REGISTRY_KEY];
  return registry && typeof registry === "object" ? registry : {};
}

// Serialize read-modify-write: simultaneous updates from separate tabs must not overwrite each other.
let registryQueue = Promise.resolve();

function mutateRegistry(mutate) {
  const task = registryQueue.catch(() => {}).then(async () => {
    const registry = await readRegistry();
    const changed = mutate(registry);
    if (changed !== false) await chrome.storage.session.set({ [REGISTRY_KEY]: registry });
  });
  registryQueue = task;
  return task;
}

function updateRegistry(tabId, message) {
  return mutateRegistry((registry) => {
    registry[String(tabId)] = {
      tabId,
      pageUrl: message.pageUrl || "",
      pageTitle: message.pageTitle || "",
      platform: message.platform || "",
      updatedAt: Date.now(),
      items: Array.isArray(message.items) ? message.items : [],
    };
  });
}

function removeRegistryTab(tabId) {
  return mutateRegistry((registry) => {
    const key = String(tabId);
    if (!Object.prototype.hasOwnProperty.call(registry, key)) return false;
    delete registry[key];
  });
}

async function setBadge(tabId, count) {
  const safeCount = Math.max(0, Number(count) || 0);
  const text = safeCount > 0 ? String(safeCount > 99 ? "99+" : safeCount) : "";
  await chrome.action.setBadgeText({ text, tabId }).catch(() => {});
  if (text) {
    await chrome.action.setBadgeBackgroundColor({ color: "#dc2626", tabId }).catch(() => {});
    if (chrome.action.setBadgeTextColor) {
      await chrome.action.setBadgeTextColor({ color: "#ffffff", tabId }).catch(() => {});
    }
  }
}

async function dispatchMultiTabDownload(items, concurrency) {
  const grouped = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const tabId = Number(item?.tabId);
    const shareUrl = String(item?.shareUrl || "");
    if (!Number.isInteger(tabId) || !shareUrl) continue;
    if (!grouped.has(tabId)) grouped.set(tabId, new Set());
    grouped.get(tabId).add(shareUrl);
  }

  let tabsStarted = 0;
  let videosRequested = 0;
  for (const [tabId, shareUrls] of grouped) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, {
        type: "start_external_download",
        shareUrls: [...shareUrls],
        concurrency,
      });
      if (!response?.ok) continue;
      tabsStarted += 1;
      videosRequested += Number(response.accepted) || shareUrls.size;
    } catch (error) {
      console.warn("[视频下载] 无法向标签页下发批量下载任务：", tabId, error);
    }
  }
  return { tabsStarted, videosRequested };
}

chrome.action.onClicked.addListener(async () => {
  const tabId = await findTargetTab();
  if (tabId == null) {
    console.warn("[视频下载] 没有可用的网页标签页");
    return;
  }
  await showPanelOnTab(tabId);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !message.type) return undefined;

  if (message.type === "update_badge" && sender.tab?.id != null) {
    setBadge(sender.tab.id, message.count);
    return undefined;
  }

  if (message.type === "update_media_registry" && sender.tab?.id != null) {
    const tabId = sender.tab.id;
    Promise.all([
      setBadge(tabId, Array.isArray(message.items) ? message.items.length : 0),
      updateRegistry(tabId, message),
    ]).catch(() => {});
    return undefined;
  }

  if (message.type === "probe_media_url" && sender.tab?.id != null) {
    probeMediaUrl(message.url).then((media) => {
      sendResponse({ ok: !!media, media });
    }).catch(() => sendResponse({ ok: false, media: null }));
    return true;
  }

  if (message.type === "get_media_registry") {
    registryQueue.catch(() => {}).then(readRegistry)
      .then((registry) => sendResponse({ ok: true, registry }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.type === "start_multi_tab_download") {
    dispatchMultiTabDownload(message.items, message.concurrency).then((result) => sendResponse({ ok: true, ...result })).catch((error) => {
      sendResponse({ ok: false, error: error.message || String(error) });
    });
    return true;
  }

  if (message.type === "open_options") {
    chrome.runtime.openOptionsPage().then(() => sendResponse({ ok: true })).catch((error) => {
      sendResponse({ ok: false, error: error.message || String(error) });
    });
    return true;
  }

  return undefined;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.action.setBadgeText({ text: "", tabId }).catch(() => {});
  removeRegistryTab(tabId).catch(() => {});
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status !== "loading") return;
  chrome.action.setBadgeText({ text: "", tabId }).catch(() => {});
  removeRegistryTab(tabId).catch(() => {});
});
