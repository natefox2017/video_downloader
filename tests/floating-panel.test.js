/**
 * Floating panel geometry and preview placement regression tests.
 * Execute the actual pure geometry/gesture functions without a browser framework.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const content = fs.readFileSync(path.join(root, "content.js"), "utf8");
const panel = fs.readFileSync(path.join(root, "panel.js"), "utf8");
const panelHtml = fs.readFileSync(path.join(root, "panel.html"), "utf8");

/**
 * Evaluate one existing implementation function in a controlled VM context.
 * Nearby JSDoc boundaries are stable because the functions are intentionally
 * documented individually in content.js.
 * @param {string} name Existing function name.
 * @param {object} context Minimal required browser dependencies.
 * @returns {Function} Function from the actual extension source.
 */
function loadFunction(name, context) {
  const start = content.indexOf("  function " + name + "(");
  assert.notEqual(start, -1, "Function missing: " + name);
  const end = content.indexOf("\n  /**", start + 1);
  assert.notEqual(end, -1, "Function boundary missing: " + name);
  return vm.runInNewContext("(" + content.slice(start, end).trim() + ")", context);
}

test("two videos use intrinsic height; only longer lists hit the 480px scroll cap", () => {
  const window = { innerHeight: 720 };
  const panelState = { contentHeight: 174 };
  const body = { style: {} };
  const floating = { isConnected: true };
  let clamps = 0;
  const fn = loadFunction("updatePanelHeight", {
    window,
    panelState,
    host: { style: { display: "" } },
    clampPanelPosition() { clamps++; },
  });
  const shadow = {
    querySelector(selector) {
      return selector === ".vd-panel__body" ? body : floating;
    },
  };

  fn(shadow);
  assert.equal(body.style.height, "174px");
  panelState.contentHeight = 900;
  fn(shadow);
  assert.equal(body.style.height, "480px");
  window.innerHeight = 210;
  fn(shadow);
  assert.equal(body.style.height, "142px");
  assert.equal(clamps, 3);

  assert.doesNotMatch(content, /--vd-max-height:\s*55vh/);
  assert.match(content, /max-height:\s*min\(var\(--vd-max-height\),\s*calc\(100dvh - 68px\)\)/);
  assert.match(content, /case "panel_resize":\s*if \(Number\.isFinite\(data\.height\)/);
});

test("a compact or expanded panel is clamped fully within the viewport", () => {
  const window = { innerWidth: 480, innerHeight: 420 };
  const fn = loadFunction("clampPanelPosition", { window });
  const panel = {
    offsetWidth: 320,
    offsetHeight: 250,
    style: {},
    getBoundingClientRect() { return { left: 250, top: 380 }; },
  };
  fn(panel);
  assert.equal(panel.style.left, "148px");
  assert.equal(panel.style.top, "158px");
  panel.offsetWidth = 52;
  panel.offsetHeight = 52;
  fn(panel, -60, 1000);
  assert.equal(panel.style.left, "12px");
  assert.equal(panel.style.top, "356px");
});

test("collapsed circle can drag with touch/mouse while taps remain distinct", () => {
  const handlers = {};
  const bar = {
    addEventListener(name, handler) { handlers[name] = handler; },
    setPointerCapture() {},
  };
  const button = { setPointerCapture() {} };
  const panelState = { collapsed: true, suppressToggleClickUntil: 0 };
  const panel = {
    classList: { add(name) { this[name] = true; }, remove(name) { this[name] = false; } },
    getBoundingClientRect() { return { left: 100, top: 80 }; },
  };
  let saved = 0;
  const calls = [];
  const fn = loadFunction("enableDrag", {
    panelState,
    savePanelPosition() { saved++; return Promise.resolve(); },
    clampPanelPosition(target, x, y) { calls.push([target, x, y]); },
  });
  fn({
    querySelector(selector) { return selector === "#vd-bar" ? bar : panel; },
  });

  const target = { closest() { return button; } };
  const start = { isPrimary: true, button: 0, target, pointerId: 1, clientX: 120, clientY: 120 };
  handlers.pointerdown(start);
  handlers.pointermove({ pointerId: 1, clientX: 122, clientY: 122 });
  assert.equal(calls.length, 0, "Short click movements must not drag");
  handlers.pointermove({
    pointerId: 1, clientX: 150, clientY: 140,
    preventDefault() {},
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1], 130);
  assert.equal(calls[0][2], 100);
  handlers.pointerup({ pointerId: 1 });
  assert.equal(saved, 1);
  assert.ok(panelState.suppressToggleClickUntil > Date.now());
  assert.equal(panel.classList["is-dragging"], false);
  assert.match(content, /Date\.now\(\) < panelState\.suppressToggleClickUntil/);
  assert.match(content, /\.vd-panel\.collapsed \{[^}]*background: #4f46e5;/);
  assert.match(content, /\.vd-panel\.collapsed \.vd-panel__bar \{[^}]*background: transparent;/);
  assert.match(content, /\.vd-panel\.collapsed #vd-collapse \{[^}]*background: #4f46e5;[^}]*color: #fff;/);
  assert.match(content, /\.vd-panel\.collapsed #vd-collapse:hover \{[^}]*background: #4338ca;[^}]*color: #fff;/);
  assert.match(content, /box-shadow: 0 6px 22px rgba\(15, 23, 42, \.22\)/);
});

test("video preview lives outside the paint-contained panel and its iframe", () => {
  const floatingMarkup = content.slice(content.indexOf("shadow.innerHTML = `"));
  assert.match(floatingMarkup, /<div class="vd-panel" id="vd-panel">[\s\S]*?<\/div>\s*<div class="vd-preview" id="vd-preview"/);
  assert.match(content, /\.vd-preview__dialog\s*\{[\s\S]*?width: min\(840px, calc\(100vw - 32px\)\)/);
  assert.match(content, /\.vd-preview\[hidden\]\s*\{\s*display: none;/);
  assert.match(content, /case "open_preview":\s*if \(typeof data\.shareUrl === "string"\)/);
  assert.match(content, /function closeVideoPreview\(\)/);
  assert.match(content, /function safePreviewUrl\(value\)/);
  assert.match(panel, /post\(\{ type: "open_preview", shareUrl: item\.shareUrl \}\)/);
  assert.match(panel, /case "preview_closed":/);
  assert.doesNotMatch(panelHtml, /<video\b|id="preview"/);
});
