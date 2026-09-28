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

  const notifications=d.notifications||[];
  $("#recent-notifications").innerHTML=notifications.slice(0,5).map(n=>`
    <div class="row"><div><div class="row-title">${n.url?`<a href="${esc(n.url)}" target="_blank" rel="noreferrer">${esc(n.title)}</a>`:esc(n.title)}</div><div class="meta">${when(n.occurred_at)}</div></div>${n.read?"":'<span class="badge">new</span>'}</div>
  `).join("")||empty(d.auth?.authenticated?"No notifications returned by Vinted.":"Connect your Vinted session to load notifications.");
}

function renderListings(){
  const rows=state.data?.listings||[];
  const q=$("#listing-search").value.trim().toLowerCase();
  const status=$("#listing-status").value;
  const filtered=rows.filter(x=>(!status||x.status===status)&&(!q||x.title.toLowerCase().includes(q)||(x.isbn||"").includes(q)));
  $("#listings-table").innerHTML=filtered.length?`
    <table><thead><tr><th>Listing</th><th>Status</th><th>ISBN</th><th>Listed</th><th>Likes</th><th>Views</th><th class="money">Price</th></tr></thead><tbody>
    ${filtered.map(x=>`<tr>
      <td class="title-cell">${linkedTitle(x)}</td>
      <td>${badge(x.status)}</td>
      <td>${esc(x.isbn||"—")}</td>
      <td>${when(x.listed_at)}</td>
      <td>${x.favourites??"—"}</td>
      <td>${x.views??"—"}</td>
      <td class="money">${money(x.price_cents,x.currency)}</td>
    </tr>`).join("")}</tbody></table>`:empty("No matching Vinted listings.");
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
  const rows=state.data?.notifications||[];
  $("#notifications-list").innerHTML=rows.length?rows.map(n=>`
    <div class="notification ${n.read?"":"unread"}">
      <div class="dot"></div>
      <div><div class="notification-title">${n.url?`<a href="${esc(n.url)}" target="_blank" rel="noreferrer">${esc(n.title)}</a>`:esc(n.title)}</div><div class="notification-body">${esc(n.body||"")}</div></div>
      <div class="notification-time">${when(n.occurred_at)}</div>
    </div>`).join(""):empty(state.data?.auth?.authenticated?"No notifications returned by Vinted.":"Connect your Vinted session to load notifications.");
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
