# video_downloader

A Chrome extension (Manifest V3) that automatically detects videos on web pages and downloads them.

![Manifest V3](https://img.shields.io/badge/Manifest-V3-blue)
![Vanilla JS](https://img.shields.io/badge/JS-Vanilla-yellow)
![No Build](https://img.shields.io/badge/Build-None-green)
![License](https://img.shields.io/badge/License-MIT-green)

## Features

- **Multi-platform extractors**: Douyin, Kuaishou, Bilibili, Weibo, Xiaohongshu, Xigua — reads each site's player data (title / author / cover / duration / multi-quality URLs)
- **Generic sniffing**: any other site is covered by scanning `<video>` elements and observing network resources; direct links and m3u8 playlists both picked up
- **Extension icon badge**: shows the number of detected videos on the current tab in real time
- **Modern panel UI**: card-style list, platform filter chips, live search, sort by newest/largest, video preview modal with frosted-glass backdrop
- **Per-card actions**: hover to quick-download a single video, or copy its direct link with one click
- **Batch downloads**: select all / invert / pick individually, adaptive concurrent workers, already-downloaded items skipped
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
3. Click the icon → floating panel appears in the top-right corner
4. Hover a card to preview, quick-download, or copy its link
5. Check items → click **Download selected**
6. Files land in your browser's default download directory

### Panel guide

| Area | What it does |
|---|---|
| Search box | Live filter by title / author |
| Platform chips | Filter by platform (only shown when 2+ platforms detected) |
| Sort button | Toggle newest-first / largest-first |
| Card hover | Play button (preview) · copy-link button · quick-download button |
| Bottom bar | Download selected / stop; shows count and total size |

## Supported platforms

| Platform | Extractor | Notes |
|---|---|---|
| 抖音 Douyin | `extractors/douyin.js` | `window.player` data |
| 快手 Kuaishou | `extractors/kuaishou.js` | video elements + embedded JSON |
| 哔哩哔哩 Bilibili | `extractors/bilibili.js` | `window.__playinfo__`; DASH audio captured separately |
| 微博 Weibo | `extractors/weibo.js` | video elements + URL field scan |
| 小红书 Xiaohongshu | `extractors/xiaohongshu.js` | `__INITIAL_STATE__` |
| 西瓜视频 Xigua | — | Generic sniffing |
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
              Shadow DOM + iframe → panel.html/js (UI only)
```

**Why two worlds**: page JS variables are only readable from the main world, so extraction runs there; downloading requires bypassing CORS, which only the isolated world can do. Extractors never download.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## Download behavior

- **Direct links**: candidate URLs tried in order until one succeeds
- **m3u8**: highest-bitrate variant selected → segments downloaded concurrently (6 workers) → merged
- **DASH video-only** (e.g. Bilibili): separate audio track downloaded automatically as `*_audio.m4a`
- **Concurrency**: adaptive — `memory budget × 70% ÷ avg video size`, clamped to 2–16 workers; heap pressure monitored live
- **Encrypted streams** (`EXT-X-KEY`): reported as unsupported, never silently skipped

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). To add a platform, see [docs/ADD_PLATFORM.md](docs/ADD_PLATFORM.md).

## License

[MIT](LICENSE)
