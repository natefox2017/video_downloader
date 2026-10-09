/**
 * Regression checks for Pico CSS / custom UI component precedence.
 * The old cascade drew duplicate chevrons and displaced arrows in table cells.
 * Node built-ins only; all checks run in the regular extension CI.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");

test("single selects render one wrapper chevron, not the Pico background icon", () => {
  const pico = read("vendor/pico.min.css");
  const ui = read("ui.css");
  assert.match(pico, /select:not\(\[multiple\],\[size\]\)\{[^}]*background-image:var\(--pico-icon-chevron\)/);
  const override = ui.match(/select\.ui-select:not\(\[multiple\], \[size\]\)\s*\{([^}]+)\}/);
  assert.ok(override, "The select override must beat Pico's attribute selector");
  assert.match(override[1], /background-image:\s*none;/);
  assert.match(override[1], /appearance:\s*none;/);
  assert.match(override[1], /padding-inline-end:\s*38px;/);
  assert.match(ui, /\.ui-select-wrap::after\s*\{[^}]*pointer-events:\s*none;/s);
  assert.equal((ui.match(/\.ui-select-wrap::after\s*\{/g) || []).length, 1);
});

test("platform dropdown arrows are bounded by the 320px select wrapper", () => {
  const settings = read("options.css");
  const script = read("options.js");
  const html = read("options.html");
  assert.match(settings, /\.platform-table \.ui-select-wrap\s*\{\s*max-width:\s*320px;/);
  assert.match(settings, /\.platform-table select\s*\{[^}]*max-width:\s*100%;/);
  assert.match(settings, /@media \(max-width: 560px\)\s*\{[\s\S]*?\.platform-table \.ui-select-wrap\s*\{\s*max-width:\s*none;/);
  assert.match(script, /<span class="ui-select-wrap"><select class="ui-select" data-platform=/);
  for (const id of ["format", "quality", "concurrency"]) {
    assert.match(html, new RegExp('<select id="' + id + '" class="ui-select">'));
  }
});

test("download concurrency dropdown keeps the chevron inside its own field", () => {
  const html = read("monitor.html");
  const monitorCss = read("monitor.css");
  assert.match(html, /<span class="ui-select-wrap">\s*<select id="concurrency" class="ui-select"/);
  assert.match(monitorCss, /\.monitor-controls__right \.ui-select-wrap\s*\{[^}]*width:\s*116px;[^}]*max-width:\s*116px;?/);
});

test("switch overrides the stronger Pico [role=switch] selector and thumb", () => {
  const pico = read("vendor/pico.min.css");
  const ui = read("ui.css");
  const html = read("options.html");
  assert.match(pico, /\[type=checkbox\]\[role=switch\]:before\{/);
  assert.match(ui, /input\.ui-switch\[type="checkbox"\]\[role="switch"\]\s*\{[^}]*appearance:\s*none;/s);
  assert.match(ui, /input\.ui-switch\[type="checkbox"\]\[role="switch"\]::before\s*\{[^}]*margin:\s*0;/s);
  assert.match(ui, /input\.ui-switch\[type="checkbox"\]\[role="switch"\]:checked::before\s*\{[^}]*translateX\(18px\);/s);
  assert.match(html, /id="remember-position" class="ui-switch" type="checkbox" role="switch"/);
});

test("feedback links share styled UI buttons and keep navigation semantics", () => {
  const html = read("options.html");
  const css = read("options.css");
  assert.match(html, /<a class="ui-button" href="https:\/\/github\.com\/natefox2017\/video_downloader\/issues\/new"/);
  assert.match(html, /<a class="ui-button ui-button--quiet" href="https:\/\/github\.com\/natefox2017\/video_downloader"/);
  assert.doesNotMatch(html, /class="secondary outline" role="button"/);
  assert.match(css, /\.feedback-actions a\.ui-button\s*\{[^}]*min-height:\s*38px;/s);
  assert.match(html, /设置会自动保存在当前浏览器中/);
});
