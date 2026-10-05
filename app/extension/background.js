const API_ORIGIN="__API_ORIGIN__";
const VINTED_PATTERNS=chrome.runtime.getManifest().content_scripts[0].matches.slice();
const SYNC_ALARM="reseller-vinted-sync";
let syncInFlight=null;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function stored(){return await chrome.storage.local.get(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin"])}
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

async function renderedUploadedAges(items){
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
  await Promise.all([worker(),worker()]);
  return results;
}
async function pair(code){const response=await fetch(API_ORIGIN+"/api/extension/pair",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:String(code||"").trim().toUpperCase(),extension_version:chrome.runtime.getManifest().version,device_name:"Chrome"})});let body={};try{body=await response.json()}catch{}if(!response.ok)throw new Error(body.detail||"Pairing failed");await chrome.storage.local.set({bridgeToken:body.token,bridgeWorkspace:body.workspace});return body}
async function unpair(){await chrome.storage.local.remove(["bridgeToken","bridgeWorkspace","syncStatus","vintedOrigin"])}
async function connectionStatus(){const data=await stored();if(!data.bridgeToken)return{paired:false,status:data.syncStatus||null,apiOrigin:API_ORIGIN};try{const remote=await api("/api/extension/status");return{paired:true,workspace:data.bridgeWorkspace,remote,status:data.syncStatus||null,apiOrigin:API_ORIGIN}}catch(error){return{paired:true,workspace:data.bridgeWorkspace,status:data.syncStatus||null,error:error instanceof Error?error.message:String(error),apiOrigin:API_ORIGIN}}}
async function runSync(reason="manual"){if(syncInFlight)return syncInFlight;syncInFlight=(async()=>{let temporary=null;try{const found=await vintedTab();temporary=found.temporary?found.tab.id:null;const tab=await waitForTab(found.tab.id);const tabUrl=new URL(String(tab.url||""));if(!tabUrl.hostname.startsWith("www.vinted."))throw new Error("Open and sign in to Vinted first.");await chrome.storage.local.set({vintedOrigin:tabUrl.origin});const response=await message(tab.id,{type:"collect-vinted-data"});if(!response?.ok)throw new Error(response?.error||"Could not read Vinted data.");const snapshot=response.snapshot;snapshot.extension_version=chrome.runtime.getManifest().version;const result=await api("/api/extension/browser-sync",{method:"POST",body:JSON.stringify(snapshot)});const status={ok:true,at:new Date().toISOString(),reason,listings:result.listings||snapshot.listings.length,orders:snapshot.orders.length};await chrome.storage.local.set({syncStatus:status});return status}catch(error){const status={ok:false,at:new Date().toISOString(),reason,error:error instanceof Error?error.message:String(error)};await chrome.storage.local.set({syncStatus:status});return status}finally{if(temporary!==null){try{await chrome.tabs.remove(temporary)}catch{}}syncInFlight=null}})();return syncInFlight}
function alarms(){chrome.alarms.create(SYNC_ALARM,{periodInMinutes:10})}
chrome.runtime.onInstalled.addListener(alarms);chrome.runtime.onStartup.addListener(alarms);chrome.alarms.onAlarm.addListener(a=>{if(a.name===SYNC_ALARM)runSync("periodic")});
chrome.runtime.onMessage.addListener((msg,_sender,sendResponse)=>{(async()=>{if(msg?.type==="pair")return sendResponse(await pair(msg.code));if(msg?.type==="unpair"){await unpair();return sendResponse({ok:true})}if(msg?.type==="sync-now")return sendResponse(await runSync("manual"));if(msg?.type==="status")return sendResponse(await connectionStatus());if(msg?.type==="rendered-uploaded-ages")return sendResponse({ok:true,ages:await renderedUploadedAges(msg.items)});sendResponse({ok:false,error:"Unknown message"})})().catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));return true});
