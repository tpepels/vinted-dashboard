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
    async text() { return String(body||""); },
  };
}

async function scenario(mode) {
  const listeners = [];
  const storage = {};
  let detailRequests = 0;
  const detailIds=[];
  const diagnosticEvents=[];
  let optionalRequests = 0;
  let currentRequestOptions = null;

  const items = Array.from({ length: mode === "multiPage" ? 202 : 50 }, (_, index) => ({
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
        sendMessage(event) { diagnosticEvents.push(event); return Promise.resolve(); },
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
    fetch: async (input, options = {}) => {
      const url = new URL(String(input));
      const pathname = url.pathname;

      if (pathname === "/api/v2/users/current") {
        currentRequestOptions = options;
        if (mode === "core403") return response(403, {});
        return response(200, { user: { id: "42", login: "seller" } });
      }
      if (pathname === "/api/v2/users/42") {
        return response(200, { user: { id: "42", login: "seller" } });
      }
      if (pathname === "/api/v2/users/42/items") {
        throw new Error("Removed endpoint must never be requested");
      }
      if (pathname === "/api/v2/wardrobe/42/items") {
        if (mode === "core429") return response(429, {});
        if (mode === "core404") return response(404, {});
        if (mode === "coreMalformed") return response(200, {success:true, data:{}});
        if (mode === "zero") return response(200, {items:[]});
        if (mode === "incomplete") return response(200, {items:items.slice(0,20), pagination:{total_entries:50,total_pages:1}});
        const page=Number(url.searchParams.get("page")||1);
        const perPage=Number(url.searchParams.get("per_page")||96);
        return response(200, {
          items:items.slice((page-1)*perPage,page*perPage),
          pagination:{total_entries:items.length,total_pages:Math.ceil(items.length/perPage)}
        });
      }
      if (/^\/api\/v2\/items\/\d+$/.test(pathname)) {
        throw new Error("Removed item JSON endpoint must not be requested");
      }
      if (/^\/items\/\d+/.test(pathname)) {
        const id = pathname.match(/^\/items\/(\d+)/)?.[1];
        detailRequests += 1;
        detailIds.push(id);
        if (mode === "detail429" && detailRequests > 1) return response(429, {});
        if (mode === "page404") return response(404, "");
        if (mode === "unrecognizedPage") return response(200, "<html><body>No item data here</body></html>");
        if (mode === "detail403") return response(403, "");
        const item = {
          id, title:"Item " + id, description: "Description " + id,
          catalog_title: "Books",
          photos: [{ url: "https://images1.vinted.net/t/" + id + ".jpg" }],
        };
        // A Next.js hydration script is a JSON string containing item JSON.
        const hydration = '4:{"item":'+JSON.stringify(item)+'}';
        return response(200, "<html><head><script>self.__next_f.push([1,"+
          JSON.stringify(hydration)+"])</script></head><body></body></html>");
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

  function collect() {
    return new Promise((resolve) => listeners[0](
      { type: "collect-vinted-data", reason: "manual" },
      {},
      (result) => resolve(result),
    ));
  }
  const result=await collect();
  const firstIds=[...detailIds];
  const second=(mode==="page404"||mode==="unrecognizedPage")?await collect():null;
  return {result,second,firstIds,detailIds,detailRequests,
    optionalRequests,currentRequestOptions,diagnosticEvents};
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

(async () => {
  const unlimited = await scenario("normal");
  assert(unlimited.result.ok === true, "Normal API detail enrichment should succeed");
  assert(unlimited.detailRequests === 3,
    "Vinted detail enrichment must never walk the entire inventory in one sync");
  assert(unlimited.result.snapshot.detail_sync.enriched === 3,
    "Normal API detail enrichment did not drain the full queue");
  assert(unlimited.result.snapshot.detail_sync.pending === 47,
    "Unfinished enrichment must remain queued for future two-hour syncs");
  assert(unlimited.result.snapshot.detail_sync.api_rate_limited === false,
    "Normal detail enrichment was incorrectly marked rate limited");

  assert(unlimited.currentRequestOptions?.credentials === "include",
    "Vinted browser API request did not explicitly include the signed-in session");
  assert(unlimited.currentRequestOptions?.headers?.["X-Requested-With"] === "XMLHttpRequest",
    "Vinted browser API request did not use the normal web X-Requested-With header");
  assert(!("X-Platform" in (unlimited.currentRequestOptions?.headers || {})),
    "Vinted browser API request still sends the synthetic X-Platform header");

  assert(unlimited.result.snapshot.inventory_complete === true,
    "Success must explicitly confirm complete wardrobe pagination");
  const multipage = await scenario("multiPage");
  assert(multipage.result.ok === true && multipage.result.snapshot.listings.length === 202,
    "All wardrobe pages must be collected without duplicates");
  const incomplete = await scenario("incomplete");
  assert(incomplete.result.ok === false && String(incomplete.result.error).includes("reports 50"),
    "A truncated first page must never mark missing stock inactive");

  const detailLimited = await scenario("detail429");
  assert(detailLimited.result.ok === true, "Detail 429 should not fail core inventory sync");
  assert(detailLimited.result.snapshot.listings.length === 50,
    "Detail 429 caused a partial core inventory snapshot");
  assert(detailLimited.result.snapshot.detail_sync.rate_limited === true,
    "Detail rate limit was not reported");
  assert(detailLimited.result.snapshot.detail_sync.enriched === 1,
    "Unexpected number of detail enrichments before 429");
  assert(detailLimited.result.snapshot.detail_sync.pending === 49,
    "Pending API-detail count should reflect only records left after the real 429");
  assert(detailLimited.result.snapshot.detail_sync.detail_endpoint_rate_limited === true,
    "Detail endpoint 429 was not distinguished from page rate limiting");
  assert(detailLimited.optionalRequests === 0,
    "Optional notification/order requests continued after detail rate limiting");

  const gone = await scenario("page404");
  assert(gone.result.ok===true && gone.second.ok===true,
    "A removed item page must never abort the successfully collected wardrobe");
  assert(gone.result.snapshot.detail_sync.enriched===0
      && gone.result.snapshot.detail_sync.failed===3,
    "404 item pages must be recorded as failures, not enrichment successes");
  assert(gone.firstIds.join(",")==="1,2,3"
      && gone.detailIds.slice(3).join(",")==="4,5,6",
    "Failed items must cool down so subsequent syncs try different items");
  assert(gone.diagnosticEvents.filter(x=>x.stage==="details.item.failed")
    .some(x=>String(x.detail).includes("reason=http_404")),
    "404 responses must appear in diagnostic events");
  const unexpectedPage = await scenario("unrecognizedPage");
  assert(unexpectedPage.result.ok === true
      && unexpectedPage.result.snapshot.detail_sync.failed === 3,
    "Empty hydration responses must not be marked enriched");
  assert(unexpectedPage.detailIds.slice(3).join(",")==="4,5,6",
    "Unrecognized HTML pages must have a retry cooldown");
  const pageForbidden=await scenario("detail403");
  assert(pageForbidden.result.ok===true && pageForbidden.result.snapshot.detail_sync.access_blocked,
    "An item-page 403 must preserve inventory while stopping optional requests");
  assert(pageForbidden.detailRequests===1 && pageForbidden.optionalRequests===0,
    "A denied item page must not trigger more requests");

  const zero=await scenario("zero");
  assert(zero.result.ok===false,"Empty wardrobes must abort rather than deactivate stock");
  assert(String(zero.result.error).includes("No inventory was uploaded"),
    "An empty result must display the data-protection reason");

  const missingEndpoint=await scenario("core404");
  assert(missingEndpoint.result.ok===false);
  assert(String(missingEndpoint.result.error).includes("HTTP 404"),
    "Required inventory endpoint failure must not be mistaken for empty inventory");

  const malformed=await scenario("coreMalformed");
  assert(malformed.result.ok===false);
  assert(String(malformed.result.error).includes("unexpected inventory response"),
    "Unexpected response shapes must fail closed");

  const coreLimited = await scenario("core429");
  assert(coreLimited.result.ok === false, "Core inventory 429 should abort the sync");
  assert(String(coreLimited.result.error || "").includes("rate limited"),
    "Core 429 did not return a clear rate-limit error");

  const forbidden = await scenario("core403");
  assert(forbidden.result.ok === false, "Current-user 403 should abort the sync");
  assert(String(forbidden.result.error || "").includes("inventory API request (HTTP 403)"),
    "Current-user 403 must identify the rejected API request");
  assert(String(forbidden.result.error || "").includes("Reloading or opening more tabs will not resolve"),
    "A forbidden API response must not be presented as a browser login failure");
  assert(!String(forbidden.result.error || "").includes("rate limited"),
    "Current-user 403 was incorrectly reported as rate limiting");
  assert(forbidden.result.rate_limited === false,
    "HTTP 403 must not set a 24-hour rate-limit cooldown");

  console.log("Vinted rate-safe inventory sync: ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
