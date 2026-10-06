const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "extension", "content.js"),
  "utf8",
);

const listeners = [];
const context = {
  console,
  URL,
  Date,
  setTimeout,
  clearTimeout,
  location: {
    origin: "https://www.vinted.pt",
    pathname: "/",
  },
  chrome: {
    runtime: {
      onMessage: {
        addListener(listener) {
          listeners.push(listener);
        },
      },
      async sendMessage() {
        return { ok: true };
      },
    },
    storage: {
      local: {
        async get() { return {}; },
        async set() {},
      },
    },
  },
  VintedAge: {
    advanceCached() { return null; },
    fromRenderedDocument() { return null; },
  },
  fetch: async () => ({
    ok: true,
    status: 200,
    async json() { return {}; },
  }),
};

vm.createContext(context);
const compiled = new vm.Script(source, { filename: "content.js" });
compiled.runInContext(context);
compiled.runInContext(context);

if (listeners.length !== 1) {
  throw new Error("content.js registered " + listeners.length + " listeners after two injections");
}
if (context.__RESELLER_DASHBOARD_VINTED_CONTENT_PROTOCOL__ !== 8) {
  throw new Error("content.js did not expose protocol guard 8");
}

console.log("Vinted content script reinjection: ok");
