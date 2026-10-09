/**
 * extractors/vimeo.js —— Vimeo 主世界抓取脚本
 *
 * 数据源：window.vimeo.config，或请求 https://player.vimeo.com/video/{id}/config。
 *   参考开源实现：zeal-arch/detector/extractors/sites/vimeo.js —— 从
 *   config.request.files 读取：
 *   - files.progressive[]：MP4 直链（音画合并），优先用；
 *   - files.hls.cdns：HLS 地址，兜底。
 *
 * 注意：config 拉取是异步的，detect() 触发后缓存结果，下次轮询上报。
 * 主世界 fetch 可能被页面 CSP 拦截，失败时降级为通用嗅探。
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

  /** 读页面内嵌的播放器配置（同步） */
  function findEmbeddedConfig() {
    try {
      if (window.vimeo && window.vimeo.config) return window.vimeo.config;
      if (window.player && window.player.config) return window.player.config;
    } catch (error) { /* 忽略 */ }
    return null;
  }

  /** 从 config 整理出 media（同步） */
  function buildMedia(config, videoId) {
    const files = config?.request?.files;
    if (!files) return null;

    const videoData = config.video || {};
    const owner = videoData.owner || {};

    // progressive MP4 直链优先（音画合并），按清晰度降序
    const videoUrls = [];
    const variants = [];
    const progressive = Array.isArray(files.progressive) ? files.progressive : [];
    const sorted = [...progressive].sort((a, b) => (b.height || 0) - (a.height || 0));
    for (const p of sorted) {
      if (p.url) {
        const text = X.absolutize(p.url);
        if (text && !videoUrls.includes(text)) {
          videoUrls.push(text);
          variants.push({
            url: text,
            label: p.quality || (p.height ? p.height + "p" : ""),
            width: Number(p.width) || 0,
            height: Number(p.height) || 0,
            bitrate: Number(p.bitrate) || 0,
            size: Number(p.size) || 0,
            mimeType: p.mime || "video/mp4",
          });
        }
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
      title: title.trim(),
      desc: X.safeText(videoData.description),
      author,
      cover: X.absolutize(cover),
      duration: videoData.duration || 0,
      size: variants[0]?.size || 0,
      type: "视频",
      fileName: X.sanitizeFileName(`vimeo_${videoId}_${title.trim()}`.slice(0, 64)) || `vimeo_${videoId}`,
      videoUrl: videoUrls[0],
      videoUrls,
      variants,
      audioUrl: "",
      imageUrls: [],
    };
  }

  // ---- 异步 config 缓存：detect() 触发拉取，下次轮询返回 ----
  let cachedVideoId = "";
  let cachedMedia = null;
  let fetchingId = "";

  function ensureConfig(videoId) {
    if (videoId === cachedVideoId || videoId === fetchingId) return;
    fetchingId = videoId;
    fetch(`https://player.vimeo.com/video/${videoId}/config`, {
      credentials: "include",
      headers: { Accept: "application/json" },
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((config) => {
        if (config) {
          const media = buildMedia(config, videoId);
          if (media) {
            cachedVideoId = videoId;
            cachedMedia = media;
          }
        }
      })
      .catch(() => { /* CSP 拦截或网络失败，降级走通用嗅探 */ })
      .finally(() => { fetchingId = ""; });
  }

  function detect() {
    const videoId = extractVideoId(location.href);
    if (!videoId) return null;

    // 1. 内嵌配置：同步直接返回
    const embedded = findEmbeddedConfig();
    if (embedded) {
      const media = buildMedia(embedded, videoId);
      if (media) {
        cachedVideoId = videoId;
        cachedMedia = media;
        return media;
      }
    }

    // 2. 缓存命中
    if (videoId === cachedVideoId && cachedMedia) return cachedMedia;

    // 3. 触发异步拉取，本次返回 null，下次轮询上报
    ensureConfig(videoId);
    return cachedVideoId === videoId ? cachedMedia : null;
  }

  X.create("vimeo", {
    platformName: "Vimeo",
    pollInterval: 2000,
    detect,
  });
})();
