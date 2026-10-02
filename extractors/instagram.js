/**
 * extractors/instagram.js —— Instagram 主世界抓取脚本
 *
 * 数据源：页面 HTML/内嵌 JSON 里的 "video_url" 字段。
 *   参考开源实现：zeal-arch/detector/extractors/sites/instagram.js ——
 *   从页面源码正则提取 "video_url":"https://..."，以及 video_versions 数组。
 *
 * 注意：Instagram 直链带过期签名，只能当时下载，存下来过会儿就 403。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 反转义 JSON 字符串里的 URL（\/ → /，\u0026 → &） */
  function unescapeUrl(text) {
    return String(text || "")
      .replace(/\\\//g, "/")
      .replace(/\\u0026/g, "&")
      .replace(/&amp;/g, "&");
  }

  /** 从页面源码提取视频地址 */
  function extractVideoUrls() {
    const urls = [];
    const seen = new Set();

    // 1. "video_url":"https://..."（转义形式）
    const pattern1 = /"video_url"\s*:\s*"((?:[^"\\]|\\.)*)"/g;
    const found1 = X.scanScriptUrls ? null : null; // 占位，下方手动扫
    void found1;

    const scripts = document.querySelectorAll("script");
    for (const script of scripts) {
      const text = script.textContent || "";
      if (!text || !text.includes("video_url")) continue;
      const slice = text.length > 2 * 1024 * 1024 ? text.slice(0, 2 * 1024 * 1024) : text;
      pattern1.lastIndex = 0;
      let match;
      while ((match = pattern1.exec(slice)) !== null) {
        const url = unescapeUrl(match[1]);
        if (url.startsWith("http") && !seen.has(url)) {
          seen.add(url);
          urls.push(url);
        }
      }
    }

    // 2. video_versions 数组里的 url（多清晰度，取最高）
    const pattern2 = /"video_versions"\s*:\s*\[([^\]]+)\]/g;
    for (const script of scripts) {
      const text = script.textContent || "";
      if (!text || !text.includes("video_versions")) continue;
      const slice = text.length > 2 * 1024 * 1024 ? text.slice(0, 2 * 1024 * 1024) : text;
      pattern2.lastIndex = 0;
      let match;
      while ((match = pattern2.exec(slice)) !== null) {
        const urlPattern = /"url"\s*:\s*"((?:[^"\\]|\\.)*)"/g;
        let um;
        while ((um = urlPattern.exec(match[1])) !== null) {
          const url = unescapeUrl(um[1]);
          if (url.startsWith("http") && !seen.has(url)) {
            seen.add(url);
            urls.push(url);
          }
        }
      }
    }

    return urls;
  }

  function getMeta() {
    let title = "";
    let author = "";
    let cover = "";
    try {
      const ogTitle = document.querySelector('meta[property="og:title"]');
      if (ogTitle) title = ogTitle.content || "";
      const ogImage = document.querySelector('meta[property="og:image"]');
      if (ogImage) cover = ogImage.content || "";
      // 作者：URL 路径第一段
      const pathMatch = location.pathname.match(/^\/([^/]+)\//);
      if (pathMatch) author = pathMatch[1];
    } catch (error) { /* 忽略 */ }
    return { title, author, cover };
  }

  /** 短链接 ID（/reel/xxx、/p/xxx）用于缓存键 */
  function getPostId() {
    const match = location.pathname.match(/\/(?:reel|p|tv)\/([^/]+)/);
    return match ? match[1] : location.href;
  }

  let lastKey = "";
  let cachedMedia = null;

  function getMedia() {
    // 只在帖子/ reel 页抓取
    if (!/\/(reel|p|tv)\//.test(location.pathname)) return null;

    const key = getPostId();
    if (key === lastKey && cachedMedia) return cachedMedia;

    const urls = extractVideoUrls();
    if (!urls.length) return null;

    const meta = getMeta();
    const title = meta.title || `instagram_${key}`;
    const author = meta.author || "未知作者";

    const media = {
      shareUrl: location.href,
      platformId: "instagram",
      platform: "Instagram",
      title: X.safeText(title).trim().slice(0, 80),
      desc: "",
      author,
      cover: X.absolutize(meta.cover),
      duration: 0,
      size: 0,
      type: "视频",
      fileName: X.sanitizeFileName(`instagram_${author}_${key}`.slice(0, 64)) || `instagram_${key}`,
      videoUrl: urls[0],
      videoUrls: urls,
      audioUrl: "",
      imageUrls: [],
      source: "页面解析",
    };
    lastKey = key;
    cachedMedia = media;
    return media;
  }

  X.create("instagram", { getMedia });
})();
