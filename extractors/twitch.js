/**
 * extractors/twitch.js —— Twitch 主世界抓取脚本
 *
 * 数据源：Twitch GQL API 获取播放 token，再拼接 usher.ttvnw.net 的 m3u8 地址。
 *   参考开源实现：zeal-arch/detector/extractors/sites/twitch.js —— 用 Twitch
 *   网页版公开 client-id 调 gql.twitch.tv 的 PlaybackAccessToken 接口：
 *   - 直播：https://usher.ttvnw.net/api/channel/hls/{channel}.m3u8?token=...&sig=...
 *   - 录播：https://usher.ttvnw.net/vod/{vodId}.m3u8?token=...&sig=...
 *
 * 注意：token 拉取是异步的，detect() 触发后缓存结果，下次轮询上报。
 * 主世界 fetch 可能被页面 CSP 拦截，失败时降级为通用嗅探。
 */

"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  // Twitch 网页版公开 client-id（twitch.tv 自身在用的）
  const CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko";
  const GQL_URL = "https://gql.twitch.tv/gql";
  const TOKEN_HASH = "3093517e37e4f4cb48906155bcd02f85e52e5f5cb03ddb43e0cba5e3cbe2ecdc";

  /** 从 URL 判断是直播 / 录播 / 剪辑 */
  function parseTwitchUrl(url) {
    const text = String(url || "");
    let match = text.match(/twitch\.tv\/videos\/(\d+)/);
    if (match) return { kind: "vod", id: match[1] };
    match = text.match(/twitch\.tv\/[^/]+\/clip\/([^/?#]+)/);
    if (match) return { kind: "clip", id: match[1] };
    match = text.match(/clips\.twitch\.tv\/([^/?#]+)/);
    if (match) return { kind: "clip", id: match[1] };
    match = text.match(/twitch\.tv\/([a-zA-Z0-9_]+)/);
    if (match && !["videos", "directory", "search", "settings"].includes(match[1])) {
      return { kind: "live", id: match[1] };
    }
    return null;
  }

  function buildHlsUrl(parsed, token) {
    const params =
      `?token=${encodeURIComponent(token.value)}` +
      `&sig=${token.signature}` +
      `&allow_source=true&allow_audio_only=true` +
      `&p=${Math.floor(Math.random() * 999999)}`;
    if (parsed.kind === "live") {
      return `https://usher.ttvnw.net/api/channel/hls/${parsed.id}.m3u8${params}`;
    }
    return `https://usher.ttvnw.net/vod/${parsed.id}.m3u8${params}`;
  }

  function getMeta() {
    let title = "";
    let cover = "";
    try {
      const ogTitle = document.querySelector('meta[property="og:title"]');
      if (ogTitle) title = ogTitle.content || "";
      const ogImage = document.querySelector('meta[property="og:image"]');
      if (ogImage) cover = ogImage.content || "";
    } catch (error) { /* 忽略 */ }
    return { title, cover };
  }

  // ---- 异步 token 缓存 ----
  let cachedKey = "";
  let cachedMedia = null;
  let fetchingKey = "";

  function ensureToken(parsed) {
    const key = `${parsed.kind}_${parsed.id}`;
    if (key === cachedKey || key === fetchingKey) return;
    fetchingKey = key;

    const isLive = parsed.kind === "live";
    fetch(GQL_URL, {
      method: "POST",
      headers: { "Client-Id": CLIENT_ID, "Content-Type": "application/json" },
      body: JSON.stringify({
        operationName: "PlaybackAccessToken",
        variables: {
          isLive,
          login: isLive ? parsed.id : "",
          isVod: !isLive,
          vodID: isLive ? "" : parsed.id,
          playerType: "site",
          platform: "web",
        },
        extensions: { persistedQuery: { version: 1, sha256Hash: TOKEN_HASH } },
      }),
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        const token = isLive
          ? data?.data?.streamPlaybackAccessToken
          : data?.data?.videoPlaybackAccessToken;
        if (!token) return;
        const hlsUrl = buildHlsUrl(parsed, token);
        const meta = getMeta();
        const label = parsed.kind === "live" ? "直播" : "录播";
        const title = meta.title || `twitch_${label}_${parsed.id}`;
        cachedKey = key;
        cachedMedia = {
          shareUrl: location.href,
          title: X.safeText(title).trim(),
          desc: "",
          author: parsed.kind === "live" ? parsed.id : "未知作者",
          cover: X.absolutize(meta.cover),
          duration: 0,
          size: 0,
          type: "视频",
          fileName: X.sanitizeFileName(`twitch_${parsed.id}_${title.trim()}`.slice(0, 64)) || `twitch_${parsed.id}`,
          videoUrl: hlsUrl,
          videoUrls: [hlsUrl],
          audioUrl: "",
          imageUrls: [],
        };
      })
      .catch(() => { /* CSP 拦截或网络失败，降级走通用嗅探 */ })
      .finally(() => { fetchingKey = ""; });
  }

  function detect() {
    const parsed = parseTwitchUrl(location.href);
    if (!parsed || parsed.kind === "clip") return null; // 剪辑走通用嗅探（多为直链 MP4）
    const key = `${parsed.kind}_${parsed.id}`;
    if (key === cachedKey && cachedMedia) return cachedMedia;
    ensureToken(parsed);
    return key === cachedKey ? cachedMedia : null;
  }

  X.create("twitch", {
    platformName: "Twitch",
    pollInterval: 2000,
    detect,
  });
})();
