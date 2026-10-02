/**
 * background.js —— 扩展 Service Worker
 *
 * 职责很轻，只做两件事：
 *   1. 点击扩展图标时，把「显示面板」指令发给当前标签页（任意网站，不再限定抖音）；
 *   2. 若该标签页还没注入内容脚本（例如扩展刚重载、页面未刷新），自动补注入一次。
 *
 * 说明：
 *   - 媒体抓取在各平台的主世界抓取脚本（extractors/*.js）与内容脚本的
 *     DOM/资源嗅探里完成，不在 background 里做；
 *   - 下载在 content.js（隔离世界，有 host_permissions，fetch 不受跨域限制）。
 */

/** 内容脚本文件（顺序不能换：rules.js 必须先于 content.js 执行） */
const CONTENT_SCRIPT_FILES = ["rules.js", "content.js"];

/** 是否为可注入内容脚本的页面（排除 chrome://、扩展页、应用商店等） */
function isInjectableUrl(url) {
  return /^https?:\/\//.test(url || "");
}

/** 按优先级找出要接收指令的标签页：当前活动页优先 */
async function findTargetTab() {
  try {
    const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (activeTab && isInjectableUrl(activeTab.url)) return activeTab.id;
  } catch (error) {
    /* 读不到 URL 时继续兜底 */
  }

  try {
    const tabs = await chrome.tabs.query({ url: ["http://*/*", "https://*/*"] });
    if (tabs.length) return tabs[tabs.length - 1].id;
  } catch (error) {
    /* 忽略 */
  }

  return null;
}

/** 向目标标签页发送「显示面板」指令，必要时补注入内容脚本 */
async function showPanelOnTab(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "show_panel" });
  } catch (error) {
    // 内容脚本尚未注入（页面在扩展重载前就打开了）→ 动态注入后再试一次
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: CONTENT_SCRIPT_FILES });
      await chrome.tabs.sendMessage(tabId, { type: "show_panel" });
    } catch (retryError) {
      console.warn("[视频下载] 无法在该标签页显示面板，请刷新页面后重试。", retryError);
    }
  }
}

chrome.action.onClicked.addListener(async () => {
  const tabId = await findTargetTab();
  if (tabId == null) {
    console.warn("[视频下载] 没有可用的网页标签页");
    return;
  }
  await showPanelOnTab(tabId);
});
