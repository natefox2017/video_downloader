/**
 * extractors/facebook.js —— Facebook 主世界抓取脚本
 *
 * 数据源：页面内嵌 JSON 里的 playable_url / playable_url_quality_hd 字段。
 *   参考开源实现：zeal-arch/detector/extractors/sites/facebook.js ——
 *   正则扫描 "playable_url":"..."（标清）和 "playable_url_quality_hd":"..."（高清）。
 *
 * 注意：Facebook 直链带过期签名，只能当时下载。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 反转义 JSON 字符串里的 URL */
  function unescapeUrl(text) {
    return String(text || "")
      .replace(/\\\//g, "/")
      .replace(/\\u0026/g, "&")
      .replace(/&amp;/g, "&");
  }

  /** 从页面源码提取高清/标清视频地址（同步） */
  function extractVideoUrls() {
    let hd = "";
    let sd = "";
    const patterns = [
      [/"playable_url_quality_hd"\s*:\s*"((?:[^"\\]|\\.)*)"/g, "hd"],
      [/"browser_native_hd_url"\s*:\s*"((?:[^"\\]|\\.)*)"/g, "hd"],
      [/"playable_url"\s*:\s*"((?:[^"\\]|\\.)*)"/g, "sd"],
      [/"browser_native_sd_url"\s*:\s*"((?:[^"\\]|\\.)*)"/g, "sd"],
    ];

    const scripts = document.querySelectorAll("script");
    for (const script of scripts) {
      const text = script.textContent || "";
      if (!text || !text.includes("playable_url")) continue;
      const slice = text.length > 2 * 1024 * 1024 ? text.slice(0, 2 * 1024 * 1024) : text;
      for (const [pattern, kind] of patterns) {
        pattern.lastIndex = 0;
        let match;
        while ((match = pattern.exec(slice)) !== null) {
          const url = unescapeUrl(match[1]);
          if (!url.startsWith("http")) continue;
          if (kind === "hd" && !hd) hd = url;
          if (kind === "sd" && !sd) sd = url;
        }
      }
      if (hd && sd) break;
    }

    const urls = [];
    if (hd) urls.push(hd);
    if (sd && sd !== hd) urls.push(sd);
    return urls;
  }

  function getMeta() {
    let title = "";
    let cover = "";
    try {
      const ogTitle = document.querySelector('meta[property="og:title"]');
      if (ogTitle) title = ogTitle.content || "";
      const ogImage = document.querySelector('meta[property="og:image"]');
      if (ogImage) cover = ogImage.content || "";
    } catch (error) { /* 忽略 */ }
    return { title, cover };
  }

  function detect() {
    // 只在视频相关页面抓取
    if (!(/\/(videos|watch|reel)\//.test(location.pathname) || /[?&]v=\d+/.test(location.search))) {
      return null;
    }

    const urls = extractVideoUrls();
    if (!urls.length) return null;

    const key =
      location.pathname.match(/\/videos\/(\d+)/)?.[1] ||
      location.search.match(/[?&]v=(\d+)/)?.[1] ||
      "video";
    const meta = getMeta();
    const title = meta.title || `facebook_${key}`;

    return {
      shareUrl: location.href,
      title: X.safeText(title).trim().slice(0, 80),
      desc: "",
      author: "未知作者",
      cover: X.absolutize(meta.cover),
      duration: 0,
      size: 0,
      type: "视频",
      fileName: X.sanitizeFileName(`facebook_${key}`.slice(0, 64)) || `facebook_${key}`,
      videoUrl: urls[0],
      videoUrls: urls,
      audioUrl: "",
      imageUrls: [],
    };
  }

  X.create("facebook", {
    platformName: "Facebook",
    pollInterval: 2000,
    detect,
  });
})();
