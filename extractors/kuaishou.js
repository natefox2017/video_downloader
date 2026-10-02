/**
 * extractors/kuaishou.js —— 快手主世界抓取脚本
 *
 * 快手网页端没有像抖音 window.player 那样稳定的对外数据结构，
 * 因此采用三层递进策略（按可靠性排序）：
 *   1. <video> 元素：正在播放的地址最可信，直接取 currentSrc；
 *   2. 页面 <script> 内嵌 JSON：扫描 "videoUrl" / "photoUrl" 等字段；
 *   3. og:video 等 meta 标签（部分分享页会带）。
 *
 * 标题/作者：优先读页面 meta 与标题栏，取不到就用兜底文案，
 * 文件名由 content.js 按通用规则拼（平台_标题_时间戳）。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 从脚本 JSON 里扫描疑似快手视频直链 */
  function scanJsonVideoUrls() {
    const found = [];
    // 快手内嵌数据常用字段：videoUrl / photoUrl / src
    const patterns = [
      /"videoUrl"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
      /"photoUrl"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
      /"src"\s*:\s*"((?:https?:)?\/\/[^"]+\.(?:mp4|m3u8)[^"]*)"/gi,
    ];
    for (const pattern of patterns) {
      for (const url of X.scanScriptUrls(pattern)) {
        if (!found.includes(url)) found.push(url);
      }
    }
    return found;
  }

  /** 读标题：og:title 优先，其次 document.title 去站点后缀 */
  function pickTitle() {
    const og = document.querySelector('meta[property="og:title"]')?.getAttribute("content");
    if (og) return og.trim();
    // 快手详情页标题节点
    try {
      const h1 = document.querySelector("h1");
      if (h1?.textContent?.trim()) return h1.textContent.trim().slice(0, 80);
    } catch (error) { /* 忽略 */ }
    return (document.title || "").replace(/_快手.*$/, "").replace(/-快手.*$/, "").replace(/快手$/, "").trim();
  }

  /** 读作者：og 标签 / 页面内常见作者节点，取不到返回空 */
  function pickAuthor() {
    const meta = document.querySelector('meta[name="author"]')?.getAttribute("content");
    if (meta) return meta.trim();
    // 快手详情页作者名节点（class 名可能变，包一层 try，失败就放弃）
    try {
      const selectors = [
        '[class*="user-name"]',
        '[class*="author-name"]',
        '[class*="nickname"]',
        '[data-e2e="video-author-name"]',
      ];
      for (const sel of selectors) {
        const node = document.querySelector(sel);
        if (node?.textContent?.trim()) return node.textContent.trim().slice(0, 40);
      }
    } catch (error) {
      /* 忽略 */
    }
    return "";
  }

  /** 读封面：og:image / video poster / 内嵌 JSON */
  function pickCover() {
    const og = document.querySelector('meta[property="og:image"]')?.getAttribute("content");
    if (og) return og;
    // 正在播放的 video 元素的 poster
    try {
      const video = document.querySelector("video[poster]");
      if (video?.poster) return video.poster;
    } catch (error) { /* 忽略 */ }
    // 内嵌 JSON 里的封面字段
    for (const pattern of [
      /"coverUrl"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
      /"poster"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
      /"thumbnailUrl"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g,
    ]) {
      const urls = X.scanScriptUrls(pattern);
      if (urls.length) return urls[0];
    }
    return "";
  }

  function detect() {
    // 第 1 层：正在播放的 video 元素（最可信）
    const elementUrls = X.collectVideoElementUrls().filter((url) => !url.startsWith("blob:"));
    // 第 2 层：内嵌 JSON 扫描
    const jsonUrls = scanJsonVideoUrls();
    // 第 3 层：meta 标签
    const metaUrls = X.collectMetaVideoUrls();

    const videoUrls = [];
    for (const url of [...elementUrls, ...jsonUrls, ...metaUrls]) {
      if (url && !videoUrls.includes(url)) videoUrls.push(url);
    }
    if (!videoUrls.length) return null;

    const title = pickTitle() || "快手视频";
    const author = pickAuthor();
    const cover = pickCover();

    return {
      // 去重键用视频地址：信息流切视频时页面 URL 不变，靠地址区分不同视频
      shareUrl: videoUrls[0] || location.href.split("#")[0],
      title,
      desc: title,
      author: author || "快手作者",
      cover,
      type: "视频",
      videoUrls,
      fileName: "", // 留空：content.js 按通用规则拼「快手_标题_时间戳」
    };
  }

  X.create("kuaishou", {
    platformName: "快手",
    pollInterval: 1500,
    detect,
  });
})();
