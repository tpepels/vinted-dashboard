const fs = require("fs");
const vm = require("vm");
const path = require("path");
const assert = require("assert");
const source = fs.readFileSync(path.join(__dirname,"..","app","extension","background.js"),"utf8");

const state = {bridgeToken:"test-token",bridgeWorkspace:"Workspace",syncStatus:{}};
const alarms = [];
const tabsCreated = [];
const tabsReloaded = [];
const windowsRemoved = [];
let collectCount = 0;
let blocked = false;
let active = false;
let focused = true;
const tab = {id:4,windowId:9,status:"complete",url:"https://www.vinted.pt/member/123",active:false};

const chrome = {
  runtime:{
    getManifest:()=>({content_scripts:[{matches:["https://www.vinted.pt/*"]}],version:"3.4.2"}),
    onInstalled:{addListener(){}},onStartup:{addListener(){}},
    onMessage:{addListener(){}},
  },
  storage:{local:{
    async get(keys){return Object.fromEntries((keys||[]).map(key=>[key,state[key]]));},
    async set(obj){Object.assign(state,obj);},
    async remove(keys){for(const key of keys)delete state[key];},
  }},
  tabs:{
    async query(){return [tab];},
    async create(args){tabsCreated.push(args);throw Error("Extension must not create tabs");},
    async reload(id){tabsReloaded.push(id);throw Error("Extension must not reload user tabs");},
    async sendMessage(id,payload){
      if(payload.type==="bridge-content-protocol")return{protocol:10};
      if(payload.type==="collect-vinted-data"){
        collectCount++;
        if(blocked)return {ok:false,rate_limited:true,error:"Vinted API rate limited the inventory sync."};
        return{ok:true,snapshot:{
          listings:[{id:"1",status:"active"}],orders:[],notifications:[],
          current_user:{id:"42"},age_scan_items:[{id:"1",url:"https://www.vinted.pt/items/1"}],
          detail_sync:{enriched:0,pending:3},
        }};
      }
      throw Error("Unexpected page message");
    },
  },
  windows:{
    async getAll(){return[{id:9,focused}];},
    async remove(id){windowsRemoved.push(id);},
  },
  alarms:{
    create(name,options){alarms.push({name,options});},
    async clear(){return true;},
    onAlarm:{addListener(){}},
  },
  scripting:{async executeScript(){throw Error("No page scripts may be injected by a normal sync");}},
};

const context={
  console,chrome,Date,URL,
  setTimeout,clearTimeout,
  fetch:async url=>({ok:true,async json(){return{listings:1};}}),
};
vm.createContext(context);
vm.runInContext(source,context,{filename:"background.js"});

(async()=>{
  context.alarms();
  await Promise.resolve();await Promise.resolve();
  assert(alarms.some(a=>a.name==="reseller-vinted-sync"
    && a.options.periodInMinutes===120&&a.options.delayInMinutes===120),
    "Two-hour alarm must not fire immediately after browser startup");
  assert(!alarms.some(a=>a.name==="reseller-vinted-age-job"),
    "Old page-age worker must not reschedule itself");

  tab.active=false;
  const first=await context.runSync("manual");
  assert(first.ok&&collectCount===1,"First deliberate sync should succeed");
  assert(first.age_scan_remaining===0,"Page-age worker must be disabled");

  const second=await context.runSync("manual");
  assert(second.ok&&collectCount===2&&!second.sync_skipped_for_interval,
    "Manual refresh must bypass the two-hour automatic interval");

  state.lastSyncAttemptAt=Date.now()-2*60*60*1000-1000;
  tab.active=true;
  const busy=await context.runSync("periodic");
  assert(!busy.ok&&collectCount===2,
    "Automatic update must skip when the Vinted tab is focused and active");

  tab.active=false;
  state.lastSyncAttemptAt=Date.now()-2*60*60*1000-1000;
  blocked=true;
  const denial=await context.runSync("periodic");
  assert(denial.api_rate_limited&&denial.api_cooldown_until>Date.now()+23*60*60*1000,
    "A Vinted denial must trigger at least 23 hours remaining cooldown");
  const blockedAgain=await context.runSync("manual");
  assert(collectCount===3&&blockedAgain.sync_skipped_for_api_cooldown,
    "Cooldown must stop user-triggered requests as well");

  assert(tabsCreated.length===0,"No Vinted tabs should be created");
  assert(tabsReloaded.length===0,"No Vinted tabs should be reloaded");
  assert(windowsRemoved.length===0,"No worker windows should be necessary");
  console.log("Vinted quiet bridge: 120-minute automatic-only throttle, manual refresh, 24h rate-limit cooldown: ok");
})().catch(error=>{console.error(error);process.exitCode=1;});
