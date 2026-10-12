"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const app = fs.readFileSync("app/product_static/app.js", "utf8");
assert.match(app, /\$\$\("\.item-marketplace-recovery"\)\.forEach/,
  "Per-item recovery handlers must bind a collection, not one DOM element");
assert.match(app, /\$\$\("\.item-marketplace-retry"\)\.forEach/,
  "Per-item retry handlers must bind a collection, not one DOM element");
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
      id:"4", channel:"shopify", type:"update", target:"SKU-4:price",
      status:"attention",verification:"not_checked",can_retry:false,
      can_inspect_remote:true,
      next_step:{kind:"inspect_remote",label:"Check live marketplace result",
        detail:"Read the linked marketplace record without resending."},
      created_at:"2026-10-09",result:{},error:"Response timed out",
    },
    {
      id:"3", channel:"shopify", type:"publish", target:"ITEM-1",
      status:"attention", verification:"manual_required", can_retry:false,
      next_step:{kind:"manual_review",label:"Check marketplace result",detail:"Inspect the remote listing."},
      created_at:"2026-10-08", result:{}, error:"uncertain",
    },
  ],
});
assert.match(root.innerHTML, /Check needed/);
assert.match(root.innerHTML, /marketplace-activity-row/);
assert.match(root.innerHTML, /Inspect book photos/);
assert.match(root.innerHTML, /Photos were sent, but we cannot tell/);
assert.match(root.innerHTML, /Try import again/);
assert.match(root.innerHTML,/Check marketplace/);
assert.match(root.innerHTML,/marketplace-operation-inspect/);
assert.match(app,/\$\$\("\.item-marketplace-inspect"\)\.forEach/,
  "Per-item remote inspections must use collection binding");
assert.match(app,/\$\$\("\.marketplace-operation-inspect"\)\.forEach/,
  "Activity remote inspections must bind all action buttons");
assert.match(root.innerHTML, /<summary>Details<\/summary>/);
assert.doesNotMatch(root.innerHTML, /Try sending photos again/);
assert.match(root.innerHTML, /marketplace-activity-filter/);
assert.match(root.innerHTML, /Check the result on the marketplace/);
assert.match(root.innerHTML, /data-id="2"/);
assert.doesNotMatch(root.innerHTML, /data-id="1"/);
assert.doesNotMatch(root.innerHTML, /data-id="3"/);
assert.match(root.innerHTML, /Needs attention \(4\)/);
assert.match(root.innerHTML, /data-filter="review"/);
assert.match(root.innerHTML, /aria-pressed="true"/);
context.state.marketplaceActivityFilter = "all";
context.renderMarketplaceOperations({operations:[
  {id:"done",channel:"biblio",type:"sync",target:"all",
   status:"succeeded",verification:"no_remote_changes",result:{},created_at:"2026-10-10"},
]});
assert.match(root.innerHTML, /Nothing needed updating/);

context.renderMarketplaceOperations({operations:[]});
assert.match(root.innerHTML, /No activity recorded/);
console.log("Marketplace operation activity UI smoke test passed");
