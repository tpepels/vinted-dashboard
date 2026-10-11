"use strict";
// Verify essential BIBLIO fields are shown before optional catalogue metadata.
// Renders the production function with read-only fixture data (no FTP calls).
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const text=fs.readFileSync("app/product_static/app.js","utf8");
const start=text.indexOf("function renderBiblioPublish(data) {");
const end=text.indexOf("async function openBiblioPublish(",start);
assert(start>=0&&end>start,"BIBLIO publication renderer not found");
const elements=new Map();
const node=id=>{
  if(!elements.has(id))elements.set(id,{textContent:"",innerHTML:"",
    disabled:false,classList:{add(){},remove(){},toggle(){}}});
  return elements.get(id);
};
const context={
  state:{biblioPublish:{data:null}},
  $:node,
  esc:value=>String(value??"").replaceAll("&","&amp;").replaceAll("<","&lt;")
    .replaceAll('"',"&quot;"),
  biblioSourceBadge:()=>"",
  document:{querySelectorAll(){return [];}},
  Number,Object,Array,Set,String
};
vm.createContext(context);
vm.runInContext(text.slice(start,end),context);
context.renderBiblioPublish({configured:true,action:"publish",item_title:"Example book",
  source:{channel:"vinted",photo_count:2,image_urls:[]},
  fields:{title:"Example book",author:"Author",description:"Text",isbn:"1234567890",
    price_cents:1500,quantity:1,book_id:"BOOK-42",condition:"Good"},
  missing:[],book_id_locked:false});
const html=node("#biblio-publish-fields").innerHTML;
assert.match(html,/class="biblio-review-main"/);
assert.match(html,/Check this listing/);
assert.match(html,/biblio-optional-book-fields/);
const boundary=html.indexOf('<details class="biblio-optional-book-fields">');
assert(boundary>0);
for(const id of ["title","author","description","isbn","price_cents","book_id",
                 "quantity","photos"]){
  const index=html.indexOf('data-field="'+id+'"');
  if(["quantity","photos"].includes(id))continue; // These are read-only.
  assert(index>=0&&index<boundary,id+" must be in the essential fields");
}
for(const id of ["publisher","edition","binding","language","pages","keywords"]){
  const index=html.indexOf('data-field="'+id+'"');
  assert(index>boundary,id+" should be optional");
}
assert.equal(node("#biblio-publish-submit").disabled,false);
assert.match(node("#biblio-publish-submit").textContent,/Publish to BIBLIO/);
console.log("BIBLIO essential listing fields and optional catalogue details: PASS");
