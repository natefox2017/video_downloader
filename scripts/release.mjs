/**
 * Release staging and CRX3 format checks, using only Node built-ins.
 *
 * The pushed tag determines the packaged version without modifying the source manifest.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ASSET_DIRECTORIES = ["extractors", "images", "vendor", "_locales", "assets", "fonts"];
const SOURCE_FILE_PATTERN = /\.(?:js|css|html)$/i;
const RELEASE_TEXT_FILES = new Set(["manifest.json", "LICENSE", "THIRD_PARTY_NOTICES.md"]);

/**
 * Parse a Git tag into a Chrome-compatible, three- or four-part version.
 * @param {string} tag Git tag name (for example, tag0.0.1, v0.0.1, 0.0.1).
 * @returns {string} Version to write into the released manifest.
 */
export function parseReleaseTag(tag) {
  const match = /^(?:tag|v)?((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*))?)$/.exec(String(tag || ""));
  if (!match) {
    throw new Error("Invalid release tag: " + tag + ". Use tag1.2.3, v1.2.3, or 1.2.3 (optional fourth numeric segment).");
  }
  for (const part of match[1].split(".")) {
    if (Number(part) > 65535) throw new Error("Chrome manifest version component out of range: " + part);
  }
  return match[1];
}

/**
 * Copy regular files and directories without allowing staged symlinks.
 * @param {string} source Source file or directory.
 * @param {string} target Destination.
 * @returns {void}
 */
function copySafe(source, target) {
  const info = fs.lstatSync(source);
  if (info.isSymbolicLink()) throw new Error("Symlinks are not supported in release assets: " + source);
  if (info.isDirectory()) {
    fs.mkdirSync(target, { recursive: true });
    for (const entry of fs.readdirSync(source)) copySafe(path.join(source, entry), path.join(target, entry));
  } else if (info.isFile()) {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(source, target);
  } else {
    throw new Error("Unsupported release asset type: " + source);
  }
}

/**
 * Fail early when manifest or page HTML references an unstaged file.
 * @param {string} targetRoot Staged extension directory.
 * @param {object} manifest Parsed MV3 manifest.
 * @returns {void}
 */
export function validateStagedFiles(targetRoot, manifest) {
  if (manifest.manifest_version !== 3) throw new Error("Expected a Manifest V3 extension.");

  const required = new Set(["manifest.json"]);
  const include = (value) => {
    if (typeof value === "string" && value.trim()) required.add(value);
  };
  include(manifest.background?.service_worker);
  include(manifest.options_ui?.page);
  include(manifest.options_page);
  include(manifest.devtools_page);
  include(manifest.side_panel?.default_path);
  include(manifest.action?.default_popup);
  Object.values(manifest.icons || {}).forEach(include);
  Object.values(manifest.action?.default_icon || {}).forEach(include);

  for (const script of manifest.content_scripts || []) {
    (script.js || []).forEach(include);
    (script.css || []).forEach(include);
  }
  for (const group of manifest.web_accessible_resources || []) {
    (group.resources || []).forEach(include);
  }

  // Standalone HTML pages can have dependencies that are not enumerated in the manifest.
  for (const name of fs.readdirSync(targetRoot).filter((name) => name.endsWith(".html"))) {
    const html = fs.readFileSync(path.join(targetRoot, name), "utf8");
    for (const element of html.match(/<(?:script|link|img)\b[^>]*>/gi) || []) {
      const match = element.match(/\b(?:src|href)=["']([^"']+)["']/i);
      if (!match) continue;
      const value = match[1];
      if (/^(?:https?:|\/\/|data:|#|mailto:)/i.test(value)) continue;
      if (value.includes("..")) throw new Error("Unsafe path in " + name + ": " + value);
      include(value.split(/[?#]/)[0]);
    }
  }

  for (const entry of required) {
    if (entry.includes("..") || path.isAbsolute(entry)) {
      throw new Error("Unsafe manifest resource path: " + entry);
    }
    if (entry.includes("*")) {
      const parent = path.join(targetRoot, path.dirname(entry));
      if (!fs.existsSync(parent) || !fs.statSync(parent).isDirectory()) {
        throw new Error("Referenced resource directory missing: " + entry);
      }
      continue;
    }
    const fullPath = path.join(targetRoot, entry);
    if (!fs.existsSync(fullPath) || !fs.statSync(fullPath).isFile()) {
      throw new Error("Referenced extension file missing: " + entry);
    }
  }
}

/**
 * Stage only extension source/assets, never docs, secrets or development files.
 * @param {string} sourceRoot Repository root.
 * @param {string} targetRoot Output directory.
 * @param {string} tag Git tag name.
 * @returns {string} Staged extension version.
 */
export function stageExtension(sourceRoot, targetRoot, tag) {
  const version = parseReleaseTag(tag);
  if (path.resolve(targetRoot) === path.resolve(sourceRoot)) {
    throw new Error("Output directory must not equal the repository root.");
  }
  const manifestPath = path.join(sourceRoot, "manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));

  fs.rmSync(targetRoot, { recursive: true, force: true });
  fs.mkdirSync(targetRoot, { recursive: true });
  for (const entry of fs.readdirSync(sourceRoot)) {
    const from = path.join(sourceRoot, entry);
    const stat = fs.lstatSync(from);
    if (stat.isFile() && (RELEASE_TEXT_FILES.has(entry) || SOURCE_FILE_PATTERN.test(entry))) {
      copySafe(from, path.join(targetRoot, entry));
    }
  }
  for (const directory of ASSET_DIRECTORIES) {
    const from = path.join(sourceRoot, directory);
    if (fs.existsSync(from)) copySafe(from, path.join(targetRoot, directory));
  }

  // Change only the packaged manifest, not the committed source manifest.
  manifest.version = version;
  fs.writeFileSync(path.join(targetRoot, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  validateStagedFiles(targetRoot, manifest);
  return version;
}

/**
 * Check CRX3 structure; signature authenticity remains the packer's responsibility.
 * @param {string} filePath Packed CRX file path.
 * @returns {void}
 */
export function checkCrx3Header(filePath) {
  const header = Buffer.alloc(12);
  const descriptor = fs.openSync(filePath, "r");
  try {
    if (fs.readSync(descriptor, header, 0, 12, 0) !== 12) throw new Error("CRX is truncated.");
  } finally {
    fs.closeSync(descriptor);
  }
  if (header.toString("ascii", 0, 4) !== "Cr24" || header.readUInt32LE(4) !== 3) {
    throw new Error("Output is not a CRX3 file.");
  }
  if (!header.readUInt32LE(8)) throw new Error("CRX3 has an empty proof header.");
}

const invokedDirectly = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) {
  try {
    const [command, ...args] = process.argv.slice(2);
    if (command === "stage" && args.length === 3) {
      const [tag, sourceRoot, targetRoot] = args;
      const version = stageExtension(path.resolve(sourceRoot), path.resolve(targetRoot), tag);
      console.log("Staged extension version " + version + ": " + targetRoot);
    } else if (command === "verify-crx" && args.length === 1) {
      checkCrx3Header(path.resolve(args[0]));
      console.log("CRX3 header is valid.");
    } else {
      throw new Error("Usage: node scripts/release.mjs stage <tag> <source-root> <output-dir> | verify-crx <crx-path>");
    }
  } catch (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  }
}
