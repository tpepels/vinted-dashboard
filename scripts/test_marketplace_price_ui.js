"use strict";
/* Exercise both price workflows without a browser, including multi-row binding. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const js = fs.readFileSync("app/product_static/app.js", "utf8");
const begin = js.indexOf("function renderItemMarketplacePanel(data) {");
const end = js.indexOf("async function openItemMarketplaces(itemId)", begin);
assert(begin > 0 && end > begin, "Item marketplace renderer must exist");
const panel = {"#item-marketplaces-title":{textContent:""},
               "#item-marketplaces-content":{innerHTML:""}};
const channels = ["woocommerce","shopify"];
const priceChecks = channels.map(channel=>({dataset:{channel},disabled:false,onclick:null}));
const priceUpdates = channels.map(channel=>({dataset:{channel},disabled:false,onclick:null}));
const readCalls=[];
const messages=[];
let approvals=0;
const context = {
  $: selector => panel[selector] || {onclick:null, classList:{add(){},remove(){}}},
  $$: selector => selector === ".item-price-check" ? priceChecks
    : selector === ".item-price-update" ? priceUpdates : [],
  state: {inventoryItems:[]},
  esc: v=>String(v??"").replace(/[&<>"']/g,
    char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char])),
  money:(cents,currency)=>String(currency || "EUR")+" "+(cents/100).toFixed(2),
  when: value=>value || "never",
  itemMarketplaceStatusLabel:()=> "Linked",
  itemMarketplaceOperationText:()=> "No operations",
  openCrossList:()=>{},
  openItemForm:()=>{},
  openItemMarketplaces:async()=>{},
  window:{confirm:()=>{approvals++;return true;}},
  flash:msg=>messages.push(msg),
  api:async(url,opts)=> {
    readCalls.push({url,opts});
    return url.endsWith("check-price")
      ? {matches:false,remote_price_cents:850,local_price_cents:1125}
      : {remote_verified:true,price_cents:1125};
  },
};
vm.createContext(context);
vm.runInContext(js.slice(begin,end),context);

const recent = new Date().toISOString();
const listing = (channel)=>({
  channel,status:"active", external_id:channel==="shopify"
    ? "gid://shopify/ProductVariant/42":"42",
  price_verified_at:recent, remote_price_cents:850,
  price_verification:"price_mismatch",
  can_sync_woocommerce_price:channel==="woocommerce",
  can_sync_shopify_price:channel==="shopify",
});
context.renderItemMarketplacePanel({
  item:{id:"item-42",title:"A book",quantity:1,currency:"EUR",
        default_price_cents:1125},
  listings:channels.map(listing), operations:[],closure_actions:[],
});
const html=panel["#item-marketplaces-content"].innerHTML;
assert.equal((html.match(/class="item-price-tools"/g)||[]).length,2);
assert.equal((html.match(/class="btn item-price-check"/g)||[]).length,2);
assert.equal((html.match(/class="btn primary item-price-update"/g)||[]).length,2);
assert.match(html,/Base price/);
assert.match(html,/Regular price/);
assert.ok(html.includes('data-channel="shopify"'));
assert.ok(html.includes('data-channel="woocommerce"'));
assert.ok(priceChecks.every(b=>typeof b.onclick==="function"));
assert.ok(priceUpdates.every(b=>typeof b.onclick==="function"));

(async()=>{
  await priceChecks[0].onclick();
  await priceChecks[1].onclick();
  await priceUpdates[0].onclick();
  await priceUpdates[1].onclick();
  assert.deepEqual(readCalls.map(x=>x.url),[
    "/api/app/inventory/item-42/marketplaces/woocommerce/check-price",
    "/api/app/inventory/item-42/marketplaces/shopify/check-price",
    "/api/app/inventory/item-42/marketplaces/woocommerce/price",
    "/api/app/inventory/item-42/marketplaces/shopify/price",
  ]);
  assert.ok(readCalls.every(x=>x.opts.method==="POST"));
  assert.equal(approvals,2);
  assert.ok(messages.some(x=>x.includes("Shopify base price")));
  assert.ok(messages.some(x=>x.includes("WooCommerce regular price")));
  console.log("Multi-store price controls and confirmation interactions: PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
