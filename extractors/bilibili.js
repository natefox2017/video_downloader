/**
 * extractors/bilibili.js —— 哔哩哔哩主世界抓取脚本
 *
 * 数据源：window.__playinfo__（B 站播放器注入的播放信息）。
 *   - data.durl[]： progressive 直链（mp4），有声音，优先用；
 *   - data.dash.video[]： DASH 分片（无声音），按带宽取最高一条兜底。
 *
 * 注意：B 站视频直链有时效性（过期较快），抓到后尽快下载；
 * 同一视频切清晰度会刷新 __playinfo__，轮询会自动跟上。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 从 __playinfo__ 收集候选地址（durl 优先，dash 按带宽降序） */
  function collectBiliUrls(playinfo) {
    const urls = [];
    const push = (url) => {
      const text = X.absolutize(url);
      if (text && !urls.includes(text)) urls.push(text);
    };

    const data = playinfo?.data || {};

    // durl：服务端合并好的 mp4（通常有声音），按 size 取最大的排前面
    const durl = Array.isArray(data.durl) ? [...data.durl].sort((a, b) => (b.size || 0) - (a.size || 0)) : [];
    durl.forEach((item) => {
      push(item.url);
      if (Array.isArray(item.backup_url)) item.backup_url.forEach(push);
    });

    // dash：只有视频轨，按带宽降序作为兜底
    const dashVideo = Array.isArray(data.dash?.video)
      ? [...data.dash.video].sort((a, b) => (b.bandwidth || 0) - (a.bandwidth || 0))
      : [];
    dashVideo.forEach((item) => {
      push(item.baseUrl || item.base_url);
      if (Array.isArray(item.backupUrl)) item.backupUrl.forEach(push);
      if (Array.isArray(item.backup_url)) item.backup_url.forEach(push);
    });

    return urls;
  }

  /** 从 __playinfo__ 取 DASH 音轨地址（视频轨无声时用） */
  function pickAudioUrl(playinfo) {
    const audios = Array.isArray(playinfo?.data?.dash?.audio)
      ? [...playinfo.data.dash.audio].sort((a, b) => (b.bandwidth || 0) - (a.bandwidth || 0))
      : [];
    for (const item of audios) {
      const url = X.absolutize(item.baseUrl || item.base_url);
      if (url) return url;
    }
    return "";
  }

  /** 估算大小：durl 自带 size；dash 用 bandwidth × duration 粗估 */
  function pickSize(playinfo) {
    const data = playinfo?.data || {};
    const durl = Array.isArray(data.durl) ? [...data.durl].sort((a, b) => (b.size || 0) - (a.size || 0)) : [];
    if (durl[0]?.size) return durl[0].size;
    const best = Array.isArray(data.dash?.video)
      ? [...data.dash.video].sort((a, b) => (b.bandwidth || 0) - (a.bandwidth || 0))[0]
      : null;
    if (best?.bandwidth && data.timelength) return Math.round((best.bandwidth / 8) * (data.timelength / 1000));
    return 0;
  }

  function pickTitle() {
    const raw = document.title || "";
    // "标题_哔哩哔哩_bilibili" → 取第一段
    return raw.split("_哔哩哔哩")[0].split("-哔哩哔哩")[0].trim() || "哔哩哔哩视频";
  }

  function pickAuthor() {
    // UP 主名字节点（class 可能变，失败就放弃）
    try {
      const node = document.querySelector(".up-name, .username, [class*='up-name']");
      if (node?.textContent?.trim()) return node.textContent.trim().slice(0, 40);
    } catch (error) {
      /* 忽略 */
    }
    return document.querySelector('meta[name="author"]')?.getAttribute("content")?.trim() || "";
  }

  function detect() {
    const playinfo = window.__playinfo__;
    if (!playinfo || typeof playinfo !== "object") return null;

    const videoUrls = collectBiliUrls(playinfo);
    if (!videoUrls.length) return null;

    const title = pickTitle();
    const author = pickAuthor();
    const cover = document.querySelector('meta[property="og:image"]')?.getAttribute("content") || "";

    return {
      // 去重键：去掉查询参数（B 站直链参数含过期时间，同一视频每次刷新的参数不同）
      shareUrl: location.href.split("?")[0] + "#" + videoUrls[0].split("?")[0].slice(-32),
      title,
      desc: title,
      author: author || "哔哩哔哩UP主",
      cover,
      duration: Math.round((playinfo?.data?.timelength || 0) / 1000),
      type: "视频",
      size: pickSize(playinfo),
      videoUrls,
      audioUrl: pickAudioUrl(playinfo), // DASH 音轨：视频轨无声时 content.js 会单独下载
      fileName: "",
    };
  }

  X.create("bilibili", {
    platformName: "哔哩哔哩",
    pollInterval: 1500,
    detect,
  });
})();
