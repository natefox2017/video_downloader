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

async function writeRegistry(registry) {
  await chrome.storage.session.set({ [REGISTRY_KEY]: registry });
}

async function updateRegistry(tabId, message) {
  const registry = await readRegistry();
  registry[String(tabId)] = {
    tabId,
    pageUrl: message.pageUrl || "",
    pageTitle: message.pageTitle || "",
    platform: message.platform || "",
    updatedAt: Date.now(),
    items: Array.isArray(message.items) ? message.items : [],
  };
  await writeRegistry(registry);
}

async function removeRegistryTab(tabId) {
  const registry = await readRegistry();
  if (!Object.prototype.hasOwnProperty.call(registry, String(tabId))) return;
  delete registry[String(tabId)];
  await writeRegistry(registry);
}

async function setBadge(tabId, count) {
  const safeCount = Math.max(0, Number(count) || 0);
  const text = safeCount > 0 ? String(safeCount > 99 ? "99+" : safeCount) : "";
  await chrome.action.setBadgeText({ text, tabId }).catch(() => {});
  if (text) {
    await chrome.action.setBadgeBackgroundColor({ color: "#2563eb", tabId }).catch(() => {});
  }
}

async function dispatchMultiTabDownload(items) {
  const grouped = new Map();
  for (const item of Array.isArray(items) ? items : []) {
    const tabId = Number(item?.tabId);
    const shareUrl = String(item?.shareUrl || "");
    if (!Number.isInteger(tabId) || !shareUrl) continue;
    if (!grouped.has(tabId)) grouped.set(tabId, []);
    grouped.get(tabId).push(shareUrl);
  }

  let tabsStarted = 0;
  let videosRequested = 0;
  for (const [tabId, shareUrls] of grouped) {
    try {
      await chrome.tabs.sendMessage(tabId, { type: "start_external_download", shareUrls });
      tabsStarted += 1;
      videosRequested += shareUrls.length;
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
    readRegistry().then((registry) => sendResponse({ ok: true, registry })).catch((error) => {
      sendResponse({ ok: false, error: error.message || String(error) });
    });
    return true;
  }

  if (message.type === "start_multi_tab_download") {
    dispatchMultiTabDownload(message.items).then((result) => sendResponse({ ok: true, ...result })).catch((error) => {
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
