# Automatic CRX3 releases

The extension is released by pushing a **version Git tag**. Normal commits and pull requests do not publish a Release.

## One-time signing setup

A consistent private key is essential: Chrome derives the extension ID from the signing key. **Never generate a different key for each release.** If you have already distributed this extension as a CRX, use the **original private key** to preserve its ID.

If this is the first CRX release, generate a key locally:

~~~bash
openssl genrsa -out video-downloader.pem 2048
~~~

Back up that file securely, then add the PEM **contents** as an Actions repository secret:

- GitHub repository → **Settings → Secrets and variables → Actions**
- **New repository secret**
- Name: **CRX_PRIVATE_KEY**
- Value: the complete private-key PEM, including BEGIN/END lines

Alternatively, with GitHub CLI installed and authenticated:

~~~bash
gh secret set CRX_PRIVATE_KEY --repo natefox2017/video_downloader < video-downloader.pem
~~~

Do not commit, upload to Releases, or share the PEM. The workflow stores it temporarily only while signing and removes the temporary file afterwards. Missing/invalid keys cause release failure; a disposable key is used **only in the PR smoke test**, never for public releases.

## Publish a new version

Tag the latest reviewed commit on main:

~~~bash
git checkout main
git pull --ff-only origin main
git tag -a tag2.3.1 -m "Release 2.3.1"
git push origin tag2.3.1
~~~

The following version tags are accepted:

| Git tag | Version packaged in manifest.json |
| --- | --- |
| tag0.0.1 | 0.0.1 |
| v2.3.1 | 2.3.1 |
| 2.3.1 | 2.3.1 |

Use three or four numeric components, each 0–65535, without prerelease suffixes.

The workflow uses the **tag's version for the staged release manifest**. It does not commit modifications to the source manifest. For easier local testing, updating the source manifest version before tagging is still recommended. For real updates, choose a version greater than the previously installed extension's version.

The tag must point to a commit already on main, and must include the release workflow files. A tag on an unmerged branch is rejected before signing.

## What the workflow produces

[.github/workflows/release.yml](../.github/workflows/release.yml) runs the following:

1. Validate the tag, extension assets, JavaScript, and repository tests.
2. Build a clean unpacked extension directory containing all HTML/CSS/JS entry points, icon assets, extractors, and vendored CSS.
3. Sign a **CRX3** with the persistent **CRX_PRIVATE_KEY** secret, using the pinned open-source package **crx3@2.0.0** on Node.js 22.
4. Create a ZIP from those same staged extension files.
5. Validate the CRX3 header, ZIP root layout, manifest version, and absence of PEM files.
6. Upload both packages and **SHA256SUMS.txt** to a GitHub Release.

Files are named:

- video-downloader-2.3.1.crx
- video-downloader-2.3.1.zip
- SHA256SUMS.txt

To check downloaded artifacts, keep the three files in one directory and run:

~~~bash
sha256sum -c SHA256SUMS.txt
~~~

## Installation and Chrome restrictions

**ZIP**: extract it, visit chrome://extensions/, enable Developer mode, and use **Load unpacked**. This is the recommended manual installation method.

**CRX3**: the signed CRX is suitable for managed/self-hosted extension delivery where browser policies allow it. Modern official Chrome may block installing non-Chrome-Web-Store CRX files directly; generating a valid CRX3 does **not** bypass Chrome's installation restrictions. Chrome Web Store publishing uses a separate upload/review flow and is **not** configured by this workflow.

If the signing key is lost, a new key produces a new extension ID. Back up the original key before your first release.

## Common failures

| Error | Fix |
| --- | --- |
| Missing signing key | Set the Actions secret **CRX_PRIVATE_KEY** before pushing the tag. |
| Invalid release tag | Use a numeric tag like **tag2.3.1**; avoid spaces and prerelease suffixes. |
| Tag not on main | Merge the release commit to main first, then create a new tag on main. |
| Referenced file missing | Verify the files linked by manifest.json and standalone HTML pages are in the repository. |
| Downloaded CRX not installable in Chrome | Use the ZIP with Load unpacked, or distribute through Chrome Web Store / managed policies. |
