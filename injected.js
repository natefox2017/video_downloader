/**
 * injected.js —— 运行在抖音页面「主世界」的抓取脚本
 *
 * 职责（只做一件事）：
 *   1. 监听抖音网页播放器（window.player）当前播放的视频，读出播放器内部的 awemeInfo 数据；
 *   2. 把数据整理成统一的媒体对象，通过 window.postMessage 交给 content.js；
 *   3. 响应 content.js 的“补抓当前视频”请求。
 *
 * 不负责下载：下载（fetch / 合并音视频 / 落盘）全部在 content.js 中完成，
 * 因为扩展隔离世界既有 host_permissions 可以绕过跨域，也能使用 chrome.* API。
 *
 * 之所以必须注入到主世界：window.player 是页面自身的 JS 变量，隔离世界读不到。
 */

(() => {
  "use strict";

  // 防止脚本被重复注入（扩展重载 / 页面 SPA 跳转时可能发生）
  if (window.__DY_DL_INJECTED__) return;
  window.__DY_DL_INJECTED__ = true;

  /** 最近一次上报过的视频地址，用于避免同一个视频重复上报 */
  let lastReportedUrl = "";

  /** 轮询检测到的播放器实例 */
  let hookedPlayer = null;

  /** 遮罩层选择器：抖音把播放器数据挂在 window.player 上 */
  const POLL_INTERVAL = 1000;

  /* ------------------------------------------------------------------ */
  /* 工具函数                                                            */
  /* ------------------------------------------------------------------ */

  /** 把任意值安全地转成字符串（用于标题兜底） */
  function safeText(value) {
    return typeof value === "string" ? value : "";
  }

  /** 清理文件名中的非法字符，避免保存失败 */
  function sanitizeFileName(name) {
    return String(name || "")
      .replace(/[\r\n]+/g, "_")
      .replace(/[/\\:*?"<>|#]+/g, "_")
      .trim();
  }

  /**
   * 生成下载文件名：作者_标题_话题标签（截断 64 个字符）
   * 与原插件保持一致的命名风格。
   */
  function buildFileName(awemeInfo) {
    const author = safeText(awemeInfo.authorInfo?.nickname);
    const desc = safeText(awemeInfo.desc);
    const tags = (awemeInfo.textExtra || [])
      .map((item) => item.hashtagName)
      .filter(Boolean)
      .map((name) => "#" + name)
      .join("_");

    let title = desc;
    (awemeInfo.textExtra || []).forEach((item) => {
      if (item.hashtagName) title = title.replace("#" + item.hashtagName, "");
    });

    return sanitizeFileName(`${author}_${title.trim()}_${tags}`.slice(0, 64)) || "douyin_video";
  }

  /**
   * 收集视频的候选直链（按可用性优先级排序）。
   * 抖音不同版本、不同接口返回的地址音轨情况不一致：
   *   - playApi（/aweme/v1/play/ 形式）通常是服务端合并好的、带声音的 mp4；
   *   - bitRateList / playAddr 里的 CDN 直链有时只有视频轨。
   * 因此这里全部收集，交给 content.js 依次尝试。
   */
  function collectVideoUrls(video) {
    if (!video) return [];
    const urls = [];
    const push = (url) => {
      if (url && !urls.includes(url)) urls.push(url);
    };

    push(video.playApi);
    if (Array.isArray(video.playAddr)) video.playAddr.forEach((item) => push(item?.src));
    if (Array.isArray(video.bitRateList)) video.bitRateList.forEach((item) => push(item?.playApi));

    // 协议相对地址补全为 https
    return urls.map((url) => (url.startsWith("//") ? "https:" + url : url));
  }

  /** 从 music 对象里挑出音频直链（用于视频无音轨时补救） */
  function pickAudioUrl(music) {
    if (!music) return "";
    const list = music.playUrl?.urlList;
    if (Array.isArray(list) && list.length) return list[0];
    if (music.playUrl?.uri) return music.playUrl.uri;
    return "";
  }

  /** 估算文件大小（抖音不同版本字段名不一致） */
  function pickFileSize(video) {
    const first = Array.isArray(video?.bitRateList) ? video.bitRateList[0] : null;
    return (
      first?.file_size ||
      first?.dataSize ||
      video?.dataSize ||
      video?.file_size ||
      0
    );
  }

  /**
   * 把播放器原始数据整理成 content.js 需要的媒体对象。
   * 返回 null 表示当前数据不完整（例如页面刚加载还没拿到视频）。
   */
  function normalizeMedia(awemeInfo) {
    if (!awemeInfo || typeof awemeInfo !== "object") return null;

    const shareUrl = awemeInfo.shareInfo?.shareUrl || "";
    if (!shareUrl) return null;

    const video = awemeInfo.video || {};
    const images = Array.isArray(awemeInfo.images) ? awemeInfo.images : [];
    const isGallery = images.length > 0 && !video.playApi;
    const videoUrls = collectVideoUrls(video);

    const media = {
      shareUrl,                                        // 唯一标识，用于去重
      title: safeText(awemeInfo.desc).trim() || "无标题",
      desc: safeText(awemeInfo.desc),
      author: safeText(awemeInfo.authorInfo?.nickname) || "未知作者",
      cover: video.cover || awemeInfo.video?.originCover || images[0]?.urlList?.[0] || "",
      duration: video.duration || 0,
      type: isGallery ? "图集" : "视频",
      size: pickFileSize(video),
      fileName: buildFileName(awemeInfo),
      videoUrl: videoUrls[0] || "",                    // 首选地址
      videoUrls,                                       // 全部候选地址（content.js 依次尝试）
      audioUrl: pickAudioUrl(awemeInfo.music),         // 配乐地址（视频无音轨时补救用）
      imageUrls: images
        .map((item) => item?.urlList?.[0] || item?.urlList?.[item?.urlList?.length - 1] || "")
        .filter(Boolean),
    };

    if (!media.videoUrl && !media.imageUrls.length) return null;
    return media;
  }

  /** 把媒体对象发给 content.js */
  function report(media) {
    window.postMessage({ source: "dy-dl-injected", type: "media_found", media }, "*");
  }

  /** 读取当前播放器中的视频并上报（带去重） */
  function reportCurrent() {
    const player = window.player;
    if (!player) return;

    const awemeInfo = player.config?.awemeInfo || player.config?.aweme_info;
    const media = normalizeMedia(awemeInfo);
    if (!media) return;

    if (media.shareUrl === lastReportedUrl) return;
    lastReportedUrl = media.shareUrl;
    report(media);
  }

  /* ------------------------------------------------------------------ */
  /* 播放器监听                                                          */
  /* ------------------------------------------------------------------ */

  /** 给播放器实例挂上事件监听，切换视频时立即上报 */
  function hookPlayer(player) {
    if (!player || typeof player.on !== "function") return;

    const handler = () => setTimeout(reportCurrent, 120);
    ["play", "seeked", "loadeddata"].forEach((event) => {
      try {
        player.on(event, handler);
      } catch (error) {
        /* 个别事件不存在时忽略 */
      }
    });
  }

  /** 每秒检查一次播放器实例是否变化（抖音是 SPA，播放器会被重建） */
  function startPlayerWatcher() {
    setInterval(() => {
      if (window.player && window.player !== hookedPlayer) {
        hookedPlayer = window.player;
        hookPlayer(hookedPlayer);
        setTimeout(reportCurrent, 200);
      }
    }, POLL_INTERVAL);
  }

  /* ------------------------------------------------------------------ */
  /* 与 content.js 的通信                                                */
  /* ------------------------------------------------------------------ */

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== "dy-dl-content") return;

    if (data.type === "request_current_media") {
      // content.js 主动请求当前视频（例如刚打开面板时）
      reportCurrent();
      // 补报一次：即使 shareUrl 未变化也强制上报，保证面板一定有数据
      const awemeInfo = window.player?.config?.awemeInfo;
      const media = normalizeMedia(awemeInfo);
      if (media) report(media);
    }
  });

  startPlayerWatcher();
  setTimeout(reportCurrent, 500);
})();
