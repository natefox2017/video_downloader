# video_downloader

> GitHub: https://github.com/natefox2017/video_downloader

A Chrome extension (Manifest V3) that automatically detects videos on web pages and downloads them.

![Manifest V3](https://img.shields.io/badge/Manifest-V3-blue)
![Vanilla JS](https://img.shields.io/badge/JS-Vanilla-yellow)
![No Build](https://img.shields.io/badge/Build-None-green)
![License](https://img.shields.io/badge/License-MIT-green)

## Features

- **Multi-platform extractors**: Douyin, Kuaishou, Bilibili, Weibo, Xiaohongshu, Xigua — reads each site's player data (title / author / cover / duration / multi-quality URLs)
- **Generic sniffing**: any other site is covered by scanning `<video>` elements and observing network resources; direct links and m3u8 playlists both picked up
- **Extension icon badge**: shows the number of detected videos on the current tab in real time, over a blue rounded-square icon with one centered white downward arrow (16/32/48/128px PNG)
- **Cross-tab video monitor**: the Batch Download item in the shared left sidebar opens a standalone Monitor page aggregating videos from all open tabs
- **Settings page**: sidebar-style admin UI for source format / MP4 preference, preferred quality, per-platform extractor-vs-sniffer strategy, per-site batch concurrency and remembered panel position; repository and bug-report links live in the sidebar
- **Compact panel UI**: shows video title, real known file size / quality, a small preview thumbnail, selection when needed, and essential download status. The content height adapts to the number of rows; only lists exceeding the 480px body cap (or the remaining viewport) scroll
- **Batch downloads**: multi-select, per-site checkboxes, filtered search, independent tab identities, per-item progress and concurrent dispatch to originating tabs
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

### From release (recommended)

Download the latest `.zip` from [Releases](../../releases), unzip, and load unpacked as above.

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
| Panel header | Drag the title bar to reposition. Collapsing creates a draggable white circular launcher with a purple arrow and shadow; click it to expand. Settings opens the standalone options page |

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
├── .github/workflows/
│   ├── ci.yml              # JS syntax + manifest validation
│   ├── release.yml         # Packaging on v* tags
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
 │   normalized media objects ────────────▶ │──▶ Blob ──▶ download
 │                                          │
 └──────────────────────────────────────────┘
              Shadow DOM host → panel.html/js (iframe, UI only)
                             → viewport preview dialog (sibling of panel)
```

**Why two worlds**: page JS variables are only readable from the main world, so extraction runs there; downloading requires bypassing CORS, which only the isolated world can do. Extractors never download.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## Download behavior

- **Direct links**: candidate URLs are ordered by the saved format / quality preference when real variant metadata is available, then tried with fallback
- **m3u8**: master-playlist variants follow the saved quality preference when resolution metadata is present → segments downloaded concurrently (6 workers) → merged
- **DASH video-only** (e.g. Bilibili): separate audio track downloaded automatically as `*_audio.m4a`
- **Concurrency**: a per-originating-tab preference (2/4/6/8 concurrent videos; 4 by default; automatic mode available) caps the adaptive estimate of `memory budget × 70% ÷ avg video size` (2–16 workers). Heap pressure is still monitored live. Multiple tabs run independently; this is not a global concurrency cap
- **Encrypted streams** (`EXT-X-KEY`): reported as unsupported, never silently skipped

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). To add a platform, see [docs/ADD_PLATFORM.md](docs/ADD_PLATFORM.md).

## License

[MIT](LICENSE)


## Settings implementation

The standalone Settings and Monitor pages use a **responsive browser-web layout**: on wide screens, a compact left-side navigation card links **批量下载 / 插件设置**, while on narrow screens the links move above the page content. The pages scroll naturally rather than simulating a fixed-height desktop application. Both pages use vendored **Pico CSS 2.1.1 (MIT)** plus local, accessible **UI component styles** for buttons, checkboxes, selects, status badges, progress bars and a video preview dialog. The UI uses flat surfaces, a blue rounded-square download-arrow logo and no gradients. They do not use a CDN or add a build step. The format setting never transcodes media: “MP4 preferred” only prioritizes an MP4 source when the site actually exposes one. Quality labels and sizes are shown only when an extractor or playlist provides real metadata; otherwise the UI reports them as unknown.

The dedicated `monitor.html` page shows the cross-tab detected-video list, grouped by website tab. The registry is stored temporarily in `chrome.storage.session`; updates from multiple tabs are serialized to avoid lost reports. The session registry holds per-tab media keys and UI-only fields (title, cover, preview URL, size, quality, status and throttled progress); no download candidate lists are duplicated into it. Checked video selections persist during live updates and search/collapse. Downloads still execute inside each originating tab's `content.js`. Closing or navigating a tab removes its prior entries.
