const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "extension", "background.js"),
  "utf8",
);

let nextTabId = 100;
const created = [];
const removed = [];
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
        version: "3.0.1",
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
      created.push({ id, ...opts });
      urls.set(id, opts.url);
      return { id, status: "complete", url: opts.url };
    },
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
      return { ok: true };
    },
    async remove(id) {
      removed.push(id);
      urls.delete(id);
    },
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

  const ages = await context.renderedUploadedAges(items, 2);

  assert(ages["9826364597"].text === "5 weeks ago", "Destination India age was not returned");
  assert(ages["9826364597"].seconds === 5 * 7 * 86400, "5 weeks converted incorrectly");
  assert(Object.keys(ages).length === 4, "Not all rendered ages were collected");

  assert(created.length === 2, "Worker pool should create exactly two inactive tabs");
  assert(created.every((tab) => tab.active === false), "Worker tabs must be inactive");
  assert(updated.length === 2, "Two worker tabs should be reused for the remaining items");
  assert(sent.filter((entry) => entry.payload.type === "read-vinted-uploaded-age").length === 4,
    "Each item page should be queried through its rendered DOM");
  assert(removed.length === 2, "Both temporary worker tabs must be closed");

  console.log("Vinted two-tab rendered Uploaded-age collector: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
