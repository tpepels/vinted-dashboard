"use strict";
// Real headless-browser regression: click the purchase-cost button and ensure
// revenue still renders when optional APIs fail. Inline assets avoid serving
// a test API or needing network access.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {execFileSync} = require("node:child_process");

const root=path.join(__dirname,"..");
const html=fs.readFileSync(path.join(root,"app/product_static/index.html"),"utf8");
const css=fs.readFileSync(path.join(root,"app/product_static/styles.css"),"utf8");
const app=fs.readFileSync(path.join(root,"app/product_static/app.js"),"utf8");
assert(!app.includes("</script>"),"Unsafe inline source for browser test");
const bootstrap=String.raw`
  (()=>{
  window.dashboardTestErrors=[];
  window.addEventListener("error",e=>window.dashboardTestErrors.push(String(e.message)+" @ "+String(e.filename)+":"+e.lineno+":"+e.colno));
  window.addEventListener("unhandledrejection",e=>window.dashboardTestErrors.push(String(e.reason)));
  window.confirm=()=>true;
  let historicalBulkApplied=false;
  let historicalManualCost=null;
  let historicalQuantity=5;
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
    if(url.includes("/api/app/sales/historical-test/book-quantity")){
      historicalQuantity=JSON.parse(options.body).quantity;
      return {ok:true,book_quantity:historicalQuantity,
        cost_cents:historicalBulkApplied?100*historicalQuantity:null};
    }
    if(url.includes("/api/app/sales/average-cost")) {
      const payload=JSON.parse(options.body || "{}");
      if(payload.preview===false)historicalBulkApplied=true;
      return {ok:true,eligible_sales:historicalBulkApplied?0:1,
        matching_sales:1,already_costed:0,other_currency:0,with_sale_amount:1,
        eligible_books:historicalQuantity,unknown_quantity_sales:0,
        skipped_unknown_quantity_sales:0,assumed_one_quantity_sales:0,
        estimated_cost_cents:historicalQuantity*100,
        updated:payload.preview===false?1:0};
    }
    if(url.includes("/api/app/sales/historical-test/acquisition-cost")) {
      historicalManualCost=JSON.parse(options.body).cost_cents;
      return {ok:true,cost_cents:historicalManualCost,cost_source:"manual_sale"};
    }
    if(url.includes("/api/app/analytics/history")){
      const period=new URL(url,"https://dashboard.example").searchParams.get("period") || "90";
      const now=new Date();
      const start=period==="ytd"
        ?new Date(Date.UTC(now.getUTCFullYear(),0,1))
        :new Date(now.getTime()-Number(period)*86400000);
      const quantity=period==="30"?2:period==="ytd"?20:9;
      const first=new Date(start.getTime()+86400000);
      return {
        period_start:start.toISOString(),period_end:now.toISOString(),
        days:period==="ytd"?200:Number(period),
        daily:[
          {date:first.toISOString().slice(0,10),sales:1,revenue_cents:1000,
            views_gained:4,favourites_gained:1},
          {date:now.toISOString().slice(0,10),sales:quantity-1,
            revenue_cents:(quantity-1)*1000,views_gained:15,favourites_gained:2},
        ],
        followers:[],top_listings:[],
      };
    }
    if(url.includes("/api/app/analytics/vinted")
       ||url.includes("/api/app/purchase-cost-suggestions"))return null;
    if(url.includes("/api/app/analytics")) {
      const period=new URL(url,"https://dashboard.example").searchParams.get("period");
      if(!period)return analytics; // Sales page intentionally stays lifetime.
      const qty=period==="30"?2:period==="ytd"?20:9;
      return {...analytics,
        period_label:period==="ytd"?"YTD":period+" days",
        sales_period_count:qty,
        sales_period_revenue_known_count:qty,
        sales_period_totals:[{currency:"EUR",cents:qty*1000}],
        sales_period_costed_count:qty,
        sales_period_cost_cents:qty*100,
        sales_period_gross_profit_cents:qty*900,
        sales_period_gross_margin_pct:90,sales_period_roi_pct:900,
      };
    }
    if(url.includes("/api/app/sales"))return {sales:[{
      id:"historical-test",direction:"sell",title:"Older sold book",channel:"vinted",
      external_order_id:"HIST-1",total_cents:1200,currency:"EUR",
      acquisition_cost_cents:historicalManualCost===null
        ?(historicalBulkApplied?historicalQuantity*100:null):historicalManualCost,
      book_quantity:historicalQuantity,quantity_source:"manual",
      acquisition_unit_cost_cents:historicalBulkApplied?100:null,
      acquisition_cost_source:historicalManualCost===null
        ?(historicalBulkApplied?"estimated_bulk":null):"manual_sale",
      status:"completed",is_closed:true,occurred_at:"2026-06-01T12:00:00Z",
      inventory_item_id:null
    }]};
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
    document.querySelector('[data-workspace-tabs="sales"] [data-workspace-tab="costs"]').click();
    await sleep(80);
    result.backfillFormVisible=!document.querySelector("#sales-costs-panel").classList.contains("hidden");
    document.querySelector("#sale-cost-backfill-preview-btn").click();
    await sleep(80);
    result.backfillPreview=document.querySelector("#sale-cost-backfill-preview").textContent.includes("5 books across 1 historical sales");
    result.backfillApplyEnabled=!document.querySelector("#sale-cost-backfill-apply").disabled;
    document.querySelector("#sale-cost-backfill-apply").click();
    await sleep(150);
    document.querySelector("#sale-cost-history-filter").value="all";
    document.querySelector("#sale-cost-history-filter").dispatchEvent(new Event("change"));
    result.backfillRecorded=document.querySelector("#sale-cost-history-table").textContent.includes("Estimated")
      && document.querySelector("#sale-cost-history-table .sale-cost-input").value==="5.00";
    const quantityRow=document.querySelector("#sale-cost-history-table .sale-quantity-edit");
    quantityRow.querySelector("input").value="6";
    quantityRow.querySelector(".sale-quantity-save").click();
    await sleep(150);
    result.bookQuantitySaved=document.querySelector("#sale-cost-history-table .sale-quantity-input").value==="6"
      && document.querySelector("#sale-cost-history-table .sale-cost-input").value==="6.00";
    const historicRow=document.querySelector("#sale-cost-history-table .sale-cost-edit");
    historicRow.querySelector("input").value="2.50";
    historicRow.querySelector(".sale-cost-save").click();
    await sleep(150);
    result.manualHistoricEdit=document.querySelector("#sale-cost-history-table").textContent.includes("Manually recorded")
      && document.querySelector("#sale-cost-history-table .sale-cost-input").value==="2.50";
    document.querySelector('.nav[data-view="analytics"]').click();
    await sleep(200);
    result.statisticsRevenue=document.querySelector("#analytics-revenue-metrics").textContent;
    result.statisticsProfit=document.querySelector("#analytics-metrics").textContent;
    const analyticsTabs=document.querySelector('[data-workspace-tabs="analytics"]');
    result.salesDashboardTabs=analyticsTabs.querySelector('[data-workspace-tab="overview"]').textContent.includes("Sales performance")
      && analyticsTabs.querySelector('[data-workspace-tab="demand"]').textContent.includes("Vinted reach")
      && analyticsTabs.querySelector('[data-workspace-tab="detail"]').textContent.includes("Stock");
    result.costCoverageRemoved=!document.querySelector("#analytics-metrics").textContent.includes("Cost coverage")
      && document.querySelector("#sale-cost-data-quality").textContent.includes("acquisition costs");
    result.monthlyOrders90=document.querySelector("#analytics-monthly-table").textContent;
    result.monthlyChart90=Boolean(document.querySelector("#analytics-monthly-sales-chart svg"))
      && Boolean(document.querySelector("#analytics-monthly-revenue-chart svg"))
      && Boolean(document.querySelector("#analytics-cumulative-sales-chart svg"))
      && Boolean(document.querySelector("#analytics-cumulative-revenue-chart svg"));
    const periodSelect=document.querySelector("#analytics-days");
    periodSelect.value="30";
    periodSelect.dispatchEvent(new Event("change",{bubbles:true}));
    await sleep(200);
    result.revenue30=document.querySelector("#analytics-revenue-metrics").textContent;
    result.profit30=document.querySelector("#analytics-metrics").textContent;
    result.monthlyOrders30=document.querySelector("#analytics-monthly-table").textContent;
    periodSelect.value="ytd";
    periodSelect.dispatchEvent(new Event("change",{bubbles:true}));
    await sleep(200);
    result.revenueYtd=document.querySelector("#analytics-revenue-metrics").textContent;
    result.profitYtd=document.querySelector("#analytics-metrics").textContent;
    result.monthlyOrdersYtd=document.querySelector("#analytics-monthly-table").textContent;
    result.reportFailureIsolated=document.querySelector("#vinted-metrics").textContent.includes("Vinted engagement unavailable")
      && Boolean(document.querySelector("#analytics-monthly-sales-chart svg"));
    document.querySelector('[data-workspace-tab="demand"]').click();
    result.vintedDashboardVisible=!document.querySelector("#analytics-demand-panel").classList.contains("hidden")
      && document.querySelector("#analytics-overview-panel").classList.contains("hidden");
    document.querySelector('[data-workspace-tab="detail"]').click();
    result.stockDashboardVisible=!document.querySelector("#analytics-detail-panel").classList.contains("hidden")
      && document.querySelector("#analytics-overview-panel").classList.contains("hidden");
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
  "<script>"+bootstrap+"</script><script src=\""+pathToFileURL(path.join(root,"app/product_static/app.js")).href+"\"></script><script>"+browserTest+"</script>");
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
  for(const key of ["initialView","clickHandler","formOpens","previewWorks","applyEnabled","applied","reportFailureIsolated",
    "backfillFormVisible","backfillPreview","backfillApplyEnabled","backfillRecorded",
    "bookQuantitySaved","manualHistoricEdit",
    "salesDashboardTabs","costCoverageRemoved","monthlyChart90","vintedDashboardVisible",
    "stockDashboardVisible"]) {
    assert.equal(result[key],true,key+": "+JSON.stringify(result));
  }
  assert(result.salesRevenue.includes("1,234.56") || result.salesRevenue.includes("1.234,56")
    || result.salesRevenue.includes("1234,56") || result.salesRevenue.includes("1234.56"),
    "Sales page must display total lifetime revenue: "+result.salesRevenue);
  assert(result.statisticsRevenue.includes("Sales revenue")&&result.statisticsRevenue.includes("9"),
    "Analytics default must show 90-day period sales: "+result.statisticsRevenue);
  assert(result.revenue30.includes("2")&&result.revenue30.includes("30 days"),
    "Analytics 30d count and label must change: "+result.revenue30);
  assert(result.revenueYtd.includes("20")&&result.revenueYtd.includes("YTD"),
    "Analytics YTD count and label must change: "+result.revenueYtd);
  assert(result.statisticsProfit.includes("90 days")
      && result.profit30.includes("30 days") && result.profitYtd.includes("YTD"),
    "Analytics profit cards must follow selected period");
  assert(result.monthlyOrders90.includes("9") && result.monthlyOrders30.includes("2")
      && result.monthlyOrdersYtd.includes("20"),
    "Monthly table must follow period and cumulative order totals");
  assert(result.salesRevenue.includes("37"),"Sales lifetime metrics must remain unchanged");
  console.log("Browser E2E: average-cost workflow, independent lifetime Sales and period-aware Analytics: PASS");
} finally {
  fs.rmSync(tmp,{recursive:true,force:true});
}
