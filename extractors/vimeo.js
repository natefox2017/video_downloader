/**
 * extractors/vimeo.js —— Vimeo 主世界抓取脚本
 *
 * 数据源：window.vimeo.config，或请求 https://player.vimeo.com/video/{id}/config。
 *   参考开源实现：zeal-arch/detector/extractors/sites/vimeo.js —— 从
 *   config.request.files 读取：
 *   - files.progressive[]：MP4 直链（音画合并），优先用；
 *   - files.hls.cdns：HLS 地址，兜底。
 *
 * 注意：主世界 fetch 可能被页面 CSP 拦截，失败时降级为通用嗅探。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 从 URL 提取 Vimeo 视频 ID */
  function extractVideoId(url) {
    let match = String(url || "").match(
      /vimeo\.com\/(?:channels\/[^/]+\/|groups\/[^/]+\/videos\/|album\/[^/]+\/video\/|video\/|)(\d+)/
    );
    if (match) return match[1];
    match = String(url || "").match(/player\.vimeo\.com\/video\/(\d+)/);
    return match ? match[1] : null;
  }

  /** 读页面内嵌的播放器配置 */
  function findEmbeddedConfig() {
    try {
      if (window.vimeo && window.vimeo.config) return window.vimeo.config;
      if (window.player && window.player.config) return window.player.config;
    } catch (error) { /* 忽略 */ }
    return null;
  }

  /** 从播放器页面拉取配置（主世界 fetch，可能被 CSP 拦截） */
  async function fetchPlayerConfig(videoId) {
    try {
      const response = await fetch(`https://player.vimeo.com/video/${videoId}/config`, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return null;
      return await response.json();
    } catch (error) {
      return null;
    }
  }

  /** 从 config 整理出 media */
  function buildMedia(config, videoId) {
    const files = config?.request?.files;
    if (!files) return null;

    const videoData = config.video || {};
    const owner = videoData.owner || {};

    // progressive MP4 直链优先（音画合并），按清晰度降序
    const videoUrls = [];
    const progressive = Array.isArray(files.progressive) ? files.progressive : [];
    const sorted = [...progressive].sort((a, b) => (b.height || 0) - (a.height || 0));
    for (const p of sorted) {
      if (p.url) {
        const text = X.absolutize(p.url);
        if (text && !videoUrls.includes(text)) videoUrls.push(text);
      }
    }

    // HLS 兜底
    let hlsUrl = "";
    if (files.hls && files.hls.cdns) {
      const cdnKey = files.hls.default_cdn;
      const cdn = files.hls.cdns[cdnKey] || Object.values(files.hls.cdns)[0];
      if (cdn && cdn.url) hlsUrl = X.absolutize(cdn.url);
    }
    if (hlsUrl && !videoUrls.includes(hlsUrl)) videoUrls.push(hlsUrl);

    if (!videoUrls.length) return null;

    const title = X.safeText(videoData.title) || document.title.replace(" on Vimeo", "") || "vimeo_video";
    const author = X.safeText(owner.name) || "未知作者";
    const cover =
      videoData.thumbs?.["1280"] || videoData.thumbs?.["640"] || videoData.thumbs?.base || "";

    return {
      shareUrl: location.href,
      platformId: "vimeo",
      platform: "Vimeo",
      title: title.trim(),
      desc: X.safeText(videoData.description),
      author,
      cover: X.absolutize(cover),
      duration: videoData.duration || 0,
      size: 0,
      type: "视频",
      fileName: X.sanitizeFileName(`vimeo_${videoId}_${title.trim()}`.slice(0, 64)) || `vimeo_${videoId}`,
      videoUrl: videoUrls[0],
      videoUrls,
      audioUrl: "",
      imageUrls: [],
      source: "页面解析",
    };
  }

  let lastVideoId = "";
  let cachedMedia = null;

  async function getMedia() {
    const videoId = extractVideoId(location.href);
    if (!videoId) return null;
    if (videoId === lastVideoId && cachedMedia) return cachedMedia;

    let config = findEmbeddedConfig();
    if (!config) config = await fetchPlayerConfig(videoId);
    if (!config) return null;

    const media = buildMedia(config, videoId);
    if (media) {
      lastVideoId = videoId;
      cachedMedia = media;
    }
    return media;
  }

  X.create("vimeo", { getMedia });
})();
