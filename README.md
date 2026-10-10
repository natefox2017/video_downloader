# video_downloader

> GitHub: https://github.com/natefox2017/video_downloader

A Chrome extension (Manifest V3) that automatically detects videos on web pages and downloads them.

![Manifest V3](https://img.shields.io/badge/Manifest-V3-blue)
![Vanilla JS](https://img.shields.io/badge/JS-Vanilla-yellow)
![No Build](https://img.shields.io/badge/Build-None-green)
![License](https://img.shields.io/badge/License-MIT-green)

## Features

- **Multi-platform extractors**: Douyin, Kuaishou, Bilibili, Weibo, Xiaohongshu, Xigua — reads each site's player data (title / author / cover / duration / multi-quality URLs)
- **Verified generic sniffing**: page `<video>` elements and matching network requests become entries only after the extension service worker validates MIME/container bytes or a video HLS manifest; standalone audio and media chunks are filtered
- **Extension icon badge**: shows the number of detected videos on the current tab in real time, over a blue rounded-square icon with one centered white downward arrow (16/32/48/128px PNG)
- **Cross-tab video monitor**: the Batch Download item in the shared left sidebar opens a standalone Monitor page aggregating videos from all open tabs
- **Settings page**: sidebar-style admin UI for source format / MP4 preference, preferred quality, per-platform extractor-vs-sniffer strategy, per-site batch concurrency and remembered panel position; repository and bug-report links live in the sidebar
- **Compact panel UI**: shows video title, real known file size / quality, a small preview thumbnail, selection when needed, and essential download status. The content height adapts to the number of rows; only lists exceeding the 480px body cap (or the remaining viewport) scroll
- **Batch downloads**: multi-select, per-site checkboxes, filtered search, independent tab identities, real transfer-byte progress, a 20-second idle-transfer timeout, completed-byte verification, bounded network retries and adaptive memory-bounded concurrency
- **m3u8 merging**: segments downloaded concurrently and merged into a single file (`.ts` for TS, `.mp4` for fMP4); encrypted streams reported as unsupported
- **Audio track handling**: detects DASH video-only streams (e.g. Bilibili) and downloads the separate audio track automatically
- **Zero build step**: vanilla JS, no frameworks, no dependencies, no bundler

## Screenshots

> *(Add screenshots here after loading the extension)*

## Installation

### From source (developer mode)

1. Clone the repository:
   ```bash
   git clone https://github.com/natefox2017/video_downloader.git
   ```
2. Open Chrome and go to `chrome://extensions/`
3. Enable **Developer mode** (top right)
4. Click **Load unpacked** and select the repository root
5. No build step — after editing code, hit the reload button on the extension card

> Requires Chrome 111+ (Manifest V3). Edge and other Chromium-based browsers work the same way.
>
> On first load Chrome will warn that the extension can "read and change all your data on all websites" — that's the `host_permissions` the generic sniffer needs. The extension never uploads anything.

### From a published release (recommended)

Download `video-downloader-v<version>-chrome-web-store.zip` from [GitHub Releases](../../releases). This is the production-minified Chrome extension ZIP: either submit it directly to the Chrome Web Store Developer Dashboard, or extract it and use Chrome's **Load unpacked** to test.

### Automatic Chrome Web Store packaging

Only a `v`-prefixed version tag (e.g. `v2.4.0`) triggers the release workflow:

```bash
git checkout main
git pull --ff-only origin main
git tag -a v2.4.0 -m "Release v2.4.0"
git push origin v2.4.0
```

The workflow stamps the released `manifest.json` with version `2.4.0`, minifies individual JavaScript files using pinned **esbuild** (without bundling or obfuscation), then publishes a Chrome Web Store ZIP and `SHA256SUMS.txt`. All extension pages, Pico CSS, assets and license notices are included. Source files in Git are unchanged.

Chrome Web Store **allows standard minification but prohibits code encryption/obfuscation intended to conceal functionality**. You must upload the **ZIP**, not the CRX. An **optional** signed CRX3 is also generated if the `CRX_PRIVATE_KEY` repository secret is configured; missing the key never blocks the store ZIP.

See [Chrome Web Store release and submission guide](docs/RELEASE_CRX.md) for review requirements, screenshots/privacy steps and private-key setup. Passing the automated package tests does not guarantee Google review approval.

## Usage

1. Open any supported video site and **play** a video (played videos are the ones that get recorded)
2. The extension icon shows a badge with the detected video count
3. Click the icon → floating panel appears; its last dragged position is restored when enabled in Settings
4. Check the rows you want (newly detected videos are selected automatically)
5. Click **Download selected**
6. For cross-site batches, open Settings and choose **批量下载** in the left sidebar. Search or expand each website group, check only the desired videos, optionally adjust the per-site concurrency (2/4/6/8 or automatic), and click **下载选中的视频**. Click any thumbnail to preview the direct video source; unsupported HLS/restricted sources show a clear message
7. Files land in your browser's default download directory

### Panel guide

| Area | What it does |
|---|---|
| Result row | Shows a small preview thumbnail, video title, file size, and selection when multiple videos are detected |
| Preview | Click the thumbnail to open a standalone viewport-sized video dialog *outside* the 320px panel iframe; close with Escape, backdrop or close button |
| Bottom bar | Download the selected video(s) or stop the active batch |
| Panel header | Drag the title bar to reposition. Collapsing creates a draggable purple circular launcher with a white arrow and shadow; click it to expand. Settings opens the standalone options page |

## Supported platforms

| Platform | Extractor | Notes |
|---|---|---|
| 抖音 Douyin | `extractors/douyin.js` | `window.player` data |
| 快手 Kuaishou | `extractors/kuaishou.js` | video elements + embedded JSON |
| 哔哩哔哩 Bilibili | `extractors/bilibili.js` | `window.__playinfo__`; DASH audio captured separately |
| 微博 Weibo | `extractors/weibo.js` | video elements + URL field scan |
| 小红书 Xiaohongshu | `extractors/xiaohongshu.js` | `__INITIAL_STATE__` |
| 西瓜视频 Xigua | — | Generic sniffing |
| YouTube | `extractors/youtube.js` | `ytInitialPlayerResponse`; progressive URLs preferred |
| TikTok | `extractors/tiktok.js` | `__UNIVERSAL_DATA_FOR_REHYDRATION__` |
| Vimeo | `extractors/vimeo.js` | player config; progressive MP4 preferred |
| Twitch | `extractors/twitch.js` | GQL playback token → usher m3u8 (live/VOD) |
| Instagram | `extractors/instagram.js` | `video_url` in page JSON |
| Facebook | `extractors/facebook.js` | `playable_url` / HD variant in page JSON |
| X (Twitter) | `extractors/twitter.js` | `video_info.variants`; highest bitrate MP4 |
| Other sites | — | Generic sniffing (video elements + network resources) |

## Project structure

```
.
├── manifest.json           # Extension manifest (MV3)
├── rules.js                # ★ Rule hub: platform detection / media matching / filenames
├── background.js           # Service worker: icon click → show panel, badge updates
├── content.js              # Content script: extraction merge, panel host, download engine
├── extractors/             # Main-world extractors (read page JS vars)
│   ├── common.js           #   Shared base: protocol, guards, utilities
│   ├── douyin.js
│   ├── kuaishou.js
│   ├── bilibili.js
│   ├── weibo.js
│   └── xiaohongshu.js
├── panel.html / .css / .js # Floating panel UI (iframe, vanilla JS)
├── options.html / .css / .js # Download preferences and per-platform settings
├── monitor.html / .css / .js # Separate cross-tab video monitor and batch queue
├── vendor/pico.min.css     # Vendored Pico CSS 2.1.1 (MIT), settings UI
├── images/                 # Extension icons
├── docs/                   # Detailed documentation
│   ├── ARCHITECTURE.md     #   How it works (two worlds, message protocol)
│   └── ADD_PLATFORM.md     #   How to add a new platform
├── scripts/release.mjs     # Tag parsing, clean extension staging and CRX3 header checks
├── scripts/build-store.sh  # Release-only esbuild minification and Chrome Web Store ZIP
├── .github/workflows/
│   ├── ci.yml              # JS syntax, tests, and disposable package smoke test
│   ├── release.yml         # v* tag: minified Chrome Web Store ZIP + optional CRX3
│   └── automerge.yml       # Auto-merge non-draft PRs
├── AGENTS.md               # Notes for AI coding assistants
├── CONTRIBUTING.md
├── LICENSE
└── README.md
```

## How it works

```
Page
 ├─ Main world (page JS context)          Isolated world (extension context)
 │   extractors/*.js                       content.js
 │   reads player data                      ▲ merges extractor reports
 │   (window.player / __playinfo__ /        │   + DOM scan + resource sniffing
 │    __INITIAL_STATE__)                    │
 │        │                                 │ fetch (host_permissions,
 │        │ window.postMessage              │        no CORS limits)
 │        ▼                                 │
 │   normalized media objects ────────────▶ │──▶ Background Range/MIME probe
 │                                          │     │ verified only
 │                                          │     ▼
 │                                          │──▶ Blob ──▶ download
 │                                          │
 └──────────────────────────────────────────┘
              Shadow DOM host → panel.html/js (iframe, UI only)
                             → viewport preview dialog (sibling of panel)
```

**Why the separate contexts**: page JS variables are read in the main world, which reports candidate URLs to the isolated content script. Cross-origin validation runs in the extension service worker (which has the declared host permissions). Actual downloads still use the existing tab-local content-script fetch/Blob pipeline; **MV3 content-script fetch remains subject to CORS**, so a CDN that denies cross-origin access may still prevent a download. Extractors never download.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## Download behavior

- **Direct links**: candidate URLs are ordered by saved format/quality preferences, then tried with fallback. Temporary failures retry a URL once with bounded backoff, resetting progress. Identity-encoded responses must match the declared Content-Length before saving; permanently invalid video responses and HTTP 403/404 never retry. This does not support resuming a large partial file across browser sessions
- **m3u8**: master-playlist variants follow the saved quality preference when resolution metadata is present → playlists retry once on transient failure → segments download concurrently (6 workers) and retry individually up to twice → merge in playlist order. A permanent segment failure aborts sibling requests for that playlist only, without aborting other videos in the batch
- **DASH video-only** (e.g. Bilibili): separate audio track downloaded automatically as `*_audio.m4a`
- **Concurrency**: a per-originating-tab preference (2/4/6/8 concurrent videos; 4 by default; automatic mode available) caps the adaptive estimate of `memory budget × 70% ÷ avg video size` (2–16 workers), additionally limited to approximately 512 MiB of concurrently fetched known video sizes (unknown sizes default to two concurrent workers). Heap pressure is still monitored live. Multiple tabs run independently; this is not a global concurrency cap
- **Encrypted streams** (`EXT-X-KEY`): reported as unsupported, never silently skipped
- **Media verification**: the service worker probes at most the first 16 KiB, rejects HTML/audio/isolated segments, and publishes videos only when source evidence is present. Signed URLs can expire after verification; final download failures remain visible as errors rather than indefinite progress.
- **Reference implementations**: [yt-dlp fragment retry policy](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/downloader/fragment.py) and [Cobalt transfer worker](https://github.com/imputnet/cobalt/blob/main/web/src/lib/task-manager/workers/fetch.ts) informed the bounded-retry and complete-byte verification approach. We keep a simpler in-memory implementation without copying those projects' code or introducing extra dependencies.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). To add a platform, see [docs/ADD_PLATFORM.md](docs/ADD_PLATFORM.md).

## License

[MIT](LICENSE)


## Settings implementation

The standalone Settings and Monitor pages use a **responsive browser-web layout**: on wide screens, a compact left-side navigation card links **批量下载 / 插件设置**, while on narrow screens the links move above the page content. The pages scroll naturally rather than simulating a fixed-height desktop application. Both pages use vendored **Pico CSS 2.1.1 (MIT)** plus local, accessible **UI component styles** for buttons, checkboxes, selects, status badges, progress bars and a video preview dialog. The UI uses flat surfaces, a blue rounded-square download-arrow logo and no gradients. They do not use a CDN or add a build step. The format setting never transcodes media: “MP4 preferred” only prioritizes an MP4 source when the site actually exposes one. Quality labels and sizes are shown only when an extractor or playlist provides real metadata; otherwise the UI reports them as unknown.

The dedicated `monitor.html` page shows the cross-tab detected-video list, grouped by website tab. The registry is stored temporarily in `chrome.storage.session`; updates from multiple tabs are serialized to avoid lost reports. The session registry holds per-tab media keys and UI-only fields (title, cover, preview URL, size, quality, status and throttled progress); no download candidate lists are duplicated into it. Checked video selections persist during live updates and search/collapse. Downloads still execute inside each originating tab's `content.js`. Closing or navigating a tab removes its prior entries.
