/**
 * Transfer regression tests: byte integrity, bounded retries and HLS cancellation.
 * Runs actual production functions in isolated VM contexts with deterministic
 * fake HTTP responses; no browser, real CDN or third-party test dependencies.
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "content.js"), "utf8");
const rules = fs.readFileSync(path.join(root, "rules.js"), "utf8");

function section(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, "Missing start marker " + startMarker);
  const end = source.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(end, -1, "Missing end marker " + endMarker);
  return source.slice(start, end);
}

const retrySource = section(
  "  /** Controllers allow Stop to cancel active fetches",
  "  /** 触发浏览器下载"
);
const hlsTransportSource = section(
  "  /**\n   * Request HLS playlist text",
  "  /** 解析相对地址"
);
const hlsDownloadSource = section(
  "  async function downloadM3u8(media, onProgress) {",
  "  /* ---------------- 单条下载调度"
);

function makeResponse(length, expectedLength = length, encoding = "") {
  const payload = new Uint8Array(length).fill(7);
  let index = 0;
  return {
    ok: true,
    status: 200,
    headers: {
      get(name) {
        if (name === "content-type") return "video/mp4";
        if (name === "content-length") return String(expectedLength);
        if (name === "content-encoding") return encoding || null;
        return null;
      },
    },
    body: {
      getReader() {
        return {
          async read() {
            return index++ ? { done: true } : { done: false, value: payload };
          },
          async cancel() {},
        };
      },
    },
    async arrayBuffer() { return payload.buffer; },
    async text() { return "#EXTM3U\n#EXTINF:5,\na.ts"; },
  };
}

/**
 * Load the real retry helpers and transport functions, injecting only their
 * browser globals; fake delays avoid slow/flaky wall-clock tests.
 */
function makeEnv(overrides = {}) {
  const task = { stopped: false };
  const pauses = [];
  const messages = [];
  const defaults = {
    DOWNLOAD_RULES: {
      MIN_VALID_BYTES: 1024,
      DIRECT_IDLE_TIMEOUT_MS: 20_000,
      DIRECT_RETRIES: 1,
      M3U8_SEGMENT_RETRIES: 2,
      M3U8_PLAYLIST_RETRIES: 1,
      M3U8_SEGMENT_TIMEOUT_MS: 30_000,
      M3U8_SEGMENT_CONCURRENCY: 2,
      TRANSFER_RETRY_BASE_DELAY_MS: 400,
      TRANSFER_RETRY_MAX_DELAY_MS: 1600,
    },
    downloadTask: task,
    fetch: async () => makeResponse(2048),
    Blob,
    AbortController,
    Uint8Array,
    console: { warn: (...args) => messages.push(args) },
    setTimeout,
    clearTimeout,
    sleep: async (ms) => { pauses.push(ms); },
    isRejectedMediaMimeType: () => false,
    sniffVideoSignature: () => "video",
    isVideoMimeType: () => true,
  };
  const context = { ...defaults, ...overrides };
  const result = vm.runInNewContext(
    retrySource + "\n" + hlsTransportSource +
      "\n({ activeDownloadControllers, transferWithRetries, isRetryableTransferError, " +
      "fetchWithProgress, fetchTextWithTimeout, fetchBytesWithTimeout })",
    context
  );
  return { ...result, context, task, pauses, messages };
}

test("full direct file matches declared Content-Length and returns a Blob", async () => {
  const env = makeEnv({ fetch: async () => makeResponse(4096) });
  const statuses = [];
  const blob = await env.fetchWithProgress("https://media.test/movie.mp4", (p) => statuses.push(p));
  assert.equal(blob.size, 4096);
  assert.equal(env.activeDownloadControllers.size, 0);
  assert.ok(statuses.some((p) => p > 0 && p < 100));
});

test("truncated video is rejected instead of being marked done", async () => {
  const env = makeEnv({ fetch: async () => makeResponse(2048, 8192) });
  await assert.rejects(
    env.fetchWithProgress("https://media.test/movie.mp4", () => {}),
    (error) => error.retryable === true && /下载不完整/.test(error.message)
  );
  assert.equal(env.activeDownloadControllers.size, 0);
});

test("compressed responses do not compare wire Content-Length with decoded bytes", async () => {
  const env = makeEnv({ fetch: async () => makeResponse(2048, 1024, "gzip") });
  const blob = await env.fetchWithProgress("https://media.test/movie.mp4", () => {});
  assert.equal(blob.size, 2048);
});

test("only temporary errors can retry; a 403 or invalid video fails promptly", async () => {
  const env = makeEnv();
  const temporary = [408, 425, 429, 500, 502, 503, 504, 522];
  for (const code of temporary) assert.equal(env.isRetryableTransferError({ status: code }), true);
  for (const code of [400, 401, 403, 404, 410, 501]) {
    assert.equal(env.isRetryableTransferError({ status: code }), false);
  }
  assert.equal(env.isRetryableTransferError(new TypeError("Failed to fetch")), true);
  assert.equal(env.isRetryableTransferError(new Error("响应内容不是有效视频")), false);

  let calls = 0;
  await assert.rejects(env.transferWithRetries(async () => {
    calls++;
    throw { status: 403 };
  }, 3, "forbidden"), (error) => error.status === 403);
  assert.equal(calls, 1);
});

test("transient retries are bounded, back off, and reset progress", async () => {
  const env = makeEnv();
  let calls = 0;
  let resets = 0;
  const value = await env.transferWithRetries(async () => {
    calls++;
    if (calls < 3) throw new TypeError("Failed to fetch");
    return "ok";
  }, 2, "test media", () => { resets++; });
  assert.equal(value, "ok");
  assert.equal(calls, 3);
  assert.equal(resets, 2);
  assert.equal(env.pauses.reduce((sum, ms) => sum + ms, 0), 1200);

  env.task.stopped = true;
  await assert.rejects(env.transferWithRetries(async () => "unexpected", 2, "stopped"), /用户已停止/);
});

test("Stop interrupts a retry delay before the next transfer attempt", async () => {
  const env = makeEnv({
    sleep: async () => { env.task.stopped = true; },
  });
  let attempts = 0;
  await assert.rejects(env.transferWithRetries(async () => {
    attempts++;
    throw new TypeError("Failed to fetch");
  }, 2, "stop-aware"), /用户已停止/);
  assert.equal(attempts, 1);
});

test("stopping aborts active HLS requests and releases their controllers", async () => {
  let started;
  const begun = new Promise((resolve) => { started = resolve; });
  const env = makeEnv({
    fetch: async (_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      }, { once: true });
      started();
    }),
  });
  const group = new Set();
  const pending = env.fetchBytesWithTimeout("https://media.test/first.ts", 30_000, group);
  await begun;
  assert.equal(group.size, 1);
  env.task.stopped = true;
  for (const controller of env.activeDownloadControllers) controller.abort();
  await assert.rejects(pending, /用户已停止/);
  assert.equal(group.size, 0);
  assert.equal(env.activeDownloadControllers.size, 0);
});

function hlsEnv(fetchSegment) {
  const env = makeEnv();
  const text = "#EXTM3U\n#EXTINF:5,\na.ts\n#EXTINF:5,\nb.ts";
  const code = retrySource + "\n" + hlsDownloadSource + "\n({ downloadM3u8 })";
  const runner = vm.runInNewContext(code, {
    ...env.context,
    fetchTextWithTimeout: async () => text,
    fetchBytesWithTimeout: fetchSegment,
    isHlsVideoPlaylist: () => true,
    parseMediaPlaylist: () => ({
      segments: ["https://media.test/a.ts", "https://media.test/b.ts"],
      initUrl: "",
    }),
    guessSegmentContainer: () => "ts",
  });
  return { ...env, run: runner.downloadM3u8 };
}

test("HLS transient fragment failures retry and concatenate in playlist order", async () => {
  const attempts = new Map();
  const env = hlsEnv(async (url) => {
    attempts.set(url, (attempts.get(url) || 0) + 1);
    if (url.endsWith("a.ts") && attempts.get(url) === 1) {
      throw new TypeError("Failed to fetch");
    }
    const byte = url.endsWith("a.ts") ? 65 : 66;
    return new Uint8Array(2048).fill(byte).buffer;
  });
  const progresses = [];
  const result = await env.run(
    { verifiedKind: "hls", verifiedUrl: "https://media.test/master.m3u8" },
    (p) => progresses.push(p)
  );
  assert.equal(attempts.get("https://media.test/a.ts"), 2);
  assert.equal(attempts.get("https://media.test/b.ts"), 1);
  assert.equal(result.ext, "ts");
  assert.equal(result.blob.size, 4096);
  const merged = new Uint8Array(await result.blob.arrayBuffer());
  assert.equal(merged[0], 65);
  assert.equal(merged[2048], 66);
  assert.deepEqual(progresses, [50, 99]);
});

test("irrecoverable HLS error aborts sibling fragments without retrying 404", async () => {
  let aborted = 0;
  let aCalls = 0;
  const env = hlsEnv(async (url, _timeout, controllers) => {
    const controller = new AbortController();
    controllers.add(controller);
    try {
      if (url.endsWith("a.ts")) {
        aCalls++;
        const error = new Error("HTTP 404");
        error.status = 404;
        throw error;
      }
      return await new Promise((_resolve, reject) => {
        controller.signal.addEventListener("abort", () => {
          aborted++;
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        }, { once: true });
      });
    } finally {
      controllers.delete(controller);
    }
  });
  await assert.rejects(
    env.run({ verifiedKind: "hls", verifiedUrl: "https://media.test/master.m3u8" }),
    /HTTP 404/
  );
  assert.equal(aCalls, 1);
  assert.equal(aborted, 1);
});

test("retry rules remain bounded and live in rules.js", () => {
  assert.match(rules, /DIRECT_RETRIES:\s*1/);
  assert.match(rules, /M3U8_SEGMENT_RETRIES:\s*2/);
  assert.match(rules, /M3U8_PLAYLIST_RETRIES:\s*1/);
  assert.match(source, /transferWithRetries\(\s*\(\) => fetchWithProgress\(url, onProgress\)/);
  assert.match(source, /transferWithRetries\(\s*\(\) => fetchBytesWithTimeout\(tasks\[index\]/);
});
