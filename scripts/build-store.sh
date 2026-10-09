#!/usr/bin/env bash
# Build a Chrome Web Store ZIP: tag-versioned, minified, and free of development files.
# This is a release-only build; development remains vanilla JS with no build step.
set -euo pipefail

tag="${1:?Usage: build-store.sh <vX.Y.Z> [output-directory]}"
output_dir="${2:-$PWD/.release}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# All output paths must be absolute; zip is invoked from within the staged directory.
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
stage="$output_dir/stage/extension"
minified="$output_dir/minified"
assets="$output_dir/assets"
mkdir -p "$assets"

node "$repo_root/scripts/release.mjs" stage "$tag" "$repo_root" "$stage"
version="$(node -p 'JSON.parse(require("node:fs").readFileSync(process.argv[1], "utf8")).version' "$stage/manifest.json")"
rm -rf "$minified"

# esbuild only TRANSFORMS each individual script; it never bundles these files.
# Keep script filenames and top-level globals stable (rules.js is shared by content.js).
# Chrome Web Store permits minification, but explicitly prohibits code obfuscation.
mapfile -d '' scripts < <(find "$stage" -type f -name '*.js' -print0 | sort -z)
if [[ "${#scripts[@]}" -eq 0 ]]; then
  echo "::error::No JavaScript files found in staged extension."
  exit 1
fi
npx --yes esbuild@0.25.12 "${scripts[@]}" \
  --outbase="$stage" \
  --outdir="$minified" \
  --minify \
  --target=chrome111 \
  --charset=utf8 \
  --legal-comments=none \
  --log-level=warning

cp -R "$minified/." "$stage/"
while IFS= read -r -d '' script; do
  node --check "$script"
done < <(find "$stage" -type f -name '*.js' -print0)

# Guard the global contracts shared between independently injected scripts.
node - "$stage" <<'NODE'
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = process.argv[2];
const context = vm.createContext({ window: {} });
vm.runInContext(fs.readFileSync(path.join(root, "rules.js"), "utf8"), context);
const globals = vm.runInContext("({PLATFORMS, DOWNLOAD_RULES, DEFAULT_SETTINGS, detectPlatform, buildFileName})", context);
assert.ok(globals.PLATFORMS.length > 0, "Platform rules must be available across scripts");
assert.ok(globals.DOWNLOAD_RULES, "Download rules must remain global");
assert.equal(typeof globals.detectPlatform, "function");
assert.equal(typeof globals.buildFileName, "function");
assert.ok(globals.DEFAULT_SETTINGS);
vm.runInContext(fs.readFileSync(path.join(root, "extractors/common.js"), "utf8"), context);
assert.equal(typeof context.window.VDExtractor.create, "function", "Extractor globals must survive minification");
console.log("Unbundled script globals are intact.");
NODE

source_bytes="$(wc -c < "$repo_root/content.js")"
minified_bytes="$(wc -c < "$stage/content.js")"
if (( minified_bytes >= source_bytes )); then
  echo "::error::Production content.js was not minified."
  exit 1
fi
if find "$stage" -type f -name '*.map' | grep -q .; then
  echo "::error::Source map accidentally included in Web Store package."
  exit 1
fi

# Chrome Web Store expects manifest.json at the ROOT of the ZIP.
zip_file="$assets/video-downloader-v${version}-chrome-web-store.zip"
rm -f "$zip_file"
(cd "$stage" && zip -q -r -X "$zip_file" .)
unzip -tq "$zip_file"
node - "$zip_file" "$version" <<'NODE'
const assert = require("node:assert/strict");
const {execFileSync} = require("node:child_process");
const zip = process.argv[2];
const expected = process.argv[3];
const contents = execFileSync("unzip", ["-Z1", zip], {encoding:"utf8"}).split("\n").filter(Boolean);
for (const file of [
  "manifest.json", "options.html", "options.js", "monitor.html", "monitor.js",
  "panel.html", "panel.js", "background.js", "rules.js",
  "vendor/pico.min.css", "images/logo.svg", "LICENSE", "THIRD_PARTY_NOTICES.md",
]) {
  assert.ok(contents.includes(file), "Missing store-package file: " + file);
}
for (const file of contents) {
  assert.ok(!/(^|\/)(?:key\.pem|[^/]*\.pem|tests|scripts|\.github|node_modules)(?:\/|$)/i.test(file), "Sensitive/internal file in package: " + file);
  assert.ok(!file.endsWith(".map"), "Source map in package: " + file);
}
const rawManifest = execFileSync("unzip", ["-p", zip, "manifest.json"], {encoding:"utf8"});
const manifest = JSON.parse(rawManifest);
assert.equal(manifest.version, expected, "Package version must match pushed tag");
assert.equal(manifest.manifest_version, 3);
console.log("Chrome Web Store ZIP verified:", zip, "version", expected);
NODE
printf 'STORE_VERSION=%s\nSTORE_ZIP=%s\n' "$version" "$zip_file"
