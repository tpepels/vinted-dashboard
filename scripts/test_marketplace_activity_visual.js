"use strict";
/* Browser viewport proof for the REAL marketplace-activity renderer and CSS.
 * Representative fixtures, not an authenticated seller session.
 */
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const {execFileSync} = require("node:child_process");

const app = fs.readFileSync("app/product_static/app.js","utf8");
const styles = fs.readFileSync("app/product_static/styles.css","utf8");
const start = app.indexOf("function renderMarketplaceOperations(data) {");
const end = app.indexOf("async function refreshMarketplaceOperations()", start);
assert(start>=0 && end>start);
const root = {innerHTML:""};
const elements = {"#marketplace-operations-list":root};
const context = {
  state:{marketplaceActivityFilter:"all"},
  $:selector=>elements[selector] || null,
  $$:()=>[],
  esc:value=>String(value??"").replace(/[&<>"']/g,c=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;",
  })[c]),
  when:value=>String(value || ""),
  Date,Number,Object,String,
};
vm.createContext(context);
vm.runInContext(app.slice(start,end),context);
context.renderMarketplaceOperations({operations:[
  {
    id:"photo-repair", channel:"biblio", type:"photos", target:"listing-id",
    status:"attention", verification:"manual_required", can_retry:false,
    next_step:{kind:"biblio_photos",label:"Inspect book photos",
      detail:"Review individual file transfer receipts and the seller listing before resending."},
    result:{photos_uploaded:3,photos_total:5},error:"Two photos need review",
    created_at:"2026-10-09T12:00:00Z",
  },
  {
    id:"safe-import", channel:"shopify", type:"sync", target:"all",
    status:"failed",verification:"not_checked",can_retry:true,
    next_step:{kind:"retry_import",label:"Retry data import",
      detail:"Read-only import. No product edits, price changes or stock adjustments."},
    error:"Could not read order page",result:{},created_at:"2026-10-09T11:00:00Z",
  },
  {
    id:"uncertain-write", channel:"woocommerce",type:"publish",target:"PRODUCT-1",
    status:"needs_verification",verification:"manual_required",can_retry:false,
    next_step:{kind:"manual_review",label:"Check marketplace result",
      detail:"The outcome is not independently verified. Review the listing on WooCommerce first."},
    error:null,result:{external_id:"123456", status:"publish"},
    created_at:"2026-10-09T10:00:00Z",
  },
]});
assert.match(root.innerHTML,/Inspect book photos/);
assert.doesNotMatch(root.innerHTML,/Try sending photos again/);
const browser = process.env.CHROME_BIN || execFileSync(
  "bash",["-lc","command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser"],
  {encoding:"utf8"},
).trim();
fs.mkdirSync("ui-evidence",{recursive:true});
const results=[];
for (const width of [360,768,1280]) {
  const html = '<!doctype html><html lang="en"><head><meta charset="utf-8">'+
    '<meta name="viewport" content="width=device-width,initial-scale=1">'+
    '<style>'+styles+'</style></head><body style="margin:0;padding:0">'+
    '<main id="connections" style="width:100%;max-width:100%;padding:12px;box-sizing:border-box">'+
    '<div class="card connection-task-panel"><div class="connection-task-content">'+
    '<div id="marketplace-operations-list">'+root.innerHTML+'</div></div></div></main>'+
    '<script>const out=document.createElement("output");out.id="activity-metrics";'+
    'out.dataset.state=encodeURIComponent(JSON.stringify({viewport:innerWidth,'+
    'pageScroll:document.documentElement.scrollWidth,'+
    'cards:document.querySelectorAll(".marketplace-activity-row").length,'+
    'actions:document.querySelectorAll(".marketplace-activity-actions button").length,'+
    'panel:Math.round(document.querySelector("#marketplace-operations-list").getBoundingClientRect().width)}));'+
    'document.body.appendChild(out);</script></body></html>';
  const filepath=path.resolve("ui-evidence/marketplace-activity-"+width+".html");
  const screenshot=path.resolve("ui-evidence/marketplace-activity-"+width+".png");
  fs.writeFileSync(filepath,html);
  const flags=["--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage",
    "--window-size="+width+",1100","--virtual-time-budget=1000"];
  const dom=execFileSync(browser,[...flags,"--dump-dom","file://"+filepath],
    {encoding:"utf8",maxBuffer:5*1024*1024,timeout:30000});
  const match=dom.match(/<output id="activity-metrics" data-state="([^"]+)"/);
  assert(match,"Browser metrics did not render at "+width);
  const metrics=JSON.parse(decodeURIComponent(match[1]));
  assert(metrics.cards===3 && metrics.actions===2,
    "Contextual BIBLIO and import buttons must render");
  assert(metrics.pageScroll<=metrics.viewport+2,
    "Horizontal overflow at "+width+"px: "+JSON.stringify(metrics));
  assert(metrics.panel>0 && metrics.panel<=metrics.viewport,
    "Activity list does not fit "+width+"px");
  execFileSync(browser,[...flags,"--screenshot="+screenshot,"file://"+filepath],
    {encoding:"utf8",timeout:30000});
  assert(fs.statSync(screenshot).size>4000);
  results.push({requestedWidth:width,...metrics,screenshot:path.relative(".",screenshot)});
}
console.log("MARKETPLACE_ACTIVITY_VIEWPORTS "+JSON.stringify(results));
console.log("Market activity responsive cards in Chromium: PASS");
