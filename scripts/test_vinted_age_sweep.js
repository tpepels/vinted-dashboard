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
      const tabs = (Array.isArray(opts.url) ? opts.url : [opts.url]).map((url) => {
        const tabId = ++nextTabId;
        urls.set(tabId, url);
        return { id: tabId, windowId: id, status: "complete", url };
      });
      windowsCreated.push({ id, ...opts, tabs });
      return { id, tabs };
    },
    async remove(id) {
      windowsRemoved.push(id);
      for (const [tabId] of [...urls]) urls.delete(tabId);
    },
  },
  tabs: {
    async query() { return []; },
    async update(id, opts) {
      if (opts.url) urls.set(id, opts.url);
      return { id, status: "complete", url: urls.get(id) };
    },
    async get(id) {
      if (!urls.has(id)) throw new Error("tab missing");
      return { id, status: "complete", url: urls.get(id) };
    },
    async sendMessage(_id, payload) {
      if (payload.type === "read-vinted-uploaded-age") {
        const n = Number(payload.item_id);
        if (n === 7) return { ok: true, age: null };
        return { ok: true, age: { seconds: n * 86400, text: n + " days ago" } };
      }
      return { ok: true, protocol: 6 };
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
  const items = Array.from({ length: 70 }, (_, index) => {
    const id = String(index + 1);
    return { id, url: "https://www.vinted.pt/items/" + id + "-test" };
  });

  const started = await context.startAgeJob(items, "manual");
  assert(started.started === true, "Manual job was not started");
  assert(started.remaining === 70, "Whole queue was not persisted");

  const first = await context.processAgeJobWave();
  assert(first.ok === true, "First resumable age chunk failed");
  assert(first.remaining === 22, "One event should process exactly four 12-item waves");
  assert(state.vintedAgeBurstJobV2.remaining.length === 22,
    "Remaining age queue was not persisted after the event");
  assert(windowsCreated.length === 1, "Age job should create one worker window");
  assert(windowsCreated[0].tabs.length === 12, "Age job should use 12 reusable tabs");
  assert(windowsRemoved.length === 0, "Worker window closed before the finite job finished");

  const second = await context.processAgeJobWave();
  assert(second.ok === true && second.remaining === 0, "Second chunk did not finish the job");
  assert(state.vintedAgeBurstJobV2 === undefined, "Finished age job was not cleared");
  assert(windowsCreated.length === 1, "Resumed job created another worker window");
  assert(windowsRemoved.length === 1, "Worker window was not closed at job completion");
  assert(Object.keys(state.vintedListingPageAgeCacheV2).length === 69,
    "Successful ages were not cached");
  assert(state.vintedAgeScanFailuresV1["7"], "Unread item was not cooldown-marked");
  assert(posted.reduce((total, body) => total + body.ages.length, 0) === 69,
    "Incremental endpoint did not receive every successful age");

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

  assert(alarms.some((row) => row.name === "reseller-vinted-age-job"),
    "Resumable age job did not schedule continuation/watchdog alarms");

  console.log("Resumable finite Vinted posting-age job: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
