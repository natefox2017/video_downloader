/**
 * extractors/douyin.js —— 抖音主世界抓取脚本
 *
 * 数据源：抖音网页播放器 window.player.config.awemeInfo。
 *  - playApi（/aweme/v1/play/ 形式）通常是服务端合并好的、带声音的 mp4；
 *  - bitRateList / playAddr 里的 CDN 直链有时只有视频轨；
 *  因此全部收集为候选地址，交给 content.js 依次尝试下载。
 *
 * 图集（图文帖）：awemeInfo.images 非空且无 playApi 时，按图集处理。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 收集视频候选直链（按可用性优先级排序） */
  function collectVideoUrls(video) {
    if (!video) return [];
    const urls = [];
    const push = (url) => {
      const text = X.absolutize(url);
      if (text && !urls.includes(text)) urls.push(text);
    };

    push(video.playApi);
    if (Array.isArray(video.playAddr)) video.playAddr.forEach((item) => push(item?.src));
    if (Array.isArray(video.bitRateList)) video.bitRateList.forEach((item) => push(item?.playApi));
    return urls;
  }

  /** 把 bitRateList 里实际存在的不同视频源整理成可选清晰度。 */
  function collectVariants(video) {
    const variants = [];
    const list = Array.isArray(video?.bitRateList) ? video.bitRateList : [];
    for (const item of list) {
      const url = X.absolutize(item?.playApi || item?.playAddr?.[0]?.src || "");
      if (!url) continue;
      const height = Number(item?.height || item?.playAddr?.[0]?.height || 0);
      const bitrate = Number(item?.bitRate || item?.bit_rate || 0);
      const size = Number(item?.file_size || item?.dataSize || 0);
      const label = X.safeText(item?.gearName || item?.gear_name || item?.qualityType || "")
        || (height ? height + "p" : bitrate ? Math.round(bitrate / 1000) + " kbps" : "");
      variants.push({ url, label, height, bitrate, size, mimeType: "video/mp4" });
    }
    return variants;
  }

  /** 从 music 对象里挑音频直链（视频无音轨时补救用） */
  function pickAudioUrl(music) {
    if (!music) return "";
    const list = music.playUrl?.urlList;
    if (Array.isArray(list) && list.length) return X.absolutize(list[0]);
    return X.absolutize(music.playUrl?.uri);
  }

  /** 提取视频 vid（用于原画接口） */
  function pickVid(video) {
    // playAddr[0].uri 通常是 vid
    if (Array.isArray(video?.playAddr)) {
      for (const item of video.playAddr) {
        if (item?.uri) return String(item.uri);
      }
    }
    // 从 playApi URL 里提取 video_id 参数
    const playApi = String(video?.playApi || "");
    const match = playApi.match(/[?&]video_id=([^&]+)/);
    if (match) return decodeURIComponent(match[1]);
    // 从 CDN URL 路径里提取（/video/tos/.../vid~... 形式）
    const urlMatch = playApi.match(/\/([a-f0-9]{32})/i);
    if (urlMatch) return urlMatch[1];
    return "";
  }

  /** 估算文件大小（抖音不同版本字段名不一致） */
  function pickFileSize(video) {
    const first = Array.isArray(video?.bitRateList) ? video.bitRateList[0] : null;
    return first?.file_size || first?.dataSize || video?.dataSize || video?.file_size || 0;
  }

  /** 文件名：作者_标题_话题标签（与旧版保持一致的命名风格） */
  function buildFileName(awemeInfo) {
    const author = X.safeText(awemeInfo.authorInfo?.nickname);
    const desc = X.safeText(awemeInfo.desc);
    const textExtra = Array.isArray(awemeInfo.textExtra) ? awemeInfo.textExtra : [];
    const tags = textExtra
      .map((item) => item.hashtagName)
      .filter(Boolean)
      .map((name) => "#" + name)
      .join("_");

    let title = desc;
    textExtra.forEach((item) => {
      if (item.hashtagName) title = title.replace("#" + item.hashtagName, "");
    });

    return X.sanitizeFileName(`${author}_${title.trim()}_${tags}`.slice(0, 64)) || "douyin_video";
  }

  /** 把播放器原始数据整理成统一 media 对象；数据不完整时返回 null */
  function detect() {
    const player = window.player;
    if (!player) return null;
    const awemeInfo = player.config?.awemeInfo || player.config?.aweme_info;
    if (!awemeInfo || typeof awemeInfo !== "object") return null;

    const shareUrl = awemeInfo.shareInfo?.shareUrl || "";
    if (!shareUrl) return null;

    const video = awemeInfo.video || {};
    const images = Array.isArray(awemeInfo.images) ? awemeInfo.images : [];
    const isGallery = images.length > 0 && !video.playApi;
    const videoUrls = collectVideoUrls(video);

    const media = {
      shareUrl,
      title: X.safeText(awemeInfo.desc).trim() || "无标题",
      desc: X.safeText(awemeInfo.desc),
      author: X.safeText(awemeInfo.authorInfo?.nickname) || "未知作者",
      cover: video.cover || video.originCover || images[0]?.urlList?.[0] || "",
      duration: video.duration || 0,
      type: isGallery ? "图集" : "视频",
      size: pickFileSize(video),
      fileName: buildFileName(awemeInfo),
      videoUrls,
      variants: collectVariants(video),
      audioUrl: pickAudioUrl(awemeInfo.music),
      originVid: pickVid(video),
      imageUrls: images
        .map((item) => item?.urlList?.[0] || item?.urlList?.[item?.urlList?.length - 1] || "")
        .filter(Boolean),
    };

    if (!media.videoUrls.length && !media.imageUrls.length) return null;
    return media;
  }

  const extractor = X.create("douyin", {
    platformName: "抖音",
    pollInterval: 1000,
    detect,
  });

  // 播放器事件钩子：切视频时立即上报，不等轮询
  if (extractor && window.player && typeof window.player.on === "function") {
    const handler = () => setTimeout(() => extractor.reportCurrent(), 120);
    ["play", "seeked", "loadeddata"].forEach((event) => {
      try {
        window.player.on(event, handler);
      } catch (error) {
        /* 个别事件不存在时忽略 */
      }
    });
  }
})();
