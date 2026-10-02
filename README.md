# video_downloader

A Chrome extension (Manifest V3) that automatically detects videos on web pages and downloads them.

- **Platform-specific extractors**: Douyin, Kuaishou, Bilibili, Weibo, Xiaohongshu — reads each site's player data, with title / author / cover / multi-quality URL candidates
- **Generic sniffing**: any other site is covered automatically by scanning page `<video>` elements and observing network resources; direct links and m3u8 playlists are both picked up
- **Batch downloads**: select all / invert / pick individual items, concurrent fetching, already-downloaded items are skipped
- **m3u8 merging**: segments are downloaded and merged into a single file (`.ts` for TS segments, `.mp4` for fMP4)
- **Zero build step**: vanilla JS, no frameworks, no dependencies, no bundler — edit the code and hit reload in the extension page

---

## Project structure

```
.
├── manifest.json           # Extension manifest (MV3): extractor scripts registered per domain
├── rules.js                # ★ Rule hub: platform detection / media matching / exclusions / filenames / download behavior
├── background.js           # Service worker: icon click → find tab → show panel
├── content.js              # Content script (isolated world): merges extraction results, floating panel, download scheduler
├── extractors/             # Main-world extractor scripts (reads page JS variables the isolated world can't see)
│   ├── common.js           #   Shared base: reporting protocol, idempotency guard, small utilities
│   ├── douyin.js           #   Douyin: window.player.config.awemeInfo
│   ├── kuaishou.js         #   Kuaishou: video elements + embedded JSON scan
│   ├── bilibili.js         #   Bilibili: window.__playinfo__ (durl first, dash fallback)
│   ├── weibo.js            #   Weibo: video elements + mp4_hd_url style field scan
│   └── xiaohongshu.js      #   Xiaohongshu: __INITIAL_STATE__ masterUrl
├── panel.html/.css/.js     # iframe panel UI (vanilla JS, no framework)
├── images/                 # Extension icons 16/32/128
├── .github/workflows/
│   ├── ci.yml              # CI: JS syntax + manifest validation (runs on push to main / PRs)
│   └── release.yml         # Packaging: only runs on v* tags, builds the zip and creates a Release
├── AGENTS.md               # Notes for AI coding assistants (architecture constraints, gotchas)
└── README.md
```

---

## Installation

1. Open Chrome and go to `chrome://extensions/`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** and select the repository root
4. No build step — after editing code, just hit the reload button on the extension card

> Requires Chrome 111+ (Manifest V3 + main-world content scripts via `world: "MAIN"`). Edge and other Chromium-based browsers work the same way.
>
> On first load Chrome will warn that the extension can "read and change all your data on all websites" — that's the `host_permissions: <all_urls>` the generic sniffer needs (downloading direct links requires bypassing CORS). The extension never uploads anything.

## Usage

1. Open any video site and play a video (**played** videos are the ones that get recorded; sniffed direct links enter the list in real time)
2. Click the extension icon → a floating panel appears in the top-right corner of the page
3. Check the items you want → click **Download selected**
4. Files land in your browser's default download directory

Each row carries a small "platform · source" tag so you can see at a glance which platform an item came from and how it was captured (page parsing / DOM sniffing / network sniffing). If the panel doesn't show up (the extension was just reloaded while the page was already open), the service worker re-injects once automatically; if that still fails, refresh the page and click the icon again.

## How it works

```
Page
 ├─ Main world                                Isolated world
 │   extractors/*.js                           content.js
 │   reads page JS vars (window.player /        ▲ dedup + registry (extractor reports
 │   __playinfo__ / __INITIAL_STATE__)          │   + DOM scan + resource sniffing)
 │        │                                     │
 │        │ window.postMessage                  │ fetch (covered by host_permissions,
 │        ▼                                     │        no CORS restrictions)
 │   normalized media objects ────────────────▶ │──▶ Blob ──▶ <a download> ──▶ download dir
 │                                              │
 └──────────────────────────────────────────────┘
              Shadow DOM host + iframe: panel.html/js (pure UI)
```

**Why two worlds are required**: page JS variables are only readable from the main world, so extraction must be injected there; downloading media URLs requires bypassing CORS, which only the isolated world can do (the extension holds `host_permissions`). So "extract" and "download" are split. Main-world extractor scripts **never download** (the page's CSP would block them).

## Download rules (rules.js)

All rules live in `rules.js` — to change a rule, edit this one file:

| # | Rule | Description |
|---|---|---|
| 1 | Platform detection | Domain matching: `douyin.com` → Douyin, `kuaishou.com` → Kuaishou, `bilibili.com` → Bilibili, `weibo.com` → Weibo, `xiaohongshu.com` → Xiaohongshu; anything else → "generic" |
| 2 | Media matching | A URL ending in `.m3u8/.mp4/.webm/.mov/.flv/.ts/.m4s/.m4a/.mp3` etc. is treated as downloadable media |
| 3 | Exclusions | Ad/tracking domains, thumbnails/previews, danmaku/subtitle files are ignored and never enter the list |
| 4 | Filenames | Author/title present → `author_title`; otherwise → `platform_timestamp`; illegal characters are sanitized |
| 5 | Download behavior | Direct links: candidate URLs are tried in order until one succeeds; m3u8: highest bitrate picked → segments downloaded concurrently → merged; encrypted streams (EXT-X-KEY) are reported as unsupported |

Adding a platform takes three steps: ① add a row to `PLATFORMS` in `rules.js`; ② add an extractor script in `extractors/` (copy an existing platform); ③ register it in `manifest.json`'s `content_scripts` with `world: "MAIN"`.

## Script responsibilities

| File | Runtime | Responsibility |
|---|---|---|
| `rules.js` | shared by both | The single source of rules: platform detection, media matching, exclusions, filenames, download behavior |
| `background.js` | Service worker | Only "find + relay": locate the tab → send `show_panel`; re-inject `content.js` when needed |
| `extractors/common.js` | Page main world | Shared extractor base: idempotency guard, reporting protocol, small utilities |
| `extractors/<platform>.js` | Page main world | Per-platform extraction: read player data → normalize into media objects → report. **No download logic** |
| `content.js` | Page isolated world | Three-way merge (extractor reports / DOM scan / resource sniffing), dedup registry, floating panel, download + concurrency scheduling |
| `panel.js` | inside iframe | Pure UI: render list, collect selections, show progress; talks to `content.js` via `postMessage` |

## Message protocol

`content.js` ⇄ `panel.js` (cross-origin iframe, `postMessage`):

| Direction | type | Description |
|---|---|---|
| content → panel | `media_list` / `panel_init` | Full list refresh (includes platform / source fields) |
| content → panel | `item_status` | Per-item status and progress (0–100) |
| content → panel | `item_error` | Per-item failure reason |
| content → panel | `batch_started` / `batch_progress` / `batch_finished` | Batch lifecycle |
| content → panel | `toast` | Lightweight notification |
| panel → content | `panel_ready` / `start_download` / `stop_download` | Panel ready, start, stop |

`extractors/*.js` ⇄ `content.js` (same window, `window.postMessage`, distinguished by the `source` field):
`media_found` (report), `request_current_media` (re-extract).

## Concurrency: let the browser decide the thread count

Instead of a hardcoded worker count, it's adaptive in two steps (see `content.js`):

1. **Estimate before starting**: `browser memory budget × 70% ÷ average video size for this batch`, clamped to `2–16`. Budget priority: `performance.memory.jsHeapSizeLimit` > half of `navigator.deviceMemory` > fallback constant (80 MB per item).
2. **Watch live heap pressure while running**: each worker checks heap usage before taking a new task; above 85% it stops taking work, resumes below 60% (hysteresis to avoid flapping), with a 20 s max wait as a backstop.

Supporting details: workers start staggered (40 ms apart, so they don't fire in the same millisecond); a shared cursor hands out tasks, so nothing is fetched twice; `fetch` progress callbacks fire at 2% steps to avoid hammering the UI; m3u8 segments use a separate small pool (6 workers).

## Packaging & releases

- **CI** (`.github/workflows/ci.yml`): runs on push to `main` / PRs — JS syntax checks for all scripts + `manifest.json` validation (referenced files exist, extractor scripts are all registered).
- **Packaging** (`.github/workflows/release.yml`): **only triggers on `v*` tags**; pushing to `main` never builds. It verifies the tag matches `manifest.json`'s `version`, then builds the zip and creates a GitHub Release with the zip attached.

```bash
# Release flow
# 1. Bump version in manifest.json (e.g. 2.4.0)
# 2. Commit and push to main
# 3. Tag and push → packaging + Release happen automatically
git tag v2.4.0 && git push origin v2.4.0
```

## Known limitations

- **Only played videos are recorded**: platform-specific extraction comes from player instances — nothing enters the list until you play it; the generic sniffer picks up media resources the page has loaded
- **Direct links expire**: platforms like Bilibili sign URLs with a short lifetime — download soon after capture
- **Encrypted streams unsupported**: m3u8 playlists with `EXT-X-KEY` fail with an explicit message, never silently skipped
- **`blob:` URLs are not collected**: in-page temporary URLs die on navigation and can't be downloaded, so they're filtered out
- **Duplicate filenames**: the browser appends `(1)` automatically; the extension doesn't rename
- **Audio-track detection is heuristic**: it only scans the first 2 MB for `soun` / `mp4a` / `ac-3` markers, informational only — failures don't affect downloads
- **`performance.memory` is Chrome-only**: other engines fall back to the device-memory estimate
- **Stop means "finish the current item"**: workers stop taking new tasks; in-flight requests run to completion
- **Top frame only**: content scripts inject into the top-level page; players nested in iframes aren't covered yet

---

## Development & contributing

- **No build step**: after editing, hit reload on the extension card at `chrome://extensions/`.
- **Pre-commit self-check** (CI runs the same checks):
  ```bash
  for f in background.js rules.js content.js panel.js extractors/*.js; do node --check "$f"; done
  node -e "JSON.parse(require('fs').readFileSync('manifest.json','utf8'))"
  ```
- For larger features, say **what** changed and **why** in the commit message; performance changes should include measured before/after data.
- Commit messages in English, format `type: brief`, types: `feat` / `fix` / `perf` / `docs` / `refactor` / `chore`.
- Project conventions for AI coding assistants, architecture invariants and gotchas: see [AGENTS.md](AGENTS.md).
