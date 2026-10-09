/**
 * content.js —— 运行在页面「隔离世界」的内容脚本
 *
 * 职责：
 *   1. 接收各平台主世界抓取脚本（extractors/*.js）上报的媒体数据，去重建档；
 *   2. 通用嗅探：扫描页面 <video> 元素 + PerformanceResourceTiming，
 *      任意网站的直链 / m3u8 都能被发现（平台未覆盖时就靠它）；
 *   3. 在页面内创建可拖拽 / 可折叠的浮层面板（Shadow DOM + iframe 加载 panel.html）；
 *   4. 执行下载：直链多线程并发 fetch；m3u8 分段下载后合并；保存到浏览器默认下载目录；
 *   5. 与 background.js 配合，响应扩展图标的显示面板请求。
 *
 * 设计要点：
 *   - 抓取只读页面、下载只走本脚本：隔离世界拥有 host_permissions，
 *     fetch 不受跨域限制；主世界抓取脚本绝不碰下载（会被页面 CSP 拦截）。
 *   - 面板用 iframe 承载，样式与页面完全隔离；关闭面板只做 display:none，
 *     正在进行的下载不会中断。
 *   - 下载成功的条目写入 localStorage，刷新页面后依然标记「已完成」。
 *   - 平台识别 / 媒体判定 / 文件名等规则全部来自 rules.js，本文件只做执行。
 */

(() => {
  "use strict";

  if (window.__VD_CONTENT_READY__) return;
  window.__VD_CONTENT_READY__ = true;

  /* ================================================================== */
  /* 一、平台识别与媒体列表                                              */
  /* ================================================================== */

  /** 当前页面命中的平台（rules.js） */
  const currentPlatform = detectPlatform(location.href);

  /** @type {Array<Object>} 去重后的媒体列表（最新的排在最前） */
  const mediaList = [];

  const SETTINGS_KEY = "vd_settings";
  const PANEL_POSITION_KEY = "vd_panel_position";
  let extensionSettings = {
    ...DEFAULT_SETTINGS,
    platformStrategies: { ...DEFAULT_SETTINGS.platformStrategies },
  };

  function applyStoredSettings(value) {
    extensionSettings = {
      ...DEFAULT_SETTINGS,
      ...(value || {}),
      platformStrategies: {
        ...DEFAULT_SETTINGS.platformStrategies,
        ...((value && value.platformStrategies) || {}),
      },
    };
  }

  chrome.storage.local.get(SETTINGS_KEY).then((data) => applyStoredSettings(data[SETTINGS_KEY])).catch(() => {});
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local" || !changes[SETTINGS_KEY]) return;
    applyStoredSettings(changes[SETTINGS_KEY].newValue);
    if (mediaList.length) {
      requestCurrentMedia();
      pushMediaList();
    }
  });

  function resolvedStrategy() {
    const override = extensionSettings.platformStrategies?.[currentPlatform.id];
    return override || currentPlatform.defaultStrategy || "sniff";
  }

  function sourceIsExtractor(source) {
    return source === "页面解析";
  }

  function preferredSource(source) {
    const strategy = resolvedStrategy();
    return strategy === "extractor" ? sourceIsExtractor(source) : !sourceIsExtractor(source);
  }

  function variantHeight(variant) {
    if (Number(variant?.height) > 0) return Number(variant.height);
    const match = String(variant?.label || "").match(/(\d{3,4})p?/i);
    return match ? Number(match[1]) : 0;
  }

  function variantIsMp4(variant) {
    return /mp4/i.test(String(variant?.mimeType || variant?.format || variant?.url || ""));
  }

  function sortedVariants(media) {
    const variants = Array.isArray(media?.variants) ? media.variants.filter((variant) => variant?.url) : [];
    const target = Number(extensionSettings.quality);
    return variants.slice().sort((a, b) => {
      if (extensionSettings.format === "mp4") {
        const formatDelta = Number(variantIsMp4(b)) - Number(variantIsMp4(a));
        if (formatDelta) return formatDelta;
      }
      const ah = variantHeight(a);
      const bh = variantHeight(b);
      if (extensionSettings.quality === "smallest") {
        const as = Number(a.size) || Number.MAX_SAFE_INTEGER;
        const bs = Number(b.size) || Number.MAX_SAFE_INTEGER;
        if (as !== bs) return as - bs;
        return ah - bh;
      }
      if (target > 0) {
        const ad = ah ? Math.abs(ah - target) : Number.MAX_SAFE_INTEGER;
        const bd = bh ? Math.abs(bh - target) : Number.MAX_SAFE_INTEGER;
        if (ad !== bd) return ad - bd;
      }
      if (ah !== bh) return bh - ah;
      return (Number(b.bitrate) || 0) - (Number(a.bitrate) || 0);
    });
  }

  function preferredVariant(media) {
    return sortedVariants(media)[0] || null;
  }

  function downloadCandidateUrls(media) {
    const urls = [];
    const push = (url) => {
      const text = String(url || "");
      if (text && !urls.includes(text)) urls.push(text);
    };
    sortedVariants(media).forEach((variant) => push(variant.url));
    (media.videoUrls || []).forEach(push);
    push(media.videoUrl);
    return urls;
  }

  /**
   * 已下载成功的 shareUrl 集合，持久化在页面域名的 localStorage。
   * 刷新页面后「已完成」标记依然保留，下次批量下载不再重复选中。
   */
  const DOWNLOADED_STORE_KEY = "__vd_downloaded_urls__";
  const DOWNLOADED_STORE_LIMIT = 1000;

  function loadDownloadedSet() {
    try {
      const raw = localStorage.getItem(DOWNLOADED_STORE_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(list) ? list : []);
    } catch (error) {
      console.warn("[视频下载] 读取下载记录失败：", error);
      return new Set();
    }
  }

  function persistDownloadedSet() {
    try {
      const list = Array.from(downloadedSet).slice(-DOWNLOADED_STORE_LIMIT);
      localStorage.setItem(DOWNLOADED_STORE_KEY, JSON.stringify(list));
    } catch (error) {
      console.warn("[视频下载] 写入下载记录失败：", error);
    }
  }

  const downloadedSet = loadDownloadedSet();

  function markDownloaded(shareUrl) {
    if (!shareUrl || downloadedSet.has(shareUrl)) return;
    downloadedSet.add(shareUrl);
    persistDownloadedSet();
  }

  /** 按 URL 在列表里找媒体（同时比对 shareUrl 与全部候选地址） */
  function findMediaByUrl(url) {
    if (!url) return null;
    return (
      mediaList.find(
        (item) =>
          item.shareUrl === url ||
          item.videoUrl === url ||
          (Array.isArray(item.videoUrls) && item.videoUrls.includes(url)) ||
          (Array.isArray(item.variants) && item.variants.some((variant) => variant?.url === url))
      ) || null
    );
  }


  /* Verify actual response bytes before admitting an item to either UI. */
  const mediaProbeCache = new Map();
  const mediaProbeQueue = [];
  let activeMediaProbes = 0;

  /**
   * Read a bounded prefix even when a CDN ignores Range and responds with
   * HTTP 200 for the entire file. Never call response.blob() for a probe.
   * @param {Response} response Partial (or full) HTTP response.
   * @param {number} limit Maximum bytes to retain.
   * @returns {Promise<Uint8Array>} Leading payload bytes.
   */
  async function readProbeBytes(response, limit) {
    if (!response.body) return new Uint8Array();
    const reader = response.body.getReader();
    const chunks = [];
    let length = 0;
    try {
      while (length < limit) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = value.subarray(0, limit - length);
        chunks.push(chunk);
        length += chunk.length;
      }
    } finally {
      reader.cancel().catch(() => {}); // Reject the rest of an ignored Range response.
    }
    const result = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      result.set(chunk, offset);
      offset += chunk.length;
    }
    return result;
  }

  /**
   * Fetch at most 16 KiB to confirm actual video bytes/MIME or a video HLS
   * manifest. A missing/incorrect Content-Type must be backed by a signature.
   * @param {string} url Candidate HTTP(S) source.
   * @returns {Promise<object|null>} Validated source metadata or null.
   */
  async function probeMediaUrl(url) {
    const source = safePreviewUrl(url);
    if (!source || shouldIgnoreUrl(source)) return null;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), DOWNLOAD_RULES.MEDIA_PROBE_TIMEOUT_MS);
    try {
      const response = await fetch(source, {
        credentials: "omit",
        headers: { Range: "bytes=0-" + (DOWNLOAD_RULES.MEDIA_PROBE_MAX_BYTES - 1) },
        signal: controller.signal,
      });
      if (!response.ok) return null;
      const mime = response.headers.get("content-type") || "";
      const playlist = isPlaylistUrl(source) || /mpegurl/i.test(mime);
      if (!playlist && isRejectedMediaMimeType(mime)) return null;

      const bytes = await readProbeBytes(response, DOWNLOAD_RULES.MEDIA_PROBE_MAX_BYTES);
      if (!bytes.length) return null;
      if (playlist) {
        const text = new TextDecoder().decode(bytes);
        if (!isHlsVideoPlaylist(text)) return null;
      } else {
        const signature = sniffVideoSignature(bytes);
        if (signature === "invalid") return null;
        if (signature !== "video" && !isVideoMimeType(mime)) return null;
      }

      const rangeTotal = Number((response.headers.get("content-range") || "").match(/\/(\d+)$/)?.[1] || 0);
      const contentLength = Number(response.headers.get("content-length")) || 0;
      return {
        url: source,
        size: rangeTotal || (response.status === 200 && !playlist ? contentLength : 0),
        kind: playlist ? "hls" : "video",
      };
    } catch (error) {
      // A temporary failure remains invisible rather than being listed as a video.
      return null;
    } finally {
      clearTimeout(timer);
      controller.abort();
    }
  }

  /** Bound network probes globally per tab, with one in-flight probe per URL. */
  function queueMediaProbe(url) {
    const cached = mediaProbeCache.get(url);
    if (cached && Date.now() - cached.time < 45_000) return cached.promise;
    const promise = new Promise((resolve) => {
      mediaProbeQueue.push({ url, resolve });
      drainMediaProbes();
    });
    mediaProbeCache.set(url, { promise, time: Date.now() });
    return promise;
  }

  /** Dispatch only a few 16 KiB probes at a time to avoid slowing the page. */
  function drainMediaProbes() {
    while (activeMediaProbes < DOWNLOAD_RULES.MEDIA_PROBE_CONCURRENCY && mediaProbeQueue.length) {
      const job = mediaProbeQueue.shift();
      activeMediaProbes++;
      probeMediaUrl(job.url).then(job.resolve, () => job.resolve(null)).finally(() => {
        activeMediaProbes--;
        drainMediaProbes();
      });
    }
  }

  /**
   * A gallery, audio URL, or unverified source must not become a selectable
   * video. Reject stale asynchronous results when a new extractor URL arrives.
   * @param {object} media Internal mutable media record.
   * @returns {void}
   */
  function verifyMediaRecord(media) {
    const candidates = downloadCandidateUrls(media)
      .filter((url) => safePreviewUrl(url) && !shouldIgnoreUrl(url))
      .filter((url) => sourceIsExtractor(media.source) || isWorthCollecting(url))
      .slice(0, 4);
    const signature = media.type === "图集" ? "" : candidates.join("\n");
    if (media.verificationSignature === signature && media.verificationState) return;
    media.verificationSignature = signature;
    media.verificationState = "pending";
    media.verifiedUrl = "";
    const generation = (media.verificationGeneration || 0) + 1;
    media.verificationGeneration = generation;

    if (!signature) {
      media.verificationState = "rejected";
      return;
    }

    (async () => {
      let verified = null;
      for (const url of candidates) {
        verified = await queueMediaProbe(url);
        if (media.verificationGeneration !== generation) return;
        if (verified) break;
      }
      if (media.verificationGeneration !== generation) return;
      media.verificationState = verified ? "verified" : "rejected";
      if (verified) {
        media.verifiedUrl = verified.url;
        if (verified.size > 0) media.verifiedSize = verified.size;
      }
      pushMediaList();
    })().catch((error) => console.warn("[视频下载] 媒体验证失败：", error));
  }

  /**
   * 媒体入库（去重 + 合并）。
   * 抓取脚本上报的元数据最丰富，嗅探到的只有 URL：同一条目多次出现时，
   * 只补全空字段、合并候选地址，不覆盖已有信息。
   */
  function upsertMedia(incoming) {
    if (!incoming || !incoming.shareUrl) return;

    const existing = findMediaByUrl(incoming.shareUrl) || findMediaByUrl(incoming.videoUrl);
    if (existing) {
      // Dedicated extractors know the real post title/cover; generic sniffing only knows the page.
      // When both describe the same media URL, prefer extractor metadata instead of keeping a
      // generic document.title such as "抖音-记录美好生活".
      const incomingIsExtractor = sourceIsExtractor(incoming.source);
      const existingIsExtractor = sourceIsExtractor(existing.source);
      const preferIncomingMetadata = incomingIsExtractor && !existingIsExtractor;
      const preferIncomingUrls = preferredSource(incoming.source) && !preferredSource(existing.source);

      for (const key of ["title", "desc", "author", "cover", "fileName"]) {
        if (incoming[key] && (!existing[key] || preferIncomingMetadata)) existing[key] = incoming[key];
      }
      if (incoming.size && (!existing.size || preferIncomingMetadata)) existing.size = incoming.size;
      if (incoming.duration && (!existing.duration || preferIncomingMetadata)) existing.duration = incoming.duration;

      const incomingUrls = [...(incoming.videoUrls || [])];
      if (incoming.videoUrl) incomingUrls.unshift(incoming.videoUrl);
      const orderedUrls = preferIncomingUrls
        ? [...incomingUrls, ...(existing.videoUrls || [])]
        : [...(existing.videoUrls || []), ...incomingUrls];
      existing.videoUrls = [...new Set(orderedUrls.filter(Boolean))];

      const variantMap = new Map();
      const orderedVariants = preferIncomingUrls
        ? [...(incoming.variants || []), ...(existing.variants || [])]
        : [...(existing.variants || []), ...(incoming.variants || [])];
      orderedVariants.forEach((variant) => {
        if (variant?.url && !variantMap.has(variant.url)) variantMap.set(variant.url, variant);
      });
      existing.variants = [...variantMap.values()];
      if (!existing.videoUrl && incoming.videoUrl) existing.videoUrl = incoming.videoUrl;

      const images = new Set([...(existing.imageUrls || []), ...(incoming.imageUrls || [])]);
      existing.imageUrls = [...images];

      if (preferIncomingMetadata) {
        existing.shareUrl = incoming.shareUrl || existing.shareUrl;
        existing.source = incoming.source;
        existing.platformId = incoming.platformId || existing.platformId;
        existing.platform = incoming.platform || existing.platform;
        if (incoming.audioUrl) existing.audioUrl = incoming.audioUrl;
        if (incoming.originVid) existing.originVid = incoming.originVid;
        if (downloadedSet.has(existing.shareUrl)) {
          existing.status = "done";
          existing.progress = 100;
        }
      }

      verifyMediaRecord(existing);
      pushMediaList();
      return;
    }

    const alreadyDownloaded = downloadedSet.has(incoming.shareUrl);
    mediaList.unshift({
      platformId: currentPlatform.id,
      platform: currentPlatform.name,
      source: "网络嗅探",
      title: "",
      desc: "",
      author: "",
      cover: "",
      duration: 0,
      size: 0,
      type: "视频",
      videoUrls: [],
      variants: [],
      imageUrls: [],
      audioUrl: "",
      ...incoming,
      status: alreadyDownloaded ? "done" : "idle",
      progress: alreadyDownloaded ? 100 : 0,
    });
    const item = mediaList[0];
    item.videoUrl = item.videoUrl || (item.videoUrls[0] || "");
    // 文件名兜底：抓取脚本没给时按通用规则拼「平台_标题_时间戳」
    if (!item.fileName) {
      item.fileName = buildFileName(item.platform || currentPlatform.name, item.author, item.title);
    }
    verifyMediaRecord(item);
    pushMediaList();
  }

  /** 接收主世界抓取脚本上报的媒体数据 */
  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== "vd-extractor" || data.type !== "media_found") return;
    if (!data.media || !data.media.shareUrl) return;
    upsertMedia(data.media);
  });

  /** 主动向主世界索要当前媒体（面板打开 / 页面加载时补抓） */
  function requestCurrentMedia() {
    window.postMessage({ source: "vd-content", type: "request_current_media" }, "*");
  }

  /* ================================================================== */
  /* 二、通用嗅探：DOM 扫描 + 资源监听                                   */
  /* ================================================================== */

  /** DOM 里 video 元素的地址是否值得收录 */
  function isWorthCollecting(url) {
    if (!url) return false;
    if (url.startsWith("blob:") || url.startsWith("data:")) return false; // 临时地址，无法下载
    if (shouldIgnoreUrl(url)) return false; // 广告 / 埋点 / 缩略图
    // Playback fragments and audio are not independently downloadable videos.
    if (/\.(?:ts|m4s|m4a|mp3|aac|wav|ogg|vtt|jpg|jpeg|png|webp)(?:[?#]|$)/i.test(url)) return false;
    return /^https?:\/\//i.test(url);
  }

  /** 从 <video> 元素收集候选地址 */
  function collectDomVideoUrls() {
    const urls = [];
    document.querySelectorAll("video").forEach((video) => {
      const candidates = [
        video.currentSrc,
        video.src,
        ...Array.from(video.querySelectorAll("source")).map((s) => s.src),
      ];
      for (const url of candidates) {
        const text = String(url || "").trim();
        if (isWorthCollecting(text) && !urls.includes(text)) urls.push(text);
      }
    });
    // og:video / twitter:player:stream（通用规则：很多站点会带）
    document
      .querySelectorAll('meta[property="og:video"], meta[name="twitter:player:stream"]')
      .forEach((meta) => {
        const text = String(meta.getAttribute("content") || "").trim();
        if (isWorthCollecting(text) && !urls.includes(text)) urls.push(text);
      });
    return urls;
  }

  /** DOM 扫描：把页面上的 video 元素收录进列表 */
  function scanDomVideos() {
    let changed = false;
    for (const url of collectDomVideoUrls()) {
      if (findMediaByUrl(url)) continue;
      const title = (document.title || "").trim().slice(0, 60) || "页面视频";
      upsertMedia({
        shareUrl: url,
        title,
        desc: title,
        type: isPlaylistUrl(url) ? "直播流" : "视频",
        videoUrls: [url],
        videoUrl: url,
        source: "DOM嗅探",
      });
      changed = true;
    }
    return changed;
  }

  /**
   * 资源嗅探：PerformanceResourceTiming 能看到页面加载过的所有资源 URL，
   * 包括 XHR / fetch 拉取的视频分段与直链。这是“通用下载”的核心：
   * 即使没有任何平台抓取脚本，只要视频在页面里播过，地址就会出现在这里。
   */
  function startResourceSniffing() {
    const seen = new Set();

    function handleEntries(entries) {
      let changed = false;
      for (const entry of entries) {
        const url = entry.name;
        if (!url || seen.has(url)) continue;
        seen.add(url);
        if (!isMediaUrl(url) || !isWorthCollecting(url)) continue;
        if (findMediaByUrl(url)) continue;

        const title = (document.title || "").trim().slice(0, 60) || "嗅探到的视频";
        upsertMedia({
          shareUrl: url,
          title,
          desc: title,
          type: isPlaylistUrl(url) ? "直播流" : "视频",
          videoUrls: [url],
          videoUrl: url,
          source: "网络嗅探",
        });
        changed = true;
      }
      if (changed) pushMediaList();
    }

    try {
      // 先收录页面加载至今的资源（buffered），再监听新增
      handleEntries(performance.getEntriesByType("resource"));
      const observer = new PerformanceObserver((list) => handleEntries(list.getEntries()));
      observer.observe({ type: "resource", buffered: false });
    } catch (error) {
      console.warn("[视频下载] 资源嗅探不可用：", error);
    }
  }

  /* ================================================================== */
  /* 三、页面内浮层面板                                                  */
  /* ================================================================== */

  const HOST_ID = "__vd_panel_host__";

  let host = null;
  let panelFrame = null;
  let activePreviewShareUrl = "";
  const panelState = { collapsed: false, contentHeight: 144, suppressToggleClickUntil: 0 };

  function buildPanel() {
    if (host) return;

    host = document.createElement("div");
    host.id = HOST_ID;
    host.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;";

    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <style>
        .vd-panel {
          --vd-width: 320px;
          --vd-max-height: 480px;
          position: fixed;
          top: 16px;
          right: 16px;
          width: var(--vd-width);
          max-width: calc(100vw - 24px);
          max-height: calc(100dvh - 24px);
          box-sizing: border-box;
          contain: layout paint;
          display: flex;
          flex-direction: column;
          background: #ffffff;
          border: 1px solid rgba(15, 23, 42, .12);
          border-radius: 12px;
          box-shadow: 0 12px 32px rgba(15, 23, 42, .18);
          overflow: hidden;
          font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
          color: #111827;
        }
        .vd-panel__bar {
          flex: 0 0 36px;
          height: 36px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 6px 0 12px;
          background: #ffffff;
          border-bottom: 1px solid #e5e7eb;
          cursor: grab;
          touch-action: none;
          user-select: none;
        }
        .vd-panel__title {
          display: flex;
          align-items: center;
          gap: 6px;
          min-width: 0;
          font-size: 12px;
          font-weight: 600;
        }
        .vd-panel__version {
          font-size: 10px;
          font-weight: 400;
          color: #9ca3af;
        }
        .vd-panel__actions {
          display: flex;
          align-items: center;
          gap: 2px;
        }
        .vd-panel__btn {
          width: 24px;
          height: 24px;
          border: 0;
          border-radius: 6px;
          background: transparent;
          color: #6b7280;
          font-size: 14px;
          line-height: 1;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .vd-panel__btn:hover {
          background: #f3f4f6;
          color: #111827;
        }
        .vd-panel__body {
          position: relative;
          flex: 0 1 auto;
          height: 96px;
          max-height: min(var(--vd-max-height), calc(100dvh - 68px));
          min-height: 0;
          overflow: hidden;
          background: #ffffff;
        }
        .vd-panel.collapsed {
          width: 52px;
          height: 52px;
          border-radius: 50%;
          border-color: rgba(79, 70, 229, .18);
          background: #fff;
          box-shadow: 0 6px 22px rgba(15, 23, 42, .22), 0 2px 8px rgba(79, 70, 229, .14);
        }
        .vd-panel.collapsed .vd-panel__bar {
          width: 100%;
          height: 100%;
          flex: 1 0 auto;
          padding: 0;
          border: 0;
          justify-content: center;
          cursor: grab;
        }
        .vd-panel.collapsed .vd-panel__title,
        .vd-panel.collapsed #vd-settings,
        .vd-panel.collapsed #vd-close,
        .vd-panel.collapsed .vd-panel__body {
          display: none;
        }
        .vd-panel.collapsed .vd-panel__actions {
          width: 100%;
          height: 100%;
        }
        .vd-panel.collapsed #vd-collapse {
          width: 100%;
          height: 100%;
          border-radius: 50%;
          background: #fff;
          color: #4f46e5;
          cursor: grab;
        }
        .vd-panel.collapsed #vd-collapse:hover {
          background: #f5f3ff;
          color: #4338ca;
        }
        .vd-panel.is-dragging .vd-panel__bar,
        .vd-panel.is-dragging #vd-collapse { cursor: grabbing; }
        #vd-collapse:focus-visible { outline: 3px solid rgba(79, 70, 229, .35); outline-offset: -3px; }
        #vd-collapse .vd-collapse__download { display: none; }
        .vd-panel.collapsed #vd-collapse .vd-collapse__minus { display: none; }
        .vd-panel.collapsed #vd-collapse .vd-collapse__download { display: block; }
        .vd-panel__frame {
          width: 100%;
          height: 100%;
          border: 0;
          display: block;
          background: #ffffff;
        }
        .vd-panel__loading {
          position: absolute;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #ffffff;
          color: #9ca3af;
          font-size: 12px;
        }
        /* The preview is a sibling of .vd-panel, never inside its iframe
           or its contain: paint boundary. It uses the viewport directly. */
        .vd-preview {
          position: fixed;
          inset: 0;
          z-index: 2;
          width: 100vw;
          height: 100dvh;
          display: flex;
          align-items: center;
          justify-content: center;
          box-sizing: border-box;
          padding: 16px;
          font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
        }
        .vd-preview[hidden] { display: none; }
        .vd-preview__backdrop {
          position: absolute;
          inset: 0;
          background: rgba(15, 23, 42, .7);
          backdrop-filter: blur(3px);
        }
        .vd-preview__dialog {
          position: relative;
          display: flex;
          flex-direction: column;
          width: min(840px, calc(100vw - 32px));
          max-height: calc(100dvh - 32px);
          box-sizing: border-box;
          overflow: hidden;
          border: 1px solid rgba(255, 255, 255, .1);
          border-radius: 14px;
          background: #fff;
          box-shadow: 0 24px 80px rgba(0, 0, 0, .32);
        }
        .vd-preview__header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          padding: 12px 16px;
          border-bottom: 1px solid #e5e7eb;
        }
        .vd-preview__title {
          min-width: 0;
          margin: 0;
          overflow: hidden;
          color: #111827;
          font-size: 14px;
          font-weight: 600;
          line-height: 1.5;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .vd-preview__close {
          display: grid;
          flex: 0 0 32px;
          place-items: center;
          width: 32px;
          height: 32px;
          padding: 0;
          border: 1px solid #e5e7eb;
          border-radius: 8px;
          background: #fff;
          color: #475569;
          font-size: 22px;
          line-height: 1;
          cursor: pointer;
        }
        .vd-preview__close:hover { background: #f8fafc; color: #0f172a; }
        .vd-preview__close:focus-visible { outline: 3px solid #c7d2fe; }
        .vd-preview__stage {
          display: flex;
          min-height: 120px;
          align-items: center;
          justify-content: center;
          overflow: hidden;
          background: #0b1020;
        }
        .vd-preview__video {
          display: block;
          width: 100%;
          height: min(62dvh, 540px);
          max-height: calc(100dvh - 145px);
          object-fit: contain;
          background: #0b1020;
        }
        .vd-preview__video[hidden], .vd-preview__message[hidden] { display: none; }
        .vd-preview__message {
          margin: 0;
          padding: 36px 24px;
          color: #e2e8f0;
          font-size: 13px;
          line-height: 1.7;
          text-align: center;
        }
        .vd-preview__note {
          margin: 0;
          padding: 11px 16px 14px;
          color: #94a3b8;
          font-size: 11px;
          line-height: 1.6;
        }
      </style>
      <div class="vd-panel" id="vd-panel">
        <div class="vd-panel__bar" id="vd-bar">
          <span class="vd-panel__title">视频下载 <span class="vd-panel__version">v${chrome.runtime.getManifest().version}</span></span>
          <span class="vd-panel__actions">
            <button class="vd-panel__btn" id="vd-settings" title="设置" aria-label="设置">
              <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="10" cy="10" r="3"/><path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M15.3 4.7l-1.4 1.4M6.1 13.9l-1.4 1.4"/></svg>
            </button>
            <button class="vd-panel__btn" id="vd-collapse" title="折叠 / 展开" aria-label="折叠 / 展开">
              <span class="vd-collapse__minus">−</span>
              <svg class="vd-collapse__download" viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 4v15m0 0 6-6m-6 6-6-6" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
            <button class="vd-panel__btn" id="vd-close" title="关闭面板（不影响正在进行的下载）">✕</button>
          </span>
        </div>
        <div class="vd-panel__body">
          <div class="vd-panel__loading" id="vd-loading">正在加载…</div>
        </div>
      </div>
      <div class="vd-preview" id="vd-preview" role="dialog" aria-modal="true"
        aria-labelledby="vd-preview-title" hidden>
        <div class="vd-preview__backdrop" id="vd-preview-backdrop"></div>
        <section class="vd-preview__dialog" aria-label="视频预览播放器">
          <header class="vd-preview__header">
            <h2 class="vd-preview__title" id="vd-preview-title">视频预览</h2>
            <button class="vd-preview__close" id="vd-preview-close" type="button" aria-label="关闭预览">×</button>
          </header>
          <div class="vd-preview__stage">
            <video class="vd-preview__video" id="vd-preview-video" controls
              playsinline preload="metadata" tabindex="0"></video>
            <p class="vd-preview__message" id="vd-preview-message" hidden></p>
          </div>
          <p class="vd-preview__note">视频预览使用网站提供的播放地址，部分加密流或限制外链的视频可能无法播放。</p>
        </section>
      </div>
    `;

    const body = shadow.querySelector(".vd-panel__body");
    panelFrame = document.createElement("iframe");
    panelFrame.className = "vd-panel__frame";
    panelFrame.setAttribute("allowtransparency", "true");
    panelFrame.src = chrome.runtime.getURL("panel.html");
    panelFrame.addEventListener("load", () => {
      const loading = shadow.querySelector("#vd-loading");
      if (loading) loading.remove();
      sendToPanel({ type: "panel_init", items: buildPanelItems() });
    });
    body.appendChild(panelFrame);

    shadow.querySelector("#vd-collapse").addEventListener("click", (event) => {
      // A drag ends in a synthesized click on some pointer devices.
      if (event.detail !== 0 && Date.now() < panelState.suppressToggleClickUntil) {
        event.preventDefault();
        return;
      }
      const panel = shadow.querySelector("#vd-panel");
      const before = panel.getBoundingClientRect();
      panelState.collapsed = !panelState.collapsed;
      panel.classList.toggle("collapsed", panelState.collapsed);
      // Keep the right edge stationary, so expanding near the viewport edge
      // opens toward the available space instead of going off-screen.
      clampPanelPosition(panel, before.right - panel.offsetWidth, before.top);
      if (!panelState.collapsed) updatePanelHeight(shadow);
    });

    shadow.querySelector("#vd-settings").addEventListener("click", () => {
      chrome.runtime.sendMessage({ type: "open_options" }).catch(() => {});
    });
    shadow.querySelector("#vd-close").addEventListener("click", () => hidePanel());

    enableDrag(shadow);
    enableVideoPreview(shadow);
    window.addEventListener("resize", () => {
      if (host?.style.display !== "none") updatePanelHeight(shadow);
    });

    (document.body || document.documentElement).appendChild(host);
    restorePanelPosition(shadow).catch(() => {});
  }

  /**
   * Keep the floating panel fully on screen after dragging, resizing or expanding.
   * @param {HTMLElement} panel Floating shadow DOM panel.
   * @param {number} [x] Preferred left viewport coordinate.
   * @param {number} [y] Preferred top viewport coordinate.
   * @returns {void}
   */
  function clampPanelPosition(panel, x, y) {
    const rect = panel.getBoundingClientRect();
    const margin = 12;
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const maxLeft = Math.max(margin, window.innerWidth - width - margin);
    const maxTop = Math.max(margin, window.innerHeight - height - margin);
    const left = Number.isFinite(x) ? x : rect.left;
    const top = Number.isFinite(y) ? y : rect.top;
    panel.style.right = "auto";
    panel.style.left = Math.max(margin, Math.min(left, maxLeft)) + "px";
    panel.style.top = Math.max(margin, Math.min(top, maxTop)) + "px";
  }

  /**
   * Size the iframe to its measured content before enabling scrolling.
   * A compact two-row list should not inherit a fixed 55vh cap.
   * @param {ShadowRoot} shadow Floating panel shadow root.
   * @returns {void}
   */
  function updatePanelHeight(shadow) {
    const body = shadow.querySelector(".vd-panel__body");
    const panel = shadow.querySelector("#vd-panel");
    if (!body || !panel) return;
    const maxBody = Math.max(72, Math.min(480, window.innerHeight - 68));
    body.style.height = Math.min(Math.max(72, Math.ceil(panelState.contentHeight)), maxBody) + "px";
    if (panel.isConnected && host?.style.display !== "none") clampPanelPosition(panel);
  }

  /**
   * Support touch, pen and mouse dragging on the title bar and collapsed button.
   * A movement threshold distinguishes a drag from a click to re-expand the bubble.
   * @param {ShadowRoot} shadow Floating panel shadow root.
   * @returns {void}
   */
  function enableDrag(shadow) {
    const bar = shadow.querySelector("#vd-bar");
    const panel = shadow.querySelector("#vd-panel");
    let gesture = null;

    bar.addEventListener("pointerdown", (event) => {
      if (!event.isPrimary || event.button !== 0) return;
      const button = event.target.closest("button");
      if (button && !panelState.collapsed) return;
      const rect = panel.getBoundingClientRect();
      gesture = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        left: rect.left,
        top: rect.top,
        moved: false,
      };
      // Capture on the actual button when collapsed so tap activation still
      // targets the button; dragged pointers continue working off the bubble.
      (button || bar).setPointerCapture(event.pointerId);
    });

    bar.addEventListener("pointermove", (event) => {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      const dx = event.clientX - gesture.startX;
      const dy = event.clientY - gesture.startY;
      if (!gesture.moved && Math.hypot(dx, dy) < 5) return;
      gesture.moved = true;
      panel.classList.add("is-dragging");
      clampPanelPosition(panel, gesture.left + dx, gesture.top + dy);
      event.preventDefault();
    });

    function finishDrag(event) {
      if (!gesture || event.pointerId !== gesture.pointerId) return;
      if (gesture.moved) {
        panelState.suppressToggleClickUntil = Date.now() + 160;
        savePanelPosition(panel).catch(() => {});
      }
      panel.classList.remove("is-dragging");
      gesture = null;
    }

    bar.addEventListener("pointerup", finishDrag);
    bar.addEventListener("pointercancel", finishDrag);
  }

  /**
   * Permit only normal remote media URLs; never insert page-supplied markup.
   * @param {*} value Media or thumbnail URL.
   * @returns {string} HTTP(S) URL or an empty string.
   */
  function safePreviewUrl(value) {
    try {
      const url = new URL(String(value || ""));
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
    } catch (error) {
      return "";
    }
  }

  /**
   * Show a clear error in the preview overlay when the source cannot play.
   * @param {string} text Explanatory error message.
   * @returns {void}
   */
  function showPreviewMessage(text) {
    const shadow = host?.shadowRoot;
    const video = shadow?.querySelector("#vd-preview-video");
    const message = shadow?.querySelector("#vd-preview-message");
    if (!video || !message) return;
    video.pause();
    video.hidden = true;
    message.textContent = text;
    message.hidden = false;
  }

  /**
   * Remove media references and return keyboard focus to the panel preview button.
   * @returns {void}
   */
  function closeVideoPreview() {
    const shadow = host?.shadowRoot;
    const overlay = shadow?.querySelector("#vd-preview");
    const video = shadow?.querySelector("#vd-preview-video");
    if (!overlay || overlay.hidden || !video) return;
    overlay.hidden = true;
    video.pause();
    video.removeAttribute("src");
    video.removeAttribute("poster");
    video.load();
    sendToPanel({ type: "preview_closed", shareUrl: activePreviewShareUrl });
    activePreviewShareUrl = "";
  }

  /**
   * Open a viewport-level dialog rather than constraining playback to the iframe.
   * @param {string} shareUrl Media identifier reported by the panel.
   * @returns {void}
   */
  function openVideoPreview(shareUrl) {
    const item = buildPanelItems().find((entry) => entry.shareUrl === shareUrl);
    if (!item || !host?.shadowRoot) return;
    const shadow = host.shadowRoot;
    const overlay = shadow.querySelector("#vd-preview");
    const video = shadow.querySelector("#vd-preview-video");
    const message = shadow.querySelector("#vd-preview-message");
    const close = shadow.querySelector("#vd-preview-close");
    activePreviewShareUrl = shareUrl;
    video.pause();
    video.removeAttribute("src");
    video.removeAttribute("poster");
    video.load();
    shadow.querySelector("#vd-preview-title").textContent = item.title || "视频预览";
    const poster = safePreviewUrl(item.cover);
    if (poster) video.poster = poster;
    video.hidden = false;
    message.hidden = true;
    overlay.hidden = false;
    close.focus();

    const source = safePreviewUrl(item.previewUrl);
    if (!source) {
      showPreviewMessage("该视频没有可用的预览地址。");
      return;
    }
    if (isPlaylistUrl(source) && !video.canPlayType("application/vnd.apple.mpegurl")) {
      showPreviewMessage("浏览器暂不支持直接预览此 HLS (m3u8) 视频流，请下载后播放。");
      return;
    }
    video.src = source;
    video.load();
    video.play().catch(() => {});
  }

  /**
   * Set up backdrop, playback errors and keyboard access outside the panel iframe.
   * @param {ShadowRoot} shadow Host's shadow root.
   * @returns {void}
   */
  function enableVideoPreview(shadow) {
    const overlay = shadow.querySelector("#vd-preview");
    const close = shadow.querySelector("#vd-preview-close");
    const video = shadow.querySelector("#vd-preview-video");
    shadow.querySelector("#vd-preview-backdrop").addEventListener("click", closeVideoPreview);
    close.addEventListener("click", closeVideoPreview);
    video.addEventListener("error", () => {
      if (!overlay.hidden && !video.hidden) {
        showPreviewMessage("视频源无法播放，可能已失效、格式不受支持或网站限制直接播放。");
      }
    });
    window.addEventListener("keydown", (event) => {
      if (overlay.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeVideoPreview();
      } else if (event.key === "Tab") {
        const targets = [close, video].filter((node) => !node.hidden);
        const index = targets.indexOf(shadow.activeElement);
        if (index === -1 || (!event.shiftKey && index === targets.length - 1)) {
          event.preventDefault();
          targets[0].focus();
        } else if (event.shiftKey && index === 0) {
          event.preventDefault();
          targets[targets.length - 1].focus();
        }
      }
    }, true);
  }

  async function restorePanelPosition(shadow) {
    if (extensionSettings.rememberPanelPosition === false) return;
    const data = await chrome.storage.local.get(PANEL_POSITION_KEY);
    const position = data[PANEL_POSITION_KEY];
    if (!position || typeof position.left !== "number" || typeof position.top !== "number") return;
    const panel = shadow.querySelector("#vd-panel");
    clampPanelPosition(panel, position.left, position.top);
  }

  async function savePanelPosition(panel) {
    if (extensionSettings.rememberPanelPosition === false) return;
    const rect = panel.getBoundingClientRect();
    await chrome.storage.local.set({
      [PANEL_POSITION_KEY]: { left: Math.round(rect.left), top: Math.round(rect.top) },
    });
  }

  function showPanel() {
    buildPanel();
    if (host && !host.isConnected) (document.body || document.documentElement).appendChild(host);
    if (host) host.style.display = "";
    updatePanelHeight(host.shadowRoot);
    // 打开面板时做一次全量补抓：主世界抓取 + DOM 扫描
    requestCurrentMedia();
    scanDomVideos();
    pushMediaList();
  }

  function hidePanel() {
    closeVideoPreview();
    if (host) host.style.display = "none";
  }

  /* ================================================================== */
  /* 四、与面板（iframe）通信                                            */
  /* ================================================================== */

  function buildPanelItems() {
    return mediaList.filter((item) => item.verificationState === "verified").map((item) => {
      const variant = preferredVariant(item);
      return {
        shareUrl: item.shareUrl,
        title: item.title,
        author: item.author,
        cover: item.cover,
        previewUrl: item.verifiedUrl || "",
        size: item.verifiedSize || (variant?.url === item.verifiedUrl ? variant?.size : 0) || item.size,
        quality: variant?.label || "",
        type: item.type,
        platform: item.platform || currentPlatform.name,
        source: item.source || "网络嗅探",
        status: item.status || "idle",
        progress: item.progress || 0,
      };
    });
  }

  function sendToPanel(message) {
    if (panelFrame && panelFrame.contentWindow) {
      panelFrame.contentWindow.postMessage({ source: "vd-content", ...message }, "*");
    }
  }

  /**
   * Publish display-only media metadata to the cross-tab monitor.
   * Keep this payload separate from the full download candidate list.
   * @param {Array<Object>} items Normalized panel/media descriptors.
   * @returns {void}
   */
  function publishRegistry(items) {
    const registryItems = items.map((item) => ({
      shareUrl: item.shareUrl,
      title: item.title,
      cover: item.cover,
      previewUrl: item.previewUrl,
      size: item.size,
      quality: item.quality,
      platform: item.platform,
      status: item.status,
      progress: item.progress,
    }));
    chrome.runtime.sendMessage({
      type: "update_media_registry",
      pageUrl: location.href,
      pageTitle: document.title || location.hostname,
      platform: currentPlatform.name,
      items: registryItems,
    }).catch(() => {});
  }

  function pushMediaList() {
    const items = buildPanelItems();
    sendToPanel({ type: "media_list", items });
    publishRegistry(items);
  }

  // Progress can change every few milliseconds. Coalesce registry writes so
  // the service worker does not serialize one storage update for every chunk.
  let registryProgressTimer = null;
  function scheduleRegistryProgress() {
    if (registryProgressTimer) return;
    registryProgressTimer = setTimeout(() => {
      registryProgressTimer = null;
      publishRegistry(buildPanelItems());
    }, 900);
  }

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.source !== "vd-panel" || event.source !== panelFrame?.contentWindow) return;

    switch (data.type) {
      case "panel_ready":
        sendToPanel({ type: "media_list", items: buildPanelItems() });
        break;
      case "panel_resize":
        if (Number.isFinite(data.height) && data.height > 0) {
          panelState.contentHeight = data.height;
          if (host?.shadowRoot) updatePanelHeight(host.shadowRoot);
        }
        break;
      case "start_download":
        startBatchDownload(Array.isArray(data.shareUrls) ? data.shareUrls : []);
        break;
      case "stop_download":
        stopBatchDownload();
        break;
      case "open_preview":
        if (typeof data.shareUrl === "string") openVideoPreview(data.shareUrl);
        break;
      default:
        break;
    }
  });

  /* ================================================================== */
  /* 五、下载引擎                                                        */
  /* ================================================================== */

  let downloadTask = { running: false, stopped: false };

  /*
   * ---------------- 线程数：交给浏览器决定 ----------------
   * 开跑前：初始线程数 = 浏览器内存预算 × 70% ÷ 本次待下视频平均体积；
   * 跑起来后：每次领新任务前看实时堆压力，吃紧就停手、缓过来再放开。
   */
  const MEMORY_BUDGET_RATIO = 0.7;
  const MIN_CONCURRENCY = DOWNLOAD_RULES.MIN_CONCURRENCY;
  const MAX_CONCURRENCY = DOWNLOAD_RULES.MAX_CONCURRENCY;
  const ASSUMED_VIDEO_BYTES = 80 * 1024 * 1024;
  const DOWNLOAD_STAGGER_MS = 40;
  const HEAP_PAUSE_RATIO = 0.85;
  const HEAP_RESUME_RATIO = 0.6;
  const HEAP_WAIT_TIMEOUT_MS = 20000;

  function readBrowserMemoryBudget() {
    const perfMemory = performance.memory;
    if (perfMemory && perfMemory.jsHeapSizeLimit > 0) {
      return { budgetBytes: perfMemory.jsHeapSizeLimit, source: "浏览器堆上限" };
    }
    const deviceMemoryGB = Number(navigator.deviceMemory);
    if (deviceMemoryGB > 0) {
      return {
        budgetBytes: deviceMemoryGB * 0.5 * 1024 * 1024 * 1024,
        source: "设备内存 " + deviceMemoryGB + "GB",
      };
    }
    return { budgetBytes: 0, source: "默认值" };
  }

  function resolveConcurrency(targets, requestedConcurrency) {
    const budget = readBrowserMemoryBudget();
    const sizes = targets.map((item) => Number(item.size) || 0).filter((size) => size > 0);
    const perVideo = sizes.length
      ? Math.round(sizes.reduce((sum, size) => sum + size, 0) / sizes.length)
      : ASSUMED_VIDEO_BYTES;

    const usableBytes = budget.budgetBytes * MEMORY_BUDGET_RATIO;
    const raw = usableBytes > 0 ? Math.floor(usableBytes / perVideo) : MIN_CONCURRENCY;

    // The manual limit is per originating tab; memory pressure can reduce it.
    const setting = requestedConcurrency == null ? extensionSettings.batchConcurrency : requestedConcurrency;
    const manualLimit = Number(setting) > 0
      ? Math.min(MAX_CONCURRENCY, Math.max(1, Math.floor(Number(setting))))
      : MAX_CONCURRENCY;
    return {
      threads: Math.min(manualLimit, Math.max(MIN_CONCURRENCY, Math.min(MAX_CONCURRENCY, raw))),
      usableBytes: Math.round(usableBytes),
      perVideo,
      sizeSampled: sizes.length > 0,
      source: budget.source,
    };
  }

  /**
   * 当前堆占用比例；拿不到 performance.memory 时返回 -1。
   * 注意：必须每次重新读 performance.memory（它是 getter，
   * 每次访问返回新快照；存进变量后反复读属性会读到冻结值）。
   */
  function readHeapRatio() {
    const perfMemory = performance.memory;
    if (!perfMemory || !perfMemory.jsHeapSizeLimit) return -1;
    return perfMemory.usedJSHeapSize / perfMemory.jsHeapSizeLimit;
  }

  async function waitForHeapHeadroom() {
    const first = readHeapRatio();
    if (first < 0 || first < HEAP_PAUSE_RATIO) return;

    const deadline = Date.now() + HEAP_WAIT_TIMEOUT_MS;
    while (!downloadTask.stopped && Date.now() < deadline) {
      await sleep(150);
      const ratio = readHeapRatio();
      if (ratio < 0 || ratio < HEAP_RESUME_RATIO) return;
    }
    console.warn("[视频下载] 内存持续吃紧，等待超时后继续下载");
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function formatSize(bytes) {
    if (!bytes || bytes <= 0) return "未知大小";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, index)).toFixed(2) + " " + units[index];
  }

  /** 带进度回调的下载（fetch → Blob）；进度按 2% 步进回调 */
  async function fetchWithProgress(url, onProgress, timeoutMs = 120000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { credentials: "omit", signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const total = Number(response.headers.get("content-length")) || 0;
      if (!response.body || !onProgress) {
        const blob = await response.blob();
        clearTimeout(timer);
        return blob;
      }

      const reader = response.body.getReader();
      const chunks = [];
      let loaded = 0;
      let lastPercent = -1;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        loaded += value.length;
        const percent = total ? Math.min(99, Math.floor((loaded / total) * 100)) : 95;
        if (percent - lastPercent >= 2 || percent >= 99) {
          lastPercent = percent;
          onProgress(percent);
        }
      }
      clearTimeout(timer);
      return new Blob(chunks, { type: response.headers.get("content-type") || "application/octet-stream" });
    } catch (error) {
      clearTimeout(timer);
      if (error.name === "AbortError") throw new Error("下载超时（120秒无响应）");
      throw error;
    }
  }

  /** 触发浏览器下载（默认保存到浏览器下载目录） */
  function saveBlob(blob, fileName) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = fileName;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
      link.remove();
      URL.revokeObjectURL(url);
    }, 60_000);
  }

  const AUDIO_PROBE_BYTES = 2 * 1024 * 1024;

  /**
   * 轻量音轨检测：在文件起始区域搜索 mp4 box 关键字。
   * 调用方必须传 blob.slice(0, AUDIO_PROBE_BYTES)，不要对整个 blob
   * 调 arrayBuffer()（会把整份视频再复制进内存一份）。
   */
  function hasAudioTrack(arrayBuffer) {
    const size = Math.min(arrayBuffer.byteLength, AUDIO_PROBE_BYTES);
    const bytes = new Uint8Array(arrayBuffer, 0, size);
    return containsAscii(bytes, "soun") || containsAscii(bytes, "mp4a") || containsAscii(bytes, "ac-3");
  }

  function containsAscii(bytes, pattern) {
    const length = pattern.length;
    outer: for (let i = 0; i <= bytes.length - length; i++) {
      for (let j = 0; j < length; j++) {
        if (bytes[i + j] !== pattern.charCodeAt(j)) continue outer;
      }
      return true;
    }
    return false;
  }

  /** 依次尝试视频的所有候选地址，返回第一个下载成功的 Blob */
  async function fetchVideoWithFallback(media, onProgress) {
    const candidates = downloadCandidateUrls(media);
    let lastError = null;

    for (const url of candidates) {
      try {
        const blob = await fetchWithProgress(url, onProgress);
        if (blob.size < DOWNLOAD_RULES.MIN_VALID_BYTES) throw new Error("文件内容为空");
        return blob;
      } catch (error) {
        lastError = error;
        console.warn("[视频下载] 该地址下载失败，改用下一个候选地址：", url, error);
      }
    }
    throw lastError || new Error("所有候选地址均下载失败");
  }

  /* ---------------- m3u8 下载：分段拉取 + 合并 ---------------- */

  /** 带超时的文本请求 */
  async function fetchTextWithTimeout(url, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { credentials: "omit", signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.text();
    } finally {
      clearTimeout(timer);
    }
  }

  /** 带超时的二进制请求 */
  async function fetchBytesWithTimeout(url, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { credentials: "omit", signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.arrayBuffer();
    } finally {
      clearTimeout(timer);
    }
  }

  /** 解析相对地址（m3u8 里大量相对路径） */
  function resolveM3u8Url(uri, base) {
    try {
      return new URL(uri, base).href;
    } catch (error) {
      return "";
    }
  }

  /** 从主播放列表里按用户清晰度偏好选择一路；缺少分辨率时退回带宽。 */
  function pickBestVariant(masterText, masterUrl) {
    const lines = masterText.split("\n");
    const variants = [];

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line.startsWith("#EXT-X-STREAM-INF:")) continue;
      const bandwidth = Number(line.match(/BANDWIDTH=(\d+)/)?.[1] || 0);
      const resolution = line.match(/RESOLUTION=(\d+)x(\d+)/i);
      const height = resolution ? Number(resolution[2]) : 0;

      for (let j = i + 1; j < lines.length; j++) {
        const uri = lines[j].trim();
        if (!uri) continue;
        if (uri.startsWith("#")) break;
        variants.push({ uri, bandwidth, height });
        break;
      }
    }

    if (!variants.length) return "";

    const target = Number(extensionSettings.quality);
    variants.sort((a, b) => {
      if (extensionSettings.quality === "smallest") {
        if (a.bandwidth !== b.bandwidth) return a.bandwidth - b.bandwidth;
        return a.height - b.height;
      }
      if (target > 0) {
        const ad = a.height ? Math.abs(a.height - target) : Number.MAX_SAFE_INTEGER;
        const bd = b.height ? Math.abs(b.height - target) : Number.MAX_SAFE_INTEGER;
        if (ad !== bd) return ad - bd;
      }
      if (a.height !== b.height) return b.height - a.height;
      return b.bandwidth - a.bandwidth;
    });

    return resolveM3u8Url(variants[0].uri, masterUrl);
  }

  /**
   * 解析媒体播放列表：分段 URL 列表 + 初始化分段（EXT-X-MAP）。
   * 遇到加密（EXT-X-KEY 带 URI）直接抛错，如实告诉用户。
   */
  function parseMediaPlaylist(playlistText, playlistUrl) {
    const lines = playlistText.split("\n");
    const segments = [];
    let initUrl = "";

    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line) continue;

      if (line.startsWith("#EXT-X-KEY")) {
        // METHOD=NONE 不算加密；带 URI 的才算
        if (/URI="/.test(line) && !/METHOD=NONE/.test(line)) {
          throw new Error(DOWNLOAD_RULES.M3U8_ENCRYPTED_MESSAGE);
        }
        continue;
      }

      if (line.startsWith("#EXT-X-MAP")) {
        const match = line.match(/URI="([^"]+)"/);
        if (match) initUrl = resolveM3u8Url(match[1], playlistUrl);
        continue;
      }

      if (!line.startsWith("#")) {
        const url = resolveM3u8Url(line, playlistUrl);
        if (url) segments.push(url);
      }
    }

    return { segments, initUrl };
  }

  /** 分段看起来像 fMP4（m4s）→ 合并后存 .mp4；否则是 MPEG-TS → 存 .ts */
  function guessSegmentContainer(segments) {
    return segments.some((url) => /\.(m4s|mp4)(\?|#|$)/i.test(url)) ? "mp4" : "ts";
  }

  /**
   * 下载 m3u8 并合并为单个文件。
   * @returns {Promise<{blob: Blob, ext: string}>}
   */
  async function downloadM3u8(media, onProgress) {
    const playlistUrl = (media.videoUrls || []).find((url) => isPlaylistUrl(url)) || media.videoUrl;
    if (!playlistUrl) throw new Error("没有可用的播放列表地址");

    // 1) 取播放列表；主列表则选最高码率
    let playlistText = await fetchTextWithTimeout(playlistUrl, DOWNLOAD_RULES.M3U8_SEGMENT_TIMEOUT_MS);
    let playlistBase = playlistUrl;
    if (playlistText.includes("#EXT-X-STREAM-INF")) {
      const variantUrl = pickBestVariant(playlistText, playlistUrl);
      if (!variantUrl) throw new Error("播放列表中没有可用的清晰度");
      playlistBase = variantUrl;
      playlistText = await fetchTextWithTimeout(variantUrl, DOWNLOAD_RULES.M3U8_SEGMENT_TIMEOUT_MS);
    }

    // 2) 解析分段
    const { segments, initUrl } = parseMediaPlaylist(playlistText, playlistBase);
    if (!segments.length) throw new Error("播放列表中没有找到视频分段");

    const tasks = [...(initUrl ? [initUrl] : []), ...segments];
    const total = tasks.length;
    const ext = guessSegmentContainer(segments);
    console.log(`[视频下载] m3u8 共 ${total} 个分段，合并为 .${ext}`);

    // 3) 并发下载分段（顺序组装，不乱序）
    const parts = new Array(total);
    let completed = 0;
    let cursor = 0;

    async function segmentWorker() {
      while (!downloadTask.stopped) {
        const index = cursor++;
        if (index >= total) return;
        const buffer = await fetchBytesWithTimeout(tasks[index], DOWNLOAD_RULES.M3U8_SEGMENT_TIMEOUT_MS);
        if (buffer.byteLength < DOWNLOAD_RULES.MIN_VALID_BYTES) {
          throw new Error(`第 ${index + 1} 个分段下载内容为空`);
        }
        parts[index] = buffer;
        completed++;
        if (onProgress) onProgress(Math.floor((completed / total) * 100));
      }
    }

    const workerCount = Math.min(DOWNLOAD_RULES.M3U8_SEGMENT_CONCURRENCY, total);
    await Promise.all(Array.from({ length: workerCount }, () => segmentWorker()));
    if (downloadTask.stopped) throw new Error("用户已停止");

    // 4) 按顺序拼接（同编码的 TS / fMP4 分段直接拼接即可播放）
    return {
      blob: new Blob(parts, { type: ext === "mp4" ? "video/mp4" : "video/mp2t" }),
      ext,
    };
  }

  /* ---------------- 单条下载调度 ---------------- */

  function reportItemStatus(media, status, progress) {
    media.status = status;
    if (typeof progress === "number") media.progress = progress;
    sendToPanel({
      type: "item_status",
      shareUrl: media.shareUrl,
      status: media.status,
      progress: media.progress || 0,
    });
    if (status === "done" || status === "error") {
      if (registryProgressTimer) clearTimeout(registryProgressTimer);
      registryProgressTimer = null;
      pushMediaList();
    } else if (progress === 0) {
      publishRegistry(buildPanelItems());
    } else {
      scheduleRegistryProgress();
    }
  }

  /**
   * 下载单条媒体：
   *   1. m3u8 → 分段下载后合并保存；
   *   2. 图集 → 逐张保存图片；
   *   3. 直链视频 → 依次尝试候选地址；
   *   4. 无音轨时尝试补救配乐（尽力而为）。
   */
  async function downloadMedia(media) {
    reportItemStatus(media, "downloading", 0);

    try {
      // ---- m3u8：分段下载后合并 ----
      const playlistUrl = (media.videoUrls || []).find((url) => isPlaylistUrl(url));
      if (playlistUrl || isPlaylistUrl(media.videoUrl)) {
        const { blob, ext } = await downloadM3u8(media, (percent) => {
          reportItemStatus(media, "downloading", percent);
        });
        saveBlob(blob, `${media.fileName}.${ext}`);
        reportItemStatus(media, "done", 100);
        return true;
      }

      // ---- 图集：逐张保存图片 ----
      if (media.type === "图集" && media.imageUrls?.length) {
        for (let index = 0; index < media.imageUrls.length; index++) {
          const blob = await fetchWithProgress(media.imageUrls[index], (percent) => {
            const overall = Math.floor(((index + percent / 100) / media.imageUrls.length) * 100);
            reportItemStatus(media, "downloading", overall);
          });
          const ext = (blob.type.split("/")[1] || "jpg").replace("jpeg", "jpg");
          saveBlob(blob, `${media.fileName}_${index + 1}.${ext}`);
        }
        reportItemStatus(media, "done", 100);
        return true;
      }

      // ---- 直链视频：依次尝试候选地址 ----
      const videoBlob = await fetchVideoWithFallback(media, (percent) => {
        reportItemStatus(media, "downloading", Math.floor(percent * 0.9));
      });
      const ext = guessMediaExt(media.videoUrl) || "mp4";
      const saveExt = ext === "m3u8" ? "mp4" : ext;
      saveBlob(videoBlob, `${media.fileName}.${saveExt}`);

      // ---- 音轨检测（仅提示用） ----
      try {
        if (!hasAudioTrack(await videoBlob.slice(0, AUDIO_PROBE_BYTES).arrayBuffer())) {
          console.warn("[视频下载] 当前视频源未检测到音轨");
          if (media.audioUrl) {
            const audioBlob = await fetchWithProgress(media.audioUrl, null);
            const isM4a = /mp4|m4a|aac/i.test(audioBlob.type || "");
            saveBlob(audioBlob, `${media.fileName}_配乐.${isM4a ? "m4a" : "mp3"}`);
            sendToPanel({ type: "toast", message: "该视频源不含音轨，已同时保存配乐文件" });
          }
        }
      } catch (checkError) {
        console.warn("[视频下载] 音轨检测失败：", checkError);
      }

      reportItemStatus(media, "done", 100);
      return true;
    } catch (error) {
      console.error("[视频下载] 下载失败：", error);
      reportItemStatus(media, "error", 0);
      sendToPanel({ type: "item_error", shareUrl: media.shareUrl, message: String(error.message || error) });
      return false;
    }
  }

  /**
   * 批量下载 —— 多线程并发调度。
   * worker 从共享游标领任务，天然不重复；全部 worker 空闲时结束。
   */
  async function startBatchDownload(shareUrls, requestedConcurrency) {
    if (downloadTask.running) return;

    const targets = [...new Set(shareUrls)]
      .map((url) => mediaList.find((item) => item.shareUrl === url))
      .filter((item) => item && item.verificationState === "verified" && item.status !== "done" && item.status !== "downloading");

    if (!targets.length) {
      sendToPanel({ type: "toast", message: "没有需要下载的视频（已完成的不再重复下载）" });
      return;
    }

    downloadTask = { running: true, stopped: false };

    const total = targets.length;
    const plan = resolveConcurrency(targets, requestedConcurrency);
    const workerCount = Math.min(plan.threads, total);
    console.log("[视频下载] 线程数", workerCount, "=", plan.source, "预算", formatSize(plan.usableBytes), "÷ 单条", formatSize(plan.perVideo));
    sendToPanel({ type: "batch_started", total, concurrency: workerCount, memory: plan });

    let cursor = 0;
    let completed = 0;
    let succeeded = 0;
    let failed = 0;

    async function worker(workerIndex) {
      await sleep(workerIndex * DOWNLOAD_STAGGER_MS);

      while (!downloadTask.stopped) {
        const index = cursor++;
        if (index >= total) return;

        await waitForHeapHeadroom();
        if (downloadTask.stopped) return;

        const media = targets[index];
        const ok = await downloadMedia(media);

        if (ok) {
          succeeded++;
          markDownloaded(media.shareUrl);
        } else {
          failed++;
        }
        completed++;
        sendToPanel({ type: "batch_progress", completed, total });
      }
    }

    await Promise.all(Array.from({ length: workerCount }, (_, index) => worker(index)));

    downloadTask.running = false;
    sendToPanel({
      type: "batch_finished",
      completed,
      succeeded,
      failed,
      total,
      stopped: downloadTask.stopped,
    });
  }

  function stopBatchDownload() {
    downloadTask.stopped = true;
  }

  /* ================================================================== */
  /* 六、与 background.js 通信 + 启动                                    */
  /* ================================================================== */

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || !message.type) return undefined;

    if (message.type === "show_panel") {
      showPanel();
      sendResponse({ ok: true });
      return true;
    }
    if (message.type === "hide_panel") {
      hidePanel();
      sendResponse({ ok: true });
      return true;
    }
    if (message.type === "start_external_download") {
      if (downloadTask.running) {
        sendResponse({ ok: false, error: "当前网页仍有下载任务在进行" });
        return true;
      }
      const shareUrls = Array.isArray(message.shareUrls) ? [...new Set(message.shareUrls)] : [];
      const available = shareUrls.filter((url) =>
        mediaList.some((item) => item.shareUrl === url && item.verificationState === "verified" && item.status !== "done")
      );
      if (!available.length) {
        sendResponse({ ok: false, error: "该网页没有待下载的视频" });
        return true;
      }
      startBatchDownload(available, message.concurrency);
      sendResponse({ ok: true, accepted: available.length });
      return true;
    }
    return undefined;
  });

  // 启动：资源嗅探（收录已加载 + 监听新增）→ DOM 扫描 → 向主世界补抓
  startResourceSniffing();
  setInterval(scanDomVideos, 3000);
  setTimeout(() => {
    scanDomVideos();
    requestCurrentMedia();
  }, 1200);
})();
