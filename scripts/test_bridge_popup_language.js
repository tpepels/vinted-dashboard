"use strict";

// The popup must never show a raw API backlog as if it were a failed upload.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync("app/extension/popup.html", "utf8");
const js = fs.readFileSync("app/extension/popup.js", "utf8");
assert.match(html, /id="sync-details"/);
assert.match(html, /How automatic updates work/);
assert.doesNotMatch(html, /API details pending/);

function element() {
  const classes = new Set();
  return {
    textContent: "", className: "", disabled: false, onclick: null,
    classList: {
      add: name => classes.add(name),
      remove: name => classes.delete(name),
      contains: name => classes.has(name),
      toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
    },
  };
}
const elements = Object.fromEntries([
  "#status", "#bridge-version", "#sync-details", "#detail-info",
  "#pair", "#paired", "#sync", "#pair-btn", "#code", "#dashboard", "#unpair",
].map(id => [id, element()]));
let response = {
  paired:true, workspace:"Personal Workspace",
  status: {
    ok:true,at:"2026-10-12T10:28:20Z",listings:280,
    detail_pending:280,detail_enriched:0,
  },
};
const ctx = {
  document: {querySelector: key => elements[key]},
  chrome: {
    runtime: {
      getManifest: () => ({version:"3.4.5"}),
      sendMessage: async () => response,
    },
    tabs: {create() {}},
  },
  Date, Number, String,
};
vm.createContext(ctx);

(async () => {
  vm.runInContext(js, ctx);
  await new Promise(resolve => setImmediate(resolve));
  assert.match(elements["#status"].textContent, /280 Vinted listings synced/);
  assert.match(elements["#status"].textContent, /Last checked/);
  assert.doesNotMatch(elements["#status"].textContent, /API details pending|deferred/);
  assert.match(elements["#detail-info"].textContent, /280 listings still need optional details/);
  assert.match(elements["#detail-info"].textContent, /basic listing update has already been sent/);
  assert.equal(elements["#sync-details"].classList.contains("hidden"), false);
  assert.equal(elements["#bridge-version"].textContent, "Chrome Bridge v3.4.5");
  response = {paired:true,workspace:"Personal Workspace",status:{
    ok:true,at:"2026-10-12T10:28:20Z",listings:280,detail_pending:0,
  }};
  await ctx.refresh();
  assert.equal(elements["#sync-details"].classList.contains("hidden"), true);
  response = {paired:true,workspace:"Personal Workspace",status:{
    ok:true,at:"2026-10-12T10:28:20Z",listings:280,detail_pending:0,
    api_cooldown_until:Date.now()+60*60*1000,
  }};
  await ctx.refresh();
  assert.match(elements["#status"].textContent, /Automatic checks paused until/);
  assert.match(elements["#detail-info"].textContent, /Manual checks remain available/);
  response = {paired:true,vinted_tab_open:false,workspace:"Personal Workspace",status:{
    ok:false,error:"Vinted denied the signed-in browser API request (HTTP 403)",at:"2026-10-12T10:28:20Z",
  }};
  await ctx.refresh();
  assert.match(elements["#status"].textContent,/Open Vinted in a browser tab/);
  assert.doesNotMatch(elements["#status"].textContent,/Last update failed|HTTP 403/);
  response = {paired:false};
  await ctx.refresh();
  assert.match(elements["#status"].textContent, /Not connected/);
  console.log("Chrome Bridge clear-language popup test passed");
})().catch(error => {console.error(error);process.exitCode = 1;});
