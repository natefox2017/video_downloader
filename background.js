/**
 * background.js — Extension service worker.
 *
 * Owns action clicks, per-tab badge counts, the cross-tab media registry, options-page opening,
 * and dispatching batch download requests back to the tab that detected each video.
 */

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
