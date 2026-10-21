"use strict";
// Manual updates can use existing tabs. Automatic updates always create and
// clean up their own background tab without disturbing the user's Vinted tab.
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const source=fs.readFileSync("app/extension/background.js","utf8");

async function scenario(mode,reason="manual",rememberedOrigin=null){
  const state={bridgeToken:"test-token",bridgeWorkspace:"Personal",
    syncStatus:{},lastSuccessfulSyncAt:0,vintedOrigin:rememberedOrigin};
  const tabs=[];
  if(mode==="existing"||mode==="loading"||mode==="stale"){
    tabs.push({id:10,windowId:1,active:false,
      status:mode==="loading"?"loading":"complete",
      url:"https://www.vinted.pt/member/42"});
  }
  const created=[],removed=[],reloaded=[];
  let getCalls=0,collections=0,posted=0;
  const chrome={
    runtime:{
      getManifest:()=>({content_scripts:[{matches:[
        "https://www.vinted.pt/*","https://www.vinted.nl/*",
      ]}],version:"3.4.8"}),
      onInstalled:{addListener(){}},onStartup:{addListener(){}},
      onMessage:{addListener(){}},
    },
    storage:{local:{
      async get(keys){return Object.fromEntries(keys.map(k=>[k,state[k]]));},
      async set(fields){Object.assign(state,fields);},
      async remove(keys){for(const k of keys)delete state[k];},
    }},
    tabs:{
      async query(){return tabs.slice();},
      async create(options){
        created.push(options);
        const tab={id:20,windowId:1,status:"complete",active:false,url:options.url};
        tabs.push(tab);
        return tab;
      },
      async get(id){
        getCalls++;
        const tab=tabs.find(t=>t.id===id);
        if(!tab)throw Error("Tab missing");
        if(mode==="loading"&&getCalls===1)tab.status="complete";
        return tab;
      },
      async remove(id){
        removed.push(id);
        const index=tabs.findIndex(t=>t.id===id);
        if(index!==-1)tabs.splice(index,1);
      },
      async reload(id){
        reloaded.push(id);
        assert.equal(id,10,"Only an existing stale tab may need reload");
      },
      async sendMessage(id,request){
        assert(tabs.some(t=>t.id===id),"Messages must target a real tab");
        if(request.type==="bridge-content-protocol"){
          return {protocol:mode==="stale"&&id===10&&!reloaded.length?9:10};
        }
        assert.equal(request.type,"collect-vinted-data");
        collections++;
        return {ok:true,snapshot:{
          listings:[{id:"123",status:"active"}],orders:[],notifications:[],
          current_user:{id:"42"},age_scan_items:[],
          detail_sync:{enriched:0,pending:0},
        }};
      },
    },
    windows:{async getAll(){return [{id:1,focused:true}];}},
    scripting:{async executeScript(){throw Error("Unexpected content script injection");}},
    alarms:{create(){},async clear(){},onAlarm:{addListener(){}}},
  };
  const ctx={chrome,Date,URL,Promise,console,setTimeout,clearTimeout,
    fetch:async url=>{
      if(String(url).endsWith("/api/extension/browser-sync"))posted++;
      return{ok:true,async json(){return{listings:1};}};
    },
  };
  vm.createContext(ctx);
  vm.runInContext(source,ctx,{filename:"background.js"});
  const result=await ctx.runSync(reason);
  return {result,created,removed,reloaded,collections,posted,tabs,getCalls,state};
}

(async()=>{
  const defaultOrigin=await scenario("absent");
  assert.equal(defaultOrigin.result.ok,true);
  assert.equal(defaultOrigin.created.length,1);
  assert.equal(defaultOrigin.created[0].url,"https://www.vinted.pt/");
  assert.equal(defaultOrigin.created[0].active,false);
  assert.deepEqual(defaultOrigin.removed,[20],
    "An extension-created tab should be removed after sync");
  assert.equal(defaultOrigin.collections,1);
  assert.equal(defaultOrigin.posted,1,"Send a complete snapshot once");

  const remembered=await scenario("absent","manual","https://www.vinted.nl");
  assert.equal(remembered.created[0].url,"https://www.vinted.nl/");
  assert.equal(remembered.result.ok,true);

  const untrusted=await scenario("absent","manual","https://www.vinted.pt.evil.test");
  assert.equal(untrusted.created[0].url,"https://www.vinted.pt/",
    "Stored origin must never redirect sync to an unapproved domain");

  const existing=await scenario("existing");
  assert.equal(existing.result.ok,true);
  assert.equal(existing.created.length,0);
  assert.equal(existing.removed.length,0);
  assert.equal(existing.reloaded.length,0);

  const loading=await scenario("loading");
  assert.equal(loading.result.ok,true);
  assert(loading.getCalls>0,"Wait for a loading Vinted tab");
  assert.equal(loading.created.length,0,"Do not duplicate an existing loading tab");

  const stale=await scenario("stale");
  assert.equal(stale.result.ok,true);
  assert.deepEqual(stale.reloaded,[10],"Repair a stale extension listener once");
  assert.equal(stale.posted,1);
  assert.equal(stale.created.length,0);

  const periodic=await scenario("absent","periodic");
  assert.equal(periodic.result.ok,true,
    "Automatic sync must succeed even when no Vinted tab is open");
  assert.equal(periodic.created.length,1);
  assert.equal(periodic.created[0].active,false);
  assert.deepEqual(periodic.removed,[20],"Automatically opened tab must be closed");
  assert.equal(periodic.posted,1);

  const periodicWithExisting=await scenario("existing","periodic");
  assert.equal(periodicWithExisting.result.ok,true);
  assert.equal(periodicWithExisting.created.length,1,
    "Scheduled sync must use its own background tab even when the user has Vinted open");
  assert.equal(periodicWithExisting.removed.length,1);
  assert.equal(periodicWithExisting.tabs.length,1,
    "Do not close the user's Vinted tab");

  const periodicWithStaleTab=await scenario("stale","periodic");
  assert.equal(periodicWithStaleTab.result.ok,true,
    "A stale user's content listener cannot block an unattended sync");
  assert.equal(periodicWithStaleTab.reloaded.length,0,
    "Do not reload the user's Vinted tab on automatic sync");
  assert.equal(periodicWithStaleTab.posted,1);
  assert.deepEqual(periodicWithStaleTab.removed,[20]);

  console.log("Vinted manual and unattended two-hour tab bootstrap: ok");
})().catch(error=>{console.error(error);process.exitCode=1;});
