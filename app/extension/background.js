const API_ORIGIN="__API_ORIGIN__";
const VINTED_PATTERNS=chrome.runtime.getManifest().content_scripts[0].matches.slice();
const SYNC_ALARM="reseller-vinted-sync";
const AGE_JOB_ALARM="reseller-vinted-age-job";
const AGE_CACHE_KEY="vintedListingPageAgeCacheV2";
const AGE_FAILURES_KEY="vintedAgeScanFailuresV1";
const AGE_JOB_KEY="vintedAgeBurstJobV2";
const AGE_WORKERS=12;
const AGE_FAILURE_COOLDOWN_MS=24*60*60*1000;
const AGE_JOB_WATCHDOG_MS=90000;
const CONTENT_PROTOCOL=6;
let syncInFlight=null;
let ageJobInFlight=null;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function stored(){return await chrome.storage.local.get(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin",AGE_FAILURES_KEY,AGE_JOB_KEY])}
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

function uniqueAgeItems(items){
  const byId=new Map();
  for(const item of(Array.isArray(items)?items:[])){
    if(!item?.id||!item?.url)continue;
    byId.set(String(item.id),{id:String(item.id),url:String(item.url)});
  }
  return [...byId.values()];
}

async function eligibleAgeScanItems(items,reason){
  const clean=uniqueAgeItems(items);
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

async function updateAgeJobStatus(job,error=null){
  const data=await chrome.storage.local.get(["syncStatus"]);
  const previous=data?.syncStatus||{};
  const status={
    ...previous,
    age_scan_running:Boolean(job&&Array.isArray(job.remaining)&&job.remaining.length),
    age_scan_remaining:job?.remaining?.length||0,
    age_scan_scanned:Number(job?.scanned||0),
    age_scan_updated:Number(job?.updated||0),
    age_scan_failed:Number(job?.failed||0),
    age_scan_skipped:Number(job?.skipped||0),
    age_scan_at:new Date().toISOString(),
  };
  if(error)status.age_scan_error=String(error);
  else if(job&&!job.last_error)status.age_scan_error=null;
  await chrome.storage.local.set({syncStatus:status});
  return status;
}

async function closeAgeWorkerWindow(job){
  if(job?.window_id!=null){
    try{await chrome.windows.remove(job.window_id)}catch{}
  }
}

async function ensureAgeWorkerTabs(job,batch){
  const ids=Array.isArray(job.tab_ids)?job.tab_ids:[];
  if(job.window_id!=null&&ids.length>=batch.length){
    try{
      const tabs=await Promise.all(ids.slice(0,batch.length).map(id=>chrome.tabs.get(id)));
      if(tabs.every(tab=>tab&&tab.id!=null))return tabs;
    }catch{}
  }

  await closeAgeWorkerWindow(job);
  const workerWindow=await chrome.windows.create({
    url:batch.map(item=>item.url),
    focused:false,
    state:"minimized",
  });
  const tabs=Array.isArray(workerWindow?.tabs)?workerWindow.tabs:[];
  if(tabs.length!==batch.length){
    if(workerWindow?.id!=null){
      try{await chrome.windows.remove(workerWindow.id)}catch{}
    }
    throw new Error("Chrome did not create the expected Vinted worker tabs.");
  }
  job.window_id=workerWindow.id;
  job.tab_ids=tabs.map(tab=>tab.id);
  await chrome.storage.local.set({[AGE_JOB_KEY]:job});
  return tabs;
}

async function renderedUploadedAgeWave(job,batch){
  const tabs=await ensureAgeWorkerTabs(job,batch);
  const results={};
  await Promise.all(batch.map(async(item,index)=>{
    try{
      const age=await readRenderedAgeFromTab(tabs[index],item);
      if(age)results[String(item.id)]=age;
    }catch{}
  }));
  return results;
}

async function startAgeJob(items,reason){
  const eligible=await eligibleAgeScanItems(items,reason);
  const data=await chrome.storage.local.get([AGE_JOB_KEY]);
  const existing=data?.[AGE_JOB_KEY];
  if(existing&&Array.isArray(existing.remaining)&&existing.remaining.length){
    const merged=new Map(existing.remaining.map(item=>[String(item.id),item]));
    for(const item of eligible)merged.set(String(item.id),item);
    existing.remaining=[...merged.values()];
    if(reason==="manual")existing.reason="manual";
    await chrome.storage.local.set({[AGE_JOB_KEY]:existing});
    chrome.alarms.create(AGE_JOB_ALARM,{when:Date.now()+500});
    await updateAgeJobStatus(existing);
    return{started:false,remaining:existing.remaining.length,scanned:existing.scanned||0,updated:existing.updated||0,failed:existing.failed||0,skipped:existing.skipped||0};
  }
  if(!eligible.length){
    return{started:false,remaining:0,scanned:0,updated:0,failed:0,skipped:uniqueAgeItems(items).length};
  }

  const job={
    version:2,
    reason,
    remaining:eligible,
    scanned:0,
    updated:0,
    failed:0,
    skipped:Math.max(0,uniqueAgeItems(items).length-eligible.length),
    window_id:null,
    tab_ids:[],
    started_at:new Date().toISOString(),
    last_error:null,
  };
  await chrome.storage.local.set({[AGE_JOB_KEY]:job});
  chrome.alarms.create(AGE_JOB_ALARM,{when:Date.now()+500});
  await updateAgeJobStatus(job);
  return{started:true,remaining:job.remaining.length,scanned:0,updated:0,failed:0,skipped:job.skipped};
}

async function finishAgeJob(job){
  await closeAgeWorkerWindow(job);
  await chrome.storage.local.remove([AGE_JOB_KEY]);
  const finished={...job,remaining:[]};
  await updateAgeJobStatus(finished);
  return finished;
}

async function processAgeJobWave(){
  if(ageJobInFlight)return ageJobInFlight;
  ageJobInFlight=(async()=>{
    const data=await chrome.storage.local.get([AGE_JOB_KEY]);
    const job=data?.[AGE_JOB_KEY];
    if(!job||!Array.isArray(job.remaining)||!job.remaining.length){
      if(job)await finishAgeJob(job);
      return{ok:true,remaining:0};
    }

    // If Chrome terminates the service worker mid-wave, this watchdog wakes a
    // fresh worker and retries the same persisted batch instead of losing it.
    chrome.alarms.create(AGE_JOB_ALARM,{when:Date.now()+AGE_JOB_WATCHDOG_MS});
    const batch=job.remaining.slice(0,AGE_WORKERS);
    try{
      const ages=await renderedUploadedAgeWave(job,batch);
      await saveAgeBatchToCache(ages);
      const updated=await postAgeUpdates(ages);
      const failed=await updateAgeFailures(batch,ages);

      job.remaining=job.remaining.slice(batch.length);
      job.scanned=Number(job.scanned||0)+batch.length;
      job.updated=Number(job.updated||0)+updated;
      job.failed=Number(job.failed||0)+failed;
      job.last_error=null;
      await chrome.storage.local.set({[AGE_JOB_KEY]:job});
      await updateAgeJobStatus(job);

      if(job.remaining.length){
        chrome.alarms.create(AGE_JOB_ALARM,{when:Date.now()+750});
        return{ok:true,remaining:job.remaining.length,updated,failed};
      }
      await finishAgeJob(job);
      return{ok:true,remaining:0,updated,failed};
    }catch(error){
      job.last_error=error instanceof Error?error.message:String(error);
      await chrome.storage.local.set({[AGE_JOB_KEY]:job});
      await updateAgeJobStatus(job,job.last_error);
      chrome.alarms.create(AGE_JOB_ALARM,{when:Date.now()+30000});
      return{ok:false,remaining:job.remaining.length,error:job.last_error};
    }
  })();
  try{return await ageJobInFlight}finally{ageJobInFlight=null}
}

async function pair(code){const response=await fetch(API_ORIGIN+"/api/extension/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:String(code||"").trim().toUpperCase(),extension_version:chrome.runtime.getManifest().version,device_name:"Chrome"})});let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.detail||"Pairing failed");await chrome.storage.local.set({bridgeToken:body.token,bridgeWorkspace:body.workspace});return body}
async function unpair(){const data=await chrome.storage.local.get([AGE_JOB_KEY]);await closeAgeWorkerWindow(data?.[AGE_JOB_KEY]);await chrome.storage.local.remove(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin",AGE_CACHE_KEY,AGE_FAILURES_KEY,AGE_JOB_KEY])}
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
      const response=await message(tab.id,{type:"collect-vinted-data",reason});
      if(!response?.ok)throw new Error(response?.error||"Could not read Vinted data.");
      const snapshot=response.snapshot;
      const ageScanItems=Array.isArray(snapshot.age_scan_items)?snapshot.age_scan_items:[];
      const detailSync=snapshot.detail_sync||{};
      delete snapshot.age_scan_items;
      delete snapshot.detail_sync;
      snapshot.extension_version=chrome.runtime.getManifest().version;

      const result=await api("/api/extension/browser-sync",{method:"POST",body:JSON.stringify(snapshot)});
      const ageJob=await startAgeJob(ageScanItems,reason);
      const status={
        ok:true,
        at:new Date().toISOString(),
        reason,
        listings:result.listings||snapshot.listings.length,
        orders:snapshot.orders.length,
        detail_enriched:Number(detailSync.enriched||0),
        detail_deferred:Number(detailSync.deferred||0),
        detail_rate_limited:Boolean(detailSync.rate_limited),
        age_scan_running:ageJob.remaining>0,
        age_scan_remaining:ageJob.remaining,
        age_scan_scanned:ageJob.scanned,
        age_scan_updated:ageJob.updated,
        age_scan_failed:ageJob.failed,
        age_scan_skipped:ageJob.skipped,
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
function alarms(){
  chrome.alarms.create(SYNC_ALARM,{periodInMinutes:10});
  chrome.storage.local.get([AGE_JOB_KEY]).then(data=>{
    const job=data?.[AGE_JOB_KEY];
    if(job&&Array.isArray(job.remaining)&&job.remaining.length){
      chrome.alarms.create(AGE_JOB_ALARM,{when:Date.now()+1000});
    }
  }).catch(()=>{});
}
chrome.runtime.onInstalled.addListener(alarms);
chrome.runtime.onStartup.addListener(alarms);
chrome.alarms.onAlarm.addListener(async a=>{
  if(a.name===AGE_JOB_ALARM)return processAgeJobWave();
  if(a.name===SYNC_ALARM){
    const data=await chrome.storage.local.get([AGE_JOB_KEY]);
    const job=data?.[AGE_JOB_KEY];
    if(job&&Array.isArray(job.remaining)&&job.remaining.length)return;
    return runSync("periodic");
  }
});
chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{
  (async()=>{
    if(msg?.type==="pair")return sendResponse(await pair(msg.code));
    if(msg?.type==="unpair"){await unpair();return sendResponse({ok:true})}
    if(msg?.type==="sync-now")return sendResponse(await runSync("manual"));
    if(msg?.type==="status")return sendResponse(await connectionStatus());
    if(msg?.type==="age-job-step")return sendResponse(await processAgeJobWave());
    sendResponse({ok:false,error:"Unknown message"});
  })().catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));
  return true;
});
