const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "extension", "background.js"),
  "utf8",
);

let created = null;
let removed = null;
let sent = [];
const chrome = {
  runtime: {
    getManifest() {
      return {
        content_scripts: [{ matches: ["https://www.vinted.pt/*"] }],
        version: "2.8.0",
      };
    },
    onInstalled: { addListener() {} },
    onStartup: { addListener() {} },
    onMessage: { addListener() {} },
  },
  tabs: {
    async query() { return []; },
    async create(opts) {
      created = opts;
      return { id: 101, status: "complete", url: opts.url };
    },
    async get(id) {
      return { id, status: "complete", url: created?.url || "https://www.vinted.pt/" };
    },
    async sendMessage(id, payload) {
      sent.push({ id, payload });
      if (payload.type === "read-vinted-uploaded-age") {
        return { ok: true, age: { seconds: 5 * 7 * 86400, text: "5 weeks ago" } };
      }
      return { ok: true };
    },
    async remove(id) { removed = id; },
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
  const age = await context.renderedUploadedAge(
    "https://www.vinted.pt/items/9826364597-destination-india-the-lonely-hearts-travel-club-2-katy-colins",
    "9826364597",
  );
  assert(age && age.text === "5 weeks ago", "Rendered Uploaded age was not returned");
  assert(age.seconds === 5 * 7 * 86400, "Rendered age seconds were wrong");
  assert(created && created.active === false, "Item page was not opened in an inactive tab");
  assert(String(created.url).includes("/items/9826364597"), "Wrong item page was opened");
  assert(sent.some((entry) => entry.payload.type === "read-vinted-uploaded-age"), "Rendered DOM was never queried");
  assert(removed === 101, "Temporary item tab was not closed");
  console.log("Vinted rendered-tab Uploaded-age collector: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
