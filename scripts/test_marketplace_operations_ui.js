"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const app = fs.readFileSync("app/product_static/app.js", "utf8");
const start = app.indexOf("function renderMarketplaceOperations(");
const end = app.indexOf("async function connections()", start);
assert(start !== -1 && end > start);
const root = {innerHTML: ""};
const refresh = {disabled: false, onclick: null};
const filter = {value:"all",onchange:null};
const operationsButtons = [];

const context = {
  state: {marketplaceActivityFilter:"all"},
  document: {
    querySelector(key) {
      if (key === "#marketplace-operations-list") return root;
      if (key === "#marketplace-operations-refresh") return refresh;
      if (key === "#marketplace-activity-filter") return filter;
      return null;
    },
    querySelectorAll() { return []; },
  },
  esc: value => String(value ?? ""),
  when: value => String(value ?? ""),
  api: async () => ({operations: []}),
  flash() {},
  window: {confirm() { return false; }},
  Number, Object, String,
};
context.$ = key => context.document.querySelector(key);
context.$$ = key => Array.from(context.document.querySelectorAll(key));
vm.createContext(context);
vm.runInContext(app.slice(start, end), context);

assert.equal(typeof context.renderMarketplaceOperations, "function");
assert.equal(typeof refresh.onclick, "function");

context.renderMarketplaceOperations({
  operations: [
    {
      id:"1", channel:"biblio", type:"photos", target:"BOOK-1",
      status:"needs_verification", verification:"manual_required", can_retry:false,
      next_step:{kind:"biblio_photos",label:"Inspect book photos", detail:"Check file receipts first."},
      created_at:"2026-10-08", result:{photos_uploaded:4}, error:null,
    },
    {
      id:"2", channel:"etsy", type:"sync", target:"all",
      status:"failed", verification:"not_checked", can_retry:true,
      next_step:{kind:"retry_import",label:"Retry data import",detail:"Read-only import."},
      created_at:"2026-10-08", result:{}, error:"timeout",
    },
    {
      id:"3", channel:"shopify", type:"publish", target:"ITEM-1",
      status:"attention", verification:"manual_required", can_retry:false,
      next_step:{kind:"manual_review",label:"Check marketplace result",detail:"Inspect the remote listing."},
      created_at:"2026-10-08", result:{}, error:"uncertain",
    },
  ],
});
assert.match(root.innerHTML, /Sent; not verified on marketplace/);
assert.match(root.innerHTML, /marketplace-activity-row/);
assert.match(root.innerHTML, /Inspect book photos/);
assert.match(root.innerHTML, /Check file receipts first/);
assert.match(root.innerHTML, /Retry read-only import/);
assert.match(root.innerHTML, /Technical transfer details/);
assert.doesNotMatch(root.innerHTML, /Try sending photos again/);
assert.match(root.innerHTML, /marketplace-activity-filter/);
assert.match(root.innerHTML, /Inspect the remote listing/);
assert.match(root.innerHTML, /data-id="2"/);
assert.doesNotMatch(root.innerHTML, /data-id="1"/);
assert.doesNotMatch(root.innerHTML, /data-id="3"/);
assert.match(root.innerHTML, /Needs review/);
assert.equal(typeof filter.onchange, "function");
filter.value="review";
filter.onchange();
assert.equal(context.state.marketplaceActivityFilter, "review");
assert.match(root.innerHTML, /Inspect book photos/);

context.renderMarketplaceOperations({operations:[]});
assert.match(root.innerHTML, /No marketplace activity yet/);
console.log("Marketplace operation activity UI smoke test passed");
