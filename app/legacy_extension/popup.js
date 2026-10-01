const $=s=>document.querySelector(s);
function show(text,state=""){const el=$("#status");el.textContent=text;el.className=`status ${state}`.trim()}
function when(value){if(!value)return"";const d=new Date(value);return Number.isNaN(d.getTime())?"":d.toLocaleString()}
async function message(payload){return await chrome.runtime.sendMessage(payload)}
async function refreshStatus(){
  const result=await message({type:"status"});
  const status=result?.status;
  if(status?.ok)show(`Last sync ${when(status.at)} · ${status.listings} listings · ${status.orders} orders`,"ok");
  else if(status?.error)show(status.error,"error");
  else show("Not synced yet.");
}
$("#sync").addEventListener("click",async()=>{
  show("Syncing…");
  const result=await message({type:"sync-now"});
  if(result?.ok)show(`Synced ${result.listings} listings and ${result.orders} orders.`,"ok");
  else show(result?.error||"Sync failed.","error");
});
$("#dashboard").addEventListener("click",()=>chrome.tabs.create({url:"http://media-server:5050"}));
$("#vinted").addEventListener("click",()=>chrome.tabs.create({url:"https://www.vinted.pt/"}));
refreshStatus();
