/**
 * Regression checks for the responsive browser dashboard and standard extension icons.
 * Uses only Node built-ins so a fresh checkout can run the complete suite.
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");

test("monitor and options share a responsive, local component system", () => {
  for (const page of ["options.html", "monitor.html"]) {
    const html = read(page);
    assert.match(html, /class="app-layout"/);
    assert.match(html, /class="app-sidebar"/);
    assert.match(html, /class="brand__logo" src="images\/logo\.svg"/);
    assert.match(html, /href="options\.html"/);
    assert.match(html, /href="monitor\.html"/);
    assert.match(html, /href="vendor\/pico\.min\.css"/);
    assert.match(html, /href="ui\.css"/);
    assert.match(html, /id="version"/);
  }
  assert.match(read("options.html"), /href="options\.html" aria-current="page"/);
  assert.match(read("monitor.html"), /href="monitor\.html" aria-current="page"/);

  const ui = read("ui.css");
  assert.match(ui, /\.ui-button\s*\{/);
  assert.match(ui, /\.ui-checkbox\s*\{[^}]*appearance:\s*none;/s);
  assert.match(ui, /select\.ui-select:not\(\[multiple\], \[size\]\)\s*\{[^}]*appearance:\s*none;/s);
  assert.match(ui, /input\.ui-switch\[type="checkbox"\]\[role="switch"\]\s*\{[^}]*appearance:\s*none;/s);
});

test("settings render their labels, help text, and concurrency selection separately", () => {
  const html = read("options.html");
  const css = read("options.css");
  for (const id of ["format", "quality", "concurrency"]) {
    assert.match(html, new RegExp('<label for="' + id + '">'));
    assert.match(html, new RegExp('<select id="' + id + '" class="ui-select">'));
  }
  assert.match(html, /<p class="field-help">每个网页单独调度/);
  assert.match(html, /class="ui-switch" type="checkbox" role="switch"/);
  assert.match(css, /\.settings-field \.field-help\s*\{[^}]*margin:\s*10px 0 0;/s);
  assert.match(css, /@media \(max-width: 560px\)/);
  assert.doesNotMatch(css, /linear-gradient|radial-gradient/i);
});

test("monitor supports individual, per-website, and filtered selection plus playback", () => {
  const html = read("monitor.html");
  const js = read("monitor.js");
  for (const id of [
    "refresh-registry", "select-all", "search-videos", "concurrency",
    "registry-summary", "registry-empty", "registry", "download-selected",
    "download-status", "preview-modal", "preview-video", "preview-close",
    "stat-sites", "stat-total", "stat-available", "selection-summary",
  ]) assert.match(html, new RegExp('id="' + id + '"'));
  assert.match(js, /data-video/);
  assert.match(js, /data-group/);
  assert.match(js, /data-preview/);
  assert.match(js, /data-collapse/);
  assert.match(js, /safeMediaUrl/);
  assert.match(js, /aria-valuenow/);
  assert.match(html, /aria-modal="true"/);
  assert.match(js, /event\.key === "Escape"/);
  assert.match(js, /videoKey\(tabId, shareUrl\)/);
  assert.match(js, /concurrency: Number\(concurrencyEl\.value\) \|\| 0/);
});

test("logo is a rounded blue square with one optically centered white arrow", () => {
  const svg = read("images/logo.svg");
  assert.equal((svg.match(/<rect\b/g) || []).length, 1);
  assert.equal((svg.match(/<path\b/g) || []).length, 1);
  assert.match(svg, /width="120" height="120" rx="28" fill="#2563eb"/);
  assert.match(svg, /d="M64 34v55M42 67l22 22 22-22"/);
  assert.match(svg, /stroke="#fff"/);
  assert.doesNotMatch(svg, /<circle\b|<line\b|<polygon\b|<image\b|gradient/i);
  for (const size of [16, 32, 48, 128]) {
    const png = fs.readFileSync(path.join(root, "images", size + ".png"));
    assert.equal(png.toString("hex", 0, 8), "89504e470d0a1a0a");
    assert.equal(png.readUInt32BE(16), size);
    assert.equal(png.readUInt32BE(20), size);
  }
});

test("browser layout scrolls and sidebar becomes navigation above content on mobile", () => {
  const css = read("options.css");
  assert.match(css, /width:\s*min\(100%,\s*1460px\)/);
  assert.match(css, /grid-template-columns:\s*252px minmax\(0, 1fr\)/);
  assert.doesNotMatch(css, /height:\s*100vh/);
  assert.match(css, /@media\s*\(max-width:\s*720px\)/);
  assert.match(css, /\.app-layout\s*\{\s*display:\s*block;\s*padding:/);
  assert.match(css, /\.app-sidebar\s*\{\s*position:\s*static;/);
  assert.match(css, /\.app-main\s*\{\s*padding:\s*0;/);
});
