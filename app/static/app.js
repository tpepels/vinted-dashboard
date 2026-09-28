const state={summary:null,listings:[],sales:[],purchases:[],notifications:[],settings:null};

const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];

function money(cents,currency="EUR"){
  if(cents===null||cents===undefined)return "—";
  return new Intl.NumberFormat(undefined,{style:"currency",currency}).format(cents/100);
}
function when(value){
  if(!value)return "—";
  const d=new Date(value);
  return new Intl.DateTimeFormat(undefined,{dateStyle:"medium",timeStyle:"short"}).format(d);
}
function esc(value=""){
  return String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"}[c]));
}
function badge(status){return `<span class="badge ${esc(status)}">${esc(status.replaceAll("_"," "))}</span>`;}
function empty(text){return `<div class="empty">${esc(text)}</div>`;}
function flash(message,error=false){
  $("#flash").innerHTML=`<div class="flash ${error?"error":""}">${esc(message)}</div>`;
  setTimeout(()=>$("#flash").innerHTML="",4500);
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

function renderSummary(){
  const s=state.summary||{};
  $("#stat-active").textContent=s.active_listings||0;
  $("#stat-sales").textContent=s.open_sales||0;
  $("#stat-purchases").textContent=s.open_purchases||0;
  $("#stat-unread").textContent=s.unread_notifications||0;
  $("#nav-listings").textContent=s.active_listings?String(s.active_listings):"";
  $("#nav-sales").textContent=s.open_sales?String(s.open_sales):"";
  $("#nav-purchases").textContent=s.open_purchases?String(s.open_purchases):"";
  $("#nav-notifications").textContent=s.unread_notifications?String(s.unread_notifications):"";

  const attention=s.attention||[];
  $("#attention").innerHTML=attention.length?attention.map(o=>`
    <div class="row">
      <div><div class="row-title">${esc(o.title)}</div><div class="meta">${o.direction==="sell"?"Sale":"Purchase"} · ${o.counterparty?esc(o.counterparty)+" · ":""}updated ${when(o.updated_at)}</div></div>
      <div>${badge(o.status)}</div>
    </div>`).join(""):empty("Nothing needs attention.");

  $("#recent-sales").innerHTML=state.sales.slice(0,5).map(o=>`
    <div class="row"><div><div class="row-title">${esc(o.title)}</div><div class="meta">${money(o.total_cents,o.currency)} · ${when(o.updated_at)}</div></div>${badge(o.status)}</div>
  `).join("")||empty("No sales yet.");

  $("#recent-notifications").innerHTML=state.notifications.slice(0,5).map(n=>`
    <div class="row"><div><div class="row-title">${esc(n.title)}</div><div class="meta">${when(n.occurred_at)}</div></div>${n.read_at?"":'<span class="badge">new</span>'}</div>
  `).join("")||empty("No Vinted notifications yet.");
}

function renderListings(){
  const q=$("#listing-search").value.trim().toLowerCase();
  const status=$("#listing-status").value;
  const rows=state.listings.filter(x=>(!status||x.status===status)&&(!q||x.title.toLowerCase().includes(q)||(x.isbn||"").includes(q)));
  $("#listings-table").innerHTML=rows.length?`
    <table><thead><tr><th>Listing</th><th>Status</th><th>ISBN</th><th>Listed</th><th class="money">Price</th></tr></thead><tbody>
    ${rows.map(x=>`<tr>
      <td class="title-cell">${x.vinted_url?`<a href="${esc(x.vinted_url)}" target="_blank" rel="noreferrer">${esc(x.title)}</a>`:esc(x.title)}</td>
      <td>${badge(x.status)}</td><td>${esc(x.isbn||"—")}</td><td>${when(x.listed_at||x.created_at)}</td><td class="money">${money(x.price_cents,x.currency)}</td>
    </tr>`).join("")}</tbody></table>`:empty("No matching listings.");
}
function orderTable(rows){
  if(!rows.length)return empty("No orders yet.");
  return `<table><thead><tr><th>Order</th><th>Status</th><th>Person</th><th>Updated</th><th class="money">Total</th></tr></thead><tbody>
    ${rows.map(o=>`<tr><td class="title-cell">${esc(o.title)}</td><td>${badge(o.status)}</td><td>${esc(o.counterparty||"—")}</td><td>${when(o.updated_at)}</td><td class="money">${money(o.total_cents,o.currency)}</td></tr>`).join("")}
  </tbody></table>`;
}
function renderOrders(){
  $("#sales-table").innerHTML=orderTable(state.sales);
  $("#purchases-table").innerHTML=orderTable(state.purchases);
}
function renderNotifications(){
  $("#notifications-list").innerHTML=state.notifications.length?state.notifications.map(n=>`
    <div class="notification ${n.read_at?"":"unread clickable"}" data-id="${n.id}">
      <div class="dot"></div>
      <div><div class="notification-title">${esc(n.title)}</div><div class="notification-body">${esc(n.body||"")}</div></div>
      <div class="notification-time">${when(n.occurred_at)}</div>
    </div>`).join(""):empty("No notifications yet.");
  $$(".notification.unread").forEach(el=>el.addEventListener("click",async()=>{
    await api(`/api/notifications/${el.dataset.id}/read`,{method:"POST"});
    await load();
  }));
}

async function load(){
  try{
    const [summary,listings,sales,purchases,notifications,settings]=await Promise.all([
      api("/api/summary"),api("/api/listings"),api("/api/orders?direction=sell"),api("/api/orders?direction=buy"),api("/api/notifications"),api("/api/settings")
    ]);
    Object.assign(state,{summary,listings,sales,purchases,notifications,settings});
    $("#profile-link").href=settings.profile_url;
    $("#sync-source").textContent=settings.email_sync_configured?"Email sync configured":"Email sync needs credentials";
    renderSummary();renderListings();renderOrders();renderNotifications();
  }catch(err){flash(err.message,true)}
}

$$(".nav-item").forEach(btn=>btn.addEventListener("click",()=>{
  $$(".nav-item").forEach(x=>x.classList.toggle("active",x===btn));
  $$(".view").forEach(x=>x.classList.toggle("active",x.id===btn.dataset.view));
  $("#page-title").textContent=btn.textContent.replace(/\d+/g,"").trim();
}));
$("#listing-search").addEventListener("input",renderListings);
$("#listing-status").addEventListener("change",renderListings);

$("#import-btn").addEventListener("click",()=>$("#csv-file").click());
$("#csv-file").addEventListener("change",async e=>{
  const file=e.target.files[0]; if(!file)return;
  const form=new FormData();form.append("file",file);
  try{const result=await api("/api/listings/import-csv",{method:"POST",body:form});flash(`Imported ${result.imported}, updated ${result.updated} listings.`);await load()}catch(err){flash(err.message,true)}
  e.target.value="";
});

$("#sync-btn").addEventListener("click",async()=>{
  const btn=$("#sync-btn");btn.disabled=true;btn.textContent="Syncing…";
  try{const r=await api("/api/sync/email",{method:"POST"});flash(`Email sync checked ${r.checked} messages, found ${r.vinted_messages} Vinted messages and imported ${r.imported} new events.`);await load()}catch(err){flash(err.message,true)}
  finally{btn.disabled=false;btn.textContent="Sync email"}
});
$("#read-all-btn").addEventListener("click",async()=>{await api("/api/notifications/read-all",{method:"POST"});await load()});

const dialog=$("#listing-dialog");
$("#add-listing-btn").addEventListener("click",()=>dialog.showModal());
$("#listing-form").addEventListener("submit",async e=>{
  e.preventDefault();
  const data=Object.fromEntries(new FormData(e.target).entries());
  if(data.price==="")data.price=null;else if(data.price!==null)data.price=Number(data.price);
  try{await api("/api/listings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});dialog.close();e.target.reset();await load()}catch(err){flash(err.message,true)}
});

load();
