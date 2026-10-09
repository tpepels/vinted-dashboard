"use strict";
/* Test the real connection-health classifier and UI actions without network. */
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const js = fs.readFileSync("app/product_static/app.js", "utf8");
const css = fs.readFileSync("app/product_static/styles.css", "utf8");
const html = fs.readFileSync("app/product_static/index.html", "utf8");
const start = js.indexOf("function connectorHealth(connector) {");
const end = js.indexOf("async function connections() {", start);
assert(start > 0 && end > start, "Real health logic should be reusable for every account");
const calls=[], flashes=[], status={textContent:""};
const state = {connectorChannel:"shopify"};
const context = {
  state, Date, Number, String, Boolean,
  connectorSchemas:{shopify:{title:"Shopify",test:true},biblio:{title:"BIBLIO"}},
  api:async (url, options) => {
    calls.push({url, options});
    return {ok:true, status:"passed",scope:"read_only"};
  },
  $:selector => selector === "#connector-config-status" ? status : null,
  flash:value => flashes.push(value),
  connections:async()=>{},
};
vm.createContext(context);
vm.runInContext(js.slice(start,end),context);
const get=payload=>context.connectorHealth(payload);
assert.equal(get({channel:"shopify",configured:false}).state,"setup");
assert.equal(get({channel:"shopify",configured:true}).state,"unverified");
assert.equal(get({channel:"shopify",configured:true,operational:true,
  connection_check:{status:"passed",checked_at:new Date().toISOString()}}).state,"verified");
assert.equal(get({channel:"shopify",configured:true,connection_check:{
  status:"passed",checked_at:"2024-01-01T00:00:00Z"}}).state,"unverified",
  "Old credentials tests may no longer prove current read access");
assert.equal(get({channel:"shopify",configured:true,authorization_required:true}).state,"attention");
assert.equal(get({channel:"shopify",configured:true,connection_check:{status:"failed"}}).state,"attention");
assert.equal(get({channel:"shopify",configured:true,last_run:{status:"failed"},
  last_synced_at:new Date().toISOString()}).state,"attention",
  "An earlier successful import cannot hide the last failed attempt");
assert.equal(get({channel:"shopify",configured:true,last_run:{status:"running"}}).state,"busy");
assert.equal(get({channel:"biblio",configured:true,operational:true,
  connection_check:{status:"passed",scope:"ftp_login",checked_at:new Date().toISOString()}}).label,
  "FTP login checked");
assert.match(get({channel:"shopify",configured:true}).next,/Test read access/);
const button={disabled:false};
(async()=>{
  const result=await context.testMarketplaceConnection("shopify",button);
  assert(result?.ok);
  assert.equal(button.disabled,false);
  assert.equal(calls[0].url,"/api/app/connectors/shopify/test-connection");
  assert.equal(calls[0].options.method,"POST");
  assert.match(status.textContent,/stock edits were not tested/);
  assert(flashes.length>0);
  for (const part of [
    "id=\"connection-health-summary\"",
    "Test read access",
    "Save account details",
    "Import listings",
  ]) assert(html.includes(part),"Missing workflow guidance: "+part);
  assert(js.includes('button.onclick = () => testMarketplaceConnection(button.dataset.c, button)'),
    "Account card must offer a working read test");
  assert(js.includes('window.confirm("Remove saved "'),
    "Credential deletion must explain consequences and require confirmation");
  assert(css.includes(".connection-state.unverified") &&
    css.includes(".connection-state.verified") &&
    css.includes(".connection-state.attention"),
  "Ready, untested and failed account statuses need distinct visual treatments");
  console.log("Marketplace read-access UX, status evidence and credential-removal guard: PASS");
})().catch(error=>{console.error(error); process.exitCode=1});
