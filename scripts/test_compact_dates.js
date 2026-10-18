"use strict";
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const src=fs.readFileSync("app/product_static/app.js","utf8");
function extract(start,end,name){
 const a=src.indexOf(start),b=src.indexOf(end,a);
 assert(a>=0&&b>a);
 return vm.runInNewContext(src.slice(a,b)+";"+name,{Date,Intl});
}
const when=extract("function when(value) {","const tableNumberFormat","when");
const dateOnly=extract("function dateOnly(value) {","function ageDays","dateOnly");
const sample="2026-10-17T21:21:50Z";
for(const label of [when(sample),dateOnly(sample)]){
 assert.match(label,/\b26\b/);
 assert.doesNotMatch(label,/2026/);
}
assert.match(when(sample),/\d{2}:\d{2}/);
assert.doesNotMatch(when(sample),/\d{2}:\d{2}:\d{2}|AM|PM/i);
console.log("Compact dates: PASS");
