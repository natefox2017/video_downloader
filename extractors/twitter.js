/**
 * extractors/twitter.js —— X/Twitter 主世界抓取脚本
 *
 * 数据源：页面内嵌 JSON 里的 video_info.variants 数组。
 *   参考开源实现：zeal-arch/detector/extractors/sites/twitter.js ——
 *   从 video_info.variants 取 content_type 为 video/mp4 的地址，按 bitrate 降序。
 *
 * 注意：X 直链带过期签名，只能当时下载。
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

  /** 从页面源码提取 video_info.variants 里的 MP4 地址（按码率降序，同步） */
  function extractVideoUrls() {
    const candidates = []; // {url, bitrate}
    const seen = new Set();
    const scripts = document.querySelectorAll("script");

    for (const script of scripts) {
      const text = script.textContent || "";
      if (!text || !text.includes("video_info")) continue;
      const slice = text.length > 2 * 1024 * 1024 ? text.slice(0, 2 * 1024 * 1024) : text;

      const variantsPattern = /"variants"\s*:\s*\[([^\]]+)\]/g;
      let vm;
      variantsPattern.lastIndex = 0;
      while ((vm = variantsPattern.exec(slice)) !== null) {
        const block = vm[1];
        const itemPattern = /\{[^{}]*"content_type"\s*:\s*"video\/mp4"[^{}]*\}/g;
        let im;
        itemPattern.lastIndex = 0;
        while ((im = itemPattern.exec(block)) !== null) {
          const bitrateMatch = im[0].match(/"bitrate"\s*:\s*(\d+)/);
          const urlMatch = im[0].match(/"url"\s*:\s*"((?:[^"\\]|\\.)*)"/);
          if (!urlMatch) continue;
          const url = unescapeUrl(urlMatch[1]);
          if (!url.startsWith("http") || seen.has(url)) continue;
          seen.add(url);
          candidates.push({ url, bitrate: bitrateMatch ? parseInt(bitrateMatch[1]) : 0 });
        }
      }
    }

    candidates.sort((a, b) => b.bitrate - a.bitrate);
    return candidates.map((c) => c.url);
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
      const pathMatch = location.pathname.match(/^\/([^/]+)\/status\//);
      if (pathMatch) author = pathMatch[1];
    } catch (error) { /* 忽略 */ }
    return { title, author, cover };
  }

  function detect() {
    // 只在推文详情页抓取
    if (!/\/status\/\d+/.test(location.pathname)) return null;

    const urls = extractVideoUrls();
    if (!urls.length) return null;

    const key = location.pathname.match(/\/status\/(\d+)/)?.[1] || "tweet";
    const meta = getMeta();
    const author = meta.author || "未知作者";
    const title = meta.title || `twitter_${key}`;

    return {
      shareUrl: location.href,
      title: X.safeText(title).trim().slice(0, 80),
      desc: "",
      author,
      cover: X.absolutize(meta.cover),
      duration: 0,
      size: 0,
      type: "视频",
      fileName: X.sanitizeFileName(`x_${author}_${key}`.slice(0, 64)) || `x_${key}`,
      videoUrl: urls[0],
      videoUrls: urls,
      audioUrl: "",
      imageUrls: [],
    };
  }

  X.create("twitter", {
    platformName: "X",
    pollInterval: 2000,
    detect,
  });
})();
