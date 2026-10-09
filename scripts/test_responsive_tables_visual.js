"use strict";
/* Browser-level layout proof against actual CSS and responsive table functions. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {execFileSync} = require("node:child_process");

const app = fs.readFileSync("app/product_static/app.js", "utf8");
const styles = fs.readFileSync("app/product_static/styles.css", "utf8");
const sortStart = app.indexOf("const tableNumberFormat =");
const sortEnd = app.indexOf('document.addEventListener("click",', sortStart);
const resizeStart = app.indexOf("const tableResizeSeen = new WeakSet();");
const mobileStart = app.indexOf("const mobileCardTableIds =");
const mobileEnd = app.indexOf("function startResponsiveTables()", mobileStart);
assert(sortStart>=0 && sortEnd>sortStart && resizeStart>=0 && mobileStart>resizeStart && mobileEnd>mobileStart);
const realHelpers = app.slice(sortStart,sortEnd)+"\n"+app.slice(resizeStart,mobileEnd);
const browser = process.env.CHROME_BIN || execFileSync(
  "bash",["-lc","command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser"],
  {encoding:"utf8"},
).trim();
const esc=text=>String(text).replace(/[&<>"]/g,
  char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[char]));
function makeTable(id, headings, values, checkbox=false) {
  const head=headings.map((name,i)=>
    "<th>"+(checkbox && i===0 ? '<input id="inventory-select-all" type="checkbox" aria-label="Select all">' : esc(name))+"</th>").join("");
  const rows=values.map(row=>"<tr>"+row.map((cell,i)=>
    "<td>"+(checkbox && i===0
      ? '<input class="inventory-select" type="checkbox" aria-label="Select item">'
      : i===row.length-1 && id!=="sales-table"
      ? '<button type="button" class="btn fixture-action">Open</button>'
      : esc(cell))+"</td>").join("")+"</tr>").join("");
  return '<div id="'+id+'" class="card table-wrap"><table><thead><tr>'+head+
    "</tr></thead><tbody>"+rows+"</tbody></table></div>";
}
const inventoryLabels=["","Item","SKU","Category","Qty","Location","Cost",
  "Ask","Margin","Channels","Status","Actions"];
const inventoryRows=[
  ["","Example novel, hardcover, a very long title","BOOK-001","book","1","Shelf A",
    "€3.00","€14.00","€11.00","Vinted","active",""],
  ["","Another novel","BOOK-002","book","3","Shelf B",
    "€1.00","€7.00","€6.00","Biblio","active",""],
];
const listingLabels=["Listing","Marketplace","Status","Listed","Age","Favourites","Views","Price","Actions"];
const listingRows=[
  ["Example novel, hardcover","Vinted","active","2026-10-02","7 days","3","55","€14.00",""],
  ["Another listing","BIBLIO","active","2026-10-01","8 days","1","19","€7.00",""],
];
const salesLabels=["Date","Channel","Item","Person","Direction","Status","Amount"];
const salesRows=[["2026-10-09","Vinted","Example novel","Buyer","sell","sold","€14.00"]];
const widths=[360,390,768,1024,1440];
fs.mkdirSync("ui-evidence",{recursive:true});
const results=[];
for(const width of widths){
  const tables=makeTable("inventory-table",inventoryLabels,inventoryRows,true)
    +makeTable("listings-table",listingLabels,listingRows)
    +makeTable("sales-table",salesLabels,salesRows)
    +'<div id="editable-table" class="card table-wrap"><table class="stock-scan-table"><thead><tr><th>SKU</th><th>Description</th><th>Quantity</th></tr></thead><tbody><tr><td><input value="BOOK-001"></td><td><input value="Example"></td><td><input value="1"></td></tr></tbody></table></div>';
  const inline=realHelpers + [
    'const shell=document.querySelector(".shell");',
    'const inventory=document.querySelector("#inventory-table table");',
    'const headerSelect=document.querySelector("#inventory-select-all");',
    'headerSelect.addEventListener("change",()=>{inventory.querySelectorAll(".inventory-select").forEach(box=>box.checked=headerSelect.checked);});',
    'document.querySelectorAll(".table-wrap table").forEach(table=>{activateResizableTable(table);activateMobileCardTable(table);});',
    'let clicked=0;',
    'document.querySelector(".fixture-action").addEventListener("click",()=>clicked++);',
    'document.querySelector(".fixture-action").click();',
    'const mobile=document.querySelector("#inventory-mobile-select-all");',
    'if(mobile){mobile.checked=true;mobile.dispatchEvent(new Event("change",{bubbles:true}));}',
    'const selected=Array.from(inventory.querySelectorAll(".inventory-select")).every(box=>box.checked);',
    'const sort=document.querySelector("#inventory-table .mobile-table-sort select");',
    'if(sort){sort.value="4:desc";sort.dispatchEvent(new Event("change",{bubbles:true}));}',
    'const sorted=inventory.tBodies[0].rows[0].cells[4].textContent.trim()==="3";',
    'const measure=id=>{const host=document.getElementById(id);const table=host.querySelector("table");const tr=table.tBodies[0].rows[0];return {host:host.clientWidth,hostScroll:host.scrollWidth,table:Math.round(table.getBoundingClientRect().width),tableDisplay:getComputedStyle(table).display,rowDisplay:getComputedStyle(tr).display,gridColumns:getComputedStyle(tr).gridTemplateColumns.trim().split(/\\s+/).filter(Boolean).length,cards:table.classList.contains("mobile-cards"),labels:tr.cells[1].dataset.label||null};};',
    'const output=document.createElement("output");',
    'output.id="responsive-table-metrics";',
    'output.dataset.state=encodeURIComponent(JSON.stringify({requestedWidth:parseInt(shell.dataset.width),shellWidth:shell.clientWidth,shellScroll:shell.scrollWidth,bodyScroll:document.documentElement.scrollWidth,viewport:innerWidth,buttonClicked:clicked,selected,sorted,mobileSortVisible:sort&&getComputedStyle(sort.closest(".mobile-table-tools")).display!=="none",inventory:measure("inventory-table"),listings:measure("listings-table"),sales:measure("sales-table"),editable:measure("editable-table")}));',
    'document.body.appendChild(output);',
  ].join("\n");
  const html='<!doctype html><html lang="en"><head><meta charset="utf-8">'+
    '<meta name="viewport" content="width=device-width, initial-scale=1">'+
    '<style>'+styles+'</style></head><body>'+
    '<div class="shell" data-width="'+width+'" style="width:'+width+'px;max-width:100%">'+
    '<aside><div class="brand"><strong>Reseller</strong></div></aside><main>'+
    '<h1>Inventory and orders</h1>'+tables+'</main></div>'+
    '<script>'+inline+'</script></body></html>';
  const file=path.join("ui-evidence","responsive-tables-"+width+".html");
  fs.writeFileSync(file,html);
  const fileUrl="file://"+path.resolve(file);
  const flags=["--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage",
    "--virtual-time-budget=900","--window-size="+width+",1100"];
  const dom=execFileSync(browser,[...flags,"--dump-dom",fileUrl],{
    encoding:"utf8",stdio:["ignore","pipe","pipe"],timeout:30000,maxBuffer:10*1024*1024,
  });
  const value=dom.match(/<output id="responsive-table-metrics" data-state="([^"]+)"/);
  assert(value,"Browser did not run responsive table helpers at "+width);
  const data=JSON.parse(decodeURIComponent(value[1]));
  // Chromium may subtract the vertical scrollbar (about 15px) from the
  // requested window width. Assert against the real content viewport.
  assert(data.shellWidth<=width && data.shellWidth>=width-20,
    "Unexpected Chromium viewport dimensions: "+JSON.stringify(data));
  assert(data.shellScroll<=data.shellWidth+2,
    "Table must not expand the page at "+width+": "+JSON.stringify(data));
  assert.equal(data.buttonClicked,1,"Row action no longer clickable");
  assert(data.selected,"Mobile Select all did not select inventory rows");
  assert(data.sorted,"Mobile table sorting did not change the row order");
  const shouldBeCards=data.inventory.host<=860;
  for(const key of ["inventory","listings","sales"]){
    assert.equal(data[key].rowDisplay,shouldBeCards?"grid":"table-row",
      "Unexpected "+key+" row layout at "+width+": "+JSON.stringify(data[key]));
    if(shouldBeCards){
      assert(data[key].table<=data[key].host+2,"Card is wider than its container");
      const columns=data[key].host>=760 ? 4 : data[key].host>=610 ? 3 : 2;
      assert.equal(data[key].gridColumns,columns,
        "Card facts should use available tablet/phone width: "+key+" at "+width);
    }
  }
  assert.equal(data.mobileSortVisible,shouldBeCards,
    "Mobile sort should appear only in card mode");
  assert.notEqual(data.editable.rowDisplay,"grid",
    "Editable table must remain a real table");
  assert(data.editable.hostScroll>=data.editable.host,
    "Editable table must retain local horizontal scrolling");
  const png=path.join("ui-evidence","responsive-tables-"+width+".png");
  execFileSync(browser,[...flags,"--screenshot="+path.resolve(png),fileUrl],{
    stdio:["ignore","pipe","pipe"],timeout:30000,
  });
  results.push(data);
}
fs.writeFileSync("ui-evidence/responsive-tables-results.json",JSON.stringify(results,null,2));
console.log("RESPONSIVE_TABLE_VIEWPORTS "+JSON.stringify(results.map(r=>({
  width:r.requestedWidth,shell:r.shellWidth,overflow:r.shellScroll>r.shellWidth+2,
  cards:r.inventory.rowDisplay==="grid",editableScroll:r.editable.hostScroll>r.editable.host,
}))));
console.log("Responsive table layout, sorting, row actions and selection: PASS");
