/**
 * Regression checks for the shared admin-sidebar layout and a single-arrow icon.
 * Node built-ins only; no browser or package installation is required in CI.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");

test("settings and monitor share a sidebar with independent active links", () => {
  for (const page of ["options.html", "monitor.html"]) {
    const html = read(page);
    assert.match(html, /class="app-layout"/);
    assert.match(html, /class="app-sidebar"/);
    assert.match(html, /class="brand__logo" src="images\/logo\.svg"/);
    assert.match(html, /href="options\.html"/);
    assert.match(html, /href="monitor\.html"/);
    assert.match(html, /href="vendor\/pico\.min\.css"/);
    assert.match(html, /id="version"/);
  }
  assert.match(read("options.html"), /href="options\.html" aria-current="page"/);
  assert.match(read("monitor.html"), /href="monitor\.html" aria-current="page"/);
});

test("settings controls and descriptions are separate elements", () => {
  const html = read("options.html");
  const css = read("options.css");
  assert.match(html, /<label for="format">视频格式<\/label>\s*<select id="format">/);
  assert.match(html, /<\/select>\s*<p class="field-help">/);
  assert.match(html, /<label for="quality">默认清晰度<\/label>\s*<select id="quality"><\/select>\s*<p class="field-help">/);
  assert.match(css, /\.settings-field \.field-help\s*\{[^}]*margin:\s*10px 0 0;/s);
  assert.match(css, /@media \(max-width: 560px\)/);
  assert.doesNotMatch(css, /linear-gradient|radial-gradient/i);
});

test("monitor keeps all download controls after the layout change", () => {
  const html = read("monitor.html");
  for (const id of ["refresh-registry", "select-all", "registry-summary", "registry-empty", "registry", "download-selected", "download-status"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
});

test("logo SVG has a flat blue circle and exactly one white arrow shape", () => {
  const logo = read("images/logo.svg");
  assert.equal((logo.match(/<circle\b/g) || []).length, 1);
  assert.equal((logo.match(/<path\b/g) || []).length, 1);
  assert.match(logo, /fill="#2563eb"/);
  assert.match(logo, /fill="#fff"/);
  assert.doesNotMatch(logo, /<rect\b|<line\b|<polygon\b|<image\b|gradient/i);
  for (const size of [16, 32, 48, 128]) {
    const png = fs.readFileSync(path.join(root, "images", `${size}.png`));
    assert.equal(png.toString("hex", 0, 8), "89504e470d0a1a0a");
    assert.equal(png.readUInt32BE(16), size);
    assert.equal(png.readUInt32BE(20), size);
  }
});
