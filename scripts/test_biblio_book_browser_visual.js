"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path");
const {execFileSync}=require("node:child_process");
const app=fs.readFileSync("app/product_static/app.js","utf8");
const css=fs.readFileSync("app/product_static/styles.css","utf8");
const html=fs.readFileSync("app/product_static/index.html","utf8");
assert(html.includes('id="biblio-postings-search"'));
assert(html.includes('id="biblio-postings-content"'));
const start=app.indexOf("function renderBiblioDashboardPostings(rows) {");
const end=app.indexOf("\nasync function loadBiblioDashboardPostings() {",start);
assert(start>=0&&end>start);
const render=app.slice(start,end);
const browser=process.env.CHROME_BIN||execFileSync("bash",["-lc",
  "command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser"],
  {encoding:"utf8"}).trim();
fs.mkdirSync("ui-evidence",{recursive:true});
const rows=Array.from({length:35},(_,i)=>({
  title:"Book "+String(i+1).padStart(2,"0"),
  book_id:"BOOK-"+String(i+1).padStart(2,"0"),
  inventory_item_id:"inventory-"+i,
  dashboard_removed:i===2,
  remote_missing:i===1,
  photo_count:4,
  photo_state:i===1?"error":"sent",
}));
const fixture=[
  'const $=sel=>document.querySelector(sel);',
  'const $$=sel=>Array.from(document.querySelectorAll(sel));',
  'const esc=v=>String(v??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",[String.fromCharCode(34)]:"&quot;"}[c]));',
  'const state={biblioSelectedBookId:null,biblioBookVisibleLimit:12};',
  'function flash(){}',
  'async function api(){return {}}',
  'async function connections(){} async function selectView(){} async function openBiblioPublish(){}',
  'async function loadBiblioDashboardPostings(){}',
  'async function openMarketplaceRecovery(kind,item,book){window.photoTarget={kind,item,book}}',
  'const books='+JSON.stringify(rows)+';',
  render,
].join("\n");
const assertions=[
  '(async()=>{',
  ' const result={width:innerWidth};',
  ' renderBiblioDashboardPostings(books);',
  ' const count=()=>$$(".biblio-book-pick").length;',
  ' if(count()!==12)throw Error("Initial book list should show only 12, got "+count());',
  ' if($(".biblio-book-detail"))throw Error("Actions must not show before choosing a book");',
  ' if($$(".biblio-posting-action").length)throw Error("Removal is exposed on every book");',
  ' if(!$(".biblio-show-more-books"))throw Error("Missing pagination control");',
  ' $$(".biblio-book-pick")[1].click();',
  ' if(!$(".biblio-book-detail")||!$(".biblio-resend-book"))throw Error("Verified missing book should show targeted resend");',
  ' if($$(".biblio-posting-action").length!==1)throw Error("Exactly one selected-book action group expected");',
  ' if(!$(".biblio-book-guidance").textContent.includes("complete BIBLIO export"))throw Error("Missing seller-side explanation");',
  ' $(".biblio-check-book-photos").click();',
  ' await new Promise(resolve=>setTimeout(resolve,0));',
  ' if(window.photoTarget?.book!=="BOOK-02")throw Error("Photo workflow must receive selected book ID");',
  ' $(".biblio-close-book").click();',
  ' if($(".biblio-book-detail"))throw Error("Closing selection should hide action group");',
  ' $(".biblio-show-more-books").click();',
  ' if(count()!==24)throw Error("Show more should expand by 12");',
  ' $("#biblio-postings-search").value="Book 03";',
  ' state.biblioBookVisibleLimit=12;',
  ' renderBiblioDashboardPostings(books);',
  ' if(count()!==1)throw Error("Search should filter to one book");',
  ' $$(".biblio-book-pick")[0].click();',
  ' if(!$(".biblio-book-guidance").textContent.includes("removed from the dashboard"))throw Error("Removed-book explanation missing");',
  ' if($(".biblio-check-book-photos"))throw Error("Removed book must not offer photo upload");',
  ' if(!$(".biblio-posting-action").textContent.includes("re-add"))throw Error("Removed book re-add missing");',
  ' result.scroll=document.documentElement.scrollWidth;',
  ' if(result.scroll>result.width+2)throw Error("Horizontal overflow: "+JSON.stringify(result));',
  ' const out=document.createElement("output");out.id="book-browser-result";out.dataset.value=encodeURIComponent(JSON.stringify(result));document.body.appendChild(out);',
  '})().catch(error=>{let o=document.createElement("output");o.id="book-browser-error";o.textContent=String(error.stack||error);document.body.appendChild(o)});',
].join("\n");
for(const width of [360,768,1280]){
  const file=path.resolve("ui-evidence/biblio-book-browser-"+width+".html");
  const screenshot=path.resolve("ui-evidence/biblio-book-browser-"+width+".png");
  fs.writeFileSync(file,'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+
    css+'</style></head><body><main style="max-width:1100px;margin:0 auto;padding:12px">'+
    '<section id="connector-detail" class="view active"><div id="connector-listings-panel" class="connector-task-page">'+
    '<div id="biblio-dashboard-postings-panel" class="card connection-task-panel" open><div class="connection-task-content">'+
    '<div class="biblio-postings-toolbar"><label>Search books<input id="biblio-postings-search" type="search"></label>'+
    '<button class="btn" type="button">Refresh</button></div><div id="biblio-postings-content"></div>'+
    '</div></div></div></section></main><script>'+fixture+'\n'+assertions+'</script></body></html>');
  const flags=["--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage",
    "--virtual-time-budget=1400","--window-size="+width+",1000"];
  const dom=execFileSync(browser,[...flags,"--dump-dom","file://"+file],
    {encoding:"utf8",maxBuffer:16*1024*1024,timeout:30000});
  const failure=dom.match(/<output id="book-browser-error">([^<]+)<\/output>/);
  assert(!failure,"Browser failed at "+width+": "+(failure?.[1]||""));
  const match=dom.match(/<output id="book-browser-result" data-value="([^"]+)"/);
  assert(match,"No browser result at "+width);
  const data=JSON.parse(decodeURIComponent(match[1]));
  assert(data.scroll<=data.width+2);
  execFileSync(browser,[...flags,"--screenshot="+screenshot,"file://"+file],
    {encoding:"utf8",timeout:30000});
  assert(fs.statSync(screenshot).size>4000);
}
console.log("BIBLIO book selection, targeted actions, search and mobile layout: PASS");
