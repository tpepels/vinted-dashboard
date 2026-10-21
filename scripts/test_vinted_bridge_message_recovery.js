"use strict";
// Regression: a lost tab response must not strand manual Vinted updates or
// accidentally suppress the next automatic update for two hours.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("app/extension/background.js", "utf8");
const LOST = "A listener indicated an asynchronous response by returning true, but the message channel closed before a response was received";

async function scenario(mode, reason = "manual", lastAttempt = Date.now()) {
  const state = {bridgeToken:"paired-token",bridgeWorkspace:"Personal",
    syncStatus:lastAttempt>0?{ok:true}:{},
    lastSyncAttemptAt:lastAttempt,lastSuccessfulSyncAt:lastAttempt};
  const tab = {id:24,windowId:9,status:"complete",active:false,url:"https://www.vinted.pt/member/42"};
  let sentToDashboard = 0;
  let collections = 0;
  let injections = 0;
  let reloads = 0;
  let openedTabs = 0;
  const chrome = {
    runtime:{
      getManifest:()=>({content_scripts:[{matches:["https://www.vinted.pt/*"]}],version:"3.4.5"}),
      onInstalled:{addListener(){}},onStartup:{addListener(){}},
      onMessage:{addListener(){}},
    },
    storage:{local:{
      async get(keys){return Object.fromEntries((keys||[]).map(key=>[key,state[key]]));},
      async set(values){Object.assign(state,values);},
      async remove(keys){for(const k of keys)delete state[k];},
    }},
    tabs:{
      async query(){return [tab];},
      async get(id){assert.equal(id,tab.id);return tab;},
      async create(){openedTabs++;throw Error("Must not open a Vinted tab");},
      async reload(){reloads++;if(mode!=="old")throw Error("Unexpected Vinted tab reload");},
      async sendMessage(id,payload){
        assert.equal(id,tab.id);
        if(payload.type==="bridge-content-protocol"){
          if(mode==="missing" && !injections)throw Error("Could not establish connection. Receiving end does not exist.");
          return {protocol:mode==="old"&&!reloads?9:10};
        }
        if(payload.type==="collect-vinted-data"){
          collections++;
          if(["recover","periodic-lost"].includes(mode)&&collections===1)throw Error(LOST);
          if(mode==="double-lost")throw Error(LOST);
          if(mode==="limited")return {ok:false,rate_limited:true,error:"Vinted API rate limited the inventory sync."};
          return {ok:true,snapshot:{
            listings:[{id:"1",status:"active"}],
            orders:[],notifications:[],current_user:{id:"42"},
            age_scan_items:[],detail_sync:{pending:1,enriched:0},
          }};
        }
        throw Error("Unexpected message "+payload.type);
      },
    },
    scripting:{
      async executeScript({target,files}){
        assert.equal(target.tabId,tab.id);
        assert.deepEqual(Array.from(files),["vinted_age.js","content.js"]);
        injections++;
      },
    },
    windows:{async getAll(){return [{id:9,focused:true}];}},
    alarms:{create(){},async clear(){},onAlarm:{addListener(){}}},
  };
  const ctx={chrome,console,Date,URL,Promise,setTimeout,clearTimeout,
    fetch:async (url)=>{
      if(String(url).endsWith("/api/extension/browser-sync"))sentToDashboard++;
      return {ok:true,async json(){return {listings:1};}};
    }};
  vm.createContext(ctx);
  vm.runInContext(source,ctx,{filename:"background.js"});
  const result=await ctx.runSync(reason);
  return {result,state,collections,injections,sentToDashboard,reloads,openedTabs};
}

(async()=>{
  const manual=await scenario("normal","manual");
  assert.equal(manual.result.ok,true);
  assert.equal(manual.collections,1,"Manual request should bypass existing two-hour interval");
  assert.equal(manual.sentToDashboard,1);

  const background=await scenario("normal","periodic");
  assert.equal(background.result.sync_skipped_for_interval,true);
  assert.equal(background.collections,0,"Automatic refresh must retain two-hour interval");

  const healed=await scenario("recover","manual");
  assert.equal(healed.result.ok,true);
  assert.equal(healed.collections,2,"Only one manual retry on lost message channel");
  assert.equal(healed.sentToDashboard,1,"Never send a partial or duplicate snapshot");
  assert.equal(healed.injections,0,"Do not inject duplicate listeners into a working tab");
  assert.equal(healed.reloads,0);
  assert.equal(healed.openedTabs,0);

  const missing=await scenario("missing","manual");
  assert.equal(missing.result.ok,true);
  assert.equal(missing.injections,1,"Recover missing content listener without reloading page");

  const failed=await scenario("double-lost","manual");
  assert.equal(failed.result.ok,false);
  assert.match(failed.result.error,/Vinted tab stopped responding/);
  assert.doesNotMatch(failed.result.error,/A listener indicated an asynchronous response/);
  assert.equal(failed.collections,2);
  assert.equal(failed.sentToDashboard,0);
  assert.equal(failed.result.api_rate_limited,false,"Tab disconnections are not a Vinted rate limit");

  const deferred=await scenario("periodic-lost","periodic",0);
  assert.equal(deferred.result.ok,false);
  assert.equal(deferred.collections,1,"Do not perform repeated automatic Vinted API collections");
  assert.match(deferred.result.error,/automatic check/);

  const stale=await scenario("old","manual");
  assert.equal(stale.result.ok,true,"Manual sync should repair an old content script");
  assert.equal(stale.reloads,1,"Reload a stale Vinted tab exactly once on manual sync");
  assert.equal(stale.injections,0,"Do not create two content listeners on protocol mismatch");
  assert.equal(stale.sentToDashboard,1);
  const stalePeriodic=await scenario("old","periodic",0);
  assert.equal(stalePeriodic.result.ok,false,"Automatic sync must leave stale tabs alone");
  assert.equal(stalePeriodic.reloads,0);
  assert.match(stalePeriodic.result.error,/older extension script/);

  const limited=await scenario("limited","manual");
  assert.equal(limited.result.api_rate_limited,true);
  assert(limited.result.api_cooldown_until>Date.now()+23*60*60*1000,
    "Real Vinted API rate limits still create the existing cooldown");
  console.log("Vinted bridge message-channel recovery and manual interval bypass: ok");
})().catch(error=>{console.error(error);process.exitCode=1;});
