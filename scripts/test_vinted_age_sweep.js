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
  vintedAgeSweepQueueV1: [
    { id: "1", url: "https://www.vinted.pt/items/1-one", attempts: 0 },
    { id: "2", url: "https://www.vinted.pt/items/2-two", attempts: 0 },
    { id: "3", url: "https://www.vinted.pt/items/3-three", attempts: 0 },
  ],
  vintedListingPageAgeCacheV2: {},
};
let nextTabId = 10;
const tabUrls = new Map();
const posted = [];
const alarms = [];

const chrome = {
  runtime: {
    getManifest() {
      return {
        content_scripts: [{ matches: ["https://www.vinted.pt/*"] }],
        version: "3.0.0",
      };
    },
    onInstalled: { addListener() {} },
    onStartup: { addListener() {} },
    onMessage: { addListener() {} },
  },
  tabs: {
    async query() { return []; },
    async create(opts) {
      const id = ++nextTabId;
      tabUrls.set(id, opts.url);
      return { id, status: "complete", url: opts.url };
    },
    async update(id, opts) {
      if (opts.url) tabUrls.set(id, opts.url);
      return { id, status: "complete", url: tabUrls.get(id) };
    },
    async get(id) {
      return { id, status: "complete", url: tabUrls.get(id) };
    },
    async sendMessage(_id, payload) {
      if (payload.type === "read-vinted-uploaded-age") {
        const days = Number(payload.item_id);
        return {
          ok: true,
          age: { seconds: days * 86400, text: days + " days ago" },
        };
      }
      return { ok: true, protocol: 3 };
    },
    async remove(id) { tabUrls.delete(id); },
    async reload() {},
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
    create(name, options) { alarms.push({ name, options }); },
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
  const result = await context.processAgeSweepWindow();
  assert(result.ok === true, "Sweep did not succeed");
  assert(result.remaining === 0, "Sweep queue was not drained");
  assert(result.updated === 3, "Server update count was wrong");
  assert(Array.isArray(state.vintedAgeSweepQueueV1) && state.vintedAgeSweepQueueV1.length === 0,
    "Persisted queue was not emptied");
  assert(Object.keys(state.vintedListingPageAgeCacheV2).length === 3,
    "Rendered ages were not cached");
  assert(posted.length === 1 && posted[0].ages.length === 3,
    "Incremental listing-age endpoint did not receive the batch");
  assert(posted[0].ages.every((row) => row.listed_age_text.endsWith("days ago")),
    "Uploaded wording was not preserved");
  console.log("Persistent Vinted Uploaded-age sweep: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
