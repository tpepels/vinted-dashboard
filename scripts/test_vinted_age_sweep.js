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
  vintedAgeScanFailuresV1: {
    old_failure: { failed_at: Date.now() - 1000 },
  },
};
let nextTabId = 100;
let nextWindowId = 20;
const urls = new Map();
const posted = [];
const windowsCreated = [];

const chrome = {
  runtime: {
    getManifest() {
      return {
        content_scripts: [{ matches: ["https://www.vinted.pt/*"] }],
        version: "3.1.0",
      };
    },
    onInstalled: { addListener() {} },
    onStartup: { addListener() {} },
    onMessage: { addListener() {} },
  },
  windows: {
    async create(opts) {
      const id = ++nextWindowId;
      const tabs = (Array.isArray(opts.url) ? opts.url : [opts.url]).map((url) => {
        const tabId = ++nextTabId;
        urls.set(tabId, url);
        return { id: tabId, windowId: id, status: "complete", url };
      });
      windowsCreated.push({ id, ...opts, tabs });
      return { id, tabs };
    },
    async remove() {
      urls.clear();
    },
  },
  tabs: {
    async query() { return []; },
    async update(id, opts) {
      if (opts.url) urls.set(id, opts.url);
      return { id, status: "complete", url: urls.get(id) };
    },
    async get(id) {
      return { id, status: "complete", url: urls.get(id) };
    },
    async sendMessage(_id, payload) {
      if (payload.type === "read-vinted-uploaded-age") {
        const n = Number(payload.item_id);
        if (n === 7) return { ok: true, age: null };
        return { ok: true, age: { seconds: n * 86400, text: n + " days ago" } };
      }
      return { ok: true, protocol: 5 };
    },
    async reload() {},
    async remove() {},
  },
  storage: {
    local: {
      async get(keys) {
        const result = {};
        for (const key of keys || []) result[key] = state[key];
        return result;
      },
      async set(values) { Object.assign(state, values); },
      async remove(keys) {
        for (const key of keys || []) delete state[key];
      },
    },
  },
  alarms: {
    create() {},
    onAlarm: { addListener() {} },
  },
  scripting: {
    async executeScript() {},
  },
};

const context = {
  console,
  URL,
  chrome,
  setTimeout,
  clearTimeout,
  Date,
  fetch: async (url, options = {}) => {
    if (String(url).endsWith("/api/extension/listing-ages")) {
      const body = JSON.parse(options.body || "{}");
      posted.push(body);
      return {
        ok: true,
        async json() {
          return { ok: true, updated: (body.ages || []).length, missing: [] };
        },
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
  const items = Array.from({ length: 40 }, (_, index) => {
    const id = String(index + 1);
    return { id, url: "https://www.vinted.pt/items/" + id + "-test" };
  });

  const result = await context.runAgeBurst(items, "manual");

  assert(result.scanned === 40, "Manual burst should scan the whole queue");
  assert(result.updated === 39, "One intentionally unread item should not be posted");
  assert(result.failed === 1, "Unread item should be counted once");
  assert(windowsCreated.length === 1, "One burst should create one worker window");
  assert(windowsCreated[0].tabs.length === 16, "Burst should use 16 concurrent tabs");
  assert(Object.keys(state.vintedListingPageAgeCacheV2).length === 39,
    "Successful ages were not cached");
  assert(state.vintedAgeScanFailuresV1["7"], "Unread item was not cooldown-marked");
  assert(posted.length === 1 && posted[0].ages.length === 39,
    "Incremental endpoint should receive successful ages in one request");

  const periodic = await context.eligibleAgeScanItems(
    [{ id: "7", url: "https://www.vinted.pt/items/7-test" }],
    "periodic",
  );
  assert(periodic.length === 0, "Recent failures should not be retried every periodic sync");

  const manual = await context.eligibleAgeScanItems(
    [{ id: "7", url: "https://www.vinted.pt/items/7-test" }],
    "manual",
  );
  assert(manual.length === 1, "Manual sync should allow an explicit retry");

  console.log("Finite Vinted posting-age burst: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
