function first(obj,...keys){if(!obj||typeof obj!=="object")return null;for(const key of keys){const v=obj[key];if(v!==undefined&&v!==null&&v!=="")return v}return null}
function idOf(v){if(v&&typeof v==="object")v=first(v,"id","user_id");return v==null||v===""?null:String(v)}
function nameOf(v){if(v&&typeof v==="object")v=first(v,"login","username","name","display_name");return v==null||v===""?null:String(v)}
function money(v){let currency="EUR";if(v&&typeof v==="object"){currency=String(first(v,"currency_code","currency","code")||"EUR");v=first(v,"amount","value","price")}if(v==null||v==="")return [null,currency];const n=Number.parseFloat(String(v).replace("€","").replaceAll(" ","").replace(",","."));return [Number.isFinite(n)?Math.round(n*100):null,currency]}
function stamp(v){if(v==null||v==="")return null;const t=String(v).trim();if(/^\d+(?:\.\d+)?$/.test(t)){let n=Number(t);if(n<1e11)n*=1000;const d=new Date(n);if(!Number.isNaN(d.getTime()))return d.toISOString()}return String(v)}
function listFrom(payload,keys){if(Array.isArray(payload))return payload;if(!payload||typeof payload!=="object")return[];for(const k of keys)if(Array.isArray(payload[k]))return payload[k];const d=payload.data;if(Array.isArray(d))return d;if(d&&typeof d==="object")for(const k of keys)if(Array.isArray(d[k]))return d[k];return[]}
function closed(v){const s=String(v||"").toLowerCase().replaceAll("-","_").replaceAll(" ","_");return["cancelled","canceled","completed","complete","closed","finished","refunded","failed"].some(x=>s.includes(x))}
function listingStatus(raw,forced){if(raw?.is_reserved)return"reserved";if(raw?.is_hidden)return"hidden";if(raw?.is_draft)return"draft";if(raw?.is_closed||raw?.is_sold)return"sold";if(forced)return forced==="closed"?"sold":forced;const s=String(first(raw,"state","item_status","listing_status")||"").toLowerCase();return["sold","reserved","hidden","draft"].includes(s)?s:"active"}
function listingRow(raw,forced){const [price,currency]=money(first(raw,"price","total_item_price","item_price"));const itemId=idOf(first(raw,"id","item_id"));let url=first(raw,"url","web_url");if(url)url=new URL(String(url),location.origin).href;else if(itemId)url=`${location.origin}/items/${itemId}`;return{id:itemId,title:String(first(raw,"title","name")||"Untitled"),price_cents:price,currency,status:listingStatus(raw,forced),vinted_url:url||null,image_url:first(raw?.photo,"full_size_url","url")||null,listed_at:stamp(first(raw,"created_at_ts","created_timestamp_ts","created_at","uploaded_at","posted_at","upload_date_dte")),favourites:first(raw,"favourite_count","favorites_count","favourites_count"),views:first(raw,"view_count","views_count")}}
function orderRow(raw,direction){const [price,currency]=money(first(raw,"price","total_price","total","amount"));const lifecycle=String(first(raw,"transaction_user_status","state")||"").toLowerCase().trim().replaceAll(" ","_").replaceAll("-","_");const status=String(first(raw,"status")||lifecycle||"open").toLowerCase().trim().replaceAll(" ","_").replaceAll("-","_");const thread=first(raw,"conversation_id","thread_id");const oid=first(raw,"id","transaction_id","order_id")||thread;let url=first(raw,"url","web_url","link");if(!url&&thread)url=`${location.origin}/inbox/${thread}`;else if(url)url=new URL(String(url),location.origin).href;return{id:String(oid||""),thread_id:String(thread||""),direction,title:String(first(raw,"title","item_title","name")||"Vinted order"),counterparty:nameOf(first(raw,"opposite_user","other_user","user")),total_cents:price,currency,status,lifecycle_status:lifecycle||status,is_closed:closed(lifecycle||status),tracking_code:first(raw,"tracking_code","tracking_number","shipment_tracking_code"),updated_at:stamp(first(raw,"updated_at","date","created_at","created_at_ts")),vinted_url:url||null}}
function notificationRow(raw){let body=first(raw,"body","text","message","description");if(body&&typeof body==="object")body=first(body,"text","value");body=String(body||"");const title=String(first(raw,"title","subject","heading")||body||"Vinted notification");let url=first(raw,"link","url","deep_link","target_url");if(url)url=new URL(String(url),location.origin).href;let category="other",item_id=null,item_title=null,actor=null;if(String(url||"").includes("/want_it/")){category="favorite";item_id=String(url).match(/\/items\/(\d+)/)?.[1]||null;const m=body.match(/^(.+?) adicionou o teu (.+?) aos seus favoritos\.?$/i)||body.match(/^(.+?) added your (.+?) to (?:their|his|her) favou?rites\.?$/i);if(m){actor=m[1].trim();item_title=m[2].trim()}}return{id:String(first(raw,"id","notification_id")||""),kind:String(first(raw,"entry_type","type","notification_type","event_type")||"notification"),category,item_id,item_title,actor,title,body,occurred_at:stamp(first(raw,"updated_at","created_at","created_at_ts","timestamp")),read:Boolean(first(raw,"is_read","read","seen")||first(raw,"read_at","seen_at")),url:url||null}}

async function fetchJson(path,params={}){const url=new URL(path,location.origin);for(const[k,v]of Object.entries(params))url.searchParams.set(k,String(v));const r=await fetch(url,{headers:{"Accept":"application/json, text/plain, */*","X-Platform":"web"}});if(r.status===404)return null;if(!r.ok)throw new Error(`Vinted returned HTTP ${r.status} for ${url.pathname}`);return await r.json()}
async function paged(path,keys,params={},perPage=96){const rows=[];for(let page=1;page<=10;page++){const payload=await fetchJson(path,{...params,page,per_page:perPage});if(payload===null)return null;const chunk=listFrom(payload,keys);rows.push(...chunk);if(!chunk.length)break;const p=payload?.pagination;if(p&&typeof p==="object"){if(Number.isInteger(p.total_pages)&&page>=p.total_pages)break;if(p.next_page==null&&chunk.length<perPage)break}else if(chunk.length<perPage)break}return rows}

async function collectVintedData(researchJobs=[]){
  const currentPayload=await fetchJson("/api/v2/users/current");
  const currentRaw=currentPayload?.user||currentPayload||{};
  const userId=idOf(currentRaw);
  if(!userId)throw new Error("This Vinted tab is not signed in.");

  let profileRaw=currentRaw;
  try{
    const profilePayload=await fetchJson(`/api/v2/users/${userId}`,{localize:"false"});
    profileRaw=profilePayload?.user||profilePayload||currentRaw;
  }catch{}

  const countValue=(...keys)=>{
    const value=first(profileRaw,...keys);
    if(Array.isArray(value))return value.length;
    const parsed=Number(value);
    return Number.isFinite(parsed)?Math.round(parsed):null;
  };
  const currentUser={
    id:userId,
    username:nameOf(profileRaw)||nameOf(currentRaw),
    followers_count:countValue("followers_count","follower_count","followers"),
    following_count:countValue("following_count","followings_count","following")
  };

  const listings=new Map();
  for(const status of["active","sold","reserved","draft","closed"]){
    try{const rows=await paged(`/api/v2/users/${userId}/items`,["items","user_items"],{status,order:"newest_first"});for(const raw of rows||[]){const row=listingRow(raw,status);if(row.id)listings.set(row.id,row)}}catch{}
  }
  try{const rows=await paged(`/api/v2/wardrobe/${userId}/items`,["items","user_items"],{order:"newest_first"});for(const raw of rows||[]){const row=listingRow(raw,null);if(!row.id)continue;const old=listings.get(row.id);if(!old||row.status==="hidden")listings.set(row.id,row)}}catch{}

  let notifications=[];
  for(const path of["/api/v2/notifications","/web/api/notifications/notifications"]){try{const payload=await fetchJson(path,{page:1,per_page:100});if(payload!==null){notifications=listFrom(payload,["notifications","items","entries"]).map(notificationRow);break}}catch{}}

  const orders=[];
  for(const[type,direction]of[["sold","sell"],["purchased","buy"]]){try{const rows=await paged("/api/v2/my_orders",["my_orders","orders","items"],{type,status:"all"},100);for(const raw of rows||[])orders.push(orderRow(raw,direction))}catch{}}

  return{collected_at:Date.now()/1000,current_user:currentUser,listings:[...listings.values()],notifications,orders,market_results:[]};
}

function parseCatalogPrice(text){
  const source=String(text||"").replace(/\u00a0/g," ");
  const patterns=[
    /€\s*([0-9]+(?:[.,][0-9]{1,2})?)/,
    /([0-9]+(?:[.,][0-9]{1,2})?)\s*€/,
    /EUR\s*([0-9]+(?:[.,][0-9]{1,2})?)/i,
    /([0-9]+(?:[.,][0-9]{1,2})?)\s*EUR/i
  ];
  for(const pattern of patterns){
    const match=source.match(pattern);
    if(!match)continue;
    const value=Number.parseFloat(match[1].replace(",","."));
    if(Number.isFinite(value)&&value>0)return Math.round(value*100);
  }
  return null;
}

function catalogCardFor(anchor){
  let node=anchor;
  for(let depth=0;depth<7&&node;depth++,node=node.parentElement){
    const text=String(node.innerText||node.textContent||"");
    if(parseCatalogPrice(text)!==null&&text.length<1800)return node;
  }
  return anchor.parentElement||anchor;
}

function titleFromCatalogCard(anchor,card,id){
  const candidates=[
    anchor.getAttribute("title"),
    anchor.getAttribute("aria-label"),
    anchor.querySelector("img")?.getAttribute("alt"),
    card?.querySelector("img")?.getAttribute("alt")
  ].filter(Boolean);
  for(const candidate of candidates){
    const text=String(candidate).trim();
    if(text&&text.length>2&&!/^imagem|^image$/i.test(text))return text;
  }
  try{
    const url=new URL(anchor.href,location.origin);
    const match=url.pathname.match(new RegExp("/items/"+id+"-([^/?#]+)"));
    if(match)return decodeURIComponent(match[1]).replaceAll("-"," ").trim();
  }catch{}
  const text=String(anchor.textContent||"").trim().replace(/\s+/g," ");
  return text||"Vinted listing";
}

async function scrapeMarketPage(){
  window.scrollTo(0,Math.min(document.body.scrollHeight,1800));
  await new Promise(resolve=>setTimeout(resolve,700));

  const results=[];
  const seen=new Set();
  const anchors=[...document.querySelectorAll('a[href*="/items/"]')];
  for(const anchor of anchors){
    let url;
    try{url=new URL(anchor.href,location.origin)}catch{continue}
    const match=url.pathname.match(/\/items\/(\d+)/);
    if(!match)continue;
    const id=match[1];
    if(seen.has(id))continue;
    const card=catalogCardFor(anchor);
    const price=parseCatalogPrice(card?.innerText||card?.textContent||"");
    if(price===null)continue;
    const title=titleFromCatalogCard(anchor,card,id);
    seen.add(id);
    results.push({
      id,
      title,
      price_cents:price,
      currency:"EUR",
      url:url.href.split("?")[0]
    });
    if(results.length>=60)break;
  }
  return{
    results,
    anchors_seen:anchors.length,
    cards_with_price:results.length,
    page_title:document.title,
    page_url:location.href
  };
}

chrome.runtime.onMessage.addListener((message,_sender,sendResponse)=>{
  if(message?.type==="collect-vinted-data"){
    collectVintedData().then(snapshot=>sendResponse({ok:true,snapshot})).catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));
    return true;
  }
  if(message?.type==="scrape-market-page"){
    scrapeMarketPage().then(result=>sendResponse({ok:true,...result})).catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));
    return true;
  }
});
