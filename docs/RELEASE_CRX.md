# Chrome Web Store release workflow

Push a **v-prefixed Git Tag**, such as **v2.4.0**, on a commit already merged into main. GitHub Actions then creates a **minified, Chrome Web Store-ready ZIP**. If you have configured your original CRX signing key, it also produces an optional signed CRX3.

## 1. Release v2.4.0

~~~bash
git checkout main
git pull --ff-only origin main
git tag -a v2.4.0 -m "Release v2.4.0"
git push origin v2.4.0
~~~

The workflow **only runs on tags starting with v** followed by a valid numeric Chrome version (`v2.4.0`, optionally `v2.4.0.1`). Ordinary commits, PRs, `tag2.4.0`, and unprefixed tags will not publish releases.

Your tag must point to a commit that is already on main. The packager updates **only the staged manifest** to `2.4.0`: the source manifest in Git is not modified. If updating an existing Chrome Web Store listing, choose a version higher than the version already published.

## 2. Download the Chrome Web Store package

After the [GitHub Actions release workflow](../.github/workflows/release.yml) succeeds, open [GitHub Releases](../../releases). For v2.4.0 it provides:

- **video-downloader-v2.4.0-chrome-web-store.zip** — upload this file to the Chrome Web Store.
- **SHA256SUMS.txt** — checksums for every published build asset.
- **video-downloader-v2.4.0.crx** — *optional* signed CRX3, only when a persistent signing key is configured. **Do not upload this to the Chrome Web Store.**

The ZIP contains `manifest.json` at its root, all required HTML/CSS/JS, Pico CSS, icons, extraction scripts, and MIT license notices. It excludes development tests, GitHub workflows, source maps, local private keys, and build scripts.

Code is minimized using **esbuild@0.25.12** on the individual JS files without bundling them. This removes comments and unnecessary whitespace, simplifies code, and shortens local variable names while preserving the existing cross-script global bindings.

### Why not encrypt/obfuscate JS?

Chrome Web Store's [Code Readability Requirements](https://developer.chrome.com/docs/webstore/program-policies/code-readability) **prohibit intentionally obfuscated code or concealed extension functionality**. Standard minification is allowed, but encryption, encoded strings, runtime-decryption loaders, control-flow obfuscation, and similar hiding techniques can lead to rejection.

The repository remains fully open source. Only the **release output** is minified, and no runtime remote code is introduced.

## 3. Submit the ZIP to Chrome Web Store

1. Open the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole) with your verified developer account.
2. Create a new item or open your existing item, then upload **video-downloader-v2.4.0-chrome-web-store.zip** (not the CRX).
3. Complete the store listing, icons/screenshots, privacy disclosures and single-purpose description. Describe why the extension needs `<all_urls>` and `storage`, and explain that the cross-tab video registry is in `chrome.storage.session`.
4. Test the exact extracted ZIP in Chrome, including real site extraction, downloads, settings, and cross-tab batch handling. Confirm no errors in the extension Service Worker.
5. Submit for review. A correctly formed ZIP is only the technical prerequisite; **Google determines approval** after its policy/functionality/privacy review.

Reference: [Google's official publishing guide](https://developer.chrome.com/docs/webstore/publish).

## 4. Optional signed CRX3

The Chrome Web Store ZIP **works without any signing secret**. To also publish a self-hosted CRX3 with a stable extension ID, store your **original** PEM private key in the GitHub Actions repository secret **CRX_PRIVATE_KEY**:

GitHub repository → **Settings → Secrets and variables → Actions → New repository secret**.

For a new self-hosted CRX3 project with no existing key:

~~~bash
openssl genrsa -out video-downloader.pem 2048
gh secret set CRX_PRIVATE_KEY --repo natefox2017/video_downloader < video-downloader.pem
~~~

Securely back up the PEM. Never commit or attach it to a release. Losing or replacing this key changes the extension ID for self-hosted CRX updates. The Chrome Web Store uses its own signing/distribution flow.

## 5. Diagnostics

| Problem | Solution |
| --- | --- |
| Workflow did not start | Push a `vX.Y.Z` tag, e.g. `v2.4.0`, from a main commit that already includes the workflow. |
| Tag rejected by parser | Use three or four integer components 0–65535 with no prerelease suffix. |
| Missing signing key | This is fine for Chrome Web Store; only the optional CRX3 is skipped. |
| ZIP file rejected by the dashboard | Verify it contains a root `manifest.json`, version has increased, and all referenced files exist. |
| Extension flagged as obfuscated | Do not use obfuscation; this release pipeline only runs ordinary esbuild minification. |
| Extension under extended review | Review `<all_urls>` permissions, privacy disclosures, code readability, remote code restrictions and real-world functionality. |

CI runs the same production ZIP build with tag `v2.4.0`, plus a **throwaway-key CRX3 smoke test that is never released**.
