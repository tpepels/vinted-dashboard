const API_ORIGIN="__API_ORIGIN__";
const VINTED_PATTERNS=chrome.runtime.getManifest().content_scripts[0].matches.slice();
const SYNC_ALARM="reseller-vinted-sync";
const AGE_SWEEP_ALARM="reseller-vinted-age-sweep";
const AGE_SWEEP_QUEUE_KEY="vintedAgeSweepQueueV1";
const AGE_CACHE_KEY="vintedListingPageAgeCacheV2";
const AGE_BATCH_SIZE=16;
const AGE_SWEEP_WINDOW_MS=90000;
let syncInFlight=null;
let ageSweepInFlight=null;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function stored(){return await chrome.storage.local.get(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin",AGE_SWEEP_QUEUE_KEY])}
async function authHeaders(){const data=await stored();if(!data.bridgeToken)throw new Error("Pair this extension with your dashboard first.");return{"Authorization":"Bearer "+data.bridgeToken,"Content-Type":"application/json"}}
async function api(path,options={}){const headers=Object.assign({},await authHeaders(),options.headers||{});const response=await fetch(API_ORIGIN+path,Object.assign({},options,{headers}));let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.detail||("Dashboard returned HTTP "+response.status));return body}
async function waitForTab(tabId,timeout=20000){const start=Date.now();while(Date.now()-start<timeout){const tab=await chrome.tabs.get(tabId);if(tab.status==="complete")return tab;await sleep(300)}throw new Error("Vinted tab did not finish loading.")}
async function message(tabId,payload){try{return await chrome.tabs.sendMessage(tabId,payload)}catch(error){if(!String(error?.message||error).includes("Receiving end does not exist"))throw error;await chrome.scripting.executeScript({target:{tabId},files:["vinted_age.js","content.js"]});await sleep(250);return await chrome.tabs.sendMessage(tabId,payload)}}
async function vintedTab(){const tabs=await chrome.tabs.query({url:VINTED_PATTERNS});if(tabs.length)return{tab:tabs[0],temporary:false};const data=await stored();if(!data.vintedOrigin)throw new Error("Open your signed-in Vinted website once, then press Sync now.");const tab=await chrome.tabs.create({url:data.vintedOrigin+"/",active:false});await waitForTab(tab.id);return{tab,temporary:true}}
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

async function renderedUploadedAges(items,workerCount=4){
  const queue=(Array.isArray(items)?items:[]).filter(item=>item?.id&&item?.url);
  if(!queue.length)return{};
  let cursor=0;
  const results={};
  async function worker(){
    const first=queue[cursor++];
    if(!first)return;
    const tab=await chrome.tabs.create({url:first.url,active:false});
    try{
      let item=first;
      while(item){
        try{
          const age=await readRenderedAgeFromTab(tab,item);
          if(age)results[String(item.id)]=age;
        }catch{}
        item=cursor<queue.length?queue[cursor++]:null;
      }
    }finally{
      try{await chrome.tabs.remove(tab.id)}catch{}
    }
  }
  const workers=Math.max(1,Math.min(Number(workerCount)||1,queue.length));
  await Promise.all(Array.from({length:workers},()=>worker()));
  return results;
}

async function enqueueAgeSweep(items){
  const data=await chrome.storage.local.get([AGE_SWEEP_QUEUE_KEY]);
  const existing=Array.isArray(data?.[AGE_SWEEP_QUEUE_KEY])?data[AGE_SWEEP_QUEUE_KEY]:[];
  const merged=new Map();
  for(const item of existing){
    if(item?.id&&item?.url)merged.set(String(item.id),{id:String(item.id),url:String(item.url)});
  }
  for(const item of(Array.isArray(items)?items:[])){
    if(item?.id&&item?.url)merged.set(String(item.id),{id:String(item.id),url:String(item.url)});
  }
  const queue=[...merged.values()];
  await chrome.storage.local.set({[AGE_SWEEP_QUEUE_KEY]:queue});
  if(queue.length)chrome.alarms.create(AGE_SWEEP_ALARM,{when:Date.now()+1000});
  return queue.length;
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

async function updateAgeSweepStatus(remaining,updated,error=null){
  const data=await chrome.storage.local.get(["syncStatus"]);
  const previous=data?.syncStatus||{};
  const status={
    ...previous,
    age_scan_remaining:Number(remaining)||0,
    age_scan_updated:Number(updated)||0,
    age_scan_at:new Date().toISOString(),
  };
  if(error)status.age_scan_error=String(error);
  else delete status.age_scan_error;
  await chrome.storage.local.set({syncStatus:status});
}

async function processAgeSweepWindow(){
  if(ageSweepInFlight)return ageSweepInFlight;
  ageSweepInFlight=(async()=>{
    const started=Date.now();
    let updated=0;
    try{
      while(Date.now()-started<AGE_SWEEP_WINDOW_MS){
        const data=await chrome.storage.local.get([AGE_SWEEP_QUEUE_KEY]);
        const queue=Array.isArray(data?.[AGE_SWEEP_QUEUE_KEY])?data[AGE_SWEEP_QUEUE_KEY]:[];
        if(!queue.length){
          await updateAgeSweepStatus(0,updated);
          return{ok:true,remaining:0,updated};
        }

        const batch=queue.slice(0,AGE_BATCH_SIZE);
        const remaining=queue.slice(AGE_BATCH_SIZE);
        await chrome.storage.local.set({[AGE_SWEEP_QUEUE_KEY]:remaining});

        const ages=await renderedUploadedAges(batch,4);
        await saveAgeBatchToCache(ages);
        const updates=Object.entries(ages).map(([external_id,age])=>({
          external_id,
          listed_age_seconds:Math.max(0,Math.round(Number(age.seconds)||0)),
          listed_age_text:age.text||null,
          observed_at:Date.now()/1000,
        }));
        if(updates.length){
          try{
            const result=await api("/api/extension/listing-ages",{
              method:"POST",
              body:JSON.stringify({ages:updates}),
            });
            updated+=Number(result.updated||0);
          }catch(error){
            await updateAgeSweepStatus(remaining.length,updated,error instanceof Error?error.message:String(error));
          }
        }
        await updateAgeSweepStatus(remaining.length,updated);
      }

      const data=await chrome.storage.local.get([AGE_SWEEP_QUEUE_KEY]);
      const remaining=Array.isArray(data?.[AGE_SWEEP_QUEUE_KEY])?data[AGE_SWEEP_QUEUE_KEY].length:0;
      if(remaining)chrome.alarms.create(AGE_SWEEP_ALARM,{when:Date.now()+30000});
      await updateAgeSweepStatus(remaining,updated);
      return{ok:true,remaining,updated};
    }catch(error){
      const data=await chrome.storage.local.get([AGE_SWEEP_QUEUE_KEY]);
      const remaining=Array.isArray(data?.[AGE_SWEEP_QUEUE_KEY])?data[AGE_SWEEP_QUEUE_KEY].length:0;
      if(remaining)chrome.alarms.create(AGE_SWEEP_ALARM,{when:Date.now()+30000});
      await updateAgeSweepStatus(remaining,updated,error instanceof Error?error.message:String(error));
      return{ok:false,remaining,updated,error:error instanceof Error?error.message:String(error)};
    }finally{
      ageSweepInFlight=null;
    }
  })();
  return ageSweepInFlight;
}
async function pair(code){const response=await fetch(API_ORIGIN+"/api/extension/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:String(code||"").trim().toUpperCase(),extension_version:chrome.runtime.getManifest().version,device_name:"Chrome"})});let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.detail||"Pairing failed");await chrome.storage.local.set({bridgeToken:body.token,bridgeWorkspace:body.workspace});return body}
async function unpair(){await chrome.storage.local.remove(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin",AGE_SWEEP_QUEUE_KEY,AGE_CACHE_KEY])}
async function connectionStatus(){const data=await stored();if(!data.bridgeToken)return{paired:false,status:data.syncStatus||null,apiOrigin:API_ORIGIN};try{const remote=await api("/api/extension/status");return{paired:true,workspace:data.bridgeWorkspace,remote,status:data.syncStatus||null,apiOrigin:API_ORIGIN}}catch(error){return{paired:true,workspace:data.bridgeWorkspace,status:data.syncStatus||null,error:error instanceof Error?error.message:String(error),apiOrigin:API_ORIGIN}}}
async function runSync(reason="manual"){
  if(syncInFlight)return syncInFlight;
  syncInFlight=(async()=>{
    let temporary=null;
    try{
      const found=await vintedTab();
      temporary=found.temporary?found.tab.id:null;
      const tab=await waitForTab(found.tab.id);
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
      const remaining=await enqueueAgeSweep(ageScanItems);
      const status={
        ok:true,
        at:new Date().toISOString(),
        reason,
        listings:result.listings||snapshot.listings.length,
        orders:snapshot.orders.length,
        age_scan_remaining:remaining,
        age_scan_updated:0,
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
  chrome.storage.local.get([AGE_SWEEP_QUEUE_KEY]).then(data=>{
    const queue=Array.isArray(data?.[AGE_SWEEP_QUEUE_KEY])?data[AGE_SWEEP_QUEUE_KEY]:[];
    if(queue.length)chrome.alarms.create(AGE_SWEEP_ALARM,{when:Date.now()+1000});
  }).catch(()=>{});
}
chrome.runtime.onInstalled.addListener(alarms);
chrome.runtime.onStartup.addListener(alarms);
chrome.alarms.onAlarm.addListener(a=>{
  if(a.name===SYNC_ALARM)runSync("periodic");
  if(a.name===AGE_SWEEP_ALARM)processAgeSweepWindow();
});
chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{
  (async()=>{
    if(msg?.type==="pair")return sendResponse(await pair(msg.code));
    if(msg?.type==="unpair"){await unpair();return sendResponse({ok:true})}
    if(msg?.type==="sync-now")return sendResponse(await runSync("manual"));
    if(msg?.type==="status")return sendResponse(await connectionStatus());
    if(msg?.type==="age-scan-now")return sendResponse(await processAgeSweepWindow());
    sendResponse({ok:false,error:"Unknown message"});
  })().catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));
  return true;
});
