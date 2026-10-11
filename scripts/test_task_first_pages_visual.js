"use strict";
/* Test the actual static page and production task controllers at several
   widths, without app credentials or marketplace requests. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {execFileSync} = require("node:child_process");
const html = fs.readFileSync("app/product_static/index.html","utf8");
const css = fs.readFileSync("app/product_static/styles.css","utf8");
const js = fs.readFileSync("app/product_static/app.js","utf8");
const start = js.indexOf("const workspaceTabState = {};");
const end = js.indexOf("const commonPageViews = new Set(",start);
assert(start>0 && end>start,"Production task-controller code missing");
const controller = js.slice(start,end);
const chrome = process.env.CHROME_BIN || execFileSync(
  "bash",["-lc","command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser"],
  {encoding:"utf8"}).trim();
const fixture=[
  'const $=selector=>document.querySelector(selector);',
  'const $$=selector=>Array.from(document.querySelectorAll(selector));',
  '// Production moves these single-instance panels to the connector page.',
  '$("#connector-page-settings").appendChild($("#connector-config"));',
  '$("#connector-page-biblio").appendChild($("#biblio-compare-panel"));',
  '$("#connector-page-vinted").appendChild($("#paired-browsers-panel"));'
].join("\n");
const assertions=[
  '(function(){try {',
  '$("#auth").classList.add("hidden");$("#shell").classList.remove("hidden");',
  'document.querySelectorAll(".view.active").forEach(el=>el.classList.remove("active"));',
  'const groups={today:["tasks","overview"],inventory:["items","checks"],',
  ' reconcile:["suggestions","manual","sales","history"],listings:["browse","insights"],',
  ' sales:["orders","costs"],analytics:["overview","demand","detail"],',
  ' imports:["import","export"],connections:["marketplaces","activity"],',
  ' connector:["overview","manage","activity"],settings:["account","support","advanced"]};',
  'for(const [group,labels] of Object.entries(groups)){',
  ' const section=$("#"+(group==="connector"?"connector-detail":group));',
  ' section.classList.add("active");',
  ' const bar=$("[data-workspace-tabs="+group+"]");',
  ' if(!bar)throw Error(group+": no navigation");',
  ' for(let i=0;i<labels.length;i++){',
  '  const label=labels[i];',
  '  const button=bar.querySelector("[data-workspace-tab="+label+"]");',
  '  button.click();',
  '  if(button.getAttribute("aria-selected")!=="true")throw Error(group+": selection not announced");',
  '  for(const alt of labels){',
  '   const panel=$$("[data-workspace-panel]").find(x=>x.dataset.workspacePanel===group+":"+alt);',
  '   const expected=alt===label;',
  '   if((getComputedStyle(panel).display!=="none")!==expected)',
  '      throw Error(group+": expected exactly "+label+" panel, failed "+alt);',
  '  }',
  ' }',
  ' // Home returns to first tab and arrow keys provide keyboard navigation.',
  ' const last=bar.querySelector("[data-workspace-tab="+labels[labels.length-1]+"]");',
  ' last.dispatchEvent(new KeyboardEvent("keydown",{key:"Home",bubbles:true}));',
  ' if(bar.querySelector("[aria-selected=true]")?.dataset.workspaceTab!==labels[0])',
  '   throw Error(group+": Home key not supported");',
  ' const first=bar.querySelector("[data-workspace-tab="+labels[0]+"]");',
  ' first.dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowRight",bubbles:true}));',
  ' if(bar.querySelector("[aria-selected=true]")?.dataset.workspaceTab!==labels[1])',
  '   throw Error(group+": ArrowRight key not supported");',
  ' activateWorkspaceTab(group,labels[0]);',
  ' section.classList.remove("active");',
  '}',
  '$("#today").classList.add("active");',
  'const required=["#reconcile-suggestions","#reconcile-sale","#reconcile-listing",',
  ' "#cross-channel-log","#analytics-metrics","#views-chart","#vinted-efficiency",',
  ' "#mapping","#preview","#mapping-preset","#export-xlsx","#listing-interest",',
  ' "#listing-price-max","#inventory-cost","#sales-channel","#diagnostics-download"];',
  'for(const id of required)if(!$(id))throw Error("Lost existing control "+id);',
  'const filter=$("#listing-interest"); filter.value="unliked";',
  'filter.dispatchEvent(new Event("change",{bubbles:true}));',
  'if(!$("#listing-more-filters summary").textContent.includes("1 active"))',
  ' throw Error("Invisible advanced filter does not announce its active state");',
  'filter.value="";updateOptionalFilterSummaries();',
  'if($("#listing-more-filters summary").textContent.includes("active"))',
  ' throw Error("Reset advanced filter still shows an active count");',
  'if($("#settings-support-panel").open||$("#settings .dashboard-danger-details").open)',
  ' throw Error("Sensitive settings must be collapsed initially");',
  'const ids=[...document.querySelectorAll("[id]")].map(e=>e.id);',
  'if(new Set(ids).size!==ids.length)throw Error("Duplicate ids");',
  'for(const [control,parent] of [["#inventory-table","#inventory-items-panel"],',
  ' ["#today-metrics","#today-overview-panel"],',
  ' ["#listing-stat-oldest","#listings-insights-panel"],',
  ' ["#purchase-cost-card","#sales-costs-panel"],',
  ' ["#connector-config","#connector-page-settings"]]){',
  ' if(!$(parent).contains($(control)))throw Error(control+" not in "+parent);',
  '}',
  'const colored=[["inventory","items"],["listings","browse"],',
  ' ["sales","orders"],["analytics","overview"]].map(([group,tab])=>{',
  ' const section=$("#"+group);section.classList.add("active");',
  ' const color=getComputedStyle(section.querySelector("[data-workspace-tab="+tab+"]")).color;',
  ' section.classList.remove("active");return color;',
  '});',
  'if(new Set(colored).size<4)throw Error("Different sections need different colors: "+colored.join("/"));',
  'const result={viewport:innerWidth,scroll:document.documentElement.scrollWidth,',
  ' tabs:document.querySelectorAll("[data-workspace-tab]").length,controls:required.length,colors:colored};',
  'if(result.scroll>result.viewport+2)throw Error("Horizontal overflow "+JSON.stringify(result));',
  'const out=document.createElement("output");out.id="task-pages-result";',
  'out.dataset.json=encodeURIComponent(JSON.stringify(result));document.body.appendChild(out);',
  '}catch(error){const out=document.createElement("output");out.id="task-pages-error";',
  'out.textContent=String(error.stack||error);document.body.appendChild(out)}})();'
].join("\n");
fs.mkdirSync("ui-evidence",{recursive:true});
const base=html.replace('<link rel="stylesheet" href="/app-static/styles.css">',"")
  .replace("</head>","<style>"+css+"</style></head>")
  .replace('<script src="/app-static/app.js"></script>',"");
for(const width of [360,768,1280]) {
  const filepath=path.resolve("ui-evidence/task-first-pages-"+width+".html");
  const png=path.resolve("ui-evidence/task-first-pages-"+width+".png");
  fs.writeFileSync(filepath,base.replace("</body>",
    () => "<script>"+fixture+"\n"+controller+"\n"+assertions+"</script></body>"));
  const flags=["--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage",
    "--virtual-time-budget=1600","--window-size="+width+",1050"];
  const dom=execFileSync(chrome,[...flags,"--dump-dom","file://"+filepath],
    {encoding:"utf8",maxBuffer:16*1024*1024,timeout:30000});
  const failure=dom.match(/<output id="task-pages-error">([^<]+)<\/output>/);
  assert(!failure,"Task tabs failed at "+width+": "+(failure?.[1]||""));
  const found=dom.match(/<output id="task-pages-result" data-json="([^"]+)"/);
  if(!found)console.error("Page tail:",dom.slice(-1200));
  assert(found,"Task tab assertions did not finish at "+width);
  const result=JSON.parse(decodeURIComponent(found[1]));
  assert.equal(result.tabs,25);
  execFileSync(chrome,[...flags,"--screenshot="+png,"file://"+filepath],
    {encoding:"utf8",timeout:30000});
  assert(fs.statSync(png).size>2000,"Chromium screenshot should be non-empty");
  console.log("TASK_FIRST_PAGE_LAYOUT "+JSON.stringify(result));
}
console.log("Ten task-tab groups, distinct section colors and responsive layout: PASS");
