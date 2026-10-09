# AGENTS.md

Notes for AI coding assistants (Codex / Claude Code / WorkBuddy, etc.) working on this repo.
**Read this file before making changes**, especially "Architecture invariants" and "Gotchas" —
every entry there is a lesson learned the hard way.

> 2026-10-02: the repo was trimmed from "Douyin downloader + video remixing tool" to a pure
> multi-platform video downloader. `video_frame_tool/` (the remixing module) is deleted;
> all constraints related to it were removed from this file.

---

## 1. Project overview

This repo contains exactly one component: a **multi-platform video downloader**
(Chrome extension, Manifest V3).

| Entry | Stack | How to load |
|---|---|---|
| `manifest.json` | Chrome MV3 + vanilla JS (no frameworks, no build) | `chrome://extensions/` → Developer mode → Load unpacked |

**Edits take effect immediately**: the extension has no build step. Do not introduce
bundlers, build tools, or npm dependencies.

---

## 2. Dev & self-check commands

```bash
# Syntax + manifest validation (CI runs the same set)
for f in background.js rules.js content.js panel.js options.js monitor.js extractors/*.js; do node --check "$f"; done
node --check scripts/release.mjs
node -e "JSON.parse(require('fs').readFileSync('manifest.json','utf8'))"
node --test tests/*.test.js
```

Functional verification (no automated test framework — verify by hand):

- After editing, hit reload at `chrome://extensions/` → open the target site, play a video → click the icon to open the panel → select and download.
- Check the console for errors; verify the downloaded file is complete and playable.
- After changing an extractor, verify on the **real page of that platform**
  (platform page structures change often — don't write selectors from memory).
- After changing the m3u8 flow, verify segment download + merging against a public test stream
  (note: encrypted streams must fail with an explicit message).

---

## 3. Architecture invariants (do not break)

1. **Extraction and downloading must live in separate worlds.**
   `extractors/*.js` runs in the main world to read page JS variables
   (`window.player`, `__playinfo__`, etc. — invisible from the isolated world);
   downloading happens only in `content.js` (the isolated world holds `host_permissions`,
   so `fetch` is not CORS-restricted).
   **Do not move `fetch`/download logic into extractor scripts** (the page's CSP will block it),
   **and do not move page-variable reading into `content.js`** (it can't see them).
2. **Rules go in `rules.js` only, nowhere else.**
   Platform detection, media URL matching, exclusion rules, filename construction,
   download-behavior constants — `rules.js` is the single source.
   New platform = edit `rules.js` + add extractor script + register in `manifest.json`;
   all three are required
   (CI checks that every platform with an extractor in rules is registered in the manifest).
3. **The panel is UI only, never downloads.**
   `panel.js` is cross-origin from the page and can only send/receive via `postMessage`;
   downloading, dedup, and concurrency all live in `content.js`.
4. **Closing the panel = `display:none`, never destroyed.**
   Hiding the panel must not interrupt an ongoing download.
5. **Keep permissions minimal.**
   Downloads are implemented as "`fetch` → `Blob` → `<a download>`" — **do not request
   the `downloads` permission just for downloading**. The `storage` permission is used only
   for extension settings, remembered panel position, and the temporary cross-tab media registry;
   downloaded-record persistence remains in the page's `localStorage`.
   `host_permissions: <all_urls>` is required for generic sniffing
   (direct-link downloads must bypass CORS) — do not narrow it.
6. **Message protocol changes must be synced both ways.**
   When adding a message type, change the sender / receiver in `content.js` **and**
   `panel.js` together, and update the protocol table in `README.md`.
   The `source` field namespace is `vd-*` (`vd-extractor` / `vd-content` / `vd-panel`).
7. **Injected scripts must be idempotent.**
   Main-world extractors guard with `window.__VD_EXTRACTOR_<PLATFORM>__`;
   the content script guards with `window.__VD_CONTENT_READY__`
   (extension reloads and SPA navigations cause repeat injections) —
   keep this pattern for any new entry point.
8. **Extractor scripts only "read and report", never decide.**
   Dedup, merging, filename fallbacks, and download-method selection
   (direct link vs m3u8) all live in `content.js`;
   when an extractor's reported media object is missing fields, `content.js`
   fills defaults — don't put business logic in extractor scripts.

---

## 4. Gotchas (read before touching the code)

| Pitfall | Explanation |
|---|---|
| **`performance.memory` is a getter returning a fresh snapshot each access** | Storing it in a variable and re-reading properties in a polling loop always returns that frozen instant — freed memory will never show. **Access `performance.memory` anew every time** |
| **`blob.arrayBuffer()` copies the entire payload** | Audio-track detection may only call it on `blob.slice(0, 2MB)`; calling it on the whole Blob duplicates a full video's worth of memory per concurrent worker |
| **Worker stagger delay must not be too long** | Measured: 10 workers × 80 ms stagger = 720 ms span, so files that return in 300 ms only ever reach a peak concurrency of 8. The current 40 ms is deliberately tight — do the math before raising it |
| **m3u8 segments must be assembled in order** | When downloading segments concurrently, reserve slots in an index array — **never** push in completion order, or the merged file will glitch or refuse to play |
| **m3u8 relative paths resolve against the playlist URL** | After picking a variant, the media playlist's base is the **variant URL**, not the original master URL — using the wrong base causes 404s |
| **Bilibili direct links expire quickly** | The `__playinfo__` durl signature has a short lifetime; download soon after capture, don't cache links for "later" |
| **`blob:` URLs can't be downloaded** | In-page `blob:` URLs are temporary object URLs that die on navigation — filter them at sniffing time, never let them into the list |
| **Main-world scripts can't use `chrome.*`** | The main world is the page's context; `chrome.runtime.getURL` etc. are unavailable; talk to the isolated world only via `window.postMessage` |
| **`world: "MAIN"` needs Chrome 111+** | The README installation section states the minimum version — keep the compatibility note in sync when touching manifest matches/world |
| **Never commit `.DS_Store` / `__pycache__`** | Already excluded in `.gitignore` |
| **A single timing run can lie — always take the median of 3 runs** | The same config can show opposite speedups in a single round. **Performance claims require repeated measurement**, or you'll optimize in the wrong direction |

---

## 5. Code style

- **Comments and docs in English**, consistent with the existing code; every file starts with a block comment describing its responsibility, every function has a docstring (parameters, return value, side effects).
- **Naming**: JS uses `camelCase` functions / `SCREAMING_SNAKE_CASE` constants; extractor scripts live in `extractors/`, filename = platform id.
- **No emoji in code or docs** (the `[video_downloader]` log prefix is the existing style — keep it).
- **Comments explain "why", not "what"**; numbers related to performance and timing (thresholds, intervals, caps) must state their source or measurement basis in the comment.
- When changing extraction/download core logic, **update in sync**: the matching section in `README.md` and the relevant entries here. Any inconsistency counts as unfinished.

---

## 6. Commit & release conventions

- Branch: `main` is the default branch.
- Commit messages in English, format `type: brief`, types: `feat` / `fix` / `perf` / `docs` / `refactor` / `chore`.
- Performance-related changes: include measured before/after data in the message body.
- One commit = one concern; keep formatting changes separate from functional changes.
- **Releases**: push a numeric version tag (`tag2.3.1`, `v2.3.1`, or `2.3.1`) on a commit
  already merged to main. `.github/workflows/release.yml` stamps the staged manifest
  with the tag's version (without editing source), signs CRX3 with the persistent
  `CRX_PRIVATE_KEY` Actions secret, packages a ZIP, and uploads both plus checksums.
  Missing secrets are a release error: **never generate an ephemeral key for a public release**.
  The signing smoke test may use a disposable key only because its artifacts are never shipped.
  See `docs/RELEASE_CRX.md`. **Pushing to main never publishes — version tags do.**

---

## 7. Known TODOs (don't act on these without confirmation)

1. **No open-source license declared yet**: do not add a `LICENSE` file until the maintainer decides.
2. **iframe-embedded players not covered yet**: content scripts inject into the top-level page only; videos nested in iframes aren't captured. Covering them requires multi-frame panel + dedup work — a large change.
3. **m3u8 encrypted streams (EXT-X-KEY) unsupported**: currently fails with an explicit message; supporting them requires implementing AES-128 segment decryption.
4. **Xigua Video has no dedicated extractor**: falls back to generic sniffing; add `extractors/xigua.js` if title/author metadata is needed.
