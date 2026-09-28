const state={data:null};

const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];

function money(cents,currency="EUR"){
  if(cents===null||cents===undefined)return "—";
  return new Intl.NumberFormat(undefined,{style:"currency",currency}).format(cents/100);
}
function when(value){
  if(!value)return "—";
  const d=new Date(value);
  if(Number.isNaN(d.getTime()))return String(value);
  return new Intl.DateTimeFormat(undefined,{dateStyle:"medium",timeStyle:"short"}).format(d);
}
function dateOnly(value){
  if(!value)return "—";
  const d=new Date(value);
  if(Number.isNaN(d.getTime()))return String(value);
  return new Intl.DateTimeFormat(undefined,{dateStyle:"medium"}).format(d);
}
function age(value){
  if(!value)return "—";
  const d=new Date(value);
  const ts=d.getTime();
  if(Number.isNaN(ts))return "—";
  const days=Math.max(0,Math.floor((Date.now()-ts)/86400000));
  if(days===0)return "Today";
  if(days===1)return "1 day";
  if(days<30)return `${days} days`;
  if(days<365){
    const months=Math.max(1,Math.floor(days/30.44));
    return `${months} month${months===1?"":"s"}`;
  }
  const years=Math.max(1,Math.floor(days/365.25));
  return `${years} year${years===1?"":"s"}`;
}
function timeValue(value){
  const n=new Date(value||0).getTime();
  return Number.isNaN(n)?0:n;
}
function esc(value=""){
  return String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[c]));
}
function badge(status){
  const text=String(status||"unknown").replaceAll("_"," ");
  return `<span class="badge ${esc(status||"unknown")}">${esc(text)}</span>`;
}
function empty(text){return `<div class="empty">${esc(text)}</div>`;}
function flash(message,error=false){
  $("#flash").innerHTML=`<div class="flash ${error?"error":""}">${esc(message)}</div>`;
  setTimeout(()=>$("#flash").innerHTML="",5000);
}
async function api(path,options={}){
  const res=await fetch(path,options);
  if(!res.ok){
    let detail=`HTTP ${res.status}`;
    try{const data=await res.json();detail=data.detail||detail}catch{}
    throw new Error(detail);
  }
  return res.json();
}
function linkedTitle(row){
  return row.vinted_url
    ? `<a href="${esc(row.vinted_url)}" target="_blank" rel="noreferrer">${esc(row.title)}</a>`
    : esc(row.title);
}

function isFavoriteNotification(n){
  return n?.category==="favorite"||String(n?.url||"").includes("/want_it/");
}
function favoriteItemTitle(n){
  if(n?.item_title)return String(n.item_title);
  const text=String(n?.body||n?.title||"");
  const pt=text.match(/adicionou o teu (.+?) aos seus favoritos\.?$/i);
  if(pt)return pt[1].trim();
  const en=text.match(/added your (.+?) to (?:their|his|her) favou?rites\.?$/i);
  if(en)return en[1].trim();
  return "Listing";
}
function favoriteActor(n){
  if(n?.actor)return String(n.actor);
  const text=String(n?.body||n?.title||"");
  const pt=text.match(/^(.+?) adicionou o teu /i);
  if(pt)return pt[1].trim();
  const en=text.match(/^(.+?) added your /i);
  return en?en[1].trim():null;
}
function favoriteItemUrl(n){
  const url=String(n?.url||"");
  return url?url.replace(/\/want_it\/new(?:\?.*)?$/,""):null;
}
function groupFavoriteNotifications(rows){
  const groups=new Map();
  for(const n of rows.filter(isFavoriteNotification)){
    const title=favoriteItemTitle(n);
    const url=favoriteItemUrl(n);
    const key=String(n.item_id||url||title);
    const existing=groups.get(key)||{
      category:"favorite_group",
      item_id:n.item_id||null,
      item_title:title,
      title,
      url,
      count:0,
      actors:[],
      occurred_at:n.occurred_at,
      read:true,
    };
    existing.count+=1;
    existing.read=existing.read&&Boolean(n.read);
    if(timeValue(n.occurred_at)>timeValue(existing.occurred_at))existing.occurred_at=n.occurred_at;
    const actor=favoriteActor(n);
    if(actor&&!existing.actors.includes(actor))existing.actors.push(actor);
    groups.set(key,existing);
  }
  return [...groups.values()].sort((a,b)=>timeValue(b.occurred_at)-timeValue(a.occurred_at));
}
function meaningfulNotifications(rows){
  return rows.filter(n=>!isFavoriteNotification(n));
}
function notificationFeed(mode){
  const rows=state.data?.notifications||[];
  const favorites=groupFavoriteNotifications(rows);
  const useful=meaningfulNotifications(rows);
  if(mode==="favorites")return favorites;
  if(mode==="all")return [...useful,...favorites].sort((a,b)=>timeValue(b.occurred_at)-timeValue(a.occurred_at));
  return useful.sort((a,b)=>timeValue(b.occurred_at)-timeValue(a.occurred_at));
}
function favoriteGroupSubtitle(n){
  const actors=n.actors||[];
  if(!actors.length)return `${n.count} favorite${n.count===1?"":"s"}`;
  const shown=actors.slice(0,3).join(", ");
  const extra=actors.length-3;
  return `${n.count} favorite${n.count===1?"":"s"} · ${shown}${extra>0?` +${extra} more`:""}`;
}
function notificationRow(n){
  if(n.category==="favorite_group"){
    const title=`${n.item_title} - ${n.count} favorite${n.count===1?"":"s"}`;
    const linked=n.url
      ? `<a href="${esc(n.url)}" target="_blank" rel="noreferrer">${esc(title)}</a>`
      : esc(title);
    return `
      <div class="notification ${n.read?"":"unread"} favorite-group">
        <div class="dot"></div>
        <div>
          <div class="notification-title">${linked}</div>
          <div class="notification-body">${esc(favoriteGroupSubtitle(n))}</div>
        </div>
        <div class="notification-time">${when(n.occurred_at)}</div>
      </div>`;
  }

  const linked=n.url
    ? `<a href="${esc(n.url)}" target="_blank" rel="noreferrer">${esc(n.title)}</a>`
    : esc(n.title);
  const body=String(n.body||"").trim();
  const showBody=body&&body!==String(n.title||"").trim();
  return `
    <div class="notification ${n.read?"":"unread"}">
      <div class="dot"></div>
      <div>
        <div class="notification-title">${linked}</div>
        ${showBody?`<div class="notification-body">${esc(body)}</div>`:""}
      </div>
      <div class="notification-time">${when(n.occurred_at)}</div>
    </div>`;
}

function renderErrors(){
  const errors=state.data?.errors||[];
  $("#source-errors").innerHTML=errors.map(x=>`<div class="source-error">${esc(x)}</div>`).join("");
}
function renderSession(){
  const auth=state.data?.auth||{};
  const label=auth.authenticated
    ? `Connected as ${auth.current_user?.username||auth.current_user?.id||"Vinted user"}`
    : auth.configured ? "Session configured, but not authenticated" : "Public data only";
  $("#session-state").textContent=label;
  $("#dialog-session-status").textContent=label;
}

function renderSummary(){
  const d=state.data||{};
  const s=d.summary||{};
  $("#stat-active").textContent=s.active_listings||0;
  $("#stat-sales").textContent=s.open_sales||0;
  $("#stat-purchases").textContent=s.open_purchases||0;
  $("#stat-unread").textContent=s.unread_notifications||0;
  $("#ytd-sales-label").textContent=`Sales ${s.ytd_year||""} YTD`;
  $("#ytd-purchases-label").textContent=`Purchases ${s.ytd_year||""} YTD`;
  $("#stat-ytd-sales").textContent=money(s.ytd_sales_cents||0,s.ytd_sales_currency||"EUR");
  $("#stat-ytd-purchases").textContent=money(s.ytd_purchases_cents||0,s.ytd_purchases_currency||"EUR");
  $("#stat-ytd-sales-count").textContent=`${s.ytd_sales_count||0} order${s.ytd_sales_count===1?"":"s"}`;
  $("#stat-ytd-purchases-count").textContent=`${s.ytd_purchases_count||0} order${s.ytd_purchases_count===1?"":"s"}`;
  $("#nav-listings").textContent=s.active_listings?String(s.active_listings):"";
  $("#nav-sales").textContent=s.open_sales?String(s.open_sales):"";
  $("#nav-purchases").textContent=s.open_purchases?String(s.open_purchases):"";
  $("#nav-notifications").textContent=s.unread_notifications?String(s.unread_notifications):"";

  const attention=d.attention||[];
  $("#attention").innerHTML=attention.length?attention.map(o=>`
    <div class="row">
      <div><div class="row-title">${linkedTitle(o)}</div><div class="meta">${o.direction==="sell"?"Sale":o.direction==="buy"?"Purchase":"Order"} · ${o.counterparty?esc(o.counterparty)+" · ":""}updated ${when(o.updated_at)}</div></div>
      <div>${badge(o.status)}</div>
    </div>`).join(""):empty(d.auth?.authenticated?"Nothing needs attention.":"Connect your Vinted session to load private orders.");

  const sales=d.sales||[];
  $("#recent-sales").innerHTML=sales.slice(0,5).map(o=>`
    <div class="row"><div><div class="row-title">${linkedTitle(o)}</div><div class="meta">${money(o.total_cents,o.currency)} · ${when(o.updated_at)}</div></div>${badge(o.status)}</div>
  `).join("")||empty(d.auth?.authenticated?"No sales returned by Vinted.":"Connect your Vinted session to load sales.");

  const rawNotifications=d.notifications||[];
  const useful=meaningfulNotifications(rawNotifications)
    .sort((a,b)=>timeValue(b.occurred_at)-timeValue(a.occurred_at))
    .slice(0,4);
  const favoriteGroups=groupFavoriteNotifications(rawNotifications);
  const favoriteCount=favoriteGroups.reduce((sum,n)=>sum+n.count,0);
  const recent=[...useful];
  if(favoriteCount){
    recent.push({
      category:"favorite_summary",
      title:`${favoriteCount} favorite${favoriteCount===1?"":"s"} across ${favoriteGroups.length} listing${favoriteGroups.length===1?"":"s"}`,
      occurred_at:favoriteGroups[0]?.occurred_at,
      read:favoriteGroups.every(n=>n.read),
    });
  }
  recent.sort((a,b)=>timeValue(b.occurred_at)-timeValue(a.occurred_at));

  $("#recent-notifications").innerHTML=recent.slice(0,5).map(n=>{
    if(n.category==="favorite_summary"){
      return `<div class="row"><div><div class="row-title">${esc(n.title)}</div><div class="meta">Favorite activity · ${when(n.occurred_at)}</div></div><span class="badge">favorites</span></div>`;
    }
    return `<div class="row"><div><div class="row-title">${n.url?`<a href="${esc(n.url)}" target="_blank" rel="noreferrer">${esc(n.title)}</a>`:esc(n.title)}</div><div class="meta">${when(n.occurred_at)}</div></div>${n.read?"":'<span class="badge">new</span>'}</div>`;
  }).join("")||empty(d.auth?.authenticated?"No useful notifications.":"Connect your Vinted session to load notifications.");
}

function listingFingerprint(title){
  return String(title||"")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g,"")
    .toLowerCase()
    .replace(/&/g," and ")
    .replace(/[^a-z0-9]+/g," ")
    .replace(/\s+/g," ")
    .trim();
}
function duplicateInfo(rows){
  const groups=new Map();
  for(const row of rows){
    const key=listingFingerprint(row.title);
    if(!key)continue;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(row);
  }
  const duplicateGroups=[...groups.values()].filter(group=>group.length>1);
  const counts=new Map();
  for(const group of duplicateGroups){
    for(const row of group)counts.set(row._index,group.length);
  }
  return {duplicateGroups,counts};
}
function median(values){
  const nums=values.filter(Number.isFinite).sort((a,b)=>a-b);
  if(!nums.length)return null;
  const mid=Math.floor(nums.length/2);
  return nums.length%2?nums[mid]:Math.round((nums[mid-1]+nums[mid])/2);
}
function listingComparator(sort){
  const number=(value,fallback=-1)=>{
    const n=Number(value);
    return Number.isFinite(n)?n:fallback;
  };
  if(sort==="oldest")return (a,b)=>{
    const ad=timeValue(a.listed_at),bd=timeValue(b.listed_at);
    if(ad&&bd)return ad-bd;
    if(ad)return -1;
    if(bd)return 1;
    return b._index-a._index;
  };
  if(sort==="price-asc")return (a,b)=>number(a.price_cents,Infinity)-number(b.price_cents,Infinity);
  if(sort==="price-desc")return (a,b)=>number(b.price_cents,-1)-number(a.price_cents,-1);
  if(sort==="likes-desc")return (a,b)=>number(b.favourites,0)-number(a.favourites,0)||a._index-b._index;
  if(sort==="views-desc")return (a,b)=>number(b.views,0)-number(a.views,0)||a._index-b._index;
  if(sort==="title-asc")return (a,b)=>String(a.title||"").localeCompare(String(b.title||""),undefined,{sensitivity:"base"});
  return (a,b)=>{
    const ad=timeValue(a.listed_at),bd=timeValue(b.listed_at);
    if(ad&&bd)return bd-ad;
    if(ad)return -1;
    if(bd)return 1;
    return a._index-b._index;
  };
}
function renderListingStats(rows,duplicates){
  const prices=rows
    .map(x=>Number(x.price_cents))
    .filter(Number.isFinite);
  const total=prices.reduce((sum,n)=>sum+n,0);
  const avg=prices.length?Math.round(total/prices.length):null;
  const med=median(prices);
  const currency=rows.find(x=>x.currency)?.currency||"EUR";
  const favorites=rows.reduce((sum,x)=>sum+(Number(x.favourites)||0),0);
  const zeroFavorites=rows.filter(x=>Number(x.favourites||0)===0).length;

  $("#listing-stat-count").textContent=String(rows.length);
  $("#listing-stat-value").textContent=money(total,currency);
  $("#listing-stat-average").textContent=money(avg,currency);
  $("#listing-stat-median").textContent=money(med,currency);
  $("#listing-stat-favorites").textContent=String(favorites);
  $("#listing-stat-zero-favorites").textContent=String(zeroFavorites);
  $("#listing-stat-duplicate-groups").textContent=String(duplicates.duplicateGroups.length);
  $("#listing-stat-duplicates").classList.toggle("has-duplicates",duplicates.duplicateGroups.length>0);
}
function renderListings(){
  const rows=(state.data?.listings||[]).map((row,_index)=>({...row,_index}));
  const q=$("#listing-search").value.trim().toLowerCase();
  const status=$("#listing-status").value;
  const interest=$("#listing-interest").value;
  const duplicateMode=$("#listing-duplicate").value;
  const minRaw=parseFloat($("#listing-price-min").value);
  const maxRaw=parseFloat($("#listing-price-max").value);
  const minCents=Number.isFinite(minRaw)?Math.round(minRaw*100):null;
  const maxCents=Number.isFinite(maxRaw)?Math.round(maxRaw*100):null;
  const sort=$("#listing-sort").value;

  const baseFiltered=rows
    .filter(x=>!status||String(x.status||"").toLowerCase()===status)
    .filter(x=>!q||String(x.title||"").toLowerCase().includes(q))
    .filter(x=>interest!=="liked"||Number(x.favourites||0)>0)
    .filter(x=>interest!=="unliked"||Number(x.favourites||0)===0)
    .filter(x=>minCents===null||x.price_cents===null||x.price_cents===undefined||Number(x.price_cents)>=minCents)
    .filter(x=>maxCents===null||x.price_cents===null||x.price_cents===undefined||Number(x.price_cents)<=maxCents);

  const duplicates=duplicateInfo(baseFiltered);
  const filtered=baseFiltered
    .filter(x=>duplicateMode!=="duplicates"||duplicates.counts.has(x._index))
    .filter(x=>duplicateMode!=="unique"||!duplicates.counts.has(x._index))
    .sort(listingComparator(sort));

  const statsDuplicates=duplicateInfo(filtered);
  renderListingStats(filtered,duplicateMode==="unique"?statsDuplicates:duplicates);
  $("#listing-count").textContent=`${filtered.length} of ${rows.length}`;

  const showLikes=filtered.some(x=>x.favourites!==null&&x.favourites!==undefined);
  const showViews=filtered.some(x=>x.views!==null&&x.views!==undefined);
  const showDate=filtered.some(x=>timeValue(x.listed_at)>0);
  const extraHeaders=`${showDate?"<th>Listed</th><th>Age</th>":""}${showLikes?"<th>Favorites</th>":""}${showViews?"<th>Views</th>":""}`;

  $("#listings-table").innerHTML=filtered.length?`
    <table><thead><tr><th>Listing</th><th>Status</th>${extraHeaders}<th class="money">Price</th></tr></thead><tbody>
    ${filtered.map(x=>{
      const duplicateCount=duplicates.counts.get(x._index);
      return `<tr class="${duplicateCount?"duplicate-row":""}">
        <td class="title-cell">
          <div class="listing-title-line">${linkedTitle(x)}${duplicateCount?`<span class="duplicate-pill" title="Potential duplicate title">${duplicateCount} copies</span>`:""}</div>
        </td>
        <td>${badge(x.status)}</td>
        ${showDate?`<td>${dateOnly(x.listed_at)}</td><td>${age(x.listed_at)}</td>`:""}
        ${showLikes?`<td>${x.favourites??"—"}</td>`:""}
        ${showViews?`<td>${x.views??"—"}</td>`:""}
        <td class="money">${money(x.price_cents,x.currency)}</td>
      </tr>`;
    }).join("")}</tbody></table>`:empty("No matching Vinted listings.");
}

function filterOrders(rows,scope){
  if(scope==="closed")return rows.filter(o=>o.is_closed);
  if(scope==="open")return rows.filter(o=>!o.is_closed);
  return rows;
}
function orderTable(rows){
  if(!rows.length)return empty(state.data?.auth?.authenticated?"No orders returned by Vinted.":"Connect your Vinted session to load orders.");
  return `<table><thead><tr><th>Order</th><th>Status</th><th>Person</th><th>Updated</th><th class="money">Total</th></tr></thead><tbody>
    ${rows.map(o=>`<tr><td class="title-cell">${linkedTitle(o)}</td><td>${badge(o.status)}</td><td>${esc(o.counterparty||"—")}</td><td>${when(o.updated_at)}</td><td class="money">${money(o.total_cents,o.currency)}</td></tr>`).join("")}
  </tbody></table>`;
}
function renderOrders(){
  $("#sales-table").innerHTML=orderTable(filterOrders(state.data?.sales||[],$("#sales-scope").value));
  $("#purchases-table").innerHTML=orderTable(filterOrders(state.data?.purchases||[],$("#purchases-scope").value));
}
function renderNotifications(){
  const mode=$("#notification-filter").value;
  const rows=notificationFeed(mode);
  const raw=state.data?.notifications||[];
  const favoriteCount=raw.filter(isFavoriteNotification).length;
  const usefulCount=raw.length-favoriteCount;
  $("#notification-count").textContent=mode==="favorites"
    ? `${favoriteCount} events · ${rows.length} listings`
    : mode==="useful"
      ? `${usefulCount} useful`
      : `${raw.length} events`;
  $("#notifications-list").innerHTML=rows.length
    ? rows.map(notificationRow).join("")
    : empty(state.data?.auth?.authenticated
      ? mode==="useful"?"No useful notifications right now.":"No notifications in this view."
      : "Connect your Vinted session to load notifications.");
}

function render(){
  const profile=state.data?.profile||{};
  if(profile.profile_url)$("#profile-link").href=profile.profile_url;
  renderErrors();renderSession();renderSummary();renderListings();renderOrders();renderNotifications();
}

async function load(refresh=false){
  try{
    state.data=await api(refresh?"/api/dashboard?refresh=true":"/api/dashboard");
    render();
  }catch(err){flash(err.message,true)}
}

$$(".nav-item").forEach(btn=>btn.addEventListener("click",()=>{
  $$(".nav-item").forEach(x=>x.classList.toggle("active",x===btn));
  $$(".view").forEach(x=>x.classList.toggle("active",x.id===btn.dataset.view));
  $("#page-title").textContent=btn.childNodes[0].textContent.trim();
}));
$("#listing-search").addEventListener("input",renderListings);
$("#listing-status").addEventListener("change",renderListings);
$("#listing-interest").addEventListener("change",renderListings);
$("#listing-duplicate").addEventListener("change",renderListings);
$("#listing-price-min").addEventListener("input",renderListings);
$("#listing-price-max").addEventListener("input",renderListings);
$("#listing-sort").addEventListener("change",renderListings);
$("#listing-stat-duplicates").addEventListener("click",()=>{
  $("#listing-duplicate").value="duplicates";
  renderListings();
});
$("#notification-filter").addEventListener("change",renderNotifications);
$("#sales-scope").addEventListener("change",renderOrders);
$("#purchases-scope").addEventListener("change",renderOrders);

$("#refresh-btn").addEventListener("click",async()=>{
  const btn=$("#refresh-btn");btn.disabled=true;btn.textContent="Refreshing…";
  try{
    state.data=await api("/api/refresh",{method:"POST"});
    render();
    flash("Refreshed directly from Vinted.");
  }catch(err){flash(err.message,true)}
  finally{btn.disabled=false;btn.textContent="Refresh Vinted"}
});

const dialog=$("#session-dialog");
$("#session-btn").addEventListener("click",()=>{ $("#session-cookie").value=""; dialog.showModal(); });
$("#session-close").addEventListener("click",()=>dialog.close());
$("#session-cancel").addEventListener("click",()=>dialog.close());
$("#session-form").addEventListener("submit",async e=>{
  e.preventDefault();
  const cookie=$("#session-cookie").value.trim();
  if(!cookie){flash("Paste the Vinted Cookie header first.",true);return}
  try{
    const result=await api("/api/session",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({cookie})});
    $("#dialog-session-status").textContent=result.authenticated?`Connected as ${result.current_user?.username||result.current_user?.id}`:"Session saved";
    $("#session-cookie").value="";
    dialog.close();
    await load(true);
    flash("Vinted session connected.");
  }catch(err){$("#dialog-session-status").textContent=err.message;flash(err.message,true)}
});
$("#session-clear").addEventListener("click",async()=>{
  try{
    await api("/api/session",{method:"DELETE"});
    $("#session-cookie").value="";
    dialog.close();
    await load(true);
    flash("Saved Vinted session cleared.");
  }catch(err){flash(err.message,true)}
});

load();
