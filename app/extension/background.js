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
const CONTENT_PROTOCOL=11;
const DIAGNOSTIC_LOG_KEY="bridgeDiagnosticLogV1";
const DIAGNOSTIC_LOG_LIMIT=160;
const LOST_MESSAGE_CHANNEL=/A listener indicated an asynchronous response|message (?:port|channel) closed|The message port closed before a response was received/i;
const MISSING_MESSAGE_RECEIVER=/Receiving end does not exist|Could not establish connection/i;
let syncInFlight=null;
let ageJobInFlight=null;
let lastAgeNavigationAt=0;
let ageNavigationChain=Promise.resolve();
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
// Temporary local-only diagnostic trace; never store request/response bodies or credentials.
let diagnosticWrite=Promise.resolve();
function safeDiagnosticText(value){
  return String(value??"")
    .replace(/Bearer\s+[^\s]+/gi,"Bearer [redacted]")
    .replace(/(authorization|cookie|password|bridgeToken|access_token|pairing.?code|token)\s*[:=]\s*["']?[^\s,"'}]+/gi,"$1=[redacted]")
    .replace(/https?:\/\/[^\s"'<>]+/gi,raw=>{
      try{
        const url=new URL(raw);
        return url.origin+url.pathname.replace(/\/(items|users|member|inbox)\/\d+/g,"/$1/[id]");
      }catch{return "[url]"}
    }).slice(0,600);
}
function logDiagnostic(stage,detail="",level="info"){
  const line=new Date().toISOString()+" ["+level.toUpperCase()+"] "
    +safeDiagnosticText(stage)+(detail?": "+safeDiagnosticText(detail):"");
  diagnosticWrite=diagnosticWrite.catch(()=>{}).then(async()=>{
    const saved=await chrome.storage.local.get([DIAGNOSTIC_LOG_KEY]);
    const lines=Array.isArray(saved?.[DIAGNOSTIC_LOG_KEY])?saved[DIAGNOSTIC_LOG_KEY]:[];
    await chrome.storage.local.set({[DIAGNOSTIC_LOG_KEY]:[...lines,line].slice(-DIAGNOSTIC_LOG_LIMIT)});
  }).catch(()=>{});
  return diagnosticWrite;
}
async function getDiagnostics(){
  await diagnosticWrite;
  const data=await chrome.storage.local.get([DIAGNOSTIC_LOG_KEY]);
  return Array.isArray(data?.[DIAGNOSTIC_LOG_KEY])?data[DIAGNOSTIC_LOG_KEY]:[];
}
async function clearDiagnostics(){
  diagnosticWrite=diagnosticWrite.catch(()=>{}).then(()=>chrome.storage.local.remove([DIAGNOSTIC_LOG_KEY]));
  await diagnosticWrite;
}

async function stored(){return await chrome.storage.local.get(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin",AGE_FAILURES_KEY,AGE_JOB_KEY])}
async function authHeaders(){const data=await stored();if(!data.bridgeToken)throw new Error("Pair this extension with your dashboard first.");return{"Authorization":"Bearer "+data.bridgeToken,"Content-Type":"application/json"}}
async function api(path,options={}){
  const headers=Object.assign({},await authHeaders(),options.headers||{});
  let response;
  try{response=await fetch(API_ORIGIN+path,Object.assign({},options,{headers}));}
  catch(error){await logDiagnostic("dashboard.network.error",path+" "+String(error?.message||error),"error");throw error}
  let body={};try{body=await response.json()}catch{}
  if(!response.ok){
    await logDiagnostic("dashboard.http.error",path+" HTTP "+response.status,"error");
    throw new Error(body.detail||("Dashboard returned HTTP "+response.status));
  }
  return body;
}
async function waitForTab(tabId,timeout=20000){const start=Date.now();while(Date.now()-start<timeout){const tab=await chrome.tabs.get(tabId);if(tab.status==="complete")return tab;await sleep(300)}throw new Error("Vinted tab did not finish loading.")}
async function message(tabId,payload){try{return await chrome.tabs.sendMessage(tabId,payload)}catch(error){if(!String(error?.message||error).includes("Receiving end does not exist"))throw error;await chrome.scripting.executeScript({target:{tabId},files:["vinted_age.js","content.js"]});await sleep(250);return await chrome.tabs.sendMessage(tabId,payload)}}
// Both scheduled and manual checks can own a short-lived background tab.
// A scheduled check always uses its own tab to avoid interfering with browsing.
function allowedVintedOrigin(raw){
  try{
    const url=new URL(String(raw||""));
    if(url.protocol!=="https:"||url.username||url.password||url.port)return null;
    const allowed=VINTED_PATTERNS.some(pattern=>new URL(pattern.replace(/\*$/,"")).origin===url.origin);
    return allowed?url.origin:null;
  }catch{return null}
}
async function createSyncTab(){
  const data=await chrome.storage.local.get(["vintedOrigin","vintedAccountId"]);
  const origin=allowedVintedOrigin(data?.vintedOrigin)||"https://www.vinted.pt";
  const id=String(data?.vintedAccountId||"");
  const path=/^[0-9]{1,20}$/.test(id)?"/member/"+id:"/";
  const created=await chrome.tabs.create({url:origin+path,active:false});
  if(created?.id==null)throw new Error("Chrome could not open a Vinted tab for this update.");
  try{
    return{tab:await waitForTab(created.id,30000),temporary:true};
  }catch(error){
    // Never leave an unsuccessful temporary navigation behind.
    try{await chrome.tabs.remove(created.id)}catch{}
    throw error;
  }
}
async function vintedTab(reason="periodic"){
  if(reason==="periodic")return createSyncTab();
  const tabs=await chrome.tabs.query({url:VINTED_PATTERNS});
  const candidates=tabs.filter(tab=>tab?.id!=null);
  const complete=candidates.filter(tab=>tab.status==="complete");
  // Manual sync can reuse the user's tab, but scheduled sync leaves it untouched.
  const ready=complete.find(tab=>tab.active)||complete[0];
  if(ready)return{tab:ready,temporary:false};
  if(candidates.length){
    const loading=candidates.find(tab=>tab.active)||candidates[0];
    return{tab:await waitForTab(loading.id,30000),temporary:false};
  }
  return createSyncTab();
}
async function probeCurrentContentScript(tab){
  let response=null;
  try{
    response=await chrome.tabs.sendMessage(tab.id,{type:"bridge-content-protocol"});
  }catch(error){
    if(!MISSING_MESSAGE_RECEIVER.test(String(error?.message||error))){
      await logDiagnostic("content.probe.error",error?.stack||error?.message||error,"error");
      throw new Error("The Vinted tab's bridge could not respond.");
    }
    await logDiagnostic("content.probe.missing","Injecting current listener");
    // Inject only when there is no listener. Never inject alongside an old
    // listener, since that can duplicate requests and responses.
    await chrome.scripting.executeScript({
      target:{tabId:tab.id},files:["vinted_age.js","content.js"],
    });
    response=await chrome.tabs.sendMessage(tab.id,{type:"bridge-content-protocol"});
  }
  if(Number(response?.protocol)===CONTENT_PROTOCOL)return tab;
  throw new Error("Vinted tab is using an older extension script.");
}
async function ensureCurrentContentScript(tab,reason="periodic",temporary=false){
  try{return await probeCurrentContentScript(tab)}
  catch(error){
    await logDiagnostic("content.probe.retry",error?.message||error,"warn");
    if(reason!=="manual"&&!temporary)throw error;
    // The caller explicitly requested an update or owns this temporary tab:
    // refresh a stale or disconnected
    // content script once, without requiring a separate browser action.
    await chrome.tabs.reload(tab.id);
    // Allow Chrome to enter the loading state before polling for completion.
    await sleep(300);
    const reloaded=await waitForTab(tab.id,30000);
    return await probeCurrentContentScript(reloaded);
  }
}
async function collectFromVintedTab(tab,reason){
  // Long-lived sendMessage replies are dropped after the Vinted fetch has
  // taken about 25 seconds. Each IPC request now returns immediately; the
  // actual scrape continues once in the content script and can be polled.
  const jobId=String(Date.now())+"-"+String(Math.random()).slice(2);
  await logDiagnostic("collect.start","tab="+tab.id+" job="+jobId+" reason="+reason);
  let started;
  try{
    started=await message(tab.id,{type:"collect-vinted-start",reason,job_id:jobId});
  }catch(error){
    await logDiagnostic("collect.start.channel.error",error?.message||error,"error");
    throw error;
  }
  if(!started?.ok)throw new Error(started?.error||"Could not start Vinted collection");
  const limit=Date.now()+12*60*1000;
  while(Date.now()<limit){
    await sleep(1200);
    let response;
    try{
      response=await message(tab.id,{type:"collect-vinted-result",job_id:jobId});
    }catch(error){
      await logDiagnostic("collect.poll.channel.error",error?.stack||error?.message||error,"error");
      // Never start a second collection after a lost response: it can double
      // the requests to Vinted and trigger access limits.
      throw new Error("Vinted collection connection was interrupted: "+String(error?.message||error));
    }
    if(response?.ok&&response.state==="running")continue;
    if(!response?.ok&&/lost its context/.test(String(response?.error||""))){
      try{
        const live=await chrome.tabs.get(tab.id);
        const location=new URL(String(live?.url||""));
        await logDiagnostic("collect.context.lost","tab="+tab.id+" path="+location.pathname+" status="+live?.status,"error");
      }catch(error){await logDiagnostic("collect.context.lost","tab missing: "+String(error?.message||error),"error")}
    }
    await logDiagnostic(response?.ok?"collect.reply.ok":"collect.reply.error",
      response?.ok?"Snapshot received":response?.error||"Empty reply",
      response?.ok?"info":"error");
    return response;
  }
  throw new Error("Vinted collection exceeded 12 minutes; no partial inventory was uploaded.");
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
async function unpair(){await stopLegacyAgeScan();await chrome.storage.local.remove(["bridgeToken","bridgeWorkspace","syncStatus","manualSyncProgress","vintedAccountId","vintedOrigin","lastSyncAttemptAt","lastSuccessfulSyncAt",AGE_CACHE_KEY,AGE_FAILURES_KEY,AGE_JOB_KEY])}
async function connectionStatus(){
  const data=await stored();
  const progress=(await chrome.storage.local.get(["manualSyncProgress"])).manualSyncProgress;
  const tabs=await chrome.tabs.query({url:VINTED_PATTERNS}).catch(()=>[]);
  const vinted_tab_open=tabs.some(tab=>tab?.id!=null);
  if(!data.bridgeToken)return{paired:false,status:data.syncStatus||null,apiOrigin:API_ORIGIN,vinted_tab_open,manualSyncProgress:progress};
  try{
    const remote=await api("/api/extension/status");
    return{paired:true,workspace:data.bridgeWorkspace,remote,status:data.syncStatus||null,apiOrigin:API_ORIGIN,vinted_tab_open,manualSyncProgress:progress};
  }catch(error){
    return{paired:true,workspace:data.bridgeWorkspace,status:data.syncStatus||null,error:error instanceof Error?error.message:String(error),apiOrigin:API_ORIGIN,vinted_tab_open,manualSyncProgress:progress};
  }
}
async function runSync(reason="manual"){
  if(syncInFlight){
    if(reason!=="manual")return syncInFlight;
    // A click during an automatic check must still make a deliberate,
    // unrestricted check after the automatic check finishes.
    await syncInFlight;
    return runSync("manual");
  }
  const statusData=await chrome.storage.local.get(["syncStatus","lastSyncAttemptAt","lastSuccessfulSyncAt"]);
  const previousStatus=statusData?.syncStatus||{};
  // Previously lastSyncAttemptAt was stored before requests that might fail.
  const lastSuccess=Number(statusData?.lastSuccessfulSyncAt
    || (previousStatus.ok===true?statusData?.lastSyncAttemptAt:0)||0);
  const previousError=String(previousStatus.error||"");
  // A stale 403 must not keep automatic checks paused.
  const apiCooldownUntil=/HTTP 403|HTTP 401|challeng|not signed in|access denied/i.test(previousError)
    ? 0 : Number(previousStatus.api_cooldown_until||0);
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
  const recentSuccess=lastSuccess>0&&Date.now()-lastSuccess<SYNC_INTERVAL_MS;
  if(reason==="periodic"&&recentSuccess)return{
    ...previousStatus,reason,
    sync_skipped_for_interval:true,
    next_sync_at:lastSuccess+SYNC_INTERVAL_MS,
  };
  syncInFlight=(async()=>{
    let temporary=null;
    await logDiagnostic("sync.start","reason="+reason+" bridge="+chrome.runtime.getManifest().version);
    try{
      if(!(await stored()).bridgeToken)throw new Error("Pair this extension with your dashboard first.");
      const found=await vintedTab(reason);
      temporary=found.temporary?found.tab.id:null;
      let tab=found.tab;
      await logDiagnostic("sync.tab","tab="+tab.id+" temporary="+Boolean(found.temporary));
      tab=await ensureCurrentContentScript(tab,reason,temporary!==null);
      await logDiagnostic("sync.content.ready","tab="+tab.id);
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
      if(!Array.isArray(snapshot.listings)||!snapshot.listings.length||snapshot.inventory_complete!==true)
        throw new Error("Refusing an empty or incomplete Vinted inventory snapshot; existing active listings are protected.");
      await logDiagnostic("sync.dashboard.upload","listings="+snapshot.listings.length+" orders="+snapshot.orders.length);

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
      const succeededAt=Date.now();
      await chrome.storage.local.set({
        syncStatus:status,lastSyncAttemptAt:succeededAt,lastSuccessfulSyncAt:succeededAt,
      });
      await logDiagnostic("sync.success","listings="+status.listings+" orders="+status.orders);
      return status;
    }catch(error){
      const message=error instanceof Error?error.message:String(error);
      await logDiagnostic("sync.failed",error?.stack||message,"error");
      const rateLimited=/rate limit|too many requests|HTTP 429/i.test(message)
        && !/HTTP 403|HTTP 401|challeng|not signed in/i.test(message);
      const status={
        ok:false,at:new Date().toISOString(),reason,error:message,
        api_rate_limited:rateLimited,
        api_cooldown_until:rateLimited?Date.now()+VINTED_ACCESS_COOLDOWN_MS:0,
      };
      // Automatic tab/auth failures do not replace the last successful status.
      if(reason==="manual"||rateLimited)await chrome.storage.local.set({syncStatus:status});
      return status;
    }finally{
      if(temporary!==null){try{await chrome.tabs.remove(temporary)}catch{}}
      await logDiagnostic("sync.finished","temporary tab closed="+(temporary!==null));
      syncInFlight=null;
    }
  })();
  return syncInFlight;
}
async function startManualSync(){
  const previous=(await chrome.storage.local.get(["manualSyncProgress"])).manualSyncProgress;
  if(previous?.state==="running"&&Date.now()-Number(previous.started_at||0)<12*60*1000)
    return {ok:true,started:true,already_running:true};
  const progress={state:"running",started_at:Date.now()};
  await chrome.storage.local.set({manualSyncProgress:progress});
  // Reply to the popup immediately rather than holding its Chrome message
  // channel open while a lengthy Vinted collection runs.
  void runSync("manual").then(async result=>{
    await chrome.storage.local.set({manualSyncProgress:{
      state:"done",started_at:progress.started_at,finished_at:Date.now(),result,
    }});
  }).catch(async error=>{
    const result={ok:false,error:String(error?.message||error)};
    await logDiagnostic("sync.unexpected.error",result.error,"error");
    await chrome.storage.local.set({manualSyncProgress:{
      state:"done",started_at:progress.started_at,finished_at:Date.now(),result,
    }});
  });
  return {ok:true,started:true};
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
    if(msg?.type==="sync-now")return sendResponse(await startManualSync());
    if(msg?.type==="sync-progress")return sendResponse({ok:true,progress:(await chrome.storage.local.get(["manualSyncProgress"])).manualSyncProgress||null});
    if(msg?.type==="diagnostics")return sendResponse({ok:true,lines:await getDiagnostics()});
    if(msg?.type==="diagnostics-clear"){await clearDiagnostics();return sendResponse({ok:true})}
    if(msg?.type==="bridge-diagnostic-event"){
      if(allowedVintedOrigin(_sender?.tab?.url)){
        await logDiagnostic("vinted."+String(msg.stage||"step"),msg.detail||"",msg.level==="error"?"error":"info");
      }
      return sendResponse({ok:true});
    }
    if(msg?.type==="status")return sendResponse(await connectionStatus());
    if(msg?.type==="age-job-step")return sendResponse({ok:false,error:"Page-age scans are disabled to protect normal Vinted browsing."});
    sendResponse({ok:false,error:"Unknown message"});
  })().catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));
  return true;
});
