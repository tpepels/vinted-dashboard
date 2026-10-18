"use strict";
const fs=require("node:fs");
const vm=require("node:vm");
const assert=require("node:assert/strict");
const path=require("node:path");

// Evaluate exactly the Vinted content-script orderQuantity implementation
// with the content script's own first() semantics. Avoid a site/network login.
const src=fs.readFileSync(path.join(__dirname,"../app/extension/content.js"),"utf8");
const start=src.indexOf("function orderQuantity(raw){");
const end=src.indexOf("function orderRow(raw,direction)",start);
assert(start>0&&end>start,"Vinted bundle count extractor must exist");
const first=(obj,...keys)=>{
  if(!obj||typeof obj!=="object")return null;
  for(const key of keys){
    const value=obj[key];
    if(value!==undefined&&value!==null&&value!=="")return value;
  }
  return null;
};
const count=vm.runInNewContext(src.slice(start,end)+";orderQuantity",{first});
assert.equal(count({items:[{id:1},{id:2},{id:3},{id:4},{id:5}]}),5);
assert.equal(count({bundle_items:[{quantity:2},{quantity:3}]}),5);
assert.equal(count({item_count:5}),5);
assert.equal(count({quantity:"5"}),5);
assert.equal(count({item:{id:123}}),null,"Do not mistake one cover item for bundle quantity");
assert.equal(count({items:[]}),null,"Empty item arrays do not verify quantity");
assert.equal(count({quantity:0}),null);
assert.equal(count({quantity:-2}),null);
assert.equal(count({items:[{quantity:1.5}]}),null);
assert.equal(count({items:[{quantity:2000}]}),null);
console.log("Vinted bundle quantity extraction: PASS");
