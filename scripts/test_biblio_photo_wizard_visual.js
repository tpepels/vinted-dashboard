"use strict";
const assert=require("node:assert/strict"), fs=require("node:fs"), path=require("node:path");
const {execFileSync}=require("node:child_process");
const src=fs.readFileSync("app/product_static/app.js","utf8");
const css=fs.readFileSync("app/product_static/styles.css","utf8");
const a=src.indexOf("function biblioActivityStatus(row)");
const b=src.indexOf("async function inspectInventoryRelationships()",a);
assert(a>=0&&b>a);
const browser=process.env.CHROME_BIN||execFileSync("bash",["-lc",
  "command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser"],
  {encoding:"utf8"}).trim();
const fixture=[
  'const $ = s => document.querySelector(s);',
  'const $$ = s => Array.from(document.querySelectorAll(s));',
  'const esc = v => String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(\'"\',"&quot;");',
  'const when = v => String(v||"");',
  'const state={biblioPhotoTarget:"",biblioPhotoInspection:null,biblioPhotoError:"",',
  ' biblioPhotoStep:"choose",biblioPhotoSelection:[],biblioPhotoConnectionChecked:false,',
  ' biblioPhotoExpanded:true,biblioActivityExpanded:false,biblioRecoveryExpanded:false,biblioBookChoices:null};',
  'const demo={book_id:"book-123",title:"Example book",active:true,photo_state:"error",',
  ' photo_error:"BDB0004 fop_read_meta: database format error",',
  ' biblio_source_photos:9,vinted_source_photos:9,last_ftp_photo_count:9,',
  ' successful_file_transfers:0,unconfirmed_file_transfers:9,',
  ' file_progress:Array.from({length:9},(_,index)=>({index,filename:"photo"+index+".jpg",',
  ' sent_to_ftp:false,preview_url:"data:image/svg+xml,"+encodeURIComponent(',
  ' \'<svg xmlns="http://www.w3.org/2000/svg" width="180" height="220"><rect width="180" height="220" fill="#eff3f1"/></svg>\')}))};',
  'async function api(url){',
  ' if(url==="/api/app/listings?channel=biblio")return {listings:[{external_id:"book-123",title:"Example book",status:"active",quantity:1}]};',
  ' if(url.startsWith("/api/app/connectors/biblio/photo-status?book_id="))return demo;',
  ' throw Error("Unexpected network action: "+url);',
  '}'
].join("\n");
const assertions=[
  '(async()=>{',
  ' const activity={health:{active_listings:1},current:{status:"error"},runs:[]};',
  ' const pageHeader=$(".connector-task-page-heading").getBoundingClientRect();',
  ' $("#mount").innerHTML=renderBiblioActivity(activity,true);',
  ' $(".biblio-photos-panel").open=true;',
  ' const wizard=$(".biblio-photo-wizard");',
  ' const card=wizard.getBoundingClientRect();',
  ' const pad=parseFloat(getComputedStyle(wizard).paddingLeft);',
  ' const stage=$("#biblio-photo-choose-step").getBoundingClientRect();',
  ' if(!Number.isFinite(pad)||pad<16)throw Error("Photo wizard content has no useful inner padding: "+pad);',
  ' if(stage.left-card.left<pad-3)throw Error("Content begins too close to wizard border");',
  ' if(card.width>862)throw Error("Wizard exceeds its intended reading width: "+card.width);',
  ' if(Math.abs(pageHeader.left-card.left)>3||Math.abs(pageHeader.width-card.width)>3)',
  '   throw Error("Wizard and page heading have different horizontal alignment");',
  ' if($(".biblio-photo-progress").children.length!==3)',
  '   throw Error("Photo wizard progress indicator lost its three steps");',
  ' if($(".biblio-progress-item[aria-current=step]")?.dataset.biblioProgress!=="choose")',
  '   throw Error("Initial progress indicator does not mark choose book");',
  ' const select=$("#biblio-photo-book-select").getBoundingClientRect();',
  ' const button=$("#biblio-inspect-photos").getBoundingClientRect();',
  ' if(select.left<card.left+pad-3||select.right>card.right-pad+3)',
  '   throw Error("Book picker escapes wizard padding");',
  ' if(button.top-select.bottom>108)throw Error("Huge unexplained gap between picker and Continue");',
  ' const visible=id=>getComputedStyle($(id)).display!=="none";',
  ' if(!visible("#biblio-photo-choose-step")||visible("#biblio-photo-review-step")||visible("#biblio-photo-confirm-step"))throw Error("Multiple initial stages visible");',
  ' await loadBiblioPhotoChoices();',
  ' $("#biblio-photo-book-select").value="book-123";',
  ' $("#biblio-photo-book-id").value="book-123";',
  ' await inspectBiblioPhotoTarget();',
  ' if(visible("#biblio-photo-choose-step")||!visible("#biblio-photo-review-step")||visible("#biblio-photo-confirm-step"))throw Error("Photo review stage did not isolate itself");',
  ' const cards=$$(".biblio-photo-choice");',
  ' if(cards.length!==9)throw Error("Expected nine photos");',
  ' const columns=new Set(cards.map(c=>Math.round(c.getBoundingClientRect().x))).size;',
  ' if(columns>3||cards.some(c=>c.scrollWidth>c.clientWidth+3))throw Error("Thumbnails too narrow or clipped");',
  ' const boxes=$$(".biblio-missing-photo-set input[type=checkbox]");',
  ' boxes[2].checked=true; boxes[6].checked=true;',
  ' state.biblioPhotoSelection=biblioPhotoSelectedIndices();',
  ' setBiblioPhotoStep("review");',
  ' if($("#biblio-send-selected-photos").disabled)throw Error("Review button disabled after selecting photos");',
  ' setBiblioPhotoStep("confirm");',
  ' if(!visible("#biblio-photo-confirm-step")||!$("#biblio-confirm-selected-photos").disabled||!visible("#biblio-photo-connection-gate"))throw Error("FTP failure must block confirmation");',
  ' if($(".biblio-progress-item[aria-current=step]")?.dataset.biblioProgress!=="confirm")',
  '   throw Error("Progress indicator must show the active confirmation step");',
  ' state.biblioPhotoConnectionChecked=true; setBiblioPhotoStep("confirm");',
  ' if($("#biblio-confirm-selected-photos").disabled)throw Error("Check did not unlock confirmation");',
  ' setBiblioPhotoStep("review");',
  ' const result={width:innerWidth,scroll:document.documentElement.scrollWidth,columns,selected:2,padding:pad,cardWidth:card.width};',
  ' if(result.scroll>result.width+2)throw Error("Horizontal overflow: "+JSON.stringify(result));',
  ' const el=document.createElement("output");el.id="wizard-result";el.dataset.value=encodeURIComponent(JSON.stringify(result));document.body.appendChild(el);',
  '})().catch(e=>{const el=document.createElement("output");el.id="wizard-error";el.textContent=String(e.stack||e);document.body.appendChild(el)});'
].join("\n");
fs.mkdirSync("ui-evidence",{recursive:true});
for(const width of [360,768,1280]){
  const file=path.resolve("ui-evidence/biblio-photo-wizard-"+width+".html");
  const screenshot=path.resolve("ui-evidence/biblio-photo-wizard-"+width+".png");
  // Reproduce the real connector tab DOM. The previous isolated fixture
  // omitted #connector-page-photos, so the overriding zero-padding CSS was
  // never applied and the test incorrectly passed.
  fs.writeFileSync(file,'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+css+
    '</style></head><body><main style="padding:12px;width:min(100%,'+width+'px)">'
    +'<section id="connector-detail" class="view active"><div id="connector-photos-panel" class="dashboard-tab-panel connector-task-page">'
    +'<div class="connector-task-page-heading"><h3>Fix missing photos</h3><p>Choose one book, check its pictures and send only the missing ones.</p></div>'
    +'<div id="connector-page-photos"><div id="mount"></div></div></div></section></main><script>'
    +fixture+'\n'+src.slice(a,b)+'\n'+assertions+'</script></body></html>');
  const flags=["--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage",
    "--virtual-time-budget=1600","--window-size="+width+",1000"];
  const dom=execFileSync(browser,[...flags,"--dump-dom","file://"+file],
    {encoding:"utf8",maxBuffer:16*1024*1024,timeout:30000});
  const error=dom.match(/<output id="wizard-error">([^<]+)<\/output>/);
  assert(!error,"Browser failed at "+width+": "+(error?.[1]||""));
  const match=dom.match(/<output id="wizard-result" data-value="([^"]+)"/);
  assert(match,"No browser result at "+width);
  const data=JSON.parse(decodeURIComponent(match[1]));
  assert(data.columns<=3&&data.selected===2);
  execFileSync(browser,[...flags,"--screenshot="+screenshot,"file://"+file],
    {encoding:"utf8",timeout:30000});
  assert(fs.statSync(screenshot).size>4000);
  console.log("BIBLIO staged photo repair layout: "+JSON.stringify(data));
}
console.log("BIBLIO wizard mobile/desktop browser checks: PASS");
