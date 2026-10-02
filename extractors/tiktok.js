/**
 * extractors/tiktok.js —— TikTok 主世界抓取脚本
 *
 * 数据源：页面内 <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"> 的 JSON。
 *   参考开源实现：
 *   - auto-clipper/autoclipper-download-extension：从 __UNIVERSAL_DATA_FOR_REHYDRATION__ 读取首屏视频；
 *   - mapletakes/newsroom：同一技术，__DEFAULT_SCOPE__["webapp.video-detail"].itemInfo.itemStruct。
 *
 * 路径：__DEFAULT_SCOPE__["webapp.video-detail"].itemInfo.itemStruct
 *   - video.playAddr → 播放地址（含水印）
 *   - video.downloadAddr → 下载地址（通常无水印，需登录）
 *   - video.duration / desc / author.nickname / video.cover
 *
 * 注意：TikTok CDN 链接绑定会话（cookies），不要复制到外部下载；
 * 本扩展在隔离世界用 fetch 下载（带 host_permissions），cookies 会自动带上。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 读取水合 JSON */
  function getRehydrationData() {
    try {
      const el = document.getElementById("__UNIVERSAL_DATA_FOR_REHYDRATION__");
      if (!el?.textContent) return null;
      return JSON.parse(el.textContent);
    } catch (error) {
      return null;
    }
  }

  /** 取 itemStruct（兼容多种 scope key） */
  function getItemStruct(data) {
    const scope = data?.__DEFAULT_SCOPE__ || {};
    const detail = scope["webapp.video-detail"] || scope["webapp.reflow.video.detail"];
    return detail?.itemInfo?.itemStruct || null;
  }

  /** 从对象里取第一个可用的 URL（TikTok 的地址是 { urlList: [] } 结构） */
  function pickUrl(obj) {
    if (!obj) return "";
    if (typeof obj === "string") return X.absolutize(obj);
    const list = obj.urlList || obj.url_list || [];
    for (const u of list) {
      if (u) return X.absolutize(u);
    }
    return "";
  }

  function detect() {
    const data = getRehydrationData();
    if (!data) return null;
    const item = getItemStruct(data);
    if (!item?.video) return null;

    const video = item.video;
    // downloadAddr（无水印）优先，playAddr 兜底
    const downloadUrl = pickUrl(video.downloadAddr);
    const playUrl = pickUrl(video.playAddr);
    const videoUrls = [];
    if (downloadUrl && !videoUrls.includes(downloadUrl)) videoUrls.push(downloadUrl);
    if (playUrl && !videoUrls.includes(playUrl)) videoUrls.push(playUrl);
    if (!videoUrls.length) return null;

    const title = (item.desc || "").trim().slice(0, 80) || "TikTok视频";
    const author = item.author?.nickname || item.author?.uniqueId || "";
    const cover = pickUrl(video.cover) || pickUrl(video.originCover);

    return {
      // 去重键：视频 ID
      shareUrl: "tiktok:" + (item.id || location.href.split("?")[0]),
      title,
      desc: title,
      author: author ? String(author).slice(0, 40) : "TikTok作者",
      cover,
      duration: video.duration || 0,
      type: "视频",
      size: 0,
      videoUrls,
      audioUrl: pickUrl(video.music?.playUrl) || "",
      fileName: "",
    };
  }

  X.create("tiktok", {
    platformName: "TikTok",
    pollInterval: 2000,
    detect,
  });
})();
