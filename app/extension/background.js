const API_ORIGIN="__API_ORIGIN__";
const VINTED_PATTERNS=chrome.runtime.getManifest().content_scripts[0].matches.slice();
const SYNC_ALARM="reseller-vinted-sync";
const AGE_CACHE_KEY="vintedListingPageAgeCacheV2";
const AGE_FAILURES_KEY="vintedAgeScanFailuresV1";
const AGE_WORKERS=16;
const AGE_FAILURE_COOLDOWN_MS=24*60*60*1000;
const CONTENT_PROTOCOL=5;
let syncInFlight=null;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function stored(){return await chrome.storage.local.get(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin",AGE_FAILURES_KEY])}
async function authHeaders(){const data=await stored();if(!data.bridgeToken)throw new Error("Pair this extension with your dashboard first.");return{"Authorization":"Bearer "+data.bridgeToken,"Content-Type":"application/json"}}
async function api(path,options={}){const headers=Object.assign({},await authHeaders(),options.headers||{});const response=await fetch(API_ORIGIN+path,Object.assign({},options,{headers}));let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.detail||("Dashboard returned HTTP "+response.status));return body}
async function waitForTab(tabId,timeout=20000){const start=Date.now();while(Date.now()-start<timeout){const tab=await chrome.tabs.get(tabId);if(tab.status==="complete")return tab;await sleep(300)}throw new Error("Vinted tab did not finish loading.")}
async function message(tabId,payload){try{return await chrome.tabs.sendMessage(tabId,payload)}catch(error){if(!String(error?.message||error).includes("Receiving end does not exist"))throw error;await chrome.scripting.executeScript({target:{tabId},files:["vinted_age.js","content.js"]});await sleep(250);return await chrome.tabs.sendMessage(tabId,payload)}}
async function vintedTab(){const tabs=await chrome.tabs.query({url:VINTED_PATTERNS});if(tabs.length)return{tab:tabs[0],temporary:false};const data=await stored();if(!data.vintedOrigin)throw new Error("Open your signed-in Vinted website once, then press Sync now.");const tab=await chrome.tabs.create({url:data.vintedOrigin+"/",active:false});await waitForTab(tab.id);return{tab,temporary:true}}
async function ensureCurrentContentScript(tab){
  let protocol=null;
  try{
    const result=await chrome.tabs.sendMessage(tab.id,{type:"bridge-content-protocol"});
    protocol=Number(result?.protocol);
  }catch{}
  if(protocol===CONTENT_PROTOCOL)return await chrome.tabs.get(tab.id);
  await chrome.tabs.reload(tab.id);
  const reloaded=await waitForTab(tab.id,30000);
  const result=await message(tab.id,{type:"bridge-content-protocol"});
  if(Number(result?.protocol)!==CONTENT_PROTOCOL){
    throw new Error("Vinted tab did not load the current bridge content script.");
  }
  return reloaded;
}
async function readRenderedAgeFromTab(tab,item){
  const target=new URL(String(item.url||""));
  if(!target.hostname.startsWith("www.vinted."))return null;
  const current=await chrome.tabs.get(tab.id);
  if(String(current.url||"")!==target.href){
    await chrome.tabs.update(tab.id,{url:target.href,active:false});
  }
  await waitForTab(tab.id,30000);
  const started=Date.now();
  while(Date.now()-started<12000){
    const result=await message(tab.id,{type:"read-vinted-uploaded-age",item_id:String(item.id)});
    if(result?.ok&&result.age)return result.age;
    await sleep(350);
  }
  return null;
}

async function renderedUploadedAgesBurst(items,workerCount=AGE_WORKERS){
  const queue=(Array.isArray(items)?items:[]).filter(item=>item?.id&&item?.url);
  if(!queue.length)return{};
  const workers=Math.max(1,Math.min(Number(workerCount)||1,queue.length));
  const initial=queue.slice(0,workers);
  let cursor=initial.length;
  const results={};
  let workerWindow=null;

  try{
    workerWindow=await chrome.windows.create({
      url:initial.map(item=>item.url),
      focused:false,
      state:"minimized",
    });
    const tabs=Array.isArray(workerWindow?.tabs)?workerWindow.tabs:[];
    if(tabs.length!==initial.length){
      throw new Error("Chrome did not create the expected Vinted worker tabs.");
    }

    async function worker(tab,item){
      let current=item;
      while(current){
        try{
          const age=await readRenderedAgeFromTab(tab,current);
          if(age)results[String(current.id)]=age;
        }catch{}
        current=cursor<queue.length?queue[cursor++]:null;
      }
    }
    await Promise.all(tabs.map((tab,index)=>worker(tab,initial[index])));
    return results;
  }finally{
    if(workerWindow?.id!=null){
      try{await chrome.windows.remove(workerWindow.id)}catch{}
    }
  }
}

async function saveAgeBatchToCache(ages){
  const data=await chrome.storage.local.get([AGE_CACHE_KEY]);
  const cache=(data&&typeof data[AGE_CACHE_KEY]==="object"&&data[AGE_CACHE_KEY])||{};
  const observed=Date.now()/1000;
  for(const[id,age]of Object.entries(ages||{})){
    if(!age||age.seconds==null)continue;
    cache[String(id)]={
      listed_age_seconds:Number(age.seconds),
      listed_age_text:age.text||null,
      age_observed_at:observed,
    };
  }
  await chrome.storage.local.set({[AGE_CACHE_KEY]:cache});
}

async function eligibleAgeScanItems(items,reason){
  const clean=(Array.isArray(items)?items:[]).filter(item=>item?.id&&item?.url);
  if(reason==="manual")return clean;
  const data=await chrome.storage.local.get([AGE_FAILURES_KEY]);
  const failures=(data&&typeof data[AGE_FAILURES_KEY]==="object"&&data[AGE_FAILURES_KEY])||{};
  const now=Date.now();
  return clean.filter(item=>{
    const failedAt=Number(failures[String(item.id)]?.failed_at||0);
    return !failedAt||now-failedAt>=AGE_FAILURE_COOLDOWN_MS;
  });
}

async function updateAgeFailures(scanned,ages){
  const data=await chrome.storage.local.get([AGE_FAILURES_KEY]);
  const failures=(data&&typeof data[AGE_FAILURES_KEY]==="object"&&data[AGE_FAILURES_KEY])||{};
  const now=Date.now();
  for(const item of scanned){
    const id=String(item.id);
    if(ages[id])delete failures[id];
    else failures[id]={failed_at:now};
  }
  await chrome.storage.local.set({[AGE_FAILURES_KEY]:failures});
  return scanned.filter(item=>!ages[String(item.id)]).length;
}

async function postAgeUpdates(ages){
  const rows=Object.entries(ages||{}).map(([external_id,age])=>({
    external_id,
    listed_age_seconds:Math.max(0,Math.round(Number(age.seconds)||0)),
    listed_age_text:age.text||null,
    observed_at:Date.now()/1000,
  }));
  let updated=0;
  for(let index=0;index<rows.length;index+=100){
    const result=await api("/api/extension/listing-ages",{
      method:"POST",
      body:JSON.stringify({ages:rows.slice(index,index+100)}),
    });
    updated+=Number(result.updated||0);
  }
  return updated;
}

async function runAgeBurst(items,reason){
  const eligible=await eligibleAgeScanItems(items,reason);
  if(!eligible.length){
    return{scanned:0,updated:0,failed:0,skipped:(Array.isArray(items)?items.length:0)};
  }
  const data=await chrome.storage.local.get(["syncStatus"]);
  await chrome.storage.local.set({
    syncStatus:{
      ...(data?.syncStatus||{}),
      age_scan_running:true,
      age_scan_remaining:eligible.length,
      age_scan_at:new Date().toISOString(),
    },
  });

  const ages=await renderedUploadedAgesBurst(eligible,AGE_WORKERS);
  await saveAgeBatchToCache(ages);
  const updated=await postAgeUpdates(ages);
  const failed=await updateAgeFailures(eligible,ages);
  return{
    scanned:eligible.length,
    updated,
    failed,
    skipped:Math.max(0,(Array.isArray(items)?items.length:0)-eligible.length),
  };
}

async function pair(code){const response=await fetch(API_ORIGIN+"/api/extension/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:String(code||"").trim().toUpperCase(),extension_version:chrome.runtime.getManifest().version,device_name:"Chrome"})});let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.detail||"Pairing failed");await chrome.storage.local.set({bridgeToken:body.token,bridgeWorkspace:body.workspace});return body}
async function unpair(){await chrome.storage.local.remove(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin",AGE_CACHE_KEY,AGE_FAILURES_KEY])}
async function connectionStatus(){const data=await stored();if(!data.bridgeToken)return{paired:false,status:data.syncStatus||null,apiOrigin:API_ORIGIN};try{const remote=await api("/api/extension/status");return{paired:true,workspace:data.bridgeWorkspace,remote,status:data.syncStatus||null,apiOrigin:API_ORIGIN}}catch(error){return{paired:true,workspace:data.bridgeWorkspace,status:data.syncStatus||null,error:error instanceof Error?error.message:String(error),apiOrigin:API_ORIGIN}}}
async function runSync(reason="manual"){
  if(syncInFlight)return syncInFlight;
  syncInFlight=(async()=>{
    let temporary=null;
    try{
      const found=await vintedTab();
      temporary=found.temporary?found.tab.id:null;
      let tab=await waitForTab(found.tab.id);
      tab=await ensureCurrentContentScript(tab);
      const tabUrl=new URL(String(tab.url||""));
      if(!tabUrl.hostname.startsWith("www.vinted."))throw new Error("Open and sign in to Vinted first.");
      await chrome.storage.local.set({vintedOrigin:tabUrl.origin});
      const response=await message(tab.id,{type:"collect-vinted-data"});
      if(!response?.ok)throw new Error(response?.error||"Could not read Vinted data.");
      const snapshot=response.snapshot;
      const ageScanItems=Array.isArray(snapshot.age_scan_items)?snapshot.age_scan_items:[];
      delete snapshot.age_scan_items;
      snapshot.extension_version=chrome.runtime.getManifest().version;

      const result=await api("/api/extension/browser-sync",{method:"POST",body:JSON.stringify(snapshot)});
      const ageBurst=await runAgeBurst(ageScanItems,reason);
      const status={
        ok:true,
        at:new Date().toISOString(),
        reason,
        listings:result.listings||snapshot.listings.length,
        orders:snapshot.orders.length,
        age_scan_running:false,
        age_scan_remaining:0,
        age_scan_scanned:ageBurst.scanned,
        age_scan_updated:ageBurst.updated,
        age_scan_failed:ageBurst.failed,
        age_scan_skipped:ageBurst.skipped,
      };
      await chrome.storage.local.set({syncStatus:status});
      return status;
    }catch(error){
      const status={ok:false,at:new Date().toISOString(),reason,error:error instanceof Error?error.message:String(error)};
      await chrome.storage.local.set({syncStatus:status});
      return status;
    }finally{
      if(temporary!==null){try{await chrome.tabs.remove(temporary)}catch{}}
      syncInFlight=null;
    }
  })();
  return syncInFlight;
}
function alarms(){chrome.alarms.create(SYNC_ALARM,{periodInMinutes:10})}
chrome.runtime.onInstalled.addListener(alarms);
chrome.runtime.onStartup.addListener(alarms);
chrome.alarms.onAlarm.addListener(a=>{if(a.name===SYNC_ALARM)runSync("periodic")});
chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{
  (async()=>{
    if(msg?.type==="pair")return sendResponse(await pair(msg.code));
    if(msg?.type==="unpair"){await unpair();return sendResponse({ok:true})}
    if(msg?.type==="sync-now")return sendResponse(await runSync("manual"));
    if(msg?.type==="status")return sendResponse(await connectionStatus());
    sendResponse({ok:false,error:"Unknown message"});
  })().catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));
  return true;
});
