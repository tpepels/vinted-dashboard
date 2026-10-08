"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("app/product_static/app.js", "utf8");
const start = source.indexOf("function renderStoreStockAudit(data)");
const end = source.indexOf("async function loadStoreStockAudit()", start);
assert(start > 0 && end > start, "Stock audit renderer must be present");
const elements = {
  "#store-stock-audit-results": {innerHTML:""},
  "#store-stock-audit-progress": {textContent:""},
  "#store-stock-audit-start": {disabled:false},
};
const ctx = {
  $: selector => elements[selector],
  $$: () => [],
  esc: value => String(value ?? "").replace(/[&<>"']/g, char =>
    ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char])),
  when: value => value || "—",
  Number, Array,
};
vm.createContext(ctx);
vm.runInContext(source.slice(start, end), ctx);
ctx.renderStoreStockAudit({
  job: {status:"running", completed_at:null},
  eligible:3,limit:100,
  counts:{matched:1,mismatch:1,error:1},
  results:[
    {item_id:"abc",title:"<script>bad</script>",channel:"shopify",
     remote_quantity:4,local_quantity:1,state:"mismatch"},
    {item_id:"def",title:"Unreachable",channel:"wix",
     remote_quantity:null,local_quantity:0,state:"error",
     message:"Could not read remote stock"},
    {item_id:"ghi",title:"Matched",channel:"woocommerce",
     remote_quantity:2,local_quantity:2,state:"matched"},
  ],
});
assert.equal(elements["#store-stock-audit-start"].disabled,true,
  "Double scans must be disabled while active");
assert.match(elements["#store-stock-audit-progress"].textContent,/Checking 3 of 3/);
const html=elements["#store-stock-audit-results"].innerHTML;
assert.match(html,/1 different/);
assert.match(html,/Store 4 · Dashboard 1/);
assert.match(html,/Review item/);
assert.match(html,/1 matching listing/);
assert.doesNotMatch(html,/<script>/,"Marketplace text must be escaped");
assert.match(html,/&lt;script&gt;/);
ctx.renderStoreStockAudit({job:null,eligible:0,limit:100,counts:{},results:[]});
assert.equal(elements["#store-stock-audit-start"].disabled,true);
assert.match(elements["#store-stock-audit-results"].innerHTML,/No store stock check/);
console.log("Store stock audit UI regression: PASS");
