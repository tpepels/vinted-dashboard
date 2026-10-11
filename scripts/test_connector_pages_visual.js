"use strict";
/* Real browser screenshot and navigation proof using the app's actual HTML,
 * CSS and navigation/connection renderers. Stub account data, no writes.
 */
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {execFileSync}=require("node:child_process");
const js=fs.readFileSync("app/product_static/app.js","utf8");
const html=fs.readFileSync("app/product_static/index.html","utf8");
const css=fs.readFileSync("app/product_static/styles.css","utf8");
const slice=(begin,end)=>{
  const a=js.indexOf(begin),b=js.indexOf(end,a);
  assert(a>=0 && b>a,begin+" source missing");
  return js.slice(a,b);
};
const schema=slice("const connectorSchemas = {","function esc(value)");
const nav=slice("const connectorPageChannels = [","async function today()");
const connections=slice("async function connections() {","function renderEtsyOAuthTools(");
const config=slice("function openConnectorConfig(channel, connector) {",
  '$("#close-connector-config").onclick');
assert.match(nav,/\$\$\("\.nav"\)\.forEach/);
assert.match(nav,/\$\$\("\.view"\)\.forEach/);
assert.match(connections,/\$\$\("\.configure"\)\.forEach/);
const channels=["vinted","biblio","ebay","etsy","woocommerce","shopify",
  "bigcommerce","squarespace","wix","depop"];
for(const channel of channels)
  assert(html.includes('data-view="connector-'+channel+'"'),channel+" missing sidebar link");
const names={vinted:"Vinted",biblio:"BIBLIO",ebay:"eBay",etsy:"Etsy",
  woocommerce:"WooCommerce",shopify:"Shopify",bigcommerce:"BigCommerce",
  squarespace:"Squarespace",wix:"Wix",depop:"Depop"};
const fixtures=channels.map(channel=>({
  channel,display_name:names[channel],description:"Marketplace "+names[channel],
  configured:["vinted","biblio","shopify"].includes(channel),
  operational:["vinted","biblio","shopify"].includes(channel),
  sync_available:channel!=="vinted",
  last_synced_at:channel==="vinted"?"2026-10-11T00:00:00Z":null,
}));
const browser=process.env.CHROME_BIN || execFileSync(
  "bash",["-lc","command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser"],
  {encoding:"utf8"}).trim();
const base=html.replace('<link rel="stylesheet" href="/app-static/styles.css">',"")
  .replace('<script src="/app-static/app.js"></script>',"")
  .replace("</head>","<style>"+css+"</style></head>");
const mocks=[
  'const state={me:{},view:"today",connectorChannel:null,connectors:[],biblioActivityTimer:null,',
  'biblioPhotoExpanded:false,biblioActivityExpanded:false,biblioRecoveryExpanded:false,',
  'marketplaceSelected:"biblio",storeStockAuditTimer:null,barcodeStream:null,diagnosticsTimer:null};',
  'const $=selector=>document.querySelector(selector);',
  'const $$=selector=>Array.from(document.querySelectorAll(selector));',
  'const esc=value=>String(value??"").replace(/[&<>"]/g,char=>',
  '({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[char]||char));',
  'function when(value){return String(value||"unknown");}',
  'function flash(){} function stopBarcodeCamera(){}',
  'async function today(){} async function inventory(){} async function reconcile(){}',
  'async function listings(){} async function sales(){} async function analytics(){}',
  'async function imports(){} async function settings(){}',
  'function renderMarketplaceDevelopment(){}',
  'async function refreshMarketplaceOperations(){',
  '$("#marketplace-operations-list").textContent="Activity available";',
  '$("#connector-operations-list").textContent="Channel activity available";}',
  'function renderBiblioActivity(){return "<details class=\\\"biblio-task-panel biblio-photos-panel\\\"><summary>Fix photos for one book</summary></details>";}',
  'function connectorHealth(row){return {state:row.configured?"verified":"setup",',
  'label:row.configured?"Read access checked":"Not set up",',
  'message:row.configured?"Credentials saved":"Connect first",',
  'next:row.configured?"Review listings":"Set up connection"};}',
  'function renderEtsyOAuthTools(){} async function loadBiblioPhotoChoices(){}',
  'const fixtureConnectors='+JSON.stringify(fixtures)+';',
  'async function api(url){',
  'if(url==="/api/app/connectors" && window.holdConnectorReads)',
  ' await new Promise(resolve=>{window.finishHeldConnectorRead=resolve});',
  'if(url==="/api/app/connectors")return {connectors:fixtureConnectors};',
  'if(url==="/api/app/extension/devices")return {devices:[],latest_version:"2.4",download_url:"/bridge.zip"};',
  'if(url==="/api/app/connectors/biblio/activity")return {',
  'health:{inventory_changes_pending:2,deletes_pending:1},current:{status:"success"},runs:[]};',
  'if(url==="/api/app/connectors/development")return null;',
  'return {operations:[]};}',
].join("\n");
function runBrowserAssertions(){
  (async()=>{
    document.querySelector("#auth").classList.add("hidden");
    document.querySelector("#shell").classList.remove("hidden");
    for(const channel of ["vinted","biblio","ebay","etsy","woocommerce",
                          "shopify","bigcommerce","squarespace","wix","depop"]){
      await selectView("connector-"+channel);
      const card=document.querySelector("#connector-page-card .connector");
      if(!card||card.dataset.connectorChannel!==channel)
        throw Error("No dedicated page for "+channel);
      if(routeFromHash()!=="connector-"+channel)throw Error("Bad URL route "+channel);
      if(!document.querySelector("#connector-detail").classList.contains("active"))
        throw Error("Page is not visible "+channel);
      if(document.querySelector("#connector-page-biblio").classList.contains("hidden")
          !==(channel!=="biblio"))
        throw Error("BIBLIO tools leaked into "+channel);
      if(document.querySelector("#connector-page-vinted").classList.contains("hidden")
          !==(channel!=="vinted"))
        throw Error("Vinted browser controls leaked into "+channel);
    }
    await selectView("connector-biblio");
    const byId=id=>document.getElementById(id);
    if(!byId("connector-page-biblio").contains(byId("biblio-compare-panel")))
      throw Error("BIBLIO seller-file verification missing from its page");
    byId("connector-page-card").querySelector(".configure").click();
    if(byId("connector-config").classList.contains("hidden")
        ||!byId("connector-page-settings").contains(byId("connector-config")))
      throw Error("BIBLIO credential form did not open in its page");
    await selectView("connector-shopify");
    if(!byId("connector-config").classList.contains("hidden"))
      throw Error("Old connector settings leaked into Shopify");
    if(!byId("connector-page-card").querySelector(".sync[data-c=shopify]"))
      throw Error("Shopify importer absent");
    await selectView("connector-vinted");
    if(!byId("connector-page-vinted").contains(byId("paired-browsers-panel"))||
       !byId("connector-page-card").querySelector(".pair"))
      throw Error("Vinted browser controls absent");
    await selectView("connections");
    if(document.querySelectorAll(".connector-overview-link").length!==10)
      throw Error("All marketplace overview links must be present");
    document.querySelector('.connector-overview-link[data-c="etsy"]').click();
    await new Promise(resolve=>setTimeout(resolve,50));
    if(state.view!=="connector-etsy")throw Error("Overview link did not navigate");
    // Exercise deep-link routing and history restoration using the same
    // popstate event emitted by Back/Forward; neither reloads the page.
    await selectView("connector-shopify");
    history.replaceState({}, "", location.pathname + "#/connectors/biblio");
    dispatchEvent(new PopStateEvent("popstate"));
    await new Promise(resolve=>setTimeout(resolve,50));
    if(state.view!=="connector-biblio" ||
       !byId("connector-detail").classList.contains("active"))
      throw Error("Back/Forward did not restore the BIBLIO page");
    history.replaceState({}, "", location.pathname + "#/connectors/not-a-service");
    dispatchEvent(new PopStateEvent("popstate"));
    await new Promise(resolve=>setTimeout(resolve,50));
    if(state.view!=="connections")
      throw Error("Unknown marketplace route was not handled safely");
    await selectView("connector-biblio");

    // A late Shopify API response must not update BIBLIO or another page.
    const headingBefore=byId("connector-page-heading").textContent;
    window.holdConnectorReads=true;
    const pending=selectView("connector-shopify");
    await selectView("settings");
    if(typeof window.finishHeldConnectorRead!=="function")
      throw Error("Delayed connector API fixture did not start");
    window.finishHeldConnectorRead();
    await pending;
    window.holdConnectorReads=false;
    if(byId("connector-page-heading").textContent!==headingBefore ||
       state.view!=="settings" || byId("page-title").textContent!=="Settings")
      throw Error("Stale connector response modified the page after navigation");
    await selectView("connector-biblio");

    // The ordinary BIBLIO tasks must remain readable without tiny or
    // overflowing cards, including at the narrow screenshot widths.
    const taskCards=[...document.querySelectorAll("#connector-page-card .connector-task-card")];
    if(taskCards.length!==3)throw Error("BIBLIO task shortcuts missing");
    for(const card of taskCards){
      const label=card.querySelector("strong");
      if(!label || parseFloat(getComputedStyle(label).fontSize)<15)
        throw Error("BIBLIO task heading is too small");
      if(card.scrollWidth>card.clientWidth+3)
        throw Error("BIBLIO task text is clipped");
    }
    const photos=document.querySelector(".biblio-photos-panel");
    photos.open=true;
    await connections();
    if(!document.querySelector(".biblio-photos-panel")?.open)
      throw Error("Open BIBLIO photo repair collapsed during refresh");

    const toggle=byId("sidebar-toggle");
    const before=toggle.getAttribute("aria-expanded");
    toggle.click();
    if(toggle.getAttribute("aria-expanded")===before)
      throw Error("Sidebar accessibility state failed to update");
    toggle.click();
    setSidebarCollapsed(window.fixtureMode==="collapsed",false);
    const aside=byId("app-sidebar"), nav=byId("workspace-navigation");
    const result={
      viewport:window.innerWidth,bodyScroll:document.documentElement.scrollWidth,
      sidebarWidth:Math.round(aside.getBoundingClientRect().width),
      asideClient:aside.clientWidth,asideScroll:aside.scrollWidth,
      navigationVisible:getComputedStyle(nav).display!=="none",
      collapsed:byId("shell").classList.contains("sidebar-collapsed"),
      ariaExpanded:toggle.getAttribute("aria-expanded"),
      view:state.view,route:routeFromHash(),
      formCopies:document.querySelectorAll("#connector-config").length,
      biblioPanelMoved:byId("connector-page-biblio").contains(byId("biblio-compare-panel")),
      vintedPanelMoved:byId("connector-page-vinted").contains(byId("paired-browsers-panel")),
      navCount:document.querySelectorAll(".nav").length
    };
    if(result.asideScroll>result.asideClient+2)throw Error("Sidebar overflow");
    if(result.collapsed!==(window.fixtureMode==="collapsed"))
      throw Error("Unexpected sidebar mode");
    if(window.fixtureMode==="expanded"&&!result.navigationVisible)
      throw Error("Expanded navigation hidden");
    const output=document.createElement("output");
    output.id="connector-route-metrics";
    output.dataset.state=encodeURIComponent(JSON.stringify(result));
    document.body.appendChild(output);
  })().catch(err=>{
    const output=document.createElement("output");
    output.id="connector-route-error";
    output.textContent=String(err.stack||err);
    document.body.appendChild(output);
  });
}
// Fail fast on malformed test injections instead of reporting a vague browser timeout.
new Function(mocks+"\n"+schema+"\n"+nav+"\n"+connections+"\n"+config
  +"\n("+runBrowserAssertions.toString()+")();");
fs.mkdirSync("ui-evidence",{recursive:true});
const results=[];
for(const width of [360,768,1280])for(const mode of ["expanded","collapsed"]){
  const filepath=path.resolve("ui-evidence/connector-page-"+width+"-"+mode+".html");
  const png=path.resolve("ui-evidence/connector-page-"+width+"-"+mode+".png");
  const init='window.fixtureMode='+JSON.stringify(mode)+';';
  const browserErrorReporter = '<script>window.onerror=function(message,source,line,col){'
    + 'var out=document.createElement("output");out.id="connector-route-error";'
    + 'out.textContent=String(message)+" at "+line+":"+col;document.body.appendChild(out);'
    + '};</script>';
  // Keep fixture bindings lexical to this test, not the browser global scope.
  // This also prevents collisions with browser-created globals in headless mode.
  const fixtureSource = "(function connectorFixture(){\n"+mocks+"\n"+schema
    +"\n"+nav+"\n"+connections+"\n"+config
    +"\n"+init+"\n("+runBrowserAssertions.toString()+")();\n})();";
  // Preserve literal multi-element '$$' DOM selectors in injected JavaScript.
  // A string replacement interprets dollar signs as replacement patterns.
  fs.writeFileSync(filepath,base.replace("</body>",() => browserErrorReporter+
    "<script>"+fixtureSource+"</script></body>"));
  const flags=["--headless=new","--no-sandbox","--disable-gpu",
    "--disable-dev-shm-usage","--virtual-time-budget=1600",
    "--window-size="+width+",1100"];
  const dom=execFileSync(browser,[...flags,"--dump-dom","file://"+filepath],
    {encoding:"utf8",maxBuffer:16*1024*1024,timeout:30000});
  const failure=dom.match(/<output id="connector-route-error">([^<]+)<\/output>/);
  assert(!failure,"Browser failed "+width+"/"+mode+": "+(failure?.[1]||""));
  const observed=dom.match(/<output id="connector-route-metrics" data-state="([^"]+)"/);
  if(!observed) console.error("Rendered page tail: "+dom.slice(-3500));
  assert(observed,"Browser navigation script did not finish "+width+"/"+mode);
  const measurement=JSON.parse(decodeURIComponent(observed[1]));
  assert(measurement.bodyScroll<=measurement.viewport+2,
    "Horizontal page overflow at "+width+"/"+mode+": "+JSON.stringify(measurement));
  assert(measurement.formCopies===1 && measurement.biblioPanelMoved &&
    measurement.vintedPanelMoved,"Connector controls were lost or duplicated");
  assert(measurement.navCount===19 && measurement.view==="connector-biblio");
  execFileSync(browser,[...flags,"--screenshot="+png,"file://"+filepath],
    {encoding:"utf8",timeout:30000});
  assert(fs.statSync(png).size>4000);
  results.push({requestedWidth:width,mode,...measurement,screenshot:path.relative(".",png)});
}
fs.writeFileSync("ui-evidence/connector-page-viewport-results.json",JSON.stringify(results,null,2)+"\n");
console.log("CONNECTOR_PAGE_LAYOUT "+JSON.stringify(results));
console.log("Ten dedicated connector pages, sidebar toggle and responsive layout: PASS");
