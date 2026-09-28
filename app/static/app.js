const state={data:null,intelligence:null,viewListingId:null};

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
function shortDay(value){
  const d=new Date(`${value}T00:00:00Z`);
  return Number.isNaN(d.getTime())?String(value):d.toLocaleDateString(undefined,{month:"short",day:"numeric"});
}
function renderLineChart(selector,rows,key,{emptyText="Not enough history yet.",suffix="",valuePrefix=""}={}){
  const el=$(selector);
  if(!el)return;
  const data=(rows||[])
    .map(row=>({day:row.day,value:Number(row[key])}))
    .filter(row=>row.day&&Number.isFinite(row.value));
  if(!data.length){el.innerHTML=empty(emptyText);return}

  const width=760,height=220,left=48,right=18,top=20,bottom=34;
  const values=data.map(row=>row.value);
  let min=Math.min(...values),max=Math.max(...values);
  if(min===max){min=Math.max(0,min-1);max=max+1}
  const x=i=>data.length===1?(left+(width-right))/2:left+i*((width-left-right)/(data.length-1));
  const y=value=>top+(max-value)*((height-top-bottom)/(max-min));
  const points=data.map((row,i)=>`${x(i).toFixed(1)},${y(row.value).toFixed(1)}`).join(" ");
  const grid=[0,.25,.5,.75,1].map(p=>{
    const yy=top+p*(height-top-bottom);
    const value=Math.round(max-p*(max-min));
    return `<g><line x1="${left}" x2="${width-right}" y1="${yy}" y2="${yy}" class="chart-grid-line"/><text x="${left-8}" y="${yy+4}" text-anchor="end" class="chart-axis-label">${esc(valuePrefix+` ${value}`.trim()+suffix)}</text></g>`;
  }).join("");
  const labels=[
    data[0],
    data[Math.floor((data.length-1)/2)],
    data[data.length-1]
  ].filter((row,index,array)=>array.findIndex(x=>x.day===row.day)===index);
  const labelSvg=labels.map(row=>{
    const i=data.indexOf(row);
    return `<text x="${x(i)}" y="${height-8}" text-anchor="${i===0?"start":i===data.length-1?"end":"middle"}" class="chart-axis-label">${esc(shortDay(row.day))}</text>`;
  }).join("");
  const dots=data.map((row,i)=>`<circle cx="${x(i)}" cy="${y(row.value)}" r="3" class="chart-point"><title>${esc(shortDay(row.day))}: ${esc(valuePrefix+row.value+suffix)}</title></circle>`).join("");
  el.innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Trend chart">${grid}<polyline points="${points}" class="chart-line"/>${dots}${labelSvg}</svg>`;
}
function renderListingViewsTrend(){
  const views=state.intelligence?.views||{};
  const rows=views.listings||[];
  if(!rows.length){
    $("#view-listing-select").innerHTML='<option>No view history yet</option>';
    renderLineChart("#listing-views-chart",[],"views",{emptyText:"View history will appear after Chrome sync records listing views."});
    return;
  }
  const select=$("#view-listing-select");
  const current=state.viewListingId&&rows.some(row=>String(row.listing_id)===String(state.viewListingId))
    ?String(state.viewListingId)
    :String(rows[0].listing_id);
  state.viewListingId=current;
  select.innerHTML=rows.map(row=>`<option value="${esc(row.listing_id)}"${String(row.listing_id)===current?" selected":""}>${esc(row.title)} · ${row.views??0} views</option>`).join("");
  const selected=rows.find(row=>String(row.listing_id)===current);
  renderLineChart("#listing-views-chart",selected?.daily||[],"views",{emptyText:"Only one snapshot so far. More points will appear automatically."});
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
function signalMap(){
  return new Map((state.intelligence?.listing_signals||[]).map(row=>[String(row.id),row]));
}
function marketByListing(){
  const map=new Map();
  for(const row of state.intelligence?.market?.recent||[]){
    const key=String(row.listing_id||"");
    if(key&&!map.has(key))map.set(key,row);
  }
  return map;
}
function marketCell(row){
  const signal=signalMap().get(String(row.id));
  const market=signal?.market||marketByListing().get(String(row.id));
  if(market?.median_cents!==null&&market?.median_cents!==undefined){
    return `<div class="market-cell"><strong>${money(market.median_cents,market.currency||row.currency||"EUR")}</strong><span>median · ${market.sample_count||0} comps</span><button class="link-button research-btn" data-id="${esc(row.id)}">Refresh</button></div>`;
  }
  if(market?.status==="queued"){
    return `<span class="market-pending">Queued</span>`;
  }
  return `<button class="link-button research-btn" data-id="${esc(row.id)}">Research</button>`;
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
  const sync=state.data?.browser_sync||{};
  let label;
  if(sync.active){
    const who=auth.current_user?.username||auth.current_user?.id||"Vinted user";
    label=`Chrome sync · ${who}${sync.collected_at?` · ${when(Number(sync.collected_at)*1000)}`:""}`;
  }else if(auth.authenticated){
    label=`Server session · ${auth.current_user?.username||auth.current_user?.id||"Vinted user"} · ${auth.refresh_token_available?"auto-refresh ready":"refresh token missing"}`;
  }else{
    label=auth.configured?"Session configured, but not authenticated":"Public data only";
  }
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
  const signals=signalMap();
  const extraHeaders=`${showDate?"<th>Listed</th><th>Age</th>":""}${showLikes?"<th>Favorites</th>":""}${showViews?"<th>Views</th>":""}<th>Market</th>`;

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
        ${showLikes?(()=>{
          const delta=signals.get(String(x.id))?.favourites_7d;
          return `<td>${x.favourites??"—"}${Number.isFinite(delta)&&delta!==0?` <span class="delta ${delta>0?"up":"down"}">${delta>0?"+":""}${delta}/7d</span>`:""}</td>`;
        })():""}
        ${showViews?(()=>{
          const delta=signals.get(String(x.id))?.views_7d;
          return `<td>${x.views??"—"}${Number.isFinite(delta)&&delta!==0?` <span class="delta ${delta>0?"up":"down"}">${delta>0?"+":""}${delta}/7d</span>`:""}</td>`;
        })():""}
        <td>${marketCell(x)}</td>
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

function renderInsights(){
  const d=state.intelligence||{};
  const today=d.today||{};
  const stale=d.stale||{};
  const sales=d.sales||{};
  const favorites=d.favorites||{};
  const audience=d.audience||{};
  const views=d.views||{};
  const market=d.market||{};

  $("#insight-actions").textContent=String(today.high_priority||0);
  $("#insight-stale90").textContent=String(stale.count_90d||0);
  $("#insight-favorites7").textContent=String(favorites.events_7d||0);
  $("#insight-net").textContent=money(sales.net_cashflow_cents||0,"EUR");
  $("#nav-insights").textContent=today.high_priority?String(today.high_priority):"";
  $("#today-action-count").textContent=`${today.count||0} suggestions`;

  $("#followers-current").textContent=audience.followers??"—";
  const followerBits=[];
  if(Number.isFinite(audience.followers_change_7d))followerBits.push(`${audience.followers_change_7d>=0?"+":""}${audience.followers_change_7d} / 7d`);
  if(Number.isFinite(audience.followers_change))followerBits.push(`${audience.followers_change>=0?"+":""}${audience.followers_change} since tracking`);
  $("#followers-change").textContent=followerBits.join(" · ");
  renderLineChart("#followers-chart",audience.daily||[],"followers",{emptyText:"Follower history starts with the next Chrome sync."});

  $("#views-total").textContent=views.total_active_views??"—";
  $("#views-gained7").textContent=Number.isFinite(views.views_gained_7d)?`+${views.views_gained_7d} views / 7d`:"";
  renderLineChart("#views-gained-chart",views.daily_views_gained||[],"views_gained",{emptyText:"Daily view gains appear after at least two Chrome syncs."});
  renderListingViewsTrend();

  const actions=today.actions||[];
  $("#today-actions").innerHTML=actions.length?actions.map(a=>`
    <div class="action-row priority-${a.priority>=90?"high":a.priority>=80?"medium":"low"}">
      <div class="action-priority">${a.priority>=90?"Now":a.priority>=80?"Review":"Watch"}</div>
      <div class="action-copy">
        <div class="row-title">${a.vinted_url?`<a href="${esc(a.vinted_url)}" target="_blank" rel="noreferrer">${esc(a.title)}</a>`:esc(a.title)}</div>
        <div class="action-name">${esc(a.action)}</div>
        <div class="meta">${esc(a.reason)} · ${a.favourites||0} favorites${Number.isFinite(a.favourites_7d)?` · ${a.favourites_7d>=0?"+":""}${a.favourites_7d}/7d`:""}</div>
      </div>
      <div class="action-buttons">
        ${a.action==="Research market"||!a.market?`<button class="btn secondary research-btn" data-id="${esc(a.listing_id)}">Research</button>`:""}
        ${a.market?.median_cents!=null?`<span class="market-price">${money(a.market.median_cents,a.market.currency||"EUR")} median</span>`:""}
      </div>
    </div>`).join(""):empty("No intelligence actions yet. History becomes more useful after a few Chrome syncs.");

  $("#analytics-sales").textContent=money(sales.sales_cents||0,"EUR");
  $("#analytics-average").textContent=money(sales.average_sale_cents,"EUR");
  $("#analytics-median").textContent=money(sales.median_sale_cents,"EUR");
  $("#analytics-days-to-sell").textContent=sales.median_days_to_sell==null?"Building history":`${sales.median_days_to_sell} days`;

  const monthly=sales.monthly||[];
  const max=Math.max(1,...monthly.map(x=>Number(x.cents)||0));
  $("#sales-monthly-chart").innerHTML=monthly.length?monthly.map(x=>{
    const height=Math.max(5,Math.round((Number(x.cents)||0)/max*100));
    const label=new Date(`${x.month}-01T00:00:00Z`).toLocaleDateString(undefined,{month:"short"});
    return `<div class="bar-column" title="${esc(label)}: ${money(x.cents,"EUR")}"><div class="bar-value">${money(x.cents,"EUR")}</div><div class="bar-track"><div class="bar-fill" style="height:${height}%"></div></div><div class="bar-label">${esc(label)}</div></div>`;
  }).join(""):empty("Sales history will appear after Chrome sync records orders.");

  $("#favorites-7d").textContent=String(favorites.events_7d||0);
  $("#favorites-30d").textContent=String(favorites.events_30d||0);
  const top=favorites.top_30d||[];
  $("#favorite-top-list").innerHTML=top.length?top.map(x=>`
    <div class="mini-row"><span>${esc(x.title||"Listing")}</span><strong>${x.events}</strong></div>
  `).join(""):empty("No favorite events recorded yet.");

  const staleRows=stale.listings||[];
  $("#stale-table").innerHTML=staleRows.length?`
    <table><thead><tr><th>Listing</th><th>Age</th><th>Favorites</th><th>7d change</th><th>Market</th><th class="money">Price</th></tr></thead><tbody>
      ${staleRows.map(x=>`<tr>
        <td class="title-cell">${linkedTitle(x)}</td>
        <td>${x.age_days} days</td>
        <td>${x.favourites??0}</td>
        <td>${Number.isFinite(x.favourites_7d)?`${x.favourites_7d>=0?"+":""}${x.favourites_7d}`:"—"}</td>
        <td>${marketCell(x)}</td>
        <td class="money">${money(x.price_cents,x.currency||"EUR")}</td>
      </tr>`).join("")}
    </tbody></table>`:empty("Nothing is 30+ days old yet, or listing dates are unavailable.");

  $("#market-queue-count").textContent=market.queued?`${market.queued} queued`:"";
  const recent=market.recent||[];
  $("#market-table").innerHTML=recent.length?`
    <table><thead><tr><th>Search</th><th>Status</th><th>Comparables</th><th>Range</th><th>Median</th><th>Checked</th></tr></thead><tbody>
      ${recent.map(x=>`<tr>
        <td class="title-cell">${esc(x.query)}</td>
        <td>${badge(x.status)}</td>
        <td>${x.sample_count??"—"}</td>
        <td>${x.min_cents==null?"—":`${money(x.min_cents,x.currency||"EUR")} - ${money(x.max_cents,x.currency||"EUR")}`}</td>
        <td><strong>${money(x.median_cents,x.currency||"EUR")}</strong></td>
        <td>${x.completed_at?when(Number(x.completed_at)*1000):x.requested_at?when(Number(x.requested_at)*1000):"—"}</td>
      </tr>`).join("")}
    </tbody></table>`:empty("No market research yet. Use Research on a listing.");
}


function render(){
  const profile=state.data?.profile||{};
  if(profile.profile_url)$("#profile-link").href=profile.profile_url;
  renderErrors();renderSession();renderSummary();renderInsights();renderListings();renderOrders();renderNotifications();
}

async function load(refresh=false){
  try{
    const dashboardPath=refresh?"/api/dashboard?refresh=true":"/api/dashboard";
    const [dashboard,intelligence]=await Promise.all([
      api(dashboardPath),
      api("/api/intelligence")
    ]);
    state.data=dashboard;
    state.intelligence=intelligence;
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
$("#view-listing-select").addEventListener("change",event=>{
  state.viewListingId=event.target.value;
  renderListingViewsTrend();
});
$("#listing-stat-duplicates").addEventListener("click",()=>{
  $("#listing-duplicate").value="duplicates";
  renderListings();
});
document.addEventListener("click",async event=>{
  const button=event.target.closest(".research-btn");
  if(!button)return;
  const id=String(button.dataset.id||"");
  const listing=(state.data?.listings||[]).find(row=>String(row.id)===id);
  if(!listing)return;
  button.disabled=true;
  const oldText=button.textContent;
  button.textContent="Queued…";
  try{
    await api("/api/market-research/request",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({listing_id:id,title:listing.title})
    });
    state.intelligence=await api("/api/intelligence");
    renderInsights();
    renderListings();
    flash("Market research queued. Chrome sync will pick it up automatically.");
  }catch(err){
    flash(err.message,true);
  }finally{
    if(button.isConnected){button.disabled=false;button.textContent=oldText}
  }
});
$("#notification-filter").addEventListener("change",renderNotifications);
$("#sales-scope").addEventListener("change",renderOrders);
$("#purchases-scope").addEventListener("change",renderOrders);

$("#refresh-btn").addEventListener("click",async()=>{
  const btn=$("#refresh-btn");btn.disabled=true;btn.textContent="Refreshing…";
  try{
    state.data=await api("/api/refresh",{method:"POST"});
    state.intelligence=await api("/api/intelligence");
    render();
    flash(state.data?.browser_sync?.active?"Refreshed from the latest Chrome snapshot.":"Refreshed directly from Vinted.");
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
    $("#dialog-session-status").textContent=result.authenticated
      ? `Connected as ${result.current_user?.username||result.current_user?.id} · ${result.refresh_token_available?"auto-refresh ready":"refresh token missing"}`
      : "Session saved";
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
