"use strict";
/* Exercise the real content comparison renderer and field-selection handlers. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("app/product_static/app.js","utf8");
const begin = source.indexOf("function itemContentComparisonMarkup(");
const end = source.indexOf("async function openItemMarketplaces(itemId)", begin);
assert(begin >= 0 && end > begin);

const elements = {
  "#item-marketplaces-title": {textContent:""},
  "#item-marketplaces-content": {innerHTML:""},
};
const readBtn = {onclick:null,disabled:false};
const updateBtn = {onclick:null,disabled:true,textContent:""};
const boxes = ["title","description"].map(value=>({
  value,checked:false,onchange:null,
}));
const toolPanel = {
  querySelectorAll: selector=>selector===".item-content-select" ? boxes : [],
  querySelector: selector=>selector===".item-content-update" ? updateBtn : null,
};
const requests=[];
const notices=[];
const confirmations=[];
const esc = value=>String(value??"").replace(/[&<>"']/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const context = {
  $: selector=>elements[selector] || {onclick:null},
  $$: selector=>selector===".item-content-check" ? [readBtn]
    : selector===".item-content-tools" ? [toolPanel] : [],
  state:{inventoryItems:[]},
  esc,
  money:(n,currency)=>currency+" "+(Number(n)/100).toFixed(2),
  when:value=>value,
  itemMarketplaceStatusLabel:()=> "Unverified",
  itemMarketplaceOperationText:()=> "No operations",
  openCrossList:()=>{},openItemForm:()=>{},openItemMarketplaces:async()=>{},
  window:{confirm:msg=>{confirmations.push(msg);return true;}},
  flash:msg=>notices.push(msg),
  api:async(url,opts)=>{requests.push({url,opts});return {ok:true};},
};
vm.createContext(context);
vm.runInContext(source.slice(begin,end), context);
const recent = new Date().toISOString();
const fields = [
  {key:"title",label:"Title",master:"Master title",marketplace:"Old title",
   status:"differs",writable:true},
  {key:"description",label:"Description",master:"Master description",
   marketplace:"Old description",status:"differs",writable:true},
  {key:"price",label:"Asking price",master:"1100",marketplace:"900",
   status:"differs",writable:false},
  {key:"isbn",label:"ISBN",master:"9780000000001",marketplace:"9780000000001",
   status:"match",writable:false},
];
context.renderItemMarketplacePanel({
  item:{id:"item-42",title:"Master title",quantity:1,currency:"EUR",default_price_cents:1100},
  listings:[{listing_id:"woo-42",channel:"woocommerce",status:"active",
    external_id:"42",price_verification:"not_checked"}],
  operations:[],closure_actions:[],
  content_comparison:{
    item_id:"item-42",
    listings:[{
      listing_id:"woo-42",channel:"woocommerce",source:"previous_live_check",
      checked_at:recent,can_check_live:true,fields,
    }],
  },
});
const html = elements["#item-marketplaces-content"].innerHTML;
assert.match(html,/Compare listing details/);
assert.match(html,/Last live WooCommerce check/);
assert.match(html,/Inventory/);
assert.match(html,/WooCommerce at last check/);
assert.match(html,/Master description/);
assert.match(html,/Old description/);
assert.match(html,/EUR 11.00/);
assert.match(html,/EUR 9.00/);
assert.equal((html.match(/class="item-content-select"/g)||[]).length,2);
assert.ok(!html.includes("Update isbn"));
assert.equal(typeof readBtn.onclick,"function");
assert.equal(typeof updateBtn.onclick,"function");
assert.equal(updateBtn.disabled,true);

(async()=>{
  boxes[0].checked=true;
  boxes[0].onchange();
  assert.equal(updateBtn.disabled,false);
  await updateBtn.onclick();
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,
    "/api/app/inventory/item-42/marketplaces/woocommerce/content");
  assert.deepEqual(JSON.parse(requests[0].opts.body),{fields:["title"]});
  assert.equal(confirmations.length,1);
  assert.ok(confirmations[0].includes("Unselected fields"));
  await readBtn.onclick();
  assert.equal(requests.length,2);
  assert.equal(requests[1].url,
    "/api/app/inventory/item-42/marketplaces/woocommerce/check-content");
  assert.ok(notices.some(m=>m.includes("confirmed")));
  console.log("Listing content comparison, field selection and check-first controls: PASS");
})().catch(error=>{console.error(error);process.exitCode=1});
