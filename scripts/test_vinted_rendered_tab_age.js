const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "extension", "background.js"),
  "utf8",
);

let nextTabId = 100;
let nextWindowId = 10;
const windowsCreated = [];
const windowsRemoved = [];
const updated = [];
const sent = [];
const urls = new Map();

const ageByItem = {
  "9826364597": { seconds: 5 * 7 * 86400, text: "5 weeks ago" },
  "1000000001": { seconds: 2 * 7 * 86400, text: "2 weeks ago" },
  "1000000002": { seconds: 9 * 86400, text: "9 days ago" },
  "1000000003": { seconds: 3 * 86400, text: "3 days ago" },
};

const state = {
  bridgeToken: "token",
  bridgeWorkspace: "Test",
  syncStatus: {},
};

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
      updated.push({ id, ...opts });
      if (opts.url) urls.set(id, opts.url);
      return { id, windowId: windowsCreated[windowsCreated.length - 1]?.id, status: "complete", url: urls.get(id) };
    },
    async get(id) {
      if (!urls.has(id)) throw new Error("tab missing");
      return { id, status: "complete", url: urls.get(id) };
    },
    async sendMessage(id, payload) {
      sent.push({ id, payload });
      if (payload.type === "read-vinted-uploaded-age") {
        return { ok: true, age: ageByItem[String(payload.item_id)] || null };
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
  fetch: async () => ({ ok: true, async json() { return {}; } }),
  chrome,
  setTimeout,
  clearTimeout,
  Date,
};
vm.createContext(context);
vm.runInContext(source, context, { filename: "background.js" });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

(async () => {
  const items = [
    {
      id: "9826364597",
      url: "https://www.vinted.pt/items/9826364597-destination-india-the-lonely-hearts-travel-club-2-katy-colins",
    },
    { id: "1000000001", url: "https://www.vinted.pt/items/1000000001-one" },
    { id: "1000000002", url: "https://www.vinted.pt/items/1000000002-two" },
    { id: "1000000003", url: "https://www.vinted.pt/items/1000000003-three" },
  ];

  const job = {
    remaining: items,
    scanned: 0,
    updated: 0,
    failed: 0,
    skipped: 0,
    window_id: null,
    tab_ids: [],
  };

  const first = await context.renderedUploadedAgeWave(job, items.slice(0, 2));
  assert(first["9826364597"].text === "5 weeks ago", "Destination India age was not returned");
  assert(first["9826364597"].seconds === 5 * 7 * 86400, "5 weeks converted incorrectly");
  assert(Object.keys(first).length === 2, "First rendered wave did not collect both ages");
  assert(windowsCreated.length === 1, "Worker window was not created");
  assert(windowsCreated[0].state === "minimized", "Worker window should be minimized");
  assert(windowsCreated[0].focused === false, "Worker window should not steal focus");
  assert(windowsCreated[0].tabs.length === 2, "Wave should use requested number of tabs");

  const second = await context.renderedUploadedAgeWave(job, items.slice(2));
  assert(Object.keys(second).length === 2, "Second rendered wave did not collect both ages");
  assert(windowsCreated.length === 1, "Worker tabs were not reused");
  assert(updated.length === 2, "Reused worker tabs should navigate to the next two items");
  assert(sent.filter((entry) => entry.payload.type === "read-vinted-uploaded-age").length === 4,
    "Each item page should be queried through its rendered DOM");

  await context.closeAgeWorkerWindow(job);
  assert(windowsRemoved.length === 1, "Worker window was not closed");

  console.log("Vinted minimized-window rendered Uploaded-age waves: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
