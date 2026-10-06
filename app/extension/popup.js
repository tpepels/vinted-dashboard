const $=s=>document.querySelector(s);
const send=p=>chrome.runtime.sendMessage(p);

function show(text,error=false){
  $("#status").textContent=text;
  $("#status").className="status "+(error?"error":"");
}

function pageCooldownText(status){
  const until=Math.max(
    Number(status?.age_scan_cooldown_until||0),
    Number(status?.page_cooldown_until||0),
  );
  return until>Date.now()?" · page scan paused until "+new Date(until).toLocaleTimeString():"";
}

function apiCooldownText(status){
  const until=Number(status?.api_cooldown_until||0);
  return until>Date.now()?" · API paused until "+new Date(until).toLocaleTimeString():"";
}

async function refresh(){
  const r=await send({type:"status"});
  $("#pair").classList.toggle("hidden",!!r.paired);
  $("#paired").classList.toggle("hidden",!r.paired);
  const version=chrome.runtime.getManifest().version;
  $("#bridge-version").textContent="Chrome Bridge v"+version;

  if(r.paired){
    const status=r.status||{};
    const last=status.at?new Date(status.at).toLocaleString():"not synced yet";
    const scanned=Number(status.age_scan_scanned||0);
    const failed=Number(status.age_scan_failed||0);
    const remaining=Number(status.age_scan_remaining||0);
    const detailPending=Number(status.detail_pending??status.detail_deferred??0);
    const detailEnriched=Number(status.detail_enriched||0);
    const ageState=status.age_scan_running
      ? " · "+remaining+" page ages remaining"+pageCooldownText(status)
      : scanned
        ? " · "+scanned+" page ages scanned"+(failed?" · "+failed+" unread":"")
        : "";
    const detailState=detailPending
      ? " · "+detailPending+" API details pending"
      : detailEnriched
        ? " · "+detailEnriched+" API details enriched"
        : "";
    show(
      (r.workspace||"Workspace")+" · v"+version+" · "+last+ageState+detailState+apiCooldownText(status),
      !!r.error,
    );
  }else{
    show("Not paired yet · v"+version);
  }
}

$("#pair-btn").onclick=async()=>{
  show("Pairing…");
  const r=await send({type:"pair",code:$("#code").value});
  if(r?.ok===false||r?.error)return show(r.error||"Pairing failed",true);
  await refresh();
};

$("#sync").onclick=async()=>{
  show("Syncing Vinted inventory and API details…");
  const r=await send({type:"sync-now"});
  const remaining=Number(r?.age_scan_remaining||0);
  const pending=Number(r?.detail_pending??r?.detail_deferred??0);
  const enriched=Number(r?.detail_enriched||0);

  if(r?.sync_skipped_for_api_cooldown){
    return show("Vinted API sync is cooling down"+apiCooldownText(r)+".");
  }

  if(!r?.ok){
    return show(r?.error||"Sync failed",true);
  }

  let text="Synced "+r.listings+" listings.";
  if(enriched)text+=" "+enriched+" API details enriched.";
  if(pending)text+=" "+pending+" API details pending.";
  if(r?.detail_rate_limited)text+=" Detail enrichment stopped after an API 429"+apiCooldownText(r)+".";
  else if(r?.api_rate_limited)text+=" A Vinted API request was rate limited"+apiCooldownText(r)+".";
  if(remaining)text+=" Scanning "+remaining+" posting ages in the minimized worker window"+pageCooldownText(r)+".";
  show(text);
};

$("#dashboard").onclick=async()=>{
  const r=await send({type:"status"});
  chrome.tabs.create({url:r.apiOrigin});
};

$("#unpair").onclick=async()=>{
  await send({type:"unpair"});
  await refresh();
};

refresh();
