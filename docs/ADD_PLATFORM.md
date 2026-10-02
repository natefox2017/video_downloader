# Adding a new platform

Three steps. No build, no restart — just reload the extension.

## 1. Register in rules.js

Add a row to `PLATFORMS`:

```js
{ id: "newplatform", name: "新平台", hosts: ["newplatform.com"], extractor: "extractors/newplatform.js" },
```

- `id`: lowercase, used as `platformId` and CSS class suffix
- `hosts`: domain substrings that identify the platform
- `extractor`: path to the extractor script, or `null` for generic sniffing only

## 2. Write the extractor

Copy the smallest existing extractor and adapt. Template:

```js
"use strict";

(() => {
  const X = window.VDExtractor;
  if (!X) return;

  function detect() {
    // 1. Find the player data object (page-specific!)
    //    Common sources: window.player, window.__playinfo__,
    //    __INITIAL_STATE__, embedded JSON in <script> tags
    const data = window.somePlayerData;
    if (!data) return null;

    // 2. Extract URLs — prefer API response data over DOM scraping
    //    DOM video elements often have blob: URLs or low-quality previews
    const videoUrls = [...];

    // 3. If the platform separates audio (DASH), capture it too
    const audioUrl = ...;

    return {
      shareUrl: "unique-key-for-dedup",
      title: "...",
      author: "...",
      cover: "...",
      duration: 123,        // seconds
      size: 456789,         // bytes, 0 if unknown
      type: "视频",
      videoUrls,
      audioUrl: audioUrl || "",
      fileName: "",          // "" = auto-generated
    };
  }

  X.create("newplatform", {
    platformName: "新平台",
    pollInterval: 1500,
    detect,
  });
})();
```

### Data source priority

1. **Platform API data** (most reliable) — player config objects, `__playinfo__`-style globals, `window` state
2. **Embedded JSON** — `X.scanScriptUrls(/"videoUrl"\s*:\s*"([^"]+)"/g)` for URLs hidden in script tags
3. **Video elements** — `X.collectVideoElementUrls()` (may be `blob:` — content.js filters those)
4. **Meta tags** — `X.collectMetaVideoUrls()` for `og:video`

### Key rules

- `detect()` must return `null` when no video is found (not an empty object)
- `shareUrl` must be stable for the same video across polls (used for dedup)
- Never do network requests or downloads in the extractor — report only
- Wrap DOM queries in try/catch — page structures change without notice

## 3. Register in manifest.json

```json
{
  "matches": ["*://*.newplatform.com/*"],
  "js": ["rules.js", "extractors/common.js", "extractors/newplatform.js"],
  "world": "MAIN",
  "run_at": "document_idle"
}
```

Add to the existing `content_scripts` array. `world: "MAIN"` is required to read page JS variables.

## Verify

1. Reload the extension at `chrome://extensions/`
2. Open the platform, play a video
3. Click the extension icon — the video should appear in the panel
4. Download it — verify the file plays with audio

## If the platform has no stable player data

Set `extractor: null` in `rules.js` and skip steps 2–3. Generic sniffing (video elements + network resources) will handle it automatically.
