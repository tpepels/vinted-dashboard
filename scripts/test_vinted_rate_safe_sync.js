const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "extension", "content.js"),
  "utf8",
);

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get() { return null; } },
    async json() { return body; },
  };
}

async function scenario(mode) {
  const listeners = [];
  const storage = {};
  let detailRequests = 0;
  let optionalRequests = 0;

  const items = Array.from({ length: 50 }, (_, index) => ({
    id: String(index + 1),
    title: "Item " + (index + 1),
    price: { amount: "5.00", currency_code: "EUR" },
    url: "https://www.vinted.pt/items/" + (index + 1) + "-item",
  }));

  const context = {
    console,
    URL,
    Date,
    location: {
      origin: "https://www.vinted.pt",
      pathname: "/",
    },
    setTimeout(fn) { fn(); return 0; },
    clearTimeout() {},
    chrome: {
      runtime: {
        onMessage: {
          addListener(listener) { listeners.push(listener); },
        },
      },
      storage: {
        local: {
          async get(keys) {
            const result = {};
            for (const key of keys || []) result[key] = storage[key];
            return result;
          },
          async set(values) { Object.assign(storage, values); },
        },
      },
    },
    VintedAge: {
      advanceCached() { return null; },
      fromRenderedDocument() { return null; },
    },
    fetch: async (input) => {
      const url = new URL(String(input));
      const pathname = url.pathname;

      if (pathname === "/api/v2/users/current") {
        return response(200, { user: { id: "42", login: "seller" } });
      }
      if (pathname === "/api/v2/users/42") {
        return response(200, { user: { id: "42", login: "seller" } });
      }
      if (pathname === "/api/v2/users/42/items") {
        const status = url.searchParams.get("status");
        if (mode === "core429" && status === "active") {
          return response(429, {});
        }
        return response(200, { items: status === "active" ? items : [] });
      }
      if (pathname === "/api/v2/wardrobe/42/items") {
        return response(200, { items: [] });
      }
      if (/^\/api\/v2\/items\/\d+$/.test(pathname)) {
        detailRequests += 1;
        if (mode === "detail429" && detailRequests > 3) {
          return response(429, {});
        }
        const id = pathname.split("/").pop();
        return response(200, {
          item: {
            id,
            description: "Description " + id,
            catalog_title: "Books",
            photos: [{ url: "https://images1.vinted.net/t/" + id + ".jpg" }],
          },
        });
      }
      if (pathname.includes("notifications") || pathname === "/api/v2/my_orders") {
        optionalRequests += 1;
        return response(200, { items: [] });
      }
      throw new Error("Unexpected request " + url.href);
    },
  };

  vm.createContext(context);
  vm.runInContext(source, context, { filename: "content.js" });
  if (listeners.length !== 1) throw new Error("content listener not installed");

  return await new Promise((resolve) => {
    listeners[0](
      { type: "collect-vinted-data", reason: "manual" },
      {},
      (result) => resolve({ result, detailRequests, optionalRequests }),
    );
  });
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

(async () => {
  const unlimited = await scenario("normal");
  assert(unlimited.result.ok === true, "Normal API detail enrichment should succeed");
  assert(unlimited.detailRequests === 50,
    "Rich-detail enrichment still has an artificial per-sync request budget");
  assert(unlimited.result.snapshot.detail_sync.enriched === 50,
    "Normal API detail enrichment did not drain the full queue");
  assert(unlimited.result.snapshot.detail_sync.pending === 0,
    "Normal API detail enrichment left records pending");
  assert(unlimited.result.snapshot.detail_sync.api_rate_limited === false,
    "Normal detail enrichment was incorrectly marked rate limited");

  const detailLimited = await scenario("detail429");
  assert(detailLimited.result.ok === true, "Detail 429 should not fail core inventory sync");
  assert(detailLimited.result.snapshot.listings.length === 50,
    "Detail 429 caused a partial core inventory snapshot");
  assert(detailLimited.result.snapshot.detail_sync.rate_limited === true,
    "Detail rate limit was not reported");
  assert(detailLimited.result.snapshot.detail_sync.enriched === 3,
    "Unexpected number of detail enrichments before 429");
  assert(detailLimited.result.snapshot.detail_sync.pending === 47,
    "Pending API-detail count should reflect only records left after the real 429");
  assert(detailLimited.result.snapshot.detail_sync.detail_endpoint_rate_limited === true,
    "Detail endpoint 429 was not distinguished from page rate limiting");
  assert(detailLimited.optionalRequests === 0,
    "Optional notification/order requests continued after detail rate limiting");

  const coreLimited = await scenario("core429");
  assert(coreLimited.result.ok === false, "Core inventory 429 should abort the sync");
  assert(String(coreLimited.result.error || "").includes("rate limited"),
    "Core 429 did not return a clear rate-limit error");

  console.log("Vinted rate-safe inventory sync: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
