/**
 * background.js —— 扩展 Service Worker
 *
 * 职责很轻：
 *   1. 点击扩展图标时，把「显示面板」指令发给当前（或最近打开的）抖音标签页；
 *   2. 若该标签页还没注入内容脚本（例如扩展刚重载、页面未刷新），自动补注入一次；
 *   3. 找不到任何抖音标签页时，打开抖音首页。
 *
 * 说明：面板本身与下载逻辑都在 content.js 中，background 只做“找人 + 传话”。
 */

/** 判断 URL 是否为抖音页面 */
function isDouyinUrl(url) {
  return /^https?:\/\/[^/]*douyin\.com\//.test(url || "");
}

/** 按优先级找出要接收指令的抖音标签页 */
async function findTargetTab() {
  // 1) 当前活动标签页就是抖音（最常见）
  try {
    const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (activeTab && isDouyinUrl(activeTab.url)) return activeTab.id;
  } catch (error) {
    /* 忽略：读不到 URL 时继续兜底 */
  }

  // 2) 任意一个抖音标签页（取最后一个，通常是用户最近在看的那个）
  try {
    const douyinTabs = await chrome.tabs.query({ url: "*://*.douyin.com/*" });
    if (douyinTabs.length) return douyinTabs[douyinTabs.length - 1].id;
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
      await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
      await chrome.tabs.sendMessage(tabId, { type: "show_panel" });
    } catch (retryError) {
      console.warn("[抖晓晓] 无法在该标签页显示面板，请刷新抖音页面后重试。", retryError);
    }
  }
}

chrome.action.onClicked.addListener(async () => {
  const tabId = await findTargetTab();
  if (tabId == null) {
    chrome.tabs.create({ url: "https://www.douyin.com/" });
    return;
  }
  await showPanelOnTab(tabId);
});
