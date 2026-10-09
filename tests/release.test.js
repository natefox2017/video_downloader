/**
 * Release-workflow staging regressions.
 * Uses Node built-ins and disposable fixture directories only.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const release = import("../scripts/release.mjs");

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "video-downloader-release-"));
  const source = path.join(root, "source");
  const target = path.join(root, "dist", "extension");
  fs.mkdirSync(source, { recursive: true });

  const manifest = {
    manifest_version: 3,
    name: "Video downloader test fixture",
    version: "9.9.9",
    background: { service_worker: "background.js" },
    action: { default_icon: { 16: "images/16.png" } },
    options_ui: { page: "options.html", open_in_tab: true },
    content_scripts: [{ js: ["rules.js", "content.js"] }],
    icons: { 16: "images/16.png" },
    web_accessible_resources: [{ resources: ["images/*"], matches: ["<all_urls>"] }],
  };
  fs.writeFileSync(path.join(source, "manifest.json"), JSON.stringify(manifest));
  for (const name of ["background.js", "rules.js", "content.js", "options.js", "monitor.js", "panel.js"]) {
    fs.writeFileSync(path.join(source, name), "// extension source\n");
  }
  for (const name of ["options.html", "monitor.html", "panel.html"]) {
    fs.writeFileSync(path.join(source, name),
      '<!doctype html><link rel="stylesheet" href="vendor/pico.min.css"><script src="options.js"></script><img src="images/logo.svg" alt="">\n');
  }
  fs.mkdirSync(path.join(source, "images"));
  fs.mkdirSync(path.join(source, "extractors"));
  fs.mkdirSync(path.join(source, "vendor"));
  fs.writeFileSync(path.join(source, "images/16.png"), "PNG fixture");
  fs.writeFileSync(path.join(source, "images/logo.svg"), "<svg></svg>");
  fs.writeFileSync(path.join(source, "vendor/pico.min.css"), "/* Pico */");
  fs.writeFileSync(path.join(source, "extractors/common.js"), "/* extractor */");
  fs.writeFileSync(path.join(source, "README.md"), "Do not package docs.");
  fs.writeFileSync(path.join(source, "key.pem"), "This must NEVER ship.");
  fs.mkdirSync(path.join(source, "tests"));
  fs.writeFileSync(path.join(source, "tests/secret.txt"), "do not ship");

  return { root, source, target };
}

test("release tags support tag0.0.1, v0.0.1 and plain 0.0.1", async () => {
  const { parseReleaseTag } = await release;
  assert.equal(parseReleaseTag("tag0.0.1"), "0.0.1");
  assert.equal(parseReleaseTag("v2.3.0"), "2.3.0");
  assert.equal(parseReleaseTag("2.3.0"), "2.3.0");
  assert.equal(parseReleaseTag("tag1.2.3.4"), "1.2.3.4");
  for (const bad of ["main", "tag1.2", "tag01.2.3", "v1.2.3-rc1", "tag65536.0.1", "tag1.2.3.4.5"]) {
    assert.throws(() => parseReleaseTag(bad), /Invalid|out of range/);
  }
});

test("staging ships all settings, monitor, Pico and extension assets, but never keys", async (t) => {
  const { stageExtension } = await release;
  const { root, source, target } = fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));

  assert.equal(stageExtension(source, target, "tag0.0.1"), "0.0.1");
  const staged = JSON.parse(fs.readFileSync(path.join(target, "manifest.json"), "utf8"));
  const original = JSON.parse(fs.readFileSync(path.join(source, "manifest.json"), "utf8"));
  assert.equal(staged.version, "0.0.1");
  assert.equal(original.version, "9.9.9");
  for (const included of ["options.html", "options.js", "monitor.html", "monitor.js", "panel.html",
    "background.js", "rules.js", "content.js", "vendor/pico.min.css",
    "images/logo.svg", "images/16.png", "extractors/common.js"]) {
    assert.ok(fs.existsSync(path.join(target, included)), included);
  }
  for (const excluded of ["README.md", "key.pem", "tests", ".github", "scripts"]) {
    assert.ok(!fs.existsSync(path.join(target, excluded)), excluded);
  }
});

test("invalid missing manifest assets fail before CRX signing", async (t) => {
  const { stageExtension } = await release;
  const { root, source, target } = fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.rmSync(path.join(source, "images/logo.svg"));
  assert.throws(() => stageExtension(source, target, "v2.3.0"), /Referenced extension file missing: images\/logo.svg/);
});

test("release staging never follows symlinked directories", async (t) => {
  const { stageExtension } = await release;
  const { root, source, target } = fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "private"));
  fs.writeFileSync(path.join(root, "private/private.pem"), "not for release");
  fs.symlinkSync(path.join(root, "private"), path.join(source, "vendor", "unsafe"));
  assert.throws(() => stageExtension(source, target, "v2.3.0"), /Symlinks are not supported/);
});

test("CRX3 verifier checks magic, version and non-empty header", async (t) => {
  const { checkCrx3Header } = await release;
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "crx3-header-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const file = path.join(root, "extension.crx");
  const header = Buffer.alloc(64);
  header.write("Cr24", 0, "ascii");
  header.writeUInt32LE(3, 4);
  header.writeUInt32LE(20, 8);
  fs.writeFileSync(file, header);
  assert.doesNotThrow(() => checkCrx3Header(file));
  header.writeUInt32LE(2, 4);
  fs.writeFileSync(file, header);
  assert.throws(() => checkCrx3Header(file), /not a CRX3/);
  header.writeUInt32LE(3, 4);
  header.writeUInt32LE(0, 8);
  fs.writeFileSync(file, header);
  assert.throws(() => checkCrx3Header(file), /empty proof header/);
});
