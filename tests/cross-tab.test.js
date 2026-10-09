/**
 * Cross-tab registry regression tests using only Node's built-in test runner.
 * Simulate MV3 APIs to verify concurrent tab updates and dispatch acknowledgements.
 */

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");

function createBackground() {
  const data = {};
  let messageListener;
  let removedListener;
  const dispatched = [];
  const rejectedTabs = new Set();

  const chrome = {
    action: {
      onClicked: { addListener() {} },
      async setBadgeText() {},
      async setBadgeBackgroundColor() {},
      async setBadgeTextColor() {},
    },
    tabs: {
      onRemoved: { addListener(callback) { removedListener = callback; } },
      onUpdated: { addListener() {} },
      async query() { return []; },
      async sendMessage(tabId, message) {
        dispatched.push({ tabId, message });
        if (rejectedTabs.has(tabId)) return { ok: false };
        return { ok: true, accepted: message.shareUrls.length };
      },
    },
    scripting: { async executeScript() {} },
    storage: {
      session: {
        async get(key) {
          // Independent snapshots expose the original lost-update race.
          await Promise.resolve();
          return { [key]: structuredClone(data[key] || {}) };
        },
        async set(values) {
          await Promise.resolve();
          Object.assign(data, structuredClone(values));
        },
      },
    },
    runtime: {
      onMessage: { addListener(callback) { messageListener = callback; } },
      async openOptionsPage() {},
    },
  };

  const code = fs.readFileSync(path.join(root, "background.js"), "utf8");
  vm.runInNewContext(code, { chrome, console });

  function report(tabId, shareUrl, title) {
    messageListener({
      type: "update_media_registry",
      pageUrl: `https://example${tabId}.test/watch`,
      pageTitle: `网站${tabId}`,
      platform: "通用",
      items: [{ shareUrl, title, status: "idle", size: 0 }],
    }, { tab: { id: tabId } }, () => {});
  }

  function request(message) {
    return new Promise((resolve, reject) => {
      try {
        const keepOpen = messageListener(message, {}, resolve);
        if (keepOpen !== true) resolve(null);
      } catch (error) {
        reject(error);
      }
    });
  }

  return { report, request, remove: (tabId) => removedListener(tabId), dispatched, rejectedTabs };
}

test("concurrent reports from different tabs are both retained", async () => {
  const env = createBackground();
  env.report(101, "video:a", "歌天音乐1");
  env.report(202, "video:b", "歌天音乐2");

  const response = await env.request({ type: "get_media_registry" });
  assert.equal(response.ok, true);
  assert.deepEqual(Object.keys(response.registry).sort(), ["101", "202"]);
  assert.equal(response.registry["101"].items[0].title, "歌天音乐1");
  assert.equal(response.registry["202"].items[0].title, "歌天音乐2");
});

test("closing one tab removes only its own detected videos", async () => {
  const env = createBackground();
  env.report(101, "video:a", "歌天音乐1");
  env.report(202, "video:b", "歌天音乐2");
  await env.request({ type: "get_media_registry" });

  env.remove(101);
  const response = await env.request({ type: "get_media_registry" });
  assert.equal(response.registry["101"], undefined);
  assert.equal(response.registry["202"].items[0].shareUrl, "video:b");
});

test("batch downloads preserve per-tab media identities and count successful acknowledgements", async () => {
  const env = createBackground();
  env.rejectedTabs.add(202);
  const result = await env.request({
    type: "start_multi_tab_download",
    items: [
      { tabId: 101, shareUrl: "video:a" },
      { tabId: 101, shareUrl: "video:a" },
      { tabId: 101, shareUrl: "video:b" },
      { tabId: 202, shareUrl: "video:c" },
    ],
  });

  assert.equal(result.ok, true);
  assert.equal(result.tabsStarted, 1);
  assert.equal(result.videosRequested, 2);
  assert.deepEqual(
    env.dispatched.map((entry) => entry.tabId),
    [101, 202]
  );
  assert.deepEqual(Array.from(env.dispatched[0].message.shareUrls), ["video:a", "video:b"]);
});

test("monitor has an independent document and settings no longer renders the media registry", () => {
  const monitor = fs.readFileSync(path.join(root, "monitor.html"), "utf8");
  const options = fs.readFileSync(path.join(root, "options.html"), "utf8");
  assert.match(monitor, /<script src="monitor\.js"><\/script>/);
  assert.match(monitor, /id="registry"/);
  assert.match(options, /href="monitor\.html"/);
  assert.doesNotMatch(options, /id="registry"/);
});
