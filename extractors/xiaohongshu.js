/**
 * extractors/xiaohongshu.js —— 小红书主世界抓取脚本
 *
 * 数据源：window.__INITIAL_STATE__.note.noteDetailMap[<id>].note.video。
 * 视频流：note.video.media.stream.h264[] / h265[]，取 masterUrl，
 * h265 优先（同清晰度下体积更小），取不到再回退 h264。
 *
 * 页面结构经常微调，因此用“字符串扫描”而非“固定路径取值”：
 * 先对 __INITIAL_STATE__ 做 JSON 序列化再扫 masterUrl，
 * 即使嵌套层级变了也能命中。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  /** 在任意对象里递归找 masterUrl（h265 优先） */
  function findMasterUrls(root) {
    const h265 = [];
    const h264 = [];
    const seen = new Set();

    function visit(node, inH265, inH264) {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) {
        node.forEach((item) => visit(item, inH265, inH264));
        return;
      }
      for (const [key, value] of Object.entries(node)) {
        if (typeof value === "string" && key === "masterUrl" && value.startsWith("http")) {
          const url = X.absolutize(value);
          if (!seen.has(url)) {
            seen.add(url);
            (inH265 ? h265 : inH264 ? h264 : h265).push(url);
          }
        } else if (value && typeof value === "object") {
          visit(value, inH265 || key === "h265", inH264 || key === "h264");
        }
      }
    }

    try {
      visit(root, false, false);
    } catch (error) {
      /* 结构异常时放弃 */
    }
    return [...h265, ...h264];
  }

  /** 兜底：直接扫描脚本 JSON 文本里的 masterUrl */
  function scanMasterUrls() {
    return X.scanScriptUrls(/"masterUrl"\s*:\s*"((?:https?:)?\/\/[^"]+)"/g);
  }

  function pickNote(state) {
    try {
      const map = state?.note?.noteDetailMap;
      if (map && typeof map === "object") {
        const first = Object.values(map)[0];
        return first?.note || null;
      }
    } catch (error) {
      /* 忽略 */
    }
    return null;
  }

  function detect() {
    const state = window.__INITIAL_STATE__;
    const note = pickNote(state);

    let videoUrls = state ? findMasterUrls(state) : [];
    if (!videoUrls.length) videoUrls = scanMasterUrls();
    // 最后兜底：正在播放的 video 元素
    if (!videoUrls.length) {
      videoUrls = X.collectVideoElementUrls().filter((url) => !url.startsWith("blob:"));
    }
    if (!videoUrls.length) return null;

    const title = X.safeText(note?.title).trim() || X.safeText(note?.desc).trim().slice(0, 60) || "小红书视频";
    const author = X.safeText(note?.user?.nickname).trim() || X.safeText(note?.user?.nickName).trim();
    const cover = document.querySelector('meta[property="og:image"]')?.getAttribute("content") || "";

    return {
      // 去重键用视频地址：信息流切视频时页面 URL 不变，靠地址区分不同视频
      shareUrl: videoUrls[0] || location.href.split("?")[0],
      title,
      desc: X.safeText(note?.desc),
      author: author || "小红书用户",
      cover,
      type: "视频",
      videoUrls,
      fileName: "",
    };
  }

  X.create("xiaohongshu", {
    platformName: "小红书",
    pollInterval: 1500,
    detect,
  });
})();
