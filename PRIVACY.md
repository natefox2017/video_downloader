# Privacy Policy — Video Download Helper

**Last updated:** October 2026

## Data collection

This extension **does not collect, transmit, or share any personal data**.

- All video detection and downloading happens **locally in your browser**.
- No analytics, no tracking, no telemetry.
- No data is sent to any server operated by the developer.

## Permissions

| Permission | Why it's needed |
|---|---|
| `host_permissions: <all_urls>` | Detect video resources on any website you visit and download them directly (bypasses CORS for media URLs) |
| `scripting` | Inject content scripts to show the download panel |
| `storage` (if added) | Remember your preferences locally |

## Third-party services

None. The extension works fully offline except for loading the video pages themselves.

## Contact

Open an issue at https://github.com/natefox2017/video_downloader/issues
