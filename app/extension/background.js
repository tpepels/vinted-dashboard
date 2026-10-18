const API_ORIGIN="__API_ORIGIN__";
const VINTED_PATTERNS=chrome.runtime.getManifest().content_scripts[0].matches.slice();
const SYNC_ALARM="reseller-vinted-sync";
const SYNC_INTERVAL_MS=2*60*60*1000;
const VINTED_ACCESS_COOLDOWN_MS=24*60*60*1000;
const AGE_JOB_ALARM="reseller-vinted-age-job";
const AGE_CACHE_KEY="vintedListingPageAgeCacheV2";
const AGE_FAILURES_KEY="vintedAgeScanFailuresV1";
const AGE_JOB_KEY="vintedAgeBurstJobV2";
const AGE_WORKERS=4;
const AGE_WAVES_PER_EVENT=8;
const AGE_NAVIGATION_MIN_INTERVAL_MS=1800;
const AGE_RATE_LIMIT_BASE_COOLDOWN_MS=30*60*1000;
const AGE_RATE_LIMIT_MAX_COOLDOWN_MS=6*60*60*1000;
const API_RATE_LIMIT_COOLDOWN_MS=60*60*1000;
const AGE_FAILURE_COOLDOWN_MS=24*60*60*1000;
const AGE_JOB_WATCHDOG_MS=240000;
const CONTENT_PROTOCOL=10;
const LOST_MESSAGE_CHANNEL=/A listener indicated an asynchronous response|message (?:port|channel) closed|The message port closed before a response was received/i;
const MISSING_MESSAGE_RECEIVER=/Receiving end does not exist|Could not establish connection/i;
let syncInFlight=null;
let ageJobInFlight=null;
let lastAgeNavigationAt=0;
let ageNavigationChain=Promise.resolve();
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function stored(){return await chrome.storage.local.get(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin",AGE_FAILURES_KEY,AGE_JOB_KEY])}
async function authHeaders(){const data=await stored();if(!data.bridgeToken)throw new Error("Pair this extension with your dashboard first.");return{"Authorization":"Bearer "+data.bridgeToken,"Content-Type":"application/json"}}
async function api(path,options={}){const headers=Object.assign({},await authHeaders(),options.headers||{});const response=await fetch(API_ORIGIN+path,Object.assign({},options,{headers}));let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.detail||("Dashboard returned HTTP "+response.status));return body}
async function waitForTab(tabId,timeout=20000){const start=Date.now();while(Date.now()-start<timeout){const tab=await chrome.tabs.get(tabId);if(tab.status==="complete")return tab;await sleep(300)}throw new Error("Vinted tab did not finish loading.")}
async function message(tabId,payload){try{return await chrome.tabs.sendMessage(tabId,payload)}catch(error){if(!String(error?.message||error).includes("Receiving end does not exist"))throw error;await chrome.scripting.executeScript({target:{tabId},files:["vinted_age.js","content.js"]});await sleep(250);return await chrome.tabs.sendMessage(tabId,payload)}}
// Quiet mode: never open a tab or reload a user's Vinted page for a sync.
async function vintedTab(reason="periodic"){
  const tabs=await chrome.tabs.query({url:VINTED_PATTERNS});
  const complete=tabs.filter(tab=>tab?.id!=null&&tab.status==="complete");
  if(!complete.length)throw new Error("Open Vinted in a browser tab when you want to sync. The extension does not open or reload Vinted automatically.");
  if(reason==="periodic"){
    // Do not run background API requests while someone is using Vinted.
    const windows=await chrome.windows.getAll({populate:false});
    const focusedIds=new Set(windows.filter(win=>win.focused).map(win=>win.id));
    const inactive=complete.find(tab=>!tab.active||!focusedIds.has(tab.windowId));
    if(!inactive)throw new Error("Vinted is in use; automatic sync postponed.");
    return{tab:inactive,temporary:false};
  }
  return{tab:complete.find(tab=>tab.active)||complete[0],temporary:false};
}
async function ensureCurrentContentScript(tab){
  let response=null;
  try{
    response=await chrome.tabs.sendMessage(tab.id,{type:"bridge-content-protocol"});
  }catch(error){
    if(!MISSING_MESSAGE_RECEIVER.test(String(error?.message||error))){
      throw new Error("The Vinted tab's bridge could not respond. Keep the tab open, reload it, then try updating again.");
    }
    // The page has no listener (for example, a tab opened while the extension
    // was being upgraded). Inject without reloading or navigating the page.
    // Never inject on a protocol mismatch: that would create two listeners.
    await chrome.scripting.executeScript({
      target:{tabId:tab.id},files:["vinted_age.js","content.js"],
    });
    response=await chrome.tabs.sendMessage(tab.id,{type:"bridge-content-protocol"});
  }
  if(Number(response?.protocol)===CONTENT_PROTOCOL)return tab;
  throw new Error("Vinted tab is using an older extension script. Reload that tab once and try again.");
}
async function collectFromVintedTab(tab,reason){
  const payload={type:"collect-vinted-data",reason};
  try{
    return await message(tab.id,payload);
  }catch(error){
    if(!LOST_MESSAGE_CHANNEL.test(String(error?.message||error)))throw error;
    // Chrome discards pending content-script replies when a tab navigates or
    // reloads. The dashboard was never sent this snapshot, so one bounded
    // manual retry is safe. Periodic checks wait for their next alarm.
    if(reason!=="manual"){
      throw new Error("Vinted changed or closed its tab during the automatic check. Next automatic check will retry; you can also check manually.");
    }
    await sleep(500);
    let ready;
    try{
      ready=await waitForTab(tab.id,15000);
      const url=new URL(String(ready.url||""));
      if(!url.hostname.startsWith("www.vinted."))throw Error("Vinted tab was replaced");
      await ensureCurrentContentScript(ready);
    }catch{
      throw new Error("Vinted tab lost its connection during the update. Open Vinted, keep that tab open, and try Check now again.");
    }
    try{
      return await message(ready.id,payload);
    }catch(retryError){
      if(!LOST_MESSAGE_CHANNEL.test(String(retryError?.message||retryError)))throw retryError;
      throw new Error("Vinted tab stopped responding during the update. Keep the tab open without navigating, reload it once, and try Check now again.");
    }
  }
}
async function stopLegacyAgeScan(){
  // Older versions may have stored an aggressive multi-tab scan. Cancel it
  // permanently, including any already-open minimized worker window.
  try{
    const data=await chrome.storage.local.get([AGE_JOB_KEY]);
    const job=data?.[AGE_JOB_KEY];
    if(job?.window_id!=null)await closeAgeWorkerWindow(job);
    await chrome.storage.local.remove([AGE_JOB_KEY]);
    await chrome.alarms.clear(AGE_JOB_ALARM);
  }catch{}
}
async function waitForAgeNavigationSlot(){
  const task=ageNavigationChain.then(async()=>{
    const delay=AGE_NAVIGATION_MIN_INTERVAL_MS-(Date.now()-lastAgeNavigationAt);
    if(delay>0)await sleep(delay);
    lastAgeNavigationAt=Date.now();
  });
  ageNavigationChain=task.catch(()=>{});
  return task;
}

async function readRenderedAgeFromTab(tab,item){
  const target=new URL(String(item.url||""));
  if(!target.hostname.startsWith("www.vinted."))return{age:null,rate_limited:false,challenged:false};
  const current=await chrome.tabs.get(tab.id);
  if(String(current.url||"")!==target.href){
    await waitForAgeNavigationSlot();
    await chrome.tabs.update(tab.id,{url:target.href,active:false});
  }
  await waitForTab(tab.id,30000);
  const started=Date.now();
  while(Date.now()-started<12000){
    const result=await message(tab.id,{type:"read-vinted-uploaded-age",item_id:String(item.id)});
    if(result?.rate_limited||result?.challenged){
      return{
        age:null,
        rate_limited:Boolean(result?.rate_limited),
        challenged:Boolean(result?.challenged),
      };
    }
    if(result?.ok&&result.age)return{age:result.age,rate_limited:false,challenged:false};
    await sleep(350);
  }
  return{age:null,rate_limited:false,challenged:false};
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
    age_scan_cooldown_until:Number(job?.cooldown_until||0),
    age_scan_rate_limit_hits:Number(job?.rate_limit_hits||0),
    page_cooldown_until:Number(job?.cooldown_until||0),
    vinted_cooldown_until:0,
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
      if(tabs.every(tab=>tab&&tab.id!=null&&tab.windowId===job.window_id))return tabs;
    }catch{}
  }

  await closeAgeWorkerWindow(job);
  const workerWindow=await chrome.windows.create({
    url:Array.from({length:batch.length},()=>"about:blank"),
    focused:false,
    state:"minimized",
  });
  let tabs=Array.isArray(workerWindow?.tabs)?workerWindow.tabs:[];
  if(tabs.length!==batch.length&&workerWindow?.id!=null){
    try{tabs=await chrome.tabs.query({windowId:workerWindow.id})}catch{}
  }
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
  let rateLimited=false,challenged=false;
  await Promise.all(batch.map(async(item,index)=>{
    try{
      const result=await readRenderedAgeFromTab(tabs[index],item);
      if(result?.age)results[String(item.id)]=result.age;
      if(result?.rate_limited)rateLimited=true;
      if(result?.challenged)challenged=true;
    }catch{}
  }));
  return{ages:results,rate_limited:rateLimited,challenged};
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
    rate_limit_hits:0,
    cooldown_until:0,
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
    let job=data?.[AGE_JOB_KEY];
    if(!job||!Array.isArray(job.remaining)||!job.remaining.length){
      if(job)await finishAgeJob(job);
      return{ok:true,remaining:0};
    }

    const now=Date.now();
    const cooldownUntil=Number(job.cooldown_until||0);
    if(cooldownUntil>now){
      await closeAgeWorkerWindow(job);
      job.window_id=null;
      job.tab_ids=[];
      await chrome.storage.local.set({[AGE_JOB_KEY]:job});
      await updateAgeJobStatus(job,"Vinted website rate limit detected; age scan is cooling down.");
      chrome.alarms.create(AGE_JOB_ALARM,{when:cooldownUntil});
      return{ok:true,paused:true,remaining:job.remaining.length,cooldown_until:cooldownUntil};
    }

    chrome.alarms.create(AGE_JOB_ALARM,{when:Date.now()+AGE_JOB_WATCHDOG_MS});
    let eventUpdated=0,eventFailed=0;
    try{
      for(let wave=0;wave<AGE_WAVES_PER_EVENT&&job.remaining.length;wave++){
        const adaptiveWorkers=Math.max(
          1,
          AGE_WORKERS-Math.min(AGE_WORKERS-1,Number(job.rate_limit_hits||0)),
        );
        const batch=job.remaining.slice(0,adaptiveWorkers);
        const waveResult=await renderedUploadedAgeWave(job,batch);
        const ages=waveResult.ages||{};
        await saveAgeBatchToCache(ages);
        const updated=await postAgeUpdates(ages);

        if(waveResult.rate_limited||waveResult.challenged){
          const succeeded=new Set(Object.keys(ages));
          const keepBatch=batch.filter(item=>!succeeded.has(String(item.id)));
          job.remaining=[...keepBatch,...job.remaining.slice(batch.length)];
          job.scanned=Number(job.scanned||0)+succeeded.size;
          job.updated=Number(job.updated||0)+updated;
          job.rate_limit_hits=Number(job.rate_limit_hits||0)+1;
          const cooldown=Math.min(
            AGE_RATE_LIMIT_MAX_COOLDOWN_MS,
            AGE_RATE_LIMIT_BASE_COOLDOWN_MS*(2**Math.max(0,job.rate_limit_hits-1)),
          );
          job.cooldown_until=Date.now()+cooldown;
          job.last_error=waveResult.rate_limited
            ?"Vinted website rate limit detected"
            :"Vinted anti-bot challenge detected";
          await closeAgeWorkerWindow(job);
          job.window_id=null;
          job.tab_ids=[];
          await chrome.storage.local.set({[AGE_JOB_KEY]:job});
          await updateAgeJobStatus(job,job.last_error+"; pausing the age scan.");
          chrome.alarms.create(AGE_JOB_ALARM,{when:job.cooldown_until});
          return{
            ok:true,
            paused:true,
            remaining:job.remaining.length,
            updated:eventUpdated+updated,
            failed:eventFailed,
            cooldown_until:job.cooldown_until,
          };
        }

        const failed=await updateAgeFailures(batch,ages);
        job.remaining=job.remaining.slice(batch.length);
        job.scanned=Number(job.scanned||0)+batch.length;
        job.updated=Number(job.updated||0)+updated;
        job.failed=Number(job.failed||0)+failed;
        job.last_error=null;
        job.cooldown_until=0;
        eventUpdated+=updated;
        eventFailed+=failed;
        await chrome.storage.local.set({[AGE_JOB_KEY]:job});
        await updateAgeJobStatus(job);
      }

      if(job.remaining.length){
        chrome.alarms.create(AGE_JOB_ALARM,{when:Date.now()+30000});
        return{ok:true,remaining:job.remaining.length,updated:eventUpdated,failed:eventFailed};
      }
      await finishAgeJob(job);
      return{ok:true,remaining:0,updated:eventUpdated,failed:eventFailed};
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
async function unpair(){await stopLegacyAgeScan();await chrome.storage.local.remove(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin","lastSyncAttemptAt",AGE_CACHE_KEY,AGE_FAILURES_KEY,AGE_JOB_KEY])}
async function connectionStatus(){const data=await stored();if(!data.bridgeToken)return{paired:false,status:data.syncStatus||null,apiOrigin:API_ORIGIN};try{const remote=await api("/api/extension/status");return{paired:true,workspace:data.bridgeWorkspace,remote,status:data.syncStatus||null,apiOrigin:API_ORIGIN}}catch(error){return{paired:true,workspace:data.bridgeWorkspace,status:data.syncStatus||null,error:error instanceof Error?error.message:String(error),apiOrigin:API_ORIGIN}}}
async function runSync(reason="manual"){
  if(syncInFlight)return syncInFlight;
  const statusData=await chrome.storage.local.get(["syncStatus","lastSyncAttemptAt"]);
  const previousStatus=statusData?.syncStatus||{};
  const lastAttempt=Number(statusData?.lastSyncAttemptAt||0);
  const apiCooldownUntil=Number(previousStatus.api_cooldown_until||0);
  // Automatic polling respects access-denial cooldowns. Explicit user actions
  // must always make a fresh attempt, even while automatic polling is paused.
  if(reason==="periodic"&&apiCooldownUntil>Date.now()){
    return{
      ...previousStatus,
      ok:true,
      reason,
      sync_skipped_for_api_cooldown:true,
      api_cooldown_until:apiCooldownUntil,
    };
  }
  const recentAttempt=lastAttempt>0&&Date.now()-lastAttempt<SYNC_INTERVAL_MS;
  if(reason==="periodic"&&recentAttempt)return{
    ...previousStatus,ok:true,reason,
    sync_skipped_for_interval:true,
    next_sync_at:lastAttempt+SYNC_INTERVAL_MS,
  };
  syncInFlight=(async()=>{
    let temporary=null;
    try{
      const found=await vintedTab(reason);
      temporary=found.temporary?found.tab.id:null;
      let tab=found.tab;
      tab=await ensureCurrentContentScript(tab);
      await chrome.storage.local.set({lastSyncAttemptAt:Date.now()});
      const tabUrl=new URL(String(tab.url||""));
      if(!tabUrl.hostname.startsWith("www.vinted."))throw new Error("Open and sign in to Vinted first.");
      await chrome.storage.local.set({vintedOrigin:tabUrl.origin});
      const response=await collectFromVintedTab(tab,reason);
      if(!response?.ok){
        if(response?.rate_limited){
          const cooldownUntil=Date.now()+VINTED_ACCESS_COOLDOWN_MS;
          const status={
            ok:false,
            at:new Date().toISOString(),
            reason,
            error:response?.error||"Vinted API rate limited the inventory sync.",
            api_rate_limited:true,
            api_cooldown_until:cooldownUntil,
            vinted_cooldown_until:0,
          };
          await chrome.storage.local.set({syncStatus:status});
          return status;
        }
        throw new Error(response?.error||"Could not read Vinted data.");
      }
      const snapshot=response.snapshot;
      const ageScanItems=Array.isArray(snapshot.age_scan_items)?snapshot.age_scan_items:[];
      const detailSync=snapshot.detail_sync||{};
      delete snapshot.age_scan_items;
      delete snapshot.detail_sync;
      snapshot.extension_version=chrome.runtime.getManifest().version;

      const result=await api("/api/extension/browser-sync",{method:"POST",body:JSON.stringify(snapshot)});
      const apiRateLimited=Boolean(detailSync.api_rate_limited??detailSync.rate_limited);
      const detailRateLimited=Boolean(detailSync.detail_endpoint_rate_limited??detailSync.rate_limited);
      // Disabled: mass page-age navigation previously spawned multiple
      // minimized tabs and interfered with regular Vinted browsing.
      const ageJob={remaining:0,scanned:0,updated:0,failed:0,skipped:ageScanItems.length};
      const status={
        ok:true,
        at:new Date().toISOString(),
        reason,
        listings:result.listings||snapshot.listings.length,
        orders:snapshot.orders.length,
        detail_enriched:Number(detailSync.enriched||0),
        detail_pending:Number(detailSync.pending??detailSync.deferred??0),
        detail_deferred:Number(detailSync.pending??detailSync.deferred??0),
        detail_rate_limited:detailRateLimited,
        api_rate_limited:apiRateLimited,
        api_cooldown_until:apiRateLimited?Date.now()+VINTED_ACCESS_COOLDOWN_MS:0,
        vinted_cooldown_until:0,
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
      const message=error instanceof Error?error.message:String(error);
      const blocked=/rate limit|too many requests|HTTP 403|challenge|access denied|not signed in/i.test(message);
      const status={
        ok:false,at:new Date().toISOString(),reason,error:message,
        api_rate_limited:blocked,
        api_cooldown_until:blocked?Date.now()+VINTED_ACCESS_COOLDOWN_MS:0,
      };
      // A deferred automatic sync should not consume the two-hour quota;
      // only a sync that was actually attempted is counted.
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
  // No catch-up sync on browser launch or extension installation.
  chrome.alarms.create(SYNC_ALARM,{
    delayInMinutes:120,periodInMinutes:120,
  });
  stopLegacyAgeScan();
}
chrome.runtime.onInstalled.addListener(alarms);
chrome.runtime.onStartup.addListener(alarms);
chrome.alarms.onAlarm.addListener(async a=>{
  if(a.name===AGE_JOB_ALARM)return stopLegacyAgeScan();
  if(a.name===SYNC_ALARM){
    return runSync("periodic");
  }
});
chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{
  (async()=>{
    if(msg?.type==="pair")return sendResponse(await pair(msg.code));
    if(msg?.type==="unpair"){await unpair();return sendResponse({ok:true})}
    if(msg?.type==="sync-now")return sendResponse(await runSync("manual"));
    if(msg?.type==="status")return sendResponse(await connectionStatus());
    if(msg?.type==="age-job-step")return sendResponse({ok:false,error:"Page-age scans are disabled to protect normal Vinted browsing."});
    sendResponse({ok:false,error:"Unknown message"});
  })().catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));
  return true;
});
