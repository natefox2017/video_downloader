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
          (Array.isArray(item.videoUrls) && item.videoUrls.includes(url))
      ) || null
    );
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
      const incomingIsExtractor = incoming.source === "页面解析";
      const existingIsExtractor = existing.source === "页面解析";
      const preferIncomingMetadata = incomingIsExtractor && !existingIsExtractor;

      for (const key of ["title", "desc", "author", "cover", "fileName"]) {
        if (incoming[key] && (!existing[key] || preferIncomingMetadata)) existing[key] = incoming[key];
      }
      if (incoming.size && (!existing.size || preferIncomingMetadata)) existing.size = incoming.size;
      if (incoming.duration && (!existing.duration || preferIncomingMetadata)) existing.duration = incoming.duration;

      const urls = new Set([...(existing.videoUrls || []), ...(incoming.videoUrls || [])]);
      if (incoming.videoUrl) urls.add(incoming.videoUrl);
      existing.videoUrls = [...urls];
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
    return true;
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
  const panelState = { collapsed: false };

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
          --vd-max-height: 55vh;
          position: fixed;
          top: 16px;
          right: 16px;
          width: var(--vd-width);
          max-width: calc(100vw - 24px);
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
          cursor: move;
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
        .vd-panel__count {
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
          height: 180px;
          max-height: var(--vd-max-height);
          min-height: 0;
          background: #ffffff;
        }
        .vd-panel.collapsed .vd-panel__body {
          display: none;
        }
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
      </style>
      <div class="vd-panel" id="vd-panel">
        <div class="vd-panel__bar" id="vd-bar">
          <span class="vd-panel__title">视频下载</span>
          <span class="vd-panel__actions">
            <button class="vd-panel__btn" id="vd-collapse" title="折叠 / 展开">−</button>
            <button class="vd-panel__btn" id="vd-close" title="关闭面板（不影响正在进行的下载）">✕</button>
          </span>
        </div>
        <div class="vd-panel__body">
          <div class="vd-panel__loading" id="vd-loading">正在加载…</div>
        </div>
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

    shadow.querySelector("#vd-collapse").addEventListener("click", () => {
      const panel = shadow.querySelector("#vd-panel");
      panelState.collapsed = !panelState.collapsed;
      panel.classList.toggle("collapsed", panelState.collapsed);
      shadow.querySelector("#vd-collapse").textContent = panelState.collapsed ? "+" : "−";
    });

    shadow.querySelector("#vd-close").addEventListener("click", () => hidePanel());

    enableDrag(shadow);

    (document.body || document.documentElement).appendChild(host);
  }

  function enableDrag(shadow) {
    const bar = shadow.querySelector("#vd-bar");
    const panel = shadow.querySelector("#vd-panel");
    let startX = 0;
    let startY = 0;
    let originLeft = 0;
    let originTop = 0;
    let dragging = false;

    bar.addEventListener("mousedown", (event) => {
      if (event.target.closest("button")) return;
      dragging = true;
      const rect = panel.getBoundingClientRect();
      startX = event.clientX;
      startY = event.clientY;
      originLeft = rect.left;
      originTop = rect.top;
      panel.style.right = "auto";
      panel.style.left = rect.left + "px";
      panel.style.top = rect.top + "px";
      event.preventDefault();
    });

    window.addEventListener("mousemove", (event) => {
      if (!dragging) return;
      const left = originLeft + (event.clientX - startX);
      const top = originTop + (event.clientY - startY);
      const maxLeft = window.innerWidth - 60;
      const maxTop = window.innerHeight - 40;
      panel.style.left = Math.max(-panel.offsetWidth + 80, Math.min(left, maxLeft)) + "px";
      panel.style.top = Math.max(0, Math.min(top, maxTop)) + "px";
    });

    window.addEventListener("mouseup", () => {
      dragging = false;
    });
  }

  function showPanel() {
    buildPanel();
    if (host && !host.isConnected) (document.body || document.documentElement).appendChild(host);
    if (host) host.style.display = "";
    // 打开面板时做一次全量补抓：主世界抓取 + DOM 扫描
    requestCurrentMedia();
    scanDomVideos();
    pushMediaList();
  }

  function hidePanel() {
    if (host) host.style.display = "none";
  }

  /* ================================================================== */
  /* 四、与面板（iframe）通信                                            */
  /* ================================================================== */

  function buildPanelItems() {
    return mediaList.map((item) => ({
      shareUrl: item.shareUrl,
      title: item.title,
      author: item.author,
      cover: item.cover,
      previewUrl: item.videoUrl || (Array.isArray(item.videoUrls) ? item.videoUrls[0] : "") || "",
      size: item.size,
      type: item.type,
      platform: item.platform || currentPlatform.name,
      source: item.source || "网络嗅探",
      status: item.status || "idle",
      progress: item.progress || 0,
    }));
  }

  function sendToPanel(message) {
    if (panelFrame && panelFrame.contentWindow) {
      panelFrame.contentWindow.postMessage({ source: "vd-content", ...message }, "*");
    }
  }

  function pushMediaList() {
    sendToPanel({ type: "media_list", items: buildPanelItems() });
    // 同步更新扩展图标徽标（显示检测到的视频数量）
    try {
      chrome.runtime.sendMessage({ type: "update_badge", count: mediaList.length });
    } catch (error) {
      /* 后台未就绪时忽略 */
    }
  }

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.source !== "vd-panel") return;

    switch (data.type) {
      case "panel_ready":
        sendToPanel({ type: "media_list", items: buildPanelItems() });
        break;
      case "panel_resize": {
        // iframe 上报内容高度 → 自适应面板高度（不超过最大高度）
        const body = host?.shadowRoot?.querySelector(".vd-panel__body");
        if (body && typeof data.height === "number" && data.height > 0) {
          const maxPx = Math.floor(window.innerHeight * 0.55);
          const h = Math.max(140, Math.min(Math.ceil(data.height), maxPx));
          body.style.height = h + "px";
        }
        break;
      }
      case "start_download":
        startBatchDownload(Array.isArray(data.shareUrls) ? data.shareUrls : []);
        break;
      case "stop_download":
        stopBatchDownload();
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

  function resolveConcurrency(targets) {
    const budget = readBrowserMemoryBudget();
    const sizes = targets.map((item) => Number(item.size) || 0).filter((size) => size > 0);
    const perVideo = sizes.length
      ? Math.round(sizes.reduce((sum, size) => sum + size, 0) / sizes.length)
      : ASSUMED_VIDEO_BYTES;

    const usableBytes = budget.budgetBytes * MEMORY_BUDGET_RATIO;
    const raw = usableBytes > 0 ? Math.floor(usableBytes / perVideo) : MIN_CONCURRENCY;

    return {
      threads: Math.max(MIN_CONCURRENCY, Math.min(MAX_CONCURRENCY, raw)),
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
    const candidates = (media.videoUrls && media.videoUrls.length ? media.videoUrls : [media.videoUrl]).filter(Boolean);
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

  /** 从主播放列表（master）里选带宽最高的一路 */
  function pickBestVariant(masterText, masterUrl) {
    const lines = masterText.split("\n");
    let bestBandwidth = -1;
    let bestUri = "";
    for (let i = 0; i < lines.length; i++) {
      const match = lines[i].match(/#EXT-X-STREAM-INF:[^\n]*BANDWIDTH=(\d+)/);
      if (match) {
        const bandwidth = Number(match[1]);
        // URI 在下一行（非 # 开头）
        for (let j = i + 1; j < lines.length; j++) {
          const uri = lines[j].trim();
          if (!uri) continue;
          if (uri.startsWith("#")) break;
          if (bandwidth > bestBandwidth) {
            bestBandwidth = bandwidth;
            bestUri = uri;
          }
          break;
        }
      }
    }
    return bestUri ? resolveM3u8Url(bestUri, masterUrl) : "";
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
  async function startBatchDownload(shareUrls) {
    if (downloadTask.running) return;

    const targets = shareUrls
      .map((url) => mediaList.find((item) => item.shareUrl === url))
      .filter((item) => item && item.status !== "done");

    if (!targets.length) {
      sendToPanel({ type: "toast", message: "没有需要下载的视频（已完成的不再重复下载）" });
      return;
    }

    downloadTask = { running: true, stopped: false };

    const total = targets.length;
    const plan = resolveConcurrency(targets);
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
