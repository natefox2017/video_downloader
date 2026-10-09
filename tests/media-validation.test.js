/**
 * Regression tests for MV3 privileged media probing and strict video admission.
 * No browser, external network, dependencies or downloaded sample files needed.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const rulesText = fs.readFileSync(path.join(root, "rules.js"), "utf8");
const backgroundText = fs.readFileSync(path.join(root, "background.js"), "utf8");
const contentText = fs.readFileSync(path.join(root, "content.js"), "utf8");
const rules = vm.runInNewContext(rulesText +
  "\n({ isMediaUrl, isHlsVideoPlaylist, sniffVideoSignature, isRejectedMediaMimeType })");

function mp4Bytes(size = 2048) {
  const bytes = new Uint8Array(size);
  bytes.set([0, 0, 0, 24, 102, 116, 121, 112, 105, 115, 111, 109]);
  return bytes;
}

function makeResponse(body, mime, status = 206) {
  let reads = 0;
  let cancelled = false;
  const payload = typeof body === "string" ? new TextEncoder().encode(body) : body;
  return {
    ok: true,
    status,
    headers: {
      get(name) {
        if (name === "content-type") return mime;
        if (name === "content-range") return status === 206 ? "bytes 0-16383/465000000" : null;
        if (name === "content-length") return String(payload.byteLength);
        return null;
      },
    },
    body: {
      getReader() {
        return {
          async read() {
            reads++;
            return reads === 1 ? { done: false, value: payload } : { done: true };
          },
          cancel() { cancelled = true; return Promise.resolve(); },
        };
      },
    },
    get cancelled() { return cancelled; },
    get reads() { return reads; },
  };
}

function createProbe(fetchResponse) {
  let listener;
  const urls = [];
  const chrome = {
    action: { onClicked: { addListener() {} } },
    runtime: { onMessage: { addListener(callback) { listener = callback; } } },
    tabs: {
      onRemoved: { addListener() {} },
      onUpdated: { addListener() {} },
    },
  };
  const context = {
    chrome, AbortController, TextDecoder, Uint8Array, setTimeout, clearTimeout,
    URL, console: { warn() {} },
    async fetch(url, options) {
      urls.push({ url, options });
      return fetchResponse(url, options);
    },
  };
  vm.runInNewContext(rulesText + "\n" + backgroundText, context);
  return {
    urls,
    request(url) {
      return new Promise((resolve) => {
        assert.equal(listener({ type: "probe_media_url", url }, { tab: { id: 42 } }, resolve), true);
      });
    },
  };
}

test("generic sniffing excludes audio and fragments without filtering standalone video", () => {
  assert.equal(rules.isMediaUrl("https://cdn.test/audio.mp3"), false);
  assert.equal(rules.isMediaUrl("https://cdn.test/audio.m4a"), false);
  assert.equal(rules.isMediaUrl("https://cdn.test/chunk.m4s?token=a"), false);
  assert.equal(rules.isMediaUrl("https://cdn.test/chunk.ts"), false);
  assert.equal(rules.isMediaUrl("https://cdn.test/movie.mp4?token=abc"), true);
  assert.equal(rules.isMediaUrl("https://cdn.test/movie.m3u8"), true);
});

test("signature filter rejects HTML and audio but accepts MP4 headers", () => {
  assert.equal(rules.sniffVideoSignature(mp4Bytes()), "video");
  assert.equal(rules.sniffVideoSignature(new TextEncoder().encode("<!doctype html><html>error")), "invalid");
  assert.equal(rules.sniffVideoSignature(new TextEncoder().encode("ID3audio")), "invalid");
  assert.equal(rules.isRejectedMediaMimeType("audio/mpeg"), true);
});

test("HLS audio-only renditions are not videos", () => {
  assert.equal(rules.isHlsVideoPlaylist("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=64000,CODECS=\"mp4a.40.2\"\naudio.m3u8"), false);
  assert.equal(rules.isHlsVideoPlaylist("#EXTM3U\n#EXTINF:10,\nsegment.aac\n"), false);
  assert.equal(rules.isHlsVideoPlaylist("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS=\"avc1.42E01E,mp4a.40.2\"\nvideo.m3u8"), true);
  assert.equal(rules.isHlsVideoPlaylist("#EXTM3U\n#EXTINF:10,\nsegment001.ts\n"), true);
});

test("privileged Range probe accepts MP4 and cancels the response after its prefix", async () => {
  const response = makeResponse(mp4Bytes(60_000), "application/octet-stream", 200);
  const env = createProbe(async () => response);
  const result = await env.request("https://cdn.example/video.mp4");
  assert.equal(result.ok, true);
  assert.equal(result.media.kind, "video");
  assert.equal(result.media.size, 60_000);
  assert.equal(response.cancelled, true);
  assert.equal(response.reads, 1, "Must not drain the whole video when Range is ignored");
  assert.equal(env.urls[0].options.headers.Range, "bytes=0-16383");
});

test("HTML errors are excluded even if the server labels them video/mp4", async () => {
  const env = createProbe(async () => makeResponse("<html>Forbidden</html>", "video/mp4"));
  const result = await env.request("https://cdn.example/fake.mp4");
  assert.equal(result.ok, false);
  assert.equal(result.media, null);
});

test("private hosts cannot be scanned via the privileged background worker", async () => {
  const env = createProbe(async () => { throw new Error("Must never fetch a private host"); });
  const result = await env.request("http://127.0.0.1/admin.mp4");
  assert.equal(result.ok, false);
  assert.equal(env.urls.length, 0);
});

test("only verified videos reach panel, monitor and downloads", () => {
  assert.match(contentText, /filter\(\(item\) => item\.verificationState === "verified"\)\.map/);
  assert.match(contentText, /item\.verificationState === "verified" && item\.status !== "done"/);
  assert.match(contentText, /type: "probe_media_url"/);
  assert.doesNotMatch(contentText, /Math\.floor\(percent \* 0\.9\)/);
  assert.match(contentText, /DOWNLOAD_RULES\.DIRECT_IDLE_TIMEOUT_MS/);
});
