/**
 * content.js —— 运行在抖音页面「隔离世界」的内容脚本
 *
 * 职责：
 *   1. 把 injected.js 注入页面主世界，接收它抓到的媒体数据并列表去重；
 *   2. 在页面内创建可拖拽 / 可折叠的浮层面板（Shadow DOM + iframe 加载 panel.html）；
 *   3. 执行下载：多线程并发 fetch 媒体文件 → 保存到浏览器默认下载目录；
 *   4. 与 background.js 配合，响应扩展图标的显示面板请求。
 *
 * 设计要点：
 *   - 面板用 iframe 承载，样式与页面完全隔离；关闭面板只做 display:none，不销毁，
 *     因此正在进行的下载不会中断（面板隐藏不影响功能）。
 *   - 下载在本脚本完成：隔离世界拥有 host_permissions，fetch 不受跨域限制。
 *   - 批量下载是多线程并发（见 DOWNLOAD_CONCURRENCY），不是一条一条串行等待。
 *   - 下载成功的条目会写入 localStorage，刷新页面后依然标记为「已完成」，
 *     从而避免下次批量下载时重复下载同一个视频。
 */

(() => {
  "use strict";

  if (window.__DY_DL_CONTENT_READY__) return;
  window.__DY_DL_CONTENT_READY__ = true;

  /* ================================================================== */
  /* 一、注入主世界抓取脚本                                              */
  /* ================================================================== */

  (function injectPageScript() {
    const script = document.createElement("script");
    script.src = chrome.runtime.getURL("injected.js");
    script.onload = () => script.remove();
    (document.head || document.documentElement).appendChild(script);
  })();

  /* ================================================================== */
  /* 二、媒体列表（按 shareUrl 去重，最新的排在最前）                    */
  /* ================================================================== */

  /** @type {Array<Object>} 去重后的媒体列表 */
  const mediaList = [];

  /**
   * 已下载成功的 shareUrl 集合。
   * 持久化在页面域名的 localStorage 里，因此刷新页面后「已完成」标记依然保留，
   * 下次批量下载时也不会重复选中、重复下载。
   */
  const DOWNLOADED_STORE_KEY = "__dy_dl_downloaded_urls__";
  const DOWNLOADED_STORE_LIMIT = 1000;

  /** 读取历史下载记录（读不到就当作空集合，不影响主流程） */
  function loadDownloadedSet() {
    try {
      const raw = localStorage.getItem(DOWNLOADED_STORE_KEY);
      const list = raw ? JSON.parse(raw) : [];
      return new Set(Array.isArray(list) ? list : []);
    } catch (error) {
      console.warn("[抖晓晓] 读取下载记录失败：", error);
      return new Set();
    }
  }

  /** 写入历史下载记录（只保留最近 1000 条，避免无限增长） */
  function persistDownloadedSet() {
    try {
      const list = Array.from(downloadedSet).slice(-DOWNLOADED_STORE_LIMIT);
      localStorage.setItem(DOWNLOADED_STORE_KEY, JSON.stringify(list));
    } catch (error) {
      console.warn("[抖晓晓] 写入下载记录失败：", error);
    }
  }

  /** 已下载集合（页面加载时先恢复历史记录） */
  const downloadedSet = loadDownloadedSet();

  /** 标记某条媒体已下载完成，并持久化 */
  function markDownloaded(shareUrl) {
    if (!shareUrl || downloadedSet.has(shareUrl)) return;
    downloadedSet.add(shareUrl);
    persistDownloadedSet();
  }

  /** 通知面板刷新列表 */
  function pushMediaList() {
    sendToPanel({ type: "media_list", items: buildPanelItems() });
  }

  /** 接收 injected.js 抓到的媒体数据（去重后入列） */
  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || data.source !== "dy-dl-injected" || data.type !== "media_found") return;

    const media = data.media;
    if (!media || !media.shareUrl) return;

    const existing = mediaList.find((item) => item.shareUrl === media.shareUrl);
    if (existing) {
      // 已在列表中：仅补全可能变化的字段（例如封面延迟加载）
      Object.assign(existing, {
        cover: existing.cover || media.cover,
        size: existing.size || media.size,
        videoUrl: media.videoUrl || existing.videoUrl,
        audioUrl: media.audioUrl || existing.audioUrl,
      });
    } else {
      // 历史记录里已有 = 之前下载过，直接标记为已完成（进度条拉满）
      const alreadyDownloaded = downloadedSet.has(media.shareUrl);
      mediaList.unshift({
        ...media,
        status: alreadyDownloaded ? "done" : "idle",
        progress: alreadyDownloaded ? 100 : 0,
      });
    }
    pushMediaList();
  });

  /** 主动向页面主世界索要当前视频 */
  function requestCurrentMedia() {
    window.postMessage({ source: "dy-dl-content", type: "request_current_media" }, "*");
  }

  /* ================================================================== */
  /* 三、页面内浮层面板                                                  */
  /* ================================================================== */

  const HOST_ID = "__dy_dl_panel_host__";

  /** 浮层容器（Shadow DOM 宿主） */
  let host = null;
  /** iframe 元素（面板本体） */
  let panelFrame = null;

  /** 面板几何状态：位置 + 折叠状态 */
  const panelState = {
    collapsed: false,
  };

  /** 创建浮层 DOM：标题栏（拖拽 / 折叠 / 关闭）+ iframe 面板 */
  function buildPanel() {
    if (host) return;

    host = document.createElement("div");
    host.id = HOST_ID;
    host.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;";

    const shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <style>
        /* 面板宽度 = 原窗口的 2/3，高度 = 原窗口的一半；均可用 CSS 变量调整 */
        .dy-panel {
          --dy-width: 350px;
          --dy-height: 42vh;
          position: fixed;
          top: 16px;
          right: 16px;
          width: var(--dy-width);
          display: flex;
          flex-direction: column;
          background: #ffffff;
          border-radius: 12px;
          box-shadow: 0 14px 40px rgba(0, 0, 0, .28), 0 0 0 1px rgba(0, 0, 0, .06);
          overflow: hidden;
          font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
          color: #1f2329;
        }
        .dy-panel__bar {
          flex: 0 0 34px;
          height: 34px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0 6px 0 10px;
          background: #ffffff;
          border-bottom: 1px solid #eef0f3;
          cursor: move;
          user-select: none;
        }
        .dy-panel__title {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 12px;
          font-weight: 600;
          letter-spacing: .2px;
        }
        .dy-panel__count {
          font-weight: 400;
          color: #8a8f99;
        }
        .dy-panel__actions { display: flex; align-items: center; gap: 2px; }
        .dy-panel__btn {
          width: 24px;
          height: 24px;
          border: 0;
          border-radius: 6px;
          background: transparent;
          color: #646a73;
          font-size: 14px;
          line-height: 1;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .dy-panel__btn:hover { background: #f2f3f5; color: #1f2329; }
        .dy-panel__body {
          position: relative;
          height: var(--dy-height);
          min-height: 0;
        }
        .dy-panel.collapsed .dy-panel__body { display: none; }
        .dy-panel__frame {
          width: 100%;
          height: 100%;
          border: 0;
          display: block;
          background: #fff;
        }
        .dy-panel__loading {
          position: absolute;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #fff;
          color: #8a8f99;
          font-size: 12px;
        }
      </style>
      <div class="dy-panel" id="dy-panel">
        <div class="dy-panel__bar" id="dy-bar">
          <span class="dy-panel__title">
            抖晓晓 抖音视频下载
            <span class="dy-panel__count" id="dy-count"></span>
          </span>
          <span class="dy-panel__actions">
            <button class="dy-panel__btn" id="dy-collapse" title="折叠 / 展开">−</button>
            <button class="dy-panel__btn" id="dy-close" title="关闭面板（不影响正在进行的下载）">✕</button>
          </span>
        </div>
        <div class="dy-panel__body">
          <div class="dy-panel__loading" id="dy-loading">正在加载…</div>
        </div>
      </div>
    `;

    const body = shadow.querySelector(".dy-panel__body");
    panelFrame = document.createElement("iframe");
    panelFrame.className = "dy-panel__frame";
    panelFrame.src = chrome.runtime.getURL("panel.html");
    panelFrame.addEventListener("load", () => {
      const loading = shadow.querySelector("#dy-loading");
      if (loading) loading.remove();
      sendToPanel({ type: "panel_init", items: buildPanelItems() });
    });
    body.appendChild(panelFrame);

    // 折叠 / 展开：仅隐藏面板主体，下载任务继续执行
    shadow.querySelector("#dy-collapse").addEventListener("click", () => {
      const panel = shadow.querySelector("#dy-panel");
      panelState.collapsed = !panelState.collapsed;
      panel.classList.toggle("collapsed", panelState.collapsed);
      shadow.querySelector("#dy-collapse").textContent = panelState.collapsed ? "+" : "−";
    });

    // 关闭：只是隐藏浮层，不影响功能（下载继续进行）
    shadow.querySelector("#dy-close").addEventListener("click", () => hidePanel());

    enableDrag(shadow);

    (document.body || document.documentElement).appendChild(host);
  }

  /** 让标题栏可以拖动整个面板 */
  function enableDrag(shadow) {
    const bar = shadow.querySelector("#dy-bar");
    const panel = shadow.querySelector("#dy-panel");
    let startX = 0;
    let startY = 0;
    let originLeft = 0;
    let originTop = 0;
    let dragging = false;

    bar.addEventListener("mousedown", (event) => {
      // 忽略按钮上的按下事件
      if (event.target.closest("button")) return;
      dragging = true;
      const rect = panel.getBoundingClientRect();
      startX = event.clientX;
      startY = event.clientY;
      originLeft = rect.left;
      originTop = rect.top;
      // 拖动后改用 left/top 定位，避免 right 定位干扰
      panel.style.right = "auto";
      panel.style.left = rect.left + "px";
      panel.style.top = rect.top + "px";
      event.preventDefault();
    });

    window.addEventListener("mousemove", (event) => {
      if (!dragging) return;
      const left = originLeft + (event.clientX - startX);
      const top = originTop + (event.clientY - startY);
      // 限制在视口内，避免拖出屏幕
      const maxLeft = window.innerWidth - 60;
      const maxTop = window.innerHeight - 40;
      panel.style.left = Math.max(-panel.offsetWidth + 80, Math.min(left, maxLeft)) + "px";
      panel.style.top = Math.max(0, Math.min(top, maxTop)) + "px";
    });

    window.addEventListener("mouseup", () => {
      dragging = false;
    });
  }

  /** 显示面板（点扩展图标时调用；只显示，不隐藏，只有点关闭按钮才隐藏） */
  function showPanel() {
    buildPanel();
    if (host && !host.isConnected) (document.body || document.documentElement).appendChild(host);
    if (host) host.style.display = "";
    requestCurrentMedia();
    pushMediaList();
  }

  /** 隐藏面板 */
  function hidePanel() {
    if (host) host.style.display = "none";
  }

  /* ================================================================== */
  /* 四、与面板（iframe）通信                                            */
  /* ================================================================== */

  /** 把列表数据整理成面板需要的结构 */
  function buildPanelItems() {
    return mediaList.map((item) => ({
      shareUrl: item.shareUrl,
      title: item.title,
      author: item.author,
      cover: item.cover,
      size: item.size,
      type: item.type,
      status: item.status || "idle",
      progress: item.progress || 0,
    }));
  }

  /** 向面板发送消息（iframe 与页面跨源，使用 postMessage） */
  function sendToPanel(message) {
    if (panelFrame && panelFrame.contentWindow) {
      panelFrame.contentWindow.postMessage({ source: "dy-dl-content", ...message }, "*");
    }
    // 同步更新标题栏数量
    const countNode = host?.shadowRoot?.querySelector("#dy-count");
    if (countNode) countNode.textContent = mediaList.length ? `（共 ${mediaList.length} 条）` : "";
  }

  /** 接收面板指令 */
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || typeof data !== "object") return;
    if (data.source !== "dy-dl-panel") return;

    switch (data.type) {
      case "panel_ready":
        sendToPanel({ type: "media_list", items: buildPanelItems() });
        break;
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

  /** 批量下载控制标志 */
  let downloadTask = { running: false, stopped: false };

  /*
   * ---------------- 线程数：交给浏览器决定 ----------------
   *
   * 浏览器没有「你该开几个并发」这种 API，但它愿意把自己的内存额度告诉我们，于是走两步：
   *   1) 开跑前算一个初始线程数 = 浏览器内存预算 × 70% ÷ 本次要下的视频平均体积；
   *   2) 跑起来之后不再认死这个数 —— 每次领新任务前先看浏览器的实时堆压力，
   *      吃紧就停手、缓过来再放开。最终同时跑几条，实际由浏览器按当前内存状况说了算。
   */

  /** 内存预算只用到 70% */
  const MEMORY_BUDGET_RATIO = 0.7;

  /** 线程数上下限：下限防止退化成单线程，上限防止极端情况下把机器拖死 */
  const MIN_CONCURRENCY = 2;
  const MAX_CONCURRENCY = 16;

  /** 拿不到视频体积时的兜底估算值 */
  const ASSUMED_VIDEO_BYTES = 80 * 1024 * 1024;

  /**
   * 各线程启动时的错峰间隔（毫秒）。
   *
   * 只用于避免 N 个 worker 在同一毫秒内齐发。**必须让 N × 本值远小于一次下载的耗时**，
   * 否则最后一个 worker 还没启动、第一个已经下完了，线程池根本跑不满
   * （实测：10 线程配 80ms 错峰，跨度 720ms，遇到 300ms 就能返回的小文件时并发峰值只有 8）。
   */
  const DOWNLOAD_STAGGER_MS = 40;

  /** 堆占用超过上限的这个比例 → 暂停领新任务（背压） */
  const HEAP_PAUSE_RATIO = 0.85;
  /** 堆占用回落到这个比例以下 → 恢复领任务（留滞回，避免在临界点反复抖动） */
  const HEAP_RESUME_RATIO = 0.6;
  /** 背压最长等待时间，兜底防止永远卡住 */
  const HEAP_WAIT_TIMEOUT_MS = 20000;

  /**
   * 读浏览器给的内存预算（按可信度排序）：
   *   - `performance.memory.jsHeapSizeLimit`：Chrome 依据物理内存与自身内存策略算出的堆上限，
   *     是「浏览器认为自己能用多少」的直接表态；
   *   - `navigator.deviceMemory`：设备物理内存，粒度很粗（只到 2 的幂且上限 8GB），按一半估可用；
   *   - 都拿不到就用兜底常量。
   */
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

  /**
   * 初始线程数 = 可用预算 ÷ 单条视频体积。
   * 单条体积取接口给的真实 `size`，这是整条估算链里最可信的一项；拿不到就退回兜底常量。
   */
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
   * 运行期背压 —— 「让浏览器自己设置线程数」真正生效的地方。
   * 每次要领新任务前先看一眼堆压力：浏览器吃紧就等着，缓过来再继续。
   * 拿不到 `performance.memory` 就直接放行，不做无谓等待。
   */
  /**
   * 当前堆占用比例；拿不到 `performance.memory` 时返回 -1（表示无信号）。
   *
   * 注意：**必须每次重新读 `performance.memory`**。Chrome 里它是个 getter，
   * 每次访问都返回一份新的快照对象；把 `performance.memory` 存进变量后在轮询里反复读它的属性，
   * 读到的会一直是那一次快照的冻结值，内存明明已经释放了也永远判不出来。
   */
  function readHeapRatio() {
    const perfMemory = performance.memory;
    if (!perfMemory || !perfMemory.jsHeapSizeLimit) return -1;
    return perfMemory.usedJSHeapSize / perfMemory.jsHeapSizeLimit;
  }

  async function waitForHeapHeadroom() {
    const first = readHeapRatio();
    if (first < 0 || first < HEAP_PAUSE_RATIO) return;

    // 已经超压：等它回落到恢复线以下再走
    const deadline = Date.now() + HEAP_WAIT_TIMEOUT_MS;
    while (!downloadTask.stopped && Date.now() < deadline) {
      await sleep(150);
      const ratio = readHeapRatio();
      if (ratio < 0 || ratio < HEAP_RESUME_RATIO) return;
    }
    console.warn("[抖晓晓] 内存持续吃紧，等待超时后继续下载");
  }

  /** 简易 sleep */
  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  /** 格式化字节数（面板展示用） */
  function formatSize(bytes) {
    if (!bytes || bytes <= 0) return "未知大小";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, index)).toFixed(2) + " " + units[index];
  }

  /** 带进度回调的下载（fetch → Blob）；进度按 2% 步进回调，避免过于频繁刷新界面 */
  async function fetchWithProgress(url, onProgress) {
    const response = await fetch(url, { credentials: "omit" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const total = Number(response.headers.get("content-length")) || 0;
    if (!response.body || !onProgress) {
      return await response.blob();
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
      // 无 content-length 时按“已下载字节数”粗略显示，最多到 95%
      const percent = total ? Math.min(99, Math.floor((loaded / total) * 100)) : 95;
      if (percent - lastPercent >= 2 || percent >= 99) {
        lastPercent = percent;
        onProgress(percent);
      }
    }
    return new Blob(chunks, { type: response.headers.get("content-type") || "application/octet-stream" });
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

  /** 音轨检测只读文件开头这么多字节（fMP4 的 moov 就在开头） */
  const AUDIO_PROBE_BYTES = 2 * 1024 * 1024;

  /**
   * 轻量音轨检测：在文件起始区域搜索 mp4 box 中的关键字。
   * fMP4 的 moov（轨道信息）位于文件开头，音频轨会带 "soun" handler 或 "mp4a" 编码标识。
   * 该检测只用于给用户提示，不影响下载本身。
   *
   * 注意：调用方必须传 `blob.slice(0, AUDIO_PROBE_BYTES)` 的结果。
   * 直接对整个 blob 调 arrayBuffer() 会把整份视频再复制进内存一份 —— 222MB 的片子就是 +222MB，
   * 多线程并发时这是最要命的一笔额外开销。
   */
  function hasAudioTrack(arrayBuffer) {
    const size = Math.min(arrayBuffer.byteLength, AUDIO_PROBE_BYTES);
    const bytes = new Uint8Array(arrayBuffer, 0, size);
    return (
      containsAscii(bytes, "soun") ||
      containsAscii(bytes, "mp4a") ||
      containsAscii(bytes, "ac-3")
    );
  }

  /** 在字节序列中查找 ASCII 关键字 */
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

  /**
   * 依次尝试视频的所有候选地址，返回第一个下载成功的 Blob。
   * 抖音不同接口给出的地址音轨情况不一致，逐个试最稳妥。
   */
  async function fetchVideoWithFallback(media, onProgress) {
    const candidates = (media.videoUrls && media.videoUrls.length ? media.videoUrls : [media.videoUrl]).filter(Boolean);
    let lastError = null;

    for (const url of candidates) {
      try {
        const blob = await fetchWithProgress(url, onProgress);
        if (blob.size < 1024) throw new Error("文件内容为空");
        return blob;
      } catch (error) {
        lastError = error;
        console.warn("[抖晓晓] 该地址下载失败，改用下一个候选地址：", url, error);
      }
    }
    throw lastError || new Error("所有候选地址均下载失败");
  }

  /** 上报单条媒体的下载状态给面板 */
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
   *   1. 直接下载视频地址；
   *   2. 解析视频是否带音轨，若没有音轨则尝试合并抖音配乐（尽力而为，失败就保留原视频）；
   *   3. 图集则逐张下载图片。
   */
  async function downloadMedia(media) {
    reportItemStatus(media, "downloading", 0);

    try {
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

      // ---- 视频：依次尝试候选地址下载，成功后立即保存 ----
      const videoBlob = await fetchVideoWithFallback(media, (percent) => {
        reportItemStatus(media, "downloading", Math.floor(percent * 0.9));
      });
      saveBlob(videoBlob, `${media.fileName}.mp4`);

      // ---- 音轨检测（仅提示用）：多数抖音地址本身自带音轨，这里只处理少数纯视频轨的情况 ----
      // 只取开头一小段送检，避免为了看一眼 moov 把整份视频复制进内存
      try {
        if (!hasAudioTrack(await videoBlob.slice(0, AUDIO_PROBE_BYTES).arrayBuffer())) {
          console.warn("[抖晓晓] 当前视频源未检测到音轨");
          if (media.audioUrl) {
            const audioBlob = await fetchWithProgress(media.audioUrl, null);
            const isM4a = /mp4|m4a|aac/i.test(audioBlob.type || "");
            saveBlob(audioBlob, `${media.fileName}_配乐.${isM4a ? "m4a" : "mp3"}`);
            sendToPanel({ type: "toast", message: "该视频源不含音轨，已同时保存配乐文件" });
          }
        }
      } catch (checkError) {
        // 检测失败不影响已保存的视频
        console.warn("[抖晓晓] 音轨检测失败：", checkError);
      }

      reportItemStatus(media, "done", 100);
      return true;
    } catch (error) {
      console.error("[抖晓晓] 下载失败：", error);
      reportItemStatus(media, "error", 0);
      sendToPanel({ type: "item_error", shareUrl: media.shareUrl, message: String(error.message || error) });
      return false;
    }
  }

  /**
   * 批量下载 —— 多线程并发调度。
   *
   * 启动 min(DOWNLOAD_CONCURRENCY, 任务数) 个 worker，每个 worker 循环从队列里
   * 领取下一条任务（游标共享，天然不重复），因此 N 条视频是并行拉取的，
   * 只有全部 worker 都空闲时才结束，整体耗时约为串行方案的 1/N。
   */
  async function startBatchDownload(shareUrls) {
    if (downloadTask.running) return;

    // 已完成的不再重复下载，这里再兜一层保险
    const targets = shareUrls
      .map((url) => mediaList.find((item) => item.shareUrl === url))
      .filter((item) => item && item.status !== "done");

    if (!targets.length) {
      sendToPanel({ type: "toast", message: "没有需要下载的视频（已完成的不再重复下载）" });
      return;
    }

    downloadTask = { running: true, stopped: false };

    const total = targets.length;
    // 线程数由浏览器内存预算算出；跑起来之后还有背压动态收放
    const plan = resolveConcurrency(targets);
    const workerCount = Math.min(plan.threads, total);
    console.log("[抖晓晓] 线程数", workerCount, "=", plan.source, "预算", formatSize(plan.usableBytes), "÷ 单条", formatSize(plan.perVideo));
    sendToPanel({ type: "batch_started", total, concurrency: workerCount, memory: plan });

    /** 共享游标：每个 worker 靠它领取任务，不会重复 */
    let cursor = 0;
    /** 已完成计数（含失败，用于进度显示） */
    let completed = 0;
    /** 成功 / 失败分别统计，便于结束时给出准确结论 */
    let succeeded = 0;
    let failed = 0;

    /** 单个 worker：只要队列里还有任务且没被叫停，就继续领活 */
    async function worker(workerIndex) {
      // 错峰启动，避免 N 个请求在同一毫秒砸出去
      await sleep(workerIndex * DOWNLOAD_STAGGER_MS);

      while (!downloadTask.stopped) {
        const index = cursor++;
        if (index >= total) return;

        // 领到任务后先看浏览器内存压力，吃紧就让路（背压）
        await waitForHeapHeadroom();
        if (downloadTask.stopped) return;

        const media = targets[index];
        const ok = await downloadMedia(media);

        if (ok) {
          succeeded++;
          markDownloaded(media.shareUrl); // 记入历史，防止下次重复下载
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

  /** 停止批量下载（各线程正在下的那一条下完即停，不再领取新任务） */
  function stopBatchDownload() {
    downloadTask.stopped = true;
  }

  /* ================================================================== */
  /* 六、与 background.js 通信                                           */
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

  // 面板未打开时也保持列表更新：页面加载后先索要一次当前视频
  setTimeout(requestCurrentMedia, 1200);
})();
