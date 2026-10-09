"use strict";
/* Exercise real sold-out WooCommerce controls, including confirmation and narrow viewports. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("app/product_static/app.js", "utf8");
const start = source.indexOf("function itemContentComparisonMarkup(");
const end = source.indexOf("async function openItemMarketplaces(itemId)", start);
assert(start >= 0 && end > start);
const elements = {
  "#item-marketplaces-title": {textContent:""},
  "#item-marketplaces-content": {innerHTML:""},
};
const check = {onclick:null,disabled:false};
const unpublish = {onclick:null,disabled:false};
const calls = [], confirmations = [], notifications = [];
const context = {
  $: selector=>elements[selector] || {onclick:null},
  $$: selector=> selector===".item-close-check" ? [check]
    : selector===".item-close-unpublish" ? [unpublish] : [],
  state:{inventoryItems:[]},
  esc:value=>String(value??"").replace(/[&<>"']/g,
    c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])),
  money:(n,c)=>c+" "+(Number(n)/100).toFixed(2),
  when:v=>v,
  itemMarketplaceStatusLabel:()=> "Unverified",
  itemMarketplaceOperationText:()=> "No operations",
  openCrossList:()=>{},openItemForm:()=>{},openItemMarketplaces:async()=>{},
  window:{confirm:text=>{confirmations.push(text);return true;}},
  flash:text=>notifications.push(text),
  api:async(url,opts)=>{calls.push({url,opts});return {
    ok:true,verified_closed:false,can_unpublish:true,status:"publish",
  };},
};
vm.createContext(context);
vm.runInContext(source.slice(start,end), context);
function render(qty, checked) {
  context.renderItemMarketplacePanel({
    item:{id:"i-1",title:"Sold book",quantity:qty,currency:"EUR",default_price_cents:1200},
    listings:[{
      channel:"woocommerce",listing_id:"woo-1",external_id:"12",status:"active",
      can_check_woocommerce_close:qty===0,close_remote_status:checked?"publish":null,
      close_checked_at:checked?new Date().toISOString():null,
    }],
    operations:[],closure_actions:[{channel:"woocommerce",status:"attention",type:"close_listing"}],
  });
  return elements["#item-marketplaces-content"].innerHTML;
}
const pending = render(0, false);
assert.match(pending,/After a sale/);
assert.match(pending,/Check publication status/);
assert.doesNotMatch(pending,/Unpublish this product/);
const verified = render(0, true);
assert.match(verified,/Unpublish this product/);
assert.doesNotMatch(render(1, true),/Unpublish this product/);
render(0, true);
assert.equal(typeof check.onclick, "function");
assert.equal(typeof unpublish.onclick, "function");
(async()=>{
  await check.onclick();
  assert.equal(calls[0].url,"/api/app/inventory/i-1/marketplaces/woocommerce/check-close");
  await unpublish.onclick();
  assert.equal(calls[1].url,"/api/app/inventory/i-1/marketplaces/woocommerce/close");
  assert.equal(confirmations.length,1);
  assert.match(confirmations[0],/draft/);
  assert.ok(notifications.some(text=>text.includes("draft")));
  console.log("WooCommerce closure UI, read-first, confirmation and sold-out guard: PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
