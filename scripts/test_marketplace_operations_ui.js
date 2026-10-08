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
const context = {
  document: {
    querySelector(key) {
      if (key === "#marketplace-operations-list") return root;
      if (key === "#marketplace-operations-refresh") return refresh;
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
      created_at:"2026-10-08", result:{photos_uploaded:4}, error:null,
    },
    {
      id:"2", channel:"etsy", type:"sync", target:"all",
      status:"failed", verification:"not_checked", can_retry:true,
      created_at:"2026-10-08", result:{}, error:"timeout",
    },
    {
      id:"3", channel:"shopify", type:"publish", target:"ITEM-1",
      status:"attention", verification:"manual_required", can_retry:false,
      created_at:"2026-10-08", result:{}, error:"uncertain",
    },
  ],
});
assert.match(root.innerHTML, /Sent; check the marketplace/);
assert.match(root.innerHTML, /Check the result on the marketplace first/);
assert.match(root.innerHTML, /data-id="2"/);
assert.doesNotMatch(root.innerHTML, /data-id="1"/);
assert.doesNotMatch(root.innerHTML, /data-id="3"/);
assert.match(root.innerHTML, /Needs your attention/);

context.renderMarketplaceOperations({operations:[]});
assert.match(root.innerHTML, /No audited marketplace operations yet/);
console.log("Marketplace operation activity UI smoke test passed");
