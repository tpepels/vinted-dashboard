"use strict";

/*
 * Repeatable, source-based UI evidence: same fixture passed into the last
 * pre-redesign renderer and this branch's renderer. We measure what a user
 * encounters before expanding a disclosure, not merely which buttons exist.
 * No calls to marketplaces or credentials are needed.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {execFileSync} = require("node:child_process");

const baselineSha = "874029bb0e9c81ad61af465ff3169ad32606c13a";
const oldSource = execFileSync(
  "git", ["show", baselineSha + ":app/product_static/app.js"],
  {encoding:"utf8", maxBuffer:10*1024*1024},
);
const newSource = fs.readFileSync("app/product_static/app.js", "utf8");
const now = new Date().toISOString();
const channels = ["woocommerce","shopify","wix"];
const item = {
  id:"proof-book-42",title:"Two copies of a novel",quantity:2,
  currency:"EUR",default_price_cents:1125,
};
function storeListing(channel) {
  return {
    channel,status:"active",
    external_id:channel==="shopify"?"gid://shopify/ProductVariant/42":"42",
    quantity:5,remote_stock_quantity:5,verification:"stock_mismatch",
    verified_at:now,attention:true,
    price_verification:"price_mismatch",remote_price_cents:850,
    price_verified_at:now,
    can_sync_woocommerce_stock:channel==="woocommerce",
    can_sync_shopify_stock:channel==="shopify",
    can_sync_wix_stock:channel==="wix",
    can_sync_woocommerce_price:channel==="woocommerce",
    can_sync_shopify_price:channel==="shopify",
    can_sync_wix_price:channel==="wix",
  };
}
const fixture={
  item,
  listings:channels.map(storeListing).concat([{
    channel:"biblio",status:"active",external_id:"BOOK-42",
    verification:"differs",attention:true,photo_count:3,
    photo_state:"ftp_uploaded",can_inspect_photos:true,
  }]),
  operations:[],closure_actions:[],
};

function render(source) {
  const begin = source.indexOf("function renderItemMarketplacePanel(data) {");
  const end = source.indexOf("async function openItemMarketplaces(itemId)", begin);
  assert(begin > 0 && end > begin, "Expected real production renderer");
  const elements={
    "#item-marketplaces-title": {textContent:""},
    "#item-marketplaces-content": {innerHTML:""},
  };
  const esc = value=>String(value??"").replace(/[&<>"']/g,
    char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const context={
    $: selector=>elements[selector]||{onclick:null},
    $$: ()=>[],
    state:{inventoryItems:[]},esc,
    money:(cents,currency)=>Number.isInteger(cents)
      ? (currency||"EUR")+" "+(cents/100).toFixed(2) : "not set",
    when: value=>value||"never",
    itemMarketplaceStatusLabel: listing=>listing.verification,
    itemMarketplaceOperationText: ()=>"No tracked operations",
    openCrossList:()=>{},openItemForm:()=>{},
    openItemMarketplaces:async()=>{},
    window:{confirm:()=>false},
    Date,Number,Array,Object,String,
  };
  vm.createContext(context);
  vm.runInContext(source.slice(begin,end), context);
  context.renderItemMarketplacePanel(fixture);
  return elements["#item-marketplaces-content"].innerHTML;
}

/* Track nested DETAILS visibility. A button is visible only when every
   enclosing details element is open. Unknown buttons remain counted so
   controls are not silently removed by the redesign. */
function measure(html) {
  const stack=[];
  let totalButtons=0,visibleButtons=0,visiblePrimary=0;
  let visibleParagraphs=0,summaryCount=0;
  const tag=/<\/?([a-z][\w-]*)\b([^>]*?)>/gi;
  for (let m; (m=tag.exec(html));) {
    const name=m[1].toLowerCase();
    const closing=m[0][1]==="/";
    if(name==="details" && !closing) {
      stack.push(/\sopen(?:\s|>|=)/i.test(m[0]));
      summaryCount++;
    } else if(name==="details" && closing) {
      stack.pop();
    } else if(!closing && stack.every(Boolean)){
      if(name==="button"){
        visibleButtons++;
        if(/class="[^"]*\bprimary\b/.test(m[0]))visiblePrimary++;
      }
      if(name==="p")visibleParagraphs++;
    }
    if(name==="button"&&!closing)totalButtons++;
  }
  return {visibleButtons,totalButtons,visiblePrimary,visibleParagraphs,disclosures:summaryCount};
}

const before=render(oldSource),after=render(newSource);
const beforeStats=measure(before),afterStats=measure(after);
assert(beforeStats.visibleButtons>afterStats.visibleButtons,
  "Initial screen must expose fewer competing buttons");
assert.equal(afterStats.totalButtons,beforeStats.totalButtons,
  "Existing marketplace actions must all remain available");
assert(after.includes("Dashboard 2 · Store 5"),
  "Stock discrepancy must show both actual quantities");
assert(after.includes("Dashboard EUR 11.25 · Store EUR 8.50"),
  "Price discrepancy must show both actual prices");
assert(after.includes("4 need review"),"Problems must be summarized prominently");
assert(after.includes('class="item-marketplace-manage"'),
  "Advanced controls must be keyboard-operable native details");
assert(after.includes('class="item-marketplace-metadata"'),
  "Technical listing references must be available on demand");
assert(after.includes('class="btn primary item-stock-update"'),
  "Safe stock updates must remain available");
assert(after.includes('class="btn primary item-price-update"'),
  "Verified price updates must remain available");
assert(after.includes("Compare with BIBLIO export"),
  "Remote verification must still be accessible");
assert(!after.includes("Store undefined") && !after.includes("Store null"),
  "No invented remote values");
const result={
  source:baselineSha,
  scenario:"3 stores with mismatched stock and prices plus 1 BIBLIO discrepancy",
  before:beforeStats,
  after:afterStats,
  change:{
    initialVisibleButtons:afterStats.visibleButtons-beforeStats.visibleButtons,
    retainedActions:afterStats.totalButtons===beforeStats.totalButtons,
    quantifiedStockComparisons:channels.length,
    quantifiedPriceComparisons:channels.length,
  },
};
const evidenceDir="ui-evidence";
fs.mkdirSync(evidenceDir,{recursive:true});
const baselineCSS=execFileSync(
  "git",["show",baselineSha+":app/product_static/styles.css"],
  {encoding:"utf8",maxBuffer:10*1024*1024},
);
const currentCSS=fs.readFileSync("app/product_static/styles.css","utf8");
function visualFixture(html, css, name) {
  return '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width,initial-scale=1">'
    + '<title>' + name + ' · Marketplace UI test fixture</title><style>'
    + css + '</style><style>'
    + '.ui-proof-frame{max-width:1160px;margin:0 auto;padding:16px}'
    + '.ui-proof-disclaimer{font-size:12px;padding:7px 0;color:#53625d}'
    + '</style></head><body><main class="ui-proof-frame">'
    + '<p class="ui-proof-disclaimer">Rendered test fixture using actual production UI code; not a live seller account.</p>'
    + '<section id="item-marketplaces-panel" class="card form">'
    + '<div class="card-head"><h2>Marketplace listings · Two copies of a novel</h2></div>'
    + '<div id="item-marketplaces-content">' + html + '</div></section>'
    + '<output id="viewport-metrics" hidden></output>'
    + '</main><script>'
    + 'window.addEventListener("load",()=>{'
    + 'const target=document.querySelector("#item-marketplaces-panel");'
    + 'const output=document.querySelector("#viewport-metrics");'
    + 'output.dataset.viewport=String(innerWidth);'
    + 'output.dataset.scroll=String(document.documentElement.scrollWidth);'
    + 'output.dataset.panel=String(Math.ceil(target.getBoundingClientRect().width));'
    + '});'
    + '<\\/script></body></html>';
}
fs.writeFileSync(evidenceDir+"/before.html",visualFixture(before,baselineCSS,"Before"));
fs.writeFileSync(evidenceDir+"/after.html",visualFixture(after,currentCSS,"After"));
fs.writeFileSync(evidenceDir+"/metrics.json",JSON.stringify(result,null,2)+"\\n");
console.log("UI_BEFORE_AFTER_EVIDENCE "+JSON.stringify(result));
console.log("Marketplace usability regression: PASS");
