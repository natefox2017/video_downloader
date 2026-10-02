/**
 * extractors/youtube.js —— YouTube 主世界抓取脚本
 *
 * 数据源：window.ytInitialPlayerResponse（YouTube 播放器注入的播放信息）。
 *   参考开源实现：nocyb/youtube-chrome-dl —— 从 ytInitialPlayerResponse.streamingData 读取：
 *   - streamingData.formats[]：progressive 直链（音画合并，有声音），优先用，无需签名破解；
 *   - streamingData.adaptiveFormats[]：DASH 分片（音视频分离），按需取视频轨 + 音轨。
 *
 * 注意：部分 formats 的 url 被 signatureCipher 加密，需要破解 player.js；
 * 本脚本只取带直接 url 的 progressive 格式（通常 360p/720p），保证开箱可用。
 * 高清 DASH（1080p+）需要签名破解，超出本脚本范围，降级为通用嗅探。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 取播放器响应对象（SPA 切视频会更新，轮询时重新读） */
  function getPlayerResponse() {
    // 主数据源：播放器注入的全局变量
    if (window.ytInitialPlayerResponse && typeof window.ytInitialPlayerResponse === "object") {
      return window.ytInitialPlayerResponse;
    }
    // 兜底：从 movie_player 取
    try {
      const player = document.getElementById("movie_player");
      if (player && typeof player.getPlayerResponse === "function") {
        const resp = player.getPlayerResponse();
        if (resp && typeof resp === "object") return resp;
      }
    } catch (error) { /* 忽略 */ }
    return null;
  }

  /** 收集 progressive 直链（自带声音），按清晰度降序 */
  function collectProgressiveUrls(streamingData) {
    const urls = [];
    const formats = Array.isArray(streamingData?.formats) ? streamingData.formats : [];
    // 按清晰度/码率降序：优先高清
    const sorted = [...formats].sort((a, b) => {
      const qa = parseInt(a.qualityLabel) || 0;
      const qb = parseInt(b.qualityLabel) || 0;
      if (qa !== qb) return qb - qa;
      return (b.bitrate || 0) - (a.bitrate || 0);
    });
    for (const f of sorted) {
      // 只要带直接 url 的（signatureCipher 需要破解，跳过）
      if (f.url) {
        const text = X.absolutize(f.url);
        if (text && !urls.includes(text)) {
          urls.push({ url: text, label: f.qualityLabel || "", size: 0 });
        }
      }
    }
    return urls;
  }

  /** 从 DASH 取最高码率视频轨 + 音轨（progressive 不可用时的兜底） */
  function collectDashUrls(streamingData) {
    const adaptives = Array.isArray(streamingData?.adaptiveFormats) ? streamingData.adaptiveFormats : [];
    let bestVideo = null;
    let bestAudio = null;
    for (const f of adaptives) {
      if (!f.url) continue; // 跳过加密的
      if (f.mimeType?.startsWith("video/")) {
        if (!bestVideo || (f.bitrate || 0) > (bestVideo.bitrate || 0)) bestVideo = f;
      } else if (f.mimeType?.startsWith("audio/")) {
        if (!bestAudio || (f.bitrate || 0) > (bestAudio.bitrate || 0)) bestAudio = f;
      }
    }
    return {
      videoUrl: bestVideo ? X.absolutize(bestVideo.url) : "",
      audioUrl: bestAudio ? X.absolutize(bestAudio.url) : "",
    };
  }

  function pickTitle(playerResponse) {
    const details = playerResponse?.videoDetails || {};
    if (details.title) return details.title.trim().slice(0, 80);
    return (document.title || "").replace(/ - YouTube$/, "").trim() || "YouTube视频";
  }

  function pickAuthor(playerResponse) {
    const details = playerResponse?.videoDetails || {};
    if (details.author) return details.author.trim().slice(0, 40);
    return "";
  }

  function detect() {
    // 只在 watch / shorts 页面工作
    if (!/^\/(watch|shorts)/.test(location.pathname)) return null;

    const playerResponse = getPlayerResponse();
    if (!playerResponse) return null;

    const streamingData = playerResponse.streamingData || {};
    const progressive = collectProgressiveUrls(streamingData);

    let videoUrls = progressive.map((p) => p.url);
    let audioUrl = "";

    // progressive 不可用时，尝试 DASH（视频轨 + 音轨分开）
    if (!videoUrls.length) {
      const dash = collectDashUrls(streamingData);
      if (dash.videoUrl) {
        videoUrls = [dash.videoUrl];
        audioUrl = dash.audioUrl;
      }
    }

    if (!videoUrls.length) return null;

    const details = playerResponse.videoDetails || {};
    const title = pickTitle(playerResponse);
    const author = pickAuthor(playerResponse);
    const cover = details.thumbnail?.thumbnails?.slice(-1)?.[0]?.url || "";

    return {
      // 去重键：视频 ID（URL 参数 v=）
      shareUrl: "youtube:" + (details.videoId || new URLSearchParams(location.search).get("v") || location.href),
      title,
      desc: title,
      author: author || "YouTube作者",
      cover,
      duration: parseInt(details.lengthSeconds) || 0,
      type: "视频",
      size: 0,
      videoUrls,
      audioUrl,
      fileName: "",
    };
  }

  X.create("youtube", {
    platformName: "YouTube",
    pollInterval: 2000,
    detect,
  });
})();
