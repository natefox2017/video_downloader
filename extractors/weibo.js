/**
 * extractors/weibo.js —— 微博主世界抓取脚本
 *
 * 微博视频页没有统一的播放器数据结构，采用两层策略：
 *   1. <video> 元素：正在播放的地址最可信；
 *   2. 页面 <script> 内嵌 JSON：扫描 mp4_hd_url / mp4_sd_url / video_url 等字段
 *      （高清优先：hd_url 排在 sd_url 前面）。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 按清晰度优先级扫描内嵌 JSON（hd 在前，sd 在后） */
  function scanJsonVideoUrls() {
    const found = [];
    const patterns = [
      /"mp4_hd_url"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
      /"mp4_sd_url"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
      /"video_url"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
      /"stream_url"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
      /"play_url"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
    ];
    for (const pattern of patterns) {
      for (const url of X.scanScriptUrls(pattern)) {
        // 微博的 url 常带反斜杠转义（https:\/\/），还原一下
        const clean = url.replace(/\\\//g, "/");
        if (clean && !found.includes(clean)) found.push(clean);
      }
    }
    return found;
  }

  function pickTitle() {
    const og = document.querySelector('meta[property="og:title"]')?.getAttribute("content");
    if (og) return og.trim().slice(0, 60);
    return (document.title || "").replace(/_微博.*$/, "").trim() || "微博视频";
  }

  function pickAuthor() {
    const nick =
      document.querySelector('meta[property="og:nick_name"]')?.getAttribute("content") ||
      document.querySelector(".WB_detail .WB_info a")?.textContent;
    return (nick || "").trim().slice(0, 40);
  }

  function detect() {
    const elementUrls = X.collectVideoElementUrls().filter((url) => !url.startsWith("blob:"));
    const jsonUrls = scanJsonVideoUrls();
    const metaUrls = X.collectMetaVideoUrls();

    const videoUrls = [];
    for (const url of [...elementUrls, ...jsonUrls, ...metaUrls]) {
      if (url && !videoUrls.includes(url)) videoUrls.push(url);
    }
    if (!videoUrls.length) return null;

    const title = pickTitle();
    const author = pickAuthor();
    const cover = document.querySelector('meta[property="og:image"]')?.getAttribute("content") || "";

    return {
      // 去重键用视频地址：信息流切视频时页面 URL 不变，靠地址区分不同视频
      shareUrl: videoUrls[0] || location.href.split("#")[0],
      title,
      desc: title,
      author: author || "微博用户",
      cover,
      type: "视频",
      videoUrls,
      fileName: "",
    };
  }

  X.create("weibo", {
    platformName: "微博",
    pollInterval: 1500,
    detect,
  });
})();
