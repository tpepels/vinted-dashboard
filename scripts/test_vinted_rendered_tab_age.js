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
      for (const [tabId] of urls) urls.delete(tabId);
    },
  },
  tabs: {
    async query() { return []; },
    async update(id, opts) {
      updated.push({ id, ...opts });
      if (opts.url) urls.set(id, opts.url);
      return { id, status: "complete", url: urls.get(id) };
    },
    async get(id) {
      return { id, status: "complete", url: urls.get(id) };
    },
    async sendMessage(id, payload) {
      sent.push({ id, payload });
      if (payload.type === "read-vinted-uploaded-age") {
        return { ok: true, age: ageByItem[String(payload.item_id)] || null };
      }
      return { ok: true, protocol: 5 };
    },
    async reload() {},
    async remove() {},
  },
  storage: {
    local: {
      async get() { return {}; },
      async set() {},
      async remove() {},
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

  const ages = await context.renderedUploadedAgesBurst(items, 2);

  assert(ages["9826364597"].text === "5 weeks ago", "Destination India age was not returned");
  assert(ages["9826364597"].seconds === 5 * 7 * 86400, "5 weeks converted incorrectly");
  assert(Object.keys(ages).length === 4, "Not all rendered ages were collected");

  assert(windowsCreated.length === 1, "Burst should use one worker window");
  assert(windowsCreated[0].state === "minimized", "Worker window should be minimized");
  assert(windowsCreated[0].focused === false, "Worker window should not steal focus");
  assert(windowsCreated[0].tabs.length === 2, "Requested worker count was not respected");
  assert(updated.length === 2, "Worker tabs should be reused for the remaining items");
  assert(sent.filter((entry) => entry.payload.type === "read-vinted-uploaded-age").length === 4,
    "Each item page should be queried through its rendered DOM");
  assert(windowsRemoved.length === 1, "Worker window was not closed");

  console.log("Vinted minimized-window rendered Uploaded-age burst: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
