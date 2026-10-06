(() => {
if (globalThis.__RESELLER_DASHBOARD_VINTED_CONTENT_PROTOCOL__ === 7) return;
globalThis.__RESELLER_DASHBOARD_VINTED_CONTENT_PROTOCOL__ = 7;
const BRIDGE_CONTENT_PROTOCOL=7;
function first(obj,...keys){if(!obj||typeof obj!=="object")return null;for(const key of keys){const v=obj[key];if(v!==undefined&&v!==null&&v!=="")return v}return null}
function idOf(v){if(v&&typeof v==="object")v=first(v,"id","user_id");return v==null||v===""?null:String(v)}
function nameOf(v){if(v&&typeof v==="object")v=first(v,"login","username","name","display_name");return v==null||v===""?null:String(v)}
function money(v){let currency="EUR";if(v&&typeof v==="object"){currency=String(first(v,"currency_code","currency","code")||"EUR");v=first(v,"amount","value","price")}if(v==null||v==="")return [null,currency];const n=Number.parseFloat(String(v).replace("€","").replaceAll(" ","").replace(",","."));return [Number.isFinite(n)?Math.round(n*100):null,currency]}
function stamp(v){if(v==null||v==="")return null;const t=String(v).trim();if(/^\d+(?:\.\d+)?$/.test(t)){let n=Number(t);if(n<1e11)n*=1000;const d=new Date(n);if(!Number.isNaN(d.getTime()))return d.toISOString()}return String(v)}
function exactStamp(v){const normalized=stamp(v);if(!normalized)return null;const d=new Date(normalized);return Number.isNaN(d.getTime())?null:d.toISOString()}
function metaText(v){if(v==null||v==="")return null;if(Array.isArray(v)){const values=v.map(metaText).filter(Boolean);return values.length?values.join(", "):null}if(typeof v==="object")v=first(v,"title","name","label","display_value","value","text");return v==null||v===""?null:String(v).trim()||null}
function metaKey(v){return String(v||"").toLowerCase().replace(/[^a-z0-9]+/g,"")}
function attributeValue(raw,...names){const wanted=new Set(names.map(metaKey));for(const [key,value] of Object.entries(raw||{})){if(wanted.has(metaKey(key))){const text=metaText(value);if(text)return text}}for(const group of[first(raw,"attributes","item_attributes","details","item_details"),first(raw,"item","product")]){if(!group)continue;if(Array.isArray(group)){for(const entry of group){if(!entry||typeof entry!=="object")continue;const key=first(entry,"code","key","name","title","label","type");if(!wanted.has(metaKey(key)))continue;const text=metaText(first(entry,"value","value_name","display_value","selected_value","title","name","label"));if(text)return text}}else if(typeof group==="object"){for(const [key,value] of Object.entries(group)){if(wanted.has(metaKey(key))){const text=metaText(value);if(text)return text}}}}return null}
function listingMetadata(raw){
  const values={
    condition:metaText(first(raw,"status_title","condition_title","condition","item_condition","quality"))||attributeValue(raw,"status_title","condition_title","condition","item_condition","quality"),
    category:metaText(first(raw,"catalog_title","category_title","category_name","catalog","category","catalogs"))||attributeValue(raw,"catalog_title","category_title","category_name","catalog","category","catalogs"),
    brand:metaText(first(raw,"brand_title","brand_name","brand"))||attributeValue(raw,"brand_title","brand_name","brand"),
    size:metaText(first(raw,"size_title","size_name","size"))||attributeValue(raw,"size_title","size_name","size"),
    color:metaText(first(raw,"color_title","colour_title","color_name","colour_name","color","colour","color1"))||attributeValue(raw,"color_title","colour_title","color_name","colour_name","color","colour","color1"),
    material:metaText(first(raw,"material_title","material_name","material"))||attributeValue(raw,"material_title","material_name","material"),
    description:metaText(first(raw,"description","item_description","itemDescription"))||attributeValue(raw,"description","item_description","itemDescription"),
    isbn:metaText(first(raw,"isbn","isbn13","isbn_13"))||attributeValue(raw,"isbn","isbn13","isbn_13"),
    author:metaText(first(raw,"author","writer"))||attributeValue(raw,"author","writer"),
    publisher:metaText(first(raw,"publisher","publishing_house"))||attributeValue(raw,"publisher","publishing_house"),
    language:metaText(first(raw,"language","book_language"))||attributeValue(raw,"language","book_language")
  };
  return Object.fromEntries(Object.entries(values).filter(([,value])=>value!=null&&value!==""));
}
function imageUrls(raw){const rows=[];for(const group of[first(raw,"photos","item_photos","images"),raw?.photo]){if(!group)continue;for(const entry of(Array.isArray(group)?group:[group])){if(!entry)continue;const url=typeof entry==="string"?entry:first(entry,"full_size_url","large_url","url","image_url");if(url&&!rows.includes(String(url)))rows.push(String(url))}}return rows}
function listFrom(payload,keys){if(Array.isArray(payload))return payload;if(!payload||typeof payload!=="object")return[];for(const k of keys)if(Array.isArray(payload[k]))return payload[k];const d=payload.data;if(Array.isArray(d))return d;if(d&&typeof d==="object")for(const k of keys)if(Array.isArray(d[k]))return d[k];return[]}
function closed(v){const s=String(v||"").toLowerCase().replaceAll("-","_").replaceAll(" ","_");return["cancelled","canceled","completed","complete","closed","finished","refunded","failed"].some(x=>s.includes(x))}
function listingStatus(raw,forced){if(raw?.is_reserved)return"reserved";if(raw?.is_hidden)return"hidden";if(raw?.is_draft)return"draft";if(raw?.is_closed||raw?.is_sold)return"sold";if(forced)return forced==="closed"?"sold":forced;const s=String(first(raw,"state","item_status","listing_status")||"").toLowerCase();return["sold","reserved","hidden","draft"].includes(s)?s:"active"}
function listingRow(raw,forced){const [price,currency]=money(first(raw,"price","total_item_price","item_price"));const itemId=idOf(first(raw,"id","item_id"));let url=first(raw,"url","web_url");if(url)url=new URL(String(url),location.origin).href;else if(itemId)url=`${location.origin}/items/${itemId}`;const listedAt=exactStamp(first(raw,"created_at_ts","created_timestamp_ts","uploaded_ts","upload_date_dte"));const images=imageUrls(raw);return{id:itemId,title:String(first(raw,"title","name")||"Untitled"),price_cents:price,currency,status:listingStatus(raw,forced),vinted_url:url||null,image_url:images[0]||null,image_urls:images,listed_at:listedAt,listed_at_source:listedAt?"list":null,listed_age_seconds:null,listed_age_source:null,listed_age_text:null,favourites:first(raw,"favourite_count","favorites_count","favourites_count"),views:first(raw,"view_count","views_count"),metadata:listingMetadata(raw)}}
function orderRow(raw,direction){const [price,currency]=money(first(raw,"price","total_price","total","amount"));const lifecycle=String(first(raw,"transaction_user_status","state")||"").toLowerCase().trim().replaceAll(" ","_").replaceAll("-","_");const status=String(first(raw,"status")||lifecycle||"open").toLowerCase().trim().replaceAll(" ","_").replaceAll("-","_");const thread=first(raw,"conversation_id","thread_id");const oid=first(raw,"id","transaction_id","order_id")||thread;const item=first(raw,"item","transaction_item","listing","item_snapshot");const itemId=idOf(first(raw,"item_id","listing_id")||item);let url=first(raw,"url","web_url","link");if(!url&&thread)url=`${location.origin}/inbox/${thread}`;else if(url)url=new URL(String(url),location.origin).href;return{id:String(oid||""),thread_id:String(thread||""),item_id:itemId,direction,title:String(first(raw,"title","item_title","name")||first(item,"title","name")||"Vinted order"),counterparty:nameOf(first(raw,"opposite_user","other_user","user")),total_cents:price,currency,status,lifecycle_status:lifecycle||status,is_closed:closed(lifecycle||status),tracking_code:first(raw,"tracking_code","tracking_number","shipment_tracking_code"),updated_at:stamp(first(raw,"updated_at","date","created_at","created_at_ts")),vinted_url:url||null}}
function notificationRow(raw){let body=first(raw,"body","text","message","description");if(body&&typeof body==="object")body=first(body,"text","value");body=String(body||"");const title=String(first(raw,"title","subject","heading")||body||"Vinted notification");let url=first(raw,"link","url","deep_link","target_url");if(url)url=new URL(String(url),location.origin).href;let category="other",item_id=null,item_title=null,actor=null;if(String(url||"").includes("/want_it/")){category="favorite";item_id=String(url).match(/\/items\/(\d+)/)?.[1]||null;const m=body.match(/^(.+?) adicionou o teu (.+?) aos seus favoritos\.?$/i)||body.match(/^(.+?) added your (.+?) to (?:their|his|her) favou?rites\.?$/i);if(m){actor=m[1].trim();item_title=m[2].trim()}}return{id:String(first(raw,"id","notification_id")||""),kind:String(first(raw,"entry_type","type","notification_type","event_type")||"notification"),category,item_id,item_title,actor,title,body,occurred_at:stamp(first(raw,"updated_at","created_at","created_at_ts","timestamp")),read:Boolean(first(raw,"is_read","read","seen")||first(raw,"read_at","seen_at")),url:url||null}}

const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let lastVintedFetchAt=0;
function rateLimitDelay(response,attempt){
  const raw=response?.headers?.get?.("retry-after");
  const seconds=Number(raw);
  if(Number.isFinite(seconds)&&seconds>0)return Math.min(30000,seconds*1000);
  return Math.min(12000,1500*(2**attempt));
}
function isRateLimitError(error){return Boolean(error?.vintedRateLimited)}
async function fetchJson(path,params={},options={}){
  const url=new URL(path,location.origin);
  for(const[k,v]of Object.entries(params))url.searchParams.set(k,String(v));
  const minDelayMs=Math.max(0,Number(options.minDelayMs??300));
  const maxRetries=Math.max(0,Number(options.maxRetries??2));
  for(let attempt=0;attempt<=maxRetries;attempt++){
    const spacing=minDelayMs-(Date.now()-lastVintedFetchAt);
    if(spacing>0)await wait(spacing);
    lastVintedFetchAt=Date.now();
    const r=await fetch(url,{headers:{"Accept":"application/json, text/plain, */*","X-Platform":"web"}});
    if(r.status===404)return null;
    if(r.status===429){
      if(attempt<maxRetries){await wait(rateLimitDelay(r,attempt));continue}
      const error=new Error("Vinted rate limited the sync. The bridge stopped before sending a partial inventory snapshot; retry after Vinted has cooled down.");
      error.vintedRateLimited=true;
      throw error;
    }
    if(!r.ok)throw new Error(`Vinted returned HTTP ${r.status} for ${url.pathname}`);
    return await r.json();
  }
  return null;
}
const LISTED_AT_CACHE_KEY="vintedListedAtCacheV3";
const LISTING_DETAIL_CACHE_KEY="vintedListingDetailCacheV4";
const LISTING_PAGE_AGE_CACHE_KEY="vintedListingPageAgeCacheV2";
async function enrichListingDates(listings){
  let stored={};try{stored=await chrome.storage.local.get([LISTED_AT_CACHE_KEY,LISTING_PAGE_AGE_CACHE_KEY])}catch{}
  const exactCache=(stored&&typeof stored[LISTED_AT_CACHE_KEY]==="object"&&stored[LISTED_AT_CACHE_KEY])||{};
  const ageCache=(stored&&typeof stored[LISTING_PAGE_AGE_CACHE_KEY]==="object"&&stored[LISTING_PAGE_AGE_CACHE_KEY])||{};
  const missing=[];let exactChanged=false,ageChanged=false;

  for(const row of listings.values()){
    if(!row?.id)continue;
    const direct=exactStamp(row.listed_at);
    if(direct){
      row.listed_at=direct;
      if(exactCache[row.id]!==direct){exactCache[row.id]=direct;exactChanged=true}
      continue;
    }

    const cachedExact=exactStamp(exactCache[row.id]);
    if(cachedExact){
      row.listed_at=cachedExact;
      row.listed_at_source="vinted_exact_cache";
      continue;
    }
    if(exactCache[row.id]){delete exactCache[row.id];exactChanged=true}
    row.listed_at=null;row.listed_at_source=null;

    const cached=ageCache[row.id];
    const cachedAge=VintedAge.advanceCached(cached);
    if(cachedAge!=null){
      row.listed_age_seconds=cachedAge;
      row.listed_age_source="vinted_page_cache";
      row.listed_age_text=cached?.listed_age_text||null;
      continue;
    }

    row.listed_age_seconds=null;
    row.listed_age_source=null;
    row.listed_age_text=null;
    if(["active","reserved","hidden","draft"].includes(String(row.status||"active"))){
      const url=new URL(row.vinted_url||`/items/${row.id}`,location.origin);
      if(url.origin===location.origin){
        missing.push({id:String(row.id),url:url.href});
      }
    }
  }

  const ids=new Set([...listings.keys()].map(String));
  for(const key of Object.keys(ageCache)){
    if(!ids.has(String(key))){delete ageCache[key];ageChanged=true}
  }
  if(exactChanged||ageChanged){
    try{
      const payload={};
      if(exactChanged)payload[LISTED_AT_CACHE_KEY]=exactCache;
      if(ageChanged)payload[LISTING_PAGE_AGE_CACHE_KEY]=ageCache;
      await chrome.storage.local.set(payload);
    }catch{}
  }
  return missing;
}

async function enrichListingDetails(listings,reason="periodic"){
  let stored={};try{stored=await chrome.storage.local.get([LISTING_DETAIL_CACHE_KEY])}catch{}
  const cache=(stored&&typeof stored[LISTING_DETAIL_CACHE_KEY]==="object"&&stored[LISTING_DETAIL_CACHE_KEY])||{};
  const eligible=[...listings.values()].filter(row=>row?.id&&["active","reserved","hidden","draft"].includes(String(row.status||"active")));
  const missing=[];let changed=false;

  function applyCached(row,cached){
    row.metadata={...(cached.metadata||{}),...(row.metadata||{})};
    const images=Array.isArray(cached.image_urls)?cached.image_urls.filter(Boolean):[];
    if(images.length){row.image_urls=images;row.image_url=images[0]}
    if(!row.listed_at&&cached.listed_at){const exact=exactStamp(cached.listed_at);if(exact){row.listed_at=exact;row.listed_at_source="vinted_detail_cache"}}
  }
  function needsDetail(row){
    const metadata=row.metadata||{};
    const images=Array.isArray(row.image_urls)?row.image_urls.filter(Boolean):[];
    const category=String(metadata.category||"").toLowerCase();
    const bookLike=/\b(book|books|fiction|literature|novel|crime|thriller|biograph|memoir|poetry|comic|manga)\b/.test(category);
    if(!metadata.description||!metadata.category||!images.length)return true;
    if(bookLike&&(!metadata.isbn||!metadata.author))return true;
    return false;
  }

  for(const row of eligible){
    const cached=cache[row.id];
    if(cached&&typeof cached==="object"){
      applyCached(row,cached);
      continue;
    }
    if(!needsDetail(row)){
      cache[row.id]={
        metadata:{...(row.metadata||{})},
        image_urls:Array.isArray(row.image_urls)?row.image_urls.filter(Boolean):[],
        listed_at:exactStamp(row.listed_at)||null,
      };
      changed=true;
      continue;
    }
    missing.push(row);
  }

  const budget=reason==="manual"?32:10;
  const queue=missing.slice(0,budget);
  let enriched=0,rateLimited=false;
  for(const row of queue){
    try{
      const payload=await fetchJson(
        `/api/v2/items/${row.id}`,
        {localize:"false"},
        {minDelayMs:900,maxRetries:1},
      );
      if(payload===null)continue;
      const raw=payload?.item||payload?.data?.item||payload?.data||payload||{};
      const metadata=listingMetadata(raw);
      const images=imageUrls(raw);
      const created=exactStamp(first(raw,"created_at_ts","created_timestamp_ts","uploaded_ts","upload_date_dte"));
      row.metadata={...metadata,...(row.metadata||{})};
      if(images.length){row.image_urls=images;row.image_url=images[0]}
      if(created&&!row.listed_at){row.listed_at=created;row.listed_at_source="vinted_detail"}
      cache[row.id]={metadata,image_urls:images,listed_at:created||null};
      changed=true;
      enriched++;
    }catch(error){
      if(isRateLimitError(error)){rateLimited=true;break}
    }
  }

  const eligibleIds=new Set(eligible.map(row=>String(row.id)));
  for(const key of Object.keys(cache)){if(!eligibleIds.has(String(key))){delete cache[key];changed=true}}
  if(changed){try{await chrome.storage.local.set({[LISTING_DETAIL_CACHE_KEY]:cache})}catch{}}
  return{
    enriched,
    attempted:queue.length,
    deferred:Math.max(0,missing.length-enriched),
    rate_limited:rateLimited,
  };
}
async function paged(path,keys,params={},perPage=96){const rows=[];for(let page=1;page<=10;page++){const payload=await fetchJson(path,{...params,page,per_page:perPage});if(payload===null)return null;const chunk=listFrom(payload,keys);rows.push(...chunk);if(!chunk.length)break;const p=payload?.pagination;if(p&&typeof p==="object"){if(Number.isInteger(p.total_pages)&&page>=p.total_pages)break;if(p.next_page==null&&chunk.length<perPage)break}else if(chunk.length<perPage)break}return rows}

async function collectVintedData(reason="periodic"){
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
  let secondaryRateLimited=false;
  for(const status of["active","sold","reserved","draft","closed"]){
    if(secondaryRateLimited)break;
    try{
      const rows=await paged(`/api/v2/users/${userId}/items`,["items","user_items"],{status,order:"newest_first"});
      for(const raw of rows||[]){const row=listingRow(raw,status);if(row.id)listings.set(row.id,row)}
    }catch(error){
      // Active listings are authoritative for physical stock. Never upload a
      // snapshot if this endpoint failed or only partially paged.
      if(status==="active")throw error;
      if(isRateLimitError(error))secondaryRateLimited=true;
    }
  }
  if(!secondaryRateLimited){
    try{
      const rows=await paged(`/api/v2/wardrobe/${userId}/items`,["items","user_items"],{order:"newest_first"});
      for(const raw of rows||[]){const row=listingRow(raw,null);if(!row.id)continue;const old=listings.get(row.id);if(!old||row.status==="hidden")listings.set(row.id,row)}
    }catch(error){
      if(isRateLimitError(error))secondaryRateLimited=true;
    }
  }
  const detailEligible=[...listings.values()].filter(row=>row?.id&&["active","reserved","hidden","draft"].includes(String(row.status||"active"))).length;
  const detailSync=secondaryRateLimited
    ? {enriched:0,attempted:0,deferred:detailEligible,rate_limited:true}
    : await enrichListingDetails(listings,reason);
  const ageScanItems=await enrichListingDates(listings);

  let notifications=[];
  const orders=[];
  if(!detailSync.rate_limited){
    for(const path of["/api/v2/notifications","/web/api/notifications/notifications"]){try{const payload=await fetchJson(path,{page:1,per_page:100});if(payload!==null){notifications=listFrom(payload,["notifications","items","entries"]).map(notificationRow).filter(row=>row.category==="favorite").map(row=>({id:row.id,category:row.category,item_id:row.item_id,item_title:row.item_title,actor:row.actor,occurred_at:row.occurred_at}));break}}catch{}}
    for(const[type,direction]of[["sold","sell"],["purchased","buy"]]){try{const rows=await paged("/api/v2/my_orders",["my_orders","orders","items"],{type,status:"all"},100);for(const raw of rows||[])orders.push(orderRow(raw,direction))}catch{}}
  }

  return{collected_at:Date.now()/1000,current_user:currentUser,listings:[...listings.values()],notifications,orders,market_results:[],age_scan_items:ageScanItems,detail_sync:detailSync};
}


function renderedPageAccessState(){
  const title=String(document.title||"").toLowerCase();
  const body=String(document.body?.innerText||document.body?.textContent||"").toLowerCase();
  const sample=(title+"\n"+body).slice(0,120000);
  const rateLimited=[
    "you are rate limited",
    "too many requests",
    "request rate limit exceeded",
    "access to this site is blocked",
    "rate limit exceeded",
  ].some(value=>sample.includes(value));
  const challenged=[
    "verify you are human",
    "confirm you are human",
    "security check",
    "captcha",
    "access denied",
  ].some(value=>sample.includes(value));
  return{rate_limited:rateLimited,challenged};
}

chrome.runtime.onMessage.addListener((message,_sender,sendResponse)=>{
  if(message?.type==="bridge-content-protocol"){
    sendResponse({ok:true,protocol:BRIDGE_CONTENT_PROTOCOL});
    return;
  }
  if(message?.type==="collect-vinted-data"){
    collectVintedData(message.reason||"periodic").then(snapshot=>sendResponse({ok:true,snapshot})).catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));
    return true;
  }
  if(message?.type==="read-vinted-uploaded-age"){
    const access=renderedPageAccessState();
    const currentId=location.pathname.match(/\/items\/(\d+)/)?.[1]||null;
    const age=!access.rate_limited&&!access.challenged&&currentId&&String(currentId)===String(message.item_id)
      ? VintedAge.fromRenderedDocument(document)
      : null;
    sendResponse({ok:true,age,...access});
  }
});

})();
