# Contributing to video_downloader

## Getting started

1. Fork and clone the repo
2. Load unpacked at `chrome://extensions/` (Developer mode)
3. Edit code → hit reload on the extension card → test on a real video page

No build step. No dependencies. No bundler.

## Self-check before committing

CI runs the same checks:

```bash
for f in background.js rules.js content.js panel.js options.js monitor.js extractors/*.js; do node --check "$f"; done
node --check scripts/release.mjs
bash -n scripts/build-store.sh
node -e "JSON.parse(require('fs').readFileSync('manifest.json','utf8'))"
node --test tests/*.test.js
```

Functional verification (no test framework — verify by hand):

- Reload extension → open the target site → play a video → click icon → panel shows the video
- Select and download → file is complete and playable
- After changing an extractor, verify on that platform's **real page** (page structures change often)

## Commit conventions

- Messages in English, format `type: brief description`
- Types: `feat` / `fix` / `perf` / `docs` / `refactor` / `chore`
- For larger features, explain **what** changed and **why**
- Performance changes should include measured before/after data

## Pull requests

- Non-draft PRs are auto-merged after CI passes (see `.github/workflows/automerge.yml`)
- Keep PRs focused; one feature/fix per PR

## Adding a platform

See [docs/ADD_PLATFORM.md](docs/ADD_PLATFORM.md).

## Architecture notes

Read [AGENTS.md](AGENTS.md) before making structural changes — it documents invariants and gotchas learned the hard way.

## Automatic releases

Push a `v`-prefixed version tag such as `v2.4.0` on main to build a production-minified **Chrome Web Store ZIP**. The optional self-hosted CRX3 uses the persistent `CRX_PRIVATE_KEY` GitHub Actions secret; the store ZIP does not need this secret. Standard JS minification is allowed by the Chrome Web Store, but deliberate obfuscation/encryption is not. See [Chrome Web Store release instructions](docs/RELEASE_CRX.md). Never commit PEM signing keys.
