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
  const worker = {id:25,windowId:9,status:"complete",active:false,url:"https://www.vinted.pt/"};
  let workerOpen = false;
  let closedWorkers = 0;
  let sentToDashboard = 0;
  let collections = 0;
  let injections = 0;
  let reloads = 0;
  let openedTabs = 0;
  let onMessage = null;
  const chrome = {
    runtime:{
      getManifest:()=>({content_scripts:[{matches:["https://www.vinted.pt/*"]}],version:"3.4.15"}),
      onInstalled:{addListener(){}},onStartup:{addListener(){}},
      onMessage:{addListener(listener){onMessage=listener;}},
    },
    storage:{local:{
      async get(keys){return Object.fromEntries((keys||[]).map(key=>[key,state[key]]));},
      async set(values){Object.assign(state,values);},
      async remove(keys){for(const k of keys)delete state[k];},
    }},
    tabs:{
      async query(){return [tab];},
      async get(id){assert([tab.id,worker.id].includes(id));return id===tab.id?tab:worker;},
      async create(args){
        assert.equal(args.active,false,"Unattended checks should not steal focus");
        openedTabs++;
        workerOpen=true;
        return worker;
      },
      async remove(id){
        assert.equal(id,worker.id,"Never close the user tab");
        workerOpen=false;
        closedWorkers++;
      },
      async reload(){reloads++;if(mode!=="old")throw Error("Unexpected Vinted tab reload");},
      async sendMessage(id,payload){
        assert([tab.id,worker.id].includes(id));
        if(payload.type==="bridge-content-protocol"){
          if(mode==="missing" && !injections)throw Error("Could not establish connection. Receiving end does not exist.");
          return {protocol:mode==="old"&&id===tab.id&&!reloads?10:12};
        }
        if(payload.type==="collect-vinted-start"){
          collections++;
          return {ok:true,started:true};
        }
        if(payload.type==="collect-vinted-result"){
          if(mode==="reported401"||mode==="foreign401"){
            // The content script reports the API error, then Chrome navigates
            // its tab to an error page and drops the polling port.
            await new Promise(resolve=>onMessage({
              type:"bridge-diagnostic-event",
              stage:"collect.failed",
              detail:"Error: Vinted refused the inventory API request (HTTP 401) for /api/v2/users/current.\n    at fetchJson",
              level:"error",
            },{tab:{id:mode==="foreign401"?999:id,url:"https://www.vinted.pt/member/42"}},resolve));
            throw Error("Frame with ID 0 is showing error page");
          }
          if(["recover","periodic-lost","double-lost"].includes(mode))throw Error(LOST);
          if(mode==="limited")return {ok:false,rate_limited:true,error:"Vinted API rate limited the inventory sync."};
          return {ok:true,snapshot:{
            listings:[{id:"1",status:"active"}],
            orders:[],notifications:[],current_user:{id:"42"},
            age_scan_items:[],detail_sync:{pending:1,enriched:0},inventory_complete:true,
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
  return {result,state,collections,injections,sentToDashboard,reloads,openedTabs,closedWorkers,workerOpen};
}

(async()=>{
  const manual=await scenario("normal","manual");
  assert.equal(manual.result.ok,true);
  assert.equal(manual.collections,1,"Manual request should bypass existing two-hour interval");
  assert.equal(manual.sentToDashboard,1);

  const background=await scenario("normal","periodic");
  assert.equal(background.result.sync_skipped_for_interval,true);
  assert.equal(background.collections,0,"Automatic refresh must retain two-hour interval");

  const lost=await scenario("recover","manual");
  assert.equal(lost.result.ok,false,"Lost polling channel must fail without restarting");
  assert.equal(lost.collections,1,"A lost channel must not duplicate API collection");
  assert.equal(lost.sentToDashboard,0,"Never upload a partial inventory");
  assert.equal(lost.injections,0);
  assert.equal(lost.reloads,0);
  assert.equal(lost.openedTabs,0);

  const missing=await scenario("missing","manual");
  assert.equal(missing.result.ok,true);
  assert.equal(missing.injections,1,"Recover missing content listener before starting");

  const failed=await scenario("double-lost","manual");
  assert.equal(failed.result.ok,false);
  assert.match(failed.result.error,/collection connection was interrupted/);
  assert.equal(failed.collections,1,"Never restart a failed collection");
  assert.equal(failed.sentToDashboard,0);
  assert.equal(failed.result.api_rate_limited,false);
  const trace=(failed.state.bridgeDiagnosticLogV1||[]).join("\n");
  assert.match(trace,/collect.poll.channel.error/);
  assert.match(trace,/A listener indicated an asynchronous response/);

  const preserved=await scenario("reported401","periodic",0);
  assert.equal(preserved.result.ok,false);
  assert.match(preserved.result.error,/HTTP 401/);
  assert.match(preserved.result.error,/users\/current/);
  assert.doesNotMatch(preserved.result.error,/^Vinted collection connection was interrupted:/);
  assert.equal(preserved.sentToDashboard,0,"Never upload a partial inventory after a 401");
  assert.equal(preserved.collections,1,"A collection failure must not trigger another attempt");
  assert.equal(preserved.closedWorkers,1,"The temporary tab must be closed");

  const foreign=await scenario("foreign401","periodic",0);
  assert.doesNotMatch(foreign.result.error,/HTTP 401/,
    "Diagnostics from another tab must not contaminate active collection errors");
  assert.match(foreign.result.error,/collection connection was interrupted/);

  const deferred=await scenario("periodic-lost","periodic",0);
  assert.equal(deferred.result.ok,false);
  assert.equal(deferred.collections,1);
  assert.match(deferred.result.error,/collection connection was interrupted/);

  const stale=await scenario("old","manual");
  assert.equal(stale.result.ok,true,"Manual sync should repair an old content script");
  assert.equal(stale.reloads,1,"Reload a stale Vinted tab exactly once on manual sync");
  assert.equal(stale.injections,0,"Do not create two content listeners on protocol mismatch");
  assert.equal(stale.sentToDashboard,1);
  const stalePeriodic=await scenario("old","periodic",0);
  assert.equal(stalePeriodic.result.ok,true,
    "Automatic sync should use a fresh tab rather than the stale user tab");
  assert.equal(stalePeriodic.reloads,0);
  assert.equal(stalePeriodic.openedTabs,1);
  assert.equal(stalePeriodic.closedWorkers,1);

  const limited=await scenario("limited","manual");
  assert.equal(limited.result.api_rate_limited,true);
  assert(limited.result.api_cooldown_until>Date.now()+23*60*60*1000,
    "Real Vinted API rate limits still create the existing cooldown");
  console.log("Vinted bridge message-channel recovery and manual interval bypass: ok");
})().catch(error=>{console.error(error);process.exitCode=1;});
