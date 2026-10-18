"use strict";
// Real headless-browser regression: click the purchase-cost button and ensure
// revenue still renders when optional APIs fail. Inline assets avoid serving
// a test API or needing network access.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {execFileSync} = require("node:child_process");

const root=path.join(__dirname,"..");
const html=fs.readFileSync(path.join(root,"app/product_static/index.html"),"utf8");
const css=fs.readFileSync(path.join(root,"app/product_static/styles.css"),"utf8");
const app=fs.readFileSync(path.join(root,"app/product_static/app.js"),"utf8");
assert(!app.includes("</script>"),"Unsafe inline source for browser test");
const bootstrap=String.raw`
  (()=>{
  window.dashboardTestErrors=[];
  window.addEventListener("error",e=>window.dashboardTestErrors.push(String(e.message)));
  window.addEventListener("unhandledrejection",e=>window.dashboardTestErrors.push(String(e.reason)));
  window.confirm=()=>true;
  const analytics={
    sales_all_time_count:37, sales_all_time_revenue_known_count:37,
    sales_all_time_totals:[{currency:"EUR",cents:123456}],
    sales_ytd_count:14, sales_ytd_revenue_known_count:14,
    sales_ytd_cents:45678, currency:"EUR",
    active_inventory:15, priced_inventory_count:12, costed_inventory_count:0,
    margin_inventory_count:0, inventory_cost_cents:0,
    inventory_ask_cents:50000, inventory_potential_margin_cents:0,
    sales_ytd_costed_count:0
  };
  const bodyFor=(url,options)=>{
    if(url.includes("/api/auth/me"))return {
      app_name:"Reseller Dashboard",workspace:{id:"test",slug:"test",name:"Test"},
      bridge_version:"3.4.5", billing:{enabled:false}
    };
    if(url.includes("/api/app/inventory/average-cost"))return {
      ok:true,eligible_items:15,affected_units:15,already_costed:0,
      other_currency:0,estimated_cost_cents:1500,updated:options?.body?.includes('"preview":false')?15:0
    };
    if(url.includes("/api/app/inventory?"))return {items:[]};
    if(url.includes("/api/app/analytics/history")||url.includes("/api/app/analytics/vinted")
       ||url.includes("/api/app/purchase-cost-suggestions"))return null;
    if(url.includes("/api/app/analytics"))return analytics;
    if(url.includes("/api/app/sales"))return {sales:[]};
    return {};
  };
  window.fetch=async(url,options={})=>{
    const result=bodyFor(String(url),options);
    return {ok:result!==null,status:result!==null?200:503,
      headers:{get:()=> "application/json"},
      json:async()=> result??{detail:"Optional report unavailable"},
      text:async()=>JSON.stringify(result)};
  };
  })();
`;
const browserTest=String.raw`
  (async()=>{
    const result={};
    const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
    await sleep(200);
    document.querySelector('.nav[data-view="inventory"]').click();
    await sleep(150);
    result.initialView=document.querySelector("#inventory").classList.contains("active");
    const button=document.querySelector("#inventory-average-cost-open");
    result.clickHandler=typeof button.onclick==="function";
    button.click();
    const form=document.querySelector("#inventory-average-cost-form");
    result.formOpens=!form.classList.contains("hidden")
      &&button.getAttribute("aria-expanded")==="true";
    document.querySelector("#inventory-average-cost-preview-btn").click();
    await sleep(120);
    result.previewWorks=document.querySelector("#inventory-average-cost-preview").textContent.includes("15 items");
    result.applyEnabled=!document.querySelector("#inventory-average-cost-apply").disabled;
    document.querySelector("#inventory-average-cost-apply").click();
    await sleep(120);
    result.applied=document.querySelector("#inventory-average-cost-form").classList.contains("hidden");
    document.querySelector('.nav[data-view="sales"]').click();
    await sleep(200);
    result.salesRevenue=document.querySelector("#sales-lifetime-metrics").textContent;
    document.querySelector('.nav[data-view="analytics"]').click();
    await sleep(200);
    result.statisticsRevenue=document.querySelector("#analytics-revenue-metrics").textContent;
    result.reportFailureIsolated=document.querySelector("#sales-chart").textContent.includes("History unavailable");
    result.errors=window.dashboardTestErrors;
    const out=document.createElement("output");
    out.id="e2e-revenue-and-average-cost";
    out.textContent=JSON.stringify(result);
    document.body.append(out);
  })().catch(e=>{
    const out=document.createElement("output");
    out.id="e2e-revenue-and-average-cost";
    out.textContent=JSON.stringify({error:String(e),stack:String(e.stack),errors:window.dashboardTestErrors});
    document.body.append(out);
  });
`;
const resultHtml=html.replace('<link rel="stylesheet" href="/app-static/styles.css">',
  "<style>"+css+"</style>").replace(
  '<script src="/app-static/app.js"></script>',
  "<script>"+bootstrap+"</script><script>"+app+"</script><script>"+browserTest+"</script>");
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),"dashboard-e2e-"));
const file=path.join(tmp,"test.html");
try {
  fs.writeFileSync(file,resultHtml);
  const browser=process.env.CHROME_BIN||execFileSync("bash",["-lc",
    "command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser"],
    {encoding:"utf8"}).trim();
  const rendered=execFileSync(browser,[
    "--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage",
    "--disable-features=Translate","--virtual-time-budget=6000",
    "--dump-dom","file://"+file],{encoding:"utf8",timeout:30000,maxBuffer:8*1024*1024});
  const match=rendered.match(/<output id="e2e-revenue-and-average-cost">([^<]+)<\/output>/);
  assert(match,"Headless browser did not finish the interaction test");
  const result=JSON.parse(match[1].replace(/&quot;/g,'"').replace(/&amp;/g,"&"));
  console.log("BROWSER_E2E_REPORT "+JSON.stringify(result));
  assert(!result.error,"Browser test failed: "+JSON.stringify(result));
  assert.deepEqual(result.errors,[],"Page had JavaScript errors: "+JSON.stringify(result.errors));
  for(const key of ["initialView","clickHandler","formOpens","previewWorks","applyEnabled","applied","reportFailureIsolated"]) {
    assert.equal(result[key],true,key+": "+JSON.stringify(result));
  }
  assert(result.salesRevenue.includes("1,234.56") || result.salesRevenue.includes("1.234,56")
    || result.salesRevenue.includes("1234,56") || result.salesRevenue.includes("1234.56"),
    "Sales page must display total lifetime revenue: "+result.salesRevenue);
  assert(result.statisticsRevenue.includes("Total revenue")&&result.statisticsRevenue.includes("37"),
    "Statistics page must display lifetime sales: "+result.statisticsRevenue);
  console.log("Browser E2E: average-cost workflow and independent revenue in Sales + Analytics: PASS");
} finally {
  fs.rmSync(tmp,{recursive:true,force:true});
}
