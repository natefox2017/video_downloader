# Architecture

## Two worlds

Chrome extensions run content scripts in an **isolated world**, separate from the page's own JavaScript (**main world**). This extension needs both:

| Need | World | Why |
|---|---|---|
| Read player data (`window.player`, `window.__playinfo__`, `__INITIAL_STATE__`) | Main | Page JS variables are invisible from the isolated world |
| Probe media bytes across origins | Extension service worker | MV3 host permissions apply to extension fetches, not to content-script cross-origin requests |
| Download verified media | Isolated | Existing page-tab fetch/Blob pipeline retains its per-tab workers; cross-origin CORS restrictions may still apply |

So the pipeline is split:

```
extractors/*.js (main world)
  → read player data → normalize to media objects
  → window.postMessage → content.js (isolated world)
  → dedup + merge with DOM scan + resource sniffing
  → background.js bounded Range verification (MIME + signatures / HLS)
  → only confirmed videos appear in panel/monitor
  → tab fetch → Blob → <a download>
```

**Rule**: extractors never download. Content script never reads page JS variables directly. The service worker verifies untrusted candidate URLs (up to 16 KiB) with host permissions; this does not lift CORS for the subsequent content-script download.

## Components

### rules.js — the rule hub

Pure functions, no `window`/`document`/`chrome.*`. Single source of truth for:

1. **Platform detection** — domain → platform (`PLATFORMS` table)
2. **Media matching** — URL patterns that count as downloadable media
3. **Exclusions** — ad/tracking domains, thumbnails, subtitle files
4. **Filenames** — `author_title` or `platform_timestamp`, sanitized
5. **Download behavior** — candidate fallback order, m3u8 handling

To change a rule, edit this one file.

### background.js — service worker

Minimal responsibilities:

- Icon click → find target tab → send `show_panel`
- Re-inject content scripts if the page was loaded before the extension
- `update_badge` messages → `chrome.action.setBadgeText` per tab
- Clear badge on tab close / navigation

### extractors/ — main world

- `common.js`: `VDExtractor.create()` — idempotency guard, reporting protocol, utilities (`scanScriptUrls`, `collectVideoElementUrls`, etc.)
- `<platform>.js`: `detect()` reads page data → returns normalized media object (or `null`)

Media object shape:

```js
{
  shareUrl,       // dedup key
  platformId, platform,
  title, desc, author, cover, duration, size,
  type: "视频" | "图集",
  videoUrl, videoUrls[],   // candidates, best first
  audioUrl,                // separate audio track (DASH), if any
  imageUrls[],
  fileName,                // without extension; "" = auto-generated
  source: "页面解析",
}
```

Extractors poll every ~1.5s (SPA navigation doesn't reload the page).

### content.js — isolated world

Three ingestion paths merged into one registry, with an asynchronous verification gate:

1. **Extractor reports** (`media_found` via `window.postMessage`)
2. **DOM scan** — `<video>` elements
3. **Resource sniffing** — `PerformanceObserver` on resource entries

Audio URLs, isolated .ts/.m4s chunks, and HTML/error responses are not confirmed standalone videos. Unverified entries are not published to the badge, floating panel or cross-tab monitor. Plus: floating panel host (Shadow DOM + iframe), independently layered video-preview dialog (sibling in the same shadow root), download scheduler, concurrency control.

### panel.* — UI (iframe)

Pure presentation. Talks to `content.js` via `postMessage` (cross-origin). Never touches the page directly. Its iframe only contains the media list and download action; the preview video element is rendered by `content.js` as a viewport-fixed sibling of the panel, outside the panel's `contain: paint` boundary.

## Message protocol

### content.js ⇄ panel.js

| Direction | type | Payload |
|---|---|---|
| content → panel | `media_list` / `panel_init` | Full item array |
| content → panel | `item_status` | `{ shareUrl, status, progress }` |
| content → panel | `item_error` | `{ shareUrl, message }` |
| content → panel | `batch_started` | `{ total, concurrency, memory }` |
| content → panel | `batch_progress` | `{ completed, total }` |
| content → panel | `batch_finished` | `{ completed, succeeded, failed, total, stopped }` |
| content → panel | `toast` | `{ message }` |
| content → panel | `preview_closed` | `{ shareUrl }` (restore focus to thumbnail) |
| panel → content | `panel_ready` | — |
| panel → content | `panel_resize` | `{ height }` measured row heights + action bar; parent caps at 480px and remaining viewport height |
| panel → content | `open_preview` | `{ shareUrl }` (open independent shadow-root video dialog) |
| panel → content | `start_download` | `{ shareUrls[] }` |
| panel → content | `stop_download` | — |

### extractors ⇄ content.js

Same-window `window.postMessage`, distinguished by `source` field:

- `vd-extractor` → `vd-content`: `media_found` `{ media }`
- `vd-content` → `vd-extractor`: `request_current_media` (re-extract on demand)

### content.js → background.js

- `chrome.runtime.sendMessage({ type: "probe_media_url", url })` — service worker checks a bounded Range response and reports `{ok, media:{url,size,kind}}`; only verified video URLs enter any list
- `chrome.runtime.sendMessage({ type: "update_badge", count })`
- `chrome.runtime.sendMessage({ type: "update_media_registry", pageUrl, pageTitle, platform, items })` — title, cover, preview URL, quality, status, progress and the per-tab media key only
- `chrome.runtime.sendMessage({ type: "get_media_registry" })` — monitor reads the latest per-tab snapshot
- `chrome.runtime.sendMessage({ type: "start_multi_tab_download", items: [{tabId, shareUrl}], concurrency })` — background groups requests by tab and forwards `start_external_download` with its per-tab worker cap
- Progress updates are coalesced into approximately one registry update every 900ms per active tab to avoid excessive session writes.

## Download engine

### Concurrency

Adaptive worker pool, not hardcoded:

1. **Estimate**: `memory budget × 70% ÷ avg video size`, clamped to 2–16, then capped by the saved per-tab preference (2/4/6/8; 4 by default; automatic disables the manual cap)
   - Budget source priority: `performance.memory.jsHeapSizeLimit` → `navigator.deviceMemory / 2` → fallback constant
2. **Live guard**: workers check heap before taking tasks; pause above 85%, resume below 60%

Known very large sources also cap combined in-flight video sizes to approximately 512 MiB per tab; sources with unknown sizes use at most two parallel workers. A direct-transfer stall aborts after 20 seconds without a received chunk, rather than claiming 85–90% while idle. Workers start staggered (40ms apart). Shared cursor distributes tasks within each tab. Multiple tabs may download concurrently and each has an independent worker cap, not a global limit. m3u8 segments use a separate 6-worker pool.

### m3u8

1. Pick highest-bandwidth variant from master playlist
2. Resolve relative URLs, handle `EXT-X-MAP`
3. Download segments concurrently → concatenate
4. `.ts` segments → `.ts` file; fMP4 → `.mp4`
5. `EXT-X-KEY` (encrypted) → explicit error, never silent

### Audio tracks

Some platforms serve DASH with separate video/audio (e.g. Bilibili when `durl` is unavailable):

1. Extractor captures `audioUrl` from API data (`dash.audio[0]`)
2. After video download, first 2MB is probed for audio markers (`soun`/`mp4a`/`ac-3`)
3. If no audio track and `audioUrl` exists → downloaded as `{fileName}_audio.m4a`

## Panel UI

- Shadow DOM host in page → iframe loads `panel.html`
- Isolated CSS (no leakage either direction)
- Draggable title bar, collapsible, per-tab badge count
