/**
 * extractors/common.js —— 主世界抓取脚本的公共底座
 *
 * 每个平台一个抓取脚本（douyin.js / kuaishou.js / …），都跑在页面「主世界」，
 * 原因只有一个：页面自己的 JS 变量（window.player、window.__playinfo__ 等）
 * 只有主世界能读到，隔离世界读不到。
 *
 * 本文件提供：
 *   - VDExtractor.create(platformId, handlers)：幂等守卫 + 上报 + 补抓请求响应；
 *   - 通用小工具：safeText / sanitizeFileName / pickFirstUrl / scanScriptUrls。
 *
 * 与 content.js 的通信（同窗口 window.postMessage，靠 source 字段区分）：
 *   extractor → content : { source:"vd-extractor", type:"media_found", media }
 *   content → extractor : { source:"vd-content",   type:"request_current_media" }
 *
 * media 统一结构（各平台抓取脚本都要整理成这个形状）：
 *   { shareUrl, platformId, platform, title, desc, author, cover, duration,
 *     size, type:"视频"|"图集", fileName(不带扩展名),
 *     videoUrl, videoUrls[], audioUrl, imageUrls[], source:"页面解析" }
 *
 * 约束：抓取脚本只做「读页面、整理、上报」，绝不做下载
 * （下载要走隔离世界的 fetch，不受跨域限制；主世界 fetch 会被页面 CSP 拦截）。
 */

"use strict";

window.VDExtractor = (() => {
  /* ---------------- 基础工具 ---------------- */

  /** 任意值安全转字符串 */
  function safeText(value) {
    return typeof value === "string" ? value : "";
  }

  /** 文件名非法字符清理 */
  function sanitizeFileName(name) {
    return String(name || "")
      .replace(/[\r\n]+/g, "_")
      .replace(/[/\\:*?"<>|#]+/g, "_")
      .trim()
      .slice(0, 80);
  }

  /** 取数组里第一个非空字符串 */
  function pickFirstUrl(list) {
    if (!Array.isArray(list)) return "";
    for (const item of list) {
      const url = typeof item === "string" ? item : item?.src || item?.url;
      if (url) return url;
    }
    return "";
  }

  /** 协议相对地址（//xxx）补全为 https */
  function absolutize(url) {
    const text = safeText(url).trim();
    if (text.startsWith("//")) return "https:" + text;
    return text;
  }

  /**
   * 在页面所有 <script> 文本里按正则扫描直链。
   * 用于兜底：页面把视频地址嵌在 JSON 里、但没有暴露成 JS 变量时。
   * @param {RegExp} pattern 必须带全局标志 g，第一个捕获组为 URL
   * @returns 去重后的 URL 数组（按出现顺序）
   */
  function scanScriptUrls(pattern) {
    const found = [];
    const seen = new Set();
    const scripts = document.querySelectorAll("script");
    for (const script of scripts) {
      const text = script.textContent || "";
      if (!text) continue;
      pattern.lastIndex = 0;
      let match;
      // 单个 script 文本太大时只扫前 2MB，避免长串 JSON 卡死主线程
      const slice = text.length > 2 * 1024 * 1024 ? text.slice(0, 2 * 1024 * 1024) : text;
      while ((match = pattern.exec(slice)) !== null) {
        const url = absolutize(match[1]);
        if (url && !seen.has(url)) {
          seen.add(url);
          found.push(url);
        }
        if (found.length >= 20) return found; // 够用了，提前收工
      }
    }
    return found;
  }

  /**
   * 从 <video> 元素收集正在播放/可播放的地址（各平台通用兜底）。
   * 注意：有些站点的 video.src 是 blob:，这种地址换页即失效，
   * 上报时由 content.js 统一过滤掉（blob: 无法跨页下载）。
   */
  function collectVideoElementUrls(root) {
    const urls = [];
    const scope = root || document;
    scope.querySelectorAll("video").forEach((video) => {
      const candidates = [
        video.currentSrc,
        video.src,
        ...Array.from(video.querySelectorAll("source")).map((s) => s.src),
      ];
      for (const url of candidates) {
        const text = safeText(url).trim();
        if (text && !urls.includes(text)) urls.push(text);
      }
    });
    return urls;
  }

  /** 读 og:video / twitter:player:stream 等 meta 里的视频地址（通用规则） */
  function collectMetaVideoUrls() {
    const urls = [];
    document.querySelectorAll('meta[property="og:video"], meta[name="twitter:player:stream"]').forEach((meta) => {
      const url = absolutize(meta.getAttribute("content"));
      if (url && !urls.includes(url)) urls.push(url);
    });
    return urls;
  }

  /* ---------------- 抓取器工厂 ---------------- */

  /**
   * 创建一个平台抓取器。
   * @param {string} platformId 平台 id（与 rules.js 的 PLATFORMS 对应）
   * @param {Object} handlers
   * @param {string} handlers.platformName 平台中文名
   * @param {function(): Object|null} handlers.detect 读取页面并返回 media 对象（或 null）
   * @param {number} [handlers.pollInterval=1500] 轮询间隔（毫秒）
   */
  function create(platformId, handlers) {
    // 幂等守卫：扩展重载 / SPA 跳转可能导致重复注入
    const guardKey = `__VD_EXTRACTOR_${platformId.toUpperCase()}__`;
    if (window[guardKey]) return null;
    window[guardKey] = true;

    const platformName = handlers.platformName || platformId;
    let lastReportedKey = "";

    /** media 补默认值并上报给 content.js */
    function report(media) {
      if (!media || typeof media !== "object") return;
      const normalized = {
        platformId,
        platform: platformName,
        source: "页面解析",
        title: "",
        desc: "",
        author: "",
        cover: "",
        duration: 0,
        size: 0,
        type: "视频",
        videoUrls: [],
        imageUrls: [],
        audioUrl: "",
        ...media,
      };
      normalized.videoUrl = normalized.videoUrl || pickFirstUrl(normalized.videoUrls);
      // 去重键：优先用平台唯一 id，没有就用首选地址
      normalized.shareUrl = normalized.shareUrl || normalized.videoUrl;
      if (!normalized.shareUrl) return;
      if (normalized.shareUrl === lastReportedKey) return;
      lastReportedKey = normalized.shareUrl;
      window.postMessage({ source: "vd-extractor", type: "media_found", media: normalized }, "*");
    }

    /** 读一次当前媒体并上报（带去重） */
    function reportCurrent() {
      try {
        const media = handlers.detect();
        if (media) report(media);
      } catch (error) {
        /* 页面结构变化导致读取失败时静默跳过，不干扰页面 */
      }
    }

    /** 强制上报一次（忽略去重，用于面板打开时的补抓） */
    function reportCurrentForce() {
      const key = lastReportedKey;
      lastReportedKey = "";
      try {
        const media = handlers.detect();
        if (media) report(media);
        else lastReportedKey = key;
      } catch (error) {
        lastReportedKey = key;
      }
    }

    // 响应 content.js 的补抓请求
    window.addEventListener("message", (event) => {
      if (event.source !== window) return;
      const data = event.data;
      if (!data || data.source !== "vd-content") return;
      if (data.type === "request_current_media") reportCurrentForce();
    });

    // 轮询：SPA 切视频不会刷新页面，靠轮询发现变化
    const interval = Math.max(500, handlers.pollInterval || 1500);
    setInterval(reportCurrent, interval);
    // 页面加载后尽快报一次
    setTimeout(reportCurrent, 600);

    return { report, reportCurrent, reportCurrentForce };
  }

  return {
    create,
    safeText,
    sanitizeFileName,
    pickFirstUrl,
    absolutize,
    scanScriptUrls,
    collectVideoElementUrls,
    collectMetaVideoUrls,
  };
})();
