"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");

const source=fs.readFileSync(path.join(__dirname,"../app/product_static/app.js"),"utf8");
const from=source.indexOf("function monthlySalesSeries(history) {");
const to=source.indexOf("let analyticsLoadSequence = 0;",from);
assert(from>0&&to>from,"Monthly analytics functions must be defined");
const elements=new Map();
const $=selector=>{
  if(!elements.has(selector))elements.set(selector,{
    innerHTML:"",insertAdjacentHTML(_position,text){this.innerHTML+=text;}
  });
  return elements.get(selector);
};
const esc=x=>String(x??"").replace(/[&<>"']/g, ch=>({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;",
})[ch]);
const money=(cents,currency)=>currency+" "+(Number(cents)/100).toFixed(2);
const sandbox={$ ,esc,money,Intl,Date,Map,Number,Math,String};
const api=vm.runInNewContext(source.slice(from,to)+
  ";({monthlySalesSeries,salesTrendChart,renderSalesTrends})",sandbox);
const history={
  period_start:"2026-07-23T13:15:00Z",
  period_end:"2026-10-18T13:15:00Z",
  daily:[
    {date:"2026-07-22",sales:99,revenue_cents:999900}, // before range
    {date:"2026-07-25",sales:1,revenue_cents:900},
    {date:"2026-09-10",sales:3,revenue_cents:3600},
    {date:"2026-10-18",sales:2,revenue_cents:2400},
    {date:"2026-10-19",sales:99,revenue_cents:999900}, // future
  ]
};
const months=api.monthlySalesSeries(history);
assert.deepEqual(Array.from(months,m=>m.month),
  ["2026-07","2026-08","2026-09","2026-10"]);
assert.deepEqual(Array.from(months,m=>m.sales),[1,0,3,2]);
assert.deepEqual(Array.from(months,m=>m.revenue_cents),[900,0,3600,2400]);
assert.deepEqual(Array.from(months,m=>m.cumulative_sales),[1,1,4,6]);
assert.deepEqual(Array.from(months,m=>m.cumulative_revenue_cents),[900,900,4500,6900]);
assert(months[0].partial&&months[3].partial,"Partial calendar months must be labeled");
assert.equal(months[1].partial,false);
api.renderSalesTrends(history,{
  currency:"EUR",period:"90",sales_period_count:6,
  sales_period_revenue_known_count:6,
  sales_period_totals:[{currency:"EUR",cents:6900}],
});
for(const target of [
  "#analytics-monthly-sales-chart","#analytics-monthly-revenue-chart",
  "#analytics-cumulative-sales-chart","#analytics-cumulative-revenue-chart",
]){
  assert($(target).innerHTML.includes("<svg"),target+" must render a chart");
  assert($(target).innerHTML.includes("<title>"),target+" must expose data point tooltips");
}
assert($("#analytics-monthly-sales-chart").innerHTML.includes("<rect"),
  "Monthly completed orders must use bars");
assert($("#analytics-cumulative-revenue-chart").innerHTML.includes("<path"),
  "Cumulative revenue must use a trend line");
const table=$("#analytics-monthly-table").innerHTML;
assert.equal((table.match(/<tr>/g)||[]).length,5,"Header plus all four monthly rows");
assert(table.includes("Partial month")&&table.includes("EUR 69.00"));
assert(!table.includes("9999.00"),"Outside-window sales must not leak into chart");
assert(api.monthlySalesSeries({
  period_start:"invalid",period_end:"2026-10-18T00:00:00Z",daily:[]
}).length===0);
assert.deepEqual(Array.from(api.monthlySalesSeries({
  period_start:"2026-01-01T00:00:00Z",period_end:"2026-04-18T12:00:00Z",
  daily:[{date:"2026-03-02",sales:2,revenue_cents:500}],
}),m=>m.cumulative_sales),[0,0,2,2],"YTD must start at zero and carry totals");

// Distinct currencies cannot be added to one revenue series.
api.renderSalesTrends(history,{
  currency:"EUR",sales_period_count:6,sales_period_revenue_known_count:6,
  sales_period_totals:[{currency:"EUR",cents:4000},{currency:"USD",cents:3000}],
});
assert($("#analytics-monthly-revenue-chart").innerHTML.includes("more than one currency"));
assert($("#analytics-cumulative-revenue-chart").innerHTML.includes("more than one currency"));
assert($("#analytics-monthly-sales-chart").innerHTML.includes("<svg"));
console.log("Monthly sales and cumulative trends: PASS");
