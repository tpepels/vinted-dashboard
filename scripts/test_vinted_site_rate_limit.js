const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "extension", "background.js"),
  "utf8",
);

const state = {
  bridgeToken: "token",
  bridgeWorkspace: "Test",
  syncStatus: {},
  vintedListingPageAgeCacheV2: {},
  vintedAgeScanFailuresV1: {},
};
let nextTabId = 100;
let nextWindowId = 20;
const urls = new Map();
const windowsRemoved = [];
const alarms = [];

const chrome = {
  runtime: {
    getManifest() {
      return {
        content_scripts: [{ matches: ["https://www.vinted.pt/*"] }],
        version: "3.2.0",
      };
    },
    onInstalled: { addListener() {} },
    onStartup: { addListener() {} },
    onMessage: { addListener() {} },
  },
  windows: {
    async create(opts) {
      const id = ++nextWindowId;
      const tabs = opts.url.map((url) => {
        const tabId = ++nextTabId;
        urls.set(tabId, url);
        return { id: tabId, windowId: id, status: "complete", url };
      });
      return { id, tabs };
    },
    async remove(id) {
      windowsRemoved.push(id);
      urls.clear();
    },
  },
  tabs: {
    async query() { return []; },
    async update(id, opts) {
      if (opts.url) urls.set(id, opts.url);
      return { id, windowId: nextWindowId, status: "complete", url: urls.get(id) };
    },
    async get(id) {
      return { id, windowId: nextWindowId, status: "complete", url: urls.get(id) };
    },
    async sendMessage(_id, payload) {
      if (payload.type === "read-vinted-uploaded-age") {
        if (String(payload.item_id) === "2") {
          return { ok: true, age: null, rate_limited: true, challenged: false };
        }
        return {
          ok: true,
          age: { seconds: Number(payload.item_id) * 86400, text: payload.item_id + " days ago" },
          rate_limited: false,
          challenged: false,
        };
      }
      return { ok: true, protocol: 7 };
    },
    async reload() {},
    async remove() {},
  },
  storage: {
    local: {
      async get(keys) {
        const out = {};
        for (const key of keys || []) out[key] = state[key];
        return out;
      },
      async set(values) { Object.assign(state, values); },
      async remove(keys) { for (const key of keys || []) delete state[key]; },
    },
  },
  alarms: {
    create(name, options) { alarms.push({ name, options }); },
    onAlarm: { addListener() {} },
  },
  scripting: { async executeScript() {} },
};

const context = {
  console,
  URL,
  chrome,
  Date,
  setTimeout: (fn, _ms) => setTimeout(fn, 0),
  clearTimeout,
  fetch: async (url, options = {}) => {
    if (String(url).endsWith("/api/extension/listing-ages")) {
      const body = JSON.parse(options.body || "{}");
      return {
        ok: true,
        async json() { return { ok: true, updated: (body.ages || []).length, missing: [] }; },
      };
    }
    return { ok: true, async json() { return {}; } };
  },
};

vm.createContext(context);
vm.runInContext(source, context, { filename: "background.js" });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

(async () => {
  const items = ["1", "2", "3", "4"].map((id) => ({
    id,
    url: "https://www.vinted.pt/items/" + id + "-test",
  }));

  await context.startAgeJob(items, "manual");
  const result = await context.processAgeJobWave();

  assert(result.ok === true && result.paused === true,
    "Site rate limit should pause rather than fail the job");
  assert(state.vintedAgeBurstJobV2.remaining.length >= 1,
    "Rate-limited items must stay in the persisted queue");
  assert(Number(state.vintedAgeBurstJobV2.cooldown_until) > Date.now(),
    "Rate-limit cooldown was not persisted");
  assert(Number(state.vintedAgeBurstJobV2.rate_limit_hits) === 1,
    "Rate-limit hit counter was not incremented");
  assert(Object.keys(state.vintedAgeScanFailuresV1).length === 0,
    "Rate-limited pages must not be recorded as unread listing failures");
  assert(windowsRemoved.length === 1,
    "Worker window should close immediately when Vinted rate limits the site");
  assert(alarms.some((entry) =>
    entry.name === "reseller-vinted-age-job"
    && Number(entry.options?.when) >= Number(state.vintedAgeBurstJobV2.cooldown_until)
  ), "Cooldown resume alarm was not scheduled");

  console.log("Vinted site rate-limit pause: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
