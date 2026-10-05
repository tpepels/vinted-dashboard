function first(obj,...keys){if(!obj||typeof obj!=="object")return null;for(const key of keys){const v=obj[key];if(v!==undefined&&v!==null&&v!=="")return v}return null}
function idOf(v){if(v&&typeof v==="object")v=first(v,"id","user_id");return v==null||v===""?null:String(v)}
function nameOf(v){if(v&&typeof v==="object")v=first(v,"login","username","name","display_name");return v==null||v===""?null:String(v)}
function money(v){let currency="EUR";if(v&&typeof v==="object"){currency=String(first(v,"currency_code","currency","code")||"EUR");v=first(v,"amount","value","price")}if(v==null||v==="")return [null,currency];const n=Number.parseFloat(String(v).replace("€","").replaceAll(" ","").replace(",","."));return [Number.isFinite(n)?Math.round(n*100):null,currency]}
function stamp(v){if(v==null||v==="")return null;const t=String(v).trim();if(/^\d+(?:\.\d+)?$/.test(t)){let n=Number(t);if(n<1e11)n*=1000;const d=new Date(n);if(!Number.isNaN(d.getTime()))return d.toISOString()}return String(v)}
function exactStamp(v){const normalized=stamp(v);if(!normalized)return null;const d=new Date(normalized);return Number.isNaN(d.getTime())?null:d.toISOString()}
function relativeAgeSeconds(v){
  if(v==null||v==="")return null;
  let text=String(v).trim().toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g,"");
  if(!text)return null;
  if(/^(today|hoje|hoy|oggi|heute|vandaag|aujourd.?hui)$/.test(text))return 0;
  if(/^(yesterday|ontem|ayer|ieri|gestern|gisteren|hier)$/.test(text))return 86400;
  text=text.replace(/\b(a|an|one|um|uma|un|una|uno|une|ein|eine|einem|einer|een)\b/g,"1");
  const match=text.match(/(\d+(?:[.,]\d+)?)\s*([a-z]+)/);
  if(!match)return null;
  const amount=Number(match[1].replace(",","."));
  if(!Number.isFinite(amount)||amount<0)return null;
  const unit=match[2];
  const groups=[
    [60,["minute","minutes","minuto","minutos","minuti","minuten"]],
    [3600,["hour","hours","hora","horas","heure","heures","ora","ore","stunde","stunden","uur","uren"]],
    [86400,["day","days","dia","dias","jour","jours","giorno","giorni","tag","tage","tagen","dag","dagen"]],
    [604800,["week","weeks","semana","semanas","semaine","semaines","settimana","settimane","woche","wochen","weken"]],
    [2630016,["month","months","mes","meses","mois","mese","mesi","monat","monate","monaten","maand","maanden"]],
    [31557600,["year","years","ano","anos","an","ans","anno","anni","jahr","jahre","jahren","jaar","jaren"]],
  ];
  for(const[seconds,names]of groups)if(names.includes(unit))return Math.round(amount*seconds);
  return null;
}
function relativeAgeFromRaw(raw){
  const value=first(raw,"upload_date","uploaded_at_relative","upload_age","uploaded_at","created_at");
  const seconds=relativeAgeSeconds(value);
  return seconds==null?null:{seconds,text:String(value)};
}
function cachedRelativeAge(cached){
  if(!cached||cached.listed_age_seconds==null)return null;
  const base=Number(cached.listed_age_seconds);
  if(!Number.isFinite(base)||base<0)return null;
  const observed=Number(cached.age_observed_at||0);
  const elapsed=observed>0?Math.max(0,Date.now()/1000-observed):0;
  return Math.round(base+elapsed);
}
function metaText(v){if(v==null||v==="")return null;if(Array.isArray(v)){const values=v.map(metaText).filter(Boolean);return values.length?values.join(", "):null}if(typeof v==="object")v=first(v,"title","name","label","display_value","value","text");return v==null||v===""?null:String(v).trim()||null}
function metaKey(v){return String(v||"").toLowerCase().replace(/[^a-z0-9]+/g,"")}
function attributeValue(raw,...names){const wanted=new Set(names.map(metaKey));for(const [key,value] of Object.entries(raw||{})){if(wanted.has(metaKey(key))){const text=metaText(value);if(text)return text}}for(const group of[first(raw,"attributes","item_attributes","details","item_details"),first(raw,"item","product")]){if(!group)continue;if(Array.isArray(group)){for(const entry of group){if(!entry||typeof entry!=="object")continue;const key=first(entry,"code","key","name","title","label","type");if(!wanted.has(metaKey(key)))continue;const text=metaText(first(entry,"value","value_name","display_value","selected_value","title","name","label"));if(text)return text}}else if(typeof group==="object"){for(const [key,value] of Object.entries(group)){if(wanted.has(metaKey(key))){const text=metaText(value);if(text)return text}}}}return null}
function listingMetadata(raw){const values={condition:attributeValue(raw,"status_title","condition_title","condition","item_condition","quality"),category:attributeValue(raw,"catalog_title","category_title","category_name","catalog","category"),brand:attributeValue(raw,"brand_title","brand_name","brand"),size:attributeValue(raw,"size_title","size_name","size"),color:attributeValue(raw,"color_title","colour_title","color_name","colour_name","color","colour","color1"),material:attributeValue(raw,"material_title","material_name","material"),description:attributeValue(raw,"description","item_description"),isbn:attributeValue(raw,"isbn","isbn13","isbn_13"),author:attributeValue(raw,"author","writer"),publisher:attributeValue(raw,"publisher","publishing_house"),language:attributeValue(raw,"language","book_language")};return Object.fromEntries(Object.entries(values).filter(([,value])=>value!=null&&value!==""))}
function imageUrls(raw){const rows=[];for(const group of[first(raw,"photos","item_photos","images"),raw?.photo]){if(!group)continue;for(const entry of(Array.isArray(group)?group:[group])){if(!entry)continue;const url=typeof entry==="string"?entry:first(entry,"full_size_url","large_url","url","image_url");if(url&&!rows.includes(String(url)))rows.push(String(url))}}return rows}
function listFrom(payload,keys){if(Array.isArray(payload))return payload;if(!payload||typeof payload!=="object")return[];for(const k of keys)if(Array.isArray(payload[k]))return payload[k];const d=payload.data;if(Array.isArray(d))return d;if(d&&typeof d==="object")for(const k of keys)if(Array.isArray(d[k]))return d[k];return[]}
function closed(v){const s=String(v||"").toLowerCase().replaceAll("-","_").replaceAll(" ","_");return["cancelled","canceled","completed","complete","closed","finished","refunded","failed"].some(x=>s.includes(x))}
function listingStatus(raw,forced){if(raw?.is_reserved)return"reserved";if(raw?.is_hidden)return"hidden";if(raw?.is_draft)return"draft";if(raw?.is_closed||raw?.is_sold)return"sold";if(forced)return forced==="closed"?"sold":forced;const s=String(first(raw,"state","item_status","listing_status")||"").toLowerCase();return["sold","reserved","hidden","draft"].includes(s)?s:"active"}
function listingRow(raw,forced){const [price,currency]=money(first(raw,"price","total_item_price","item_price"));const itemId=idOf(first(raw,"id","item_id"));let url=first(raw,"url","web_url");if(url)url=new URL(String(url),location.origin).href;else if(itemId)url=`${location.origin}/items/${itemId}`;const listedAt=exactStamp(first(raw,"created_at_ts","created_timestamp_ts","created_at","uploaded_at","uploaded_ts","posted_at","upload_date_dte"));const relative=relativeAgeFromRaw(raw);const images=imageUrls(raw);return{id:itemId,title:String(first(raw,"title","name")||"Untitled"),price_cents:price,currency,status:listingStatus(raw,forced),vinted_url:url||null,image_url:images[0]||null,image_urls:images,listed_at:listedAt,listed_at_source:listedAt?"list":null,listed_age_seconds:relative?.seconds??null,listed_age_source:relative?"vinted_relative":null,listed_age_text:relative?.text??null,favourites:first(raw,"favourite_count","favorites_count","favourites_count"),views:first(raw,"view_count","views_count"),metadata:listingMetadata(raw)}}
function orderRow(raw,direction){const [price,currency]=money(first(raw,"price","total_price","total","amount"));const lifecycle=String(first(raw,"transaction_user_status","state")||"").toLowerCase().trim().replaceAll(" ","_").replaceAll("-","_");const status=String(first(raw,"status")||lifecycle||"open").toLowerCase().trim().replaceAll(" ","_").replaceAll("-","_");const thread=first(raw,"conversation_id","thread_id");const oid=first(raw,"id","transaction_id","order_id")||thread;const item=first(raw,"item","transaction_item","listing","item_snapshot");const itemId=idOf(first(raw,"item_id","listing_id")||item);let url=first(raw,"url","web_url","link");if(!url&&thread)url=`${location.origin}/inbox/${thread}`;else if(url)url=new URL(String(url),location.origin).href;return{id:String(oid||""),thread_id:String(thread||""),item_id:itemId,direction,title:String(first(raw,"title","item_title","name")||first(item,"title","name")||"Vinted order"),counterparty:nameOf(first(raw,"opposite_user","other_user","user")),total_cents:price,currency,status,lifecycle_status:lifecycle||status,is_closed:closed(lifecycle||status),tracking_code:first(raw,"tracking_code","tracking_number","shipment_tracking_code"),updated_at:stamp(first(raw,"updated_at","date","created_at","created_at_ts")),vinted_url:url||null}}
function notificationRow(raw){let body=first(raw,"body","text","message","description");if(body&&typeof body==="object")body=first(body,"text","value");body=String(body||"");const title=String(first(raw,"title","subject","heading")||body||"Vinted notification");let url=first(raw,"link","url","deep_link","target_url");if(url)url=new URL(String(url),location.origin).href;let category="other",item_id=null,item_title=null,actor=null;if(String(url||"").includes("/want_it/")){category="favorite";item_id=String(url).match(/\/items\/(\d+)/)?.[1]||null;const m=body.match(/^(.+?) adicionou o teu (.+?) aos seus favoritos\.?$/i)||body.match(/^(.+?) added your (.+?) to (?:their|his|her) favou?rites\.?$/i);if(m){actor=m[1].trim();item_title=m[2].trim()}}return{id:String(first(raw,"id","notification_id")||""),kind:String(first(raw,"entry_type","type","notification_type","event_type")||"notification"),category,item_id,item_title,actor,title,body,occurred_at:stamp(first(raw,"updated_at","created_at","created_at_ts","timestamp")),read:Boolean(first(raw,"is_read","read","seen")||first(raw,"read_at","seen_at")),url:url||null}}

async function fetchJson(path,params={}){const url=new URL(path,location.origin);for(const[k,v]of Object.entries(params))url.searchParams.set(k,String(v));const r=await fetch(url,{headers:{"Accept":"application/json, text/plain, */*","X-Platform":"web"}});if(r.status===404)return null;if(!r.ok)throw new Error(`Vinted returned HTTP ${r.status} for ${url.pathname}`);return await r.json()}
const LISTED_AT_CACHE_KEY="vintedListedAtCacheV2";
const LISTING_DETAIL_CACHE_KEY="vintedListingDetailCacheV2";
async function enrichListingDates(listings){
  let stored={};try{stored=await chrome.storage.local.get([LISTED_AT_CACHE_KEY])}catch{}
  const cache=(stored&&typeof stored[LISTED_AT_CACHE_KEY]==="object"&&stored[LISTED_AT_CACHE_KEY])||{};
  const missing=[];let changed=false;
  for(const row of listings.values()){
    if(!row?.id)continue;
    const direct=exactStamp(row.listed_at);
    if(direct){row.listed_at=direct;if(cache[row.id]!==direct){cache[row.id]=direct;changed=true}continue}
    const cached=exactStamp(cache[row.id]);
    if(cached){row.listed_at=cached;row.listed_at_source="vinted_cache";continue}
    if(cache[row.id]){delete cache[row.id];changed=true}
    row.listed_at=null;row.listed_at_source=null;
    if(row.listed_age_seconds!=null)continue;
    missing.push(row);
  }
  let cursor=0,rateLimited=false;
  async function worker(){
    while(!rateLimited&&cursor<missing.length){
      const row=missing[cursor++];
      try{
        const payload=await fetchJson(`/api/v2/items/${row.id}`,{localize:"false"});
        const raw=payload?.item||payload?.data?.item||payload?.data||payload||{};
        const created=exactStamp(first(raw,"created_at_ts","created_timestamp_ts","created_at","uploaded_at","uploaded_ts","posted_at","upload_date_dte"));
        const relative=relativeAgeFromRaw(raw);
        if(created){
          row.listed_at=created;row.listed_at_source="vinted_detail";cache[row.id]=created;changed=true;
        }else if(relative){
          row.listed_age_seconds=relative.seconds;row.listed_age_source="vinted_relative";row.listed_age_text=relative.text;
        }
        row.metadata={...listingMetadata(raw),...(row.metadata||{})};
        const detailImages=imageUrls(raw);if(detailImages.length){row.image_urls=detailImages;row.image_url=detailImages[0]}
      }catch(error){
        if(String(error?.message||error).includes("HTTP 429"))rateLimited=true;
      }
    }
  }
  await Promise.all([worker(),worker(),worker(),worker()]);
  if(changed){try{await chrome.storage.local.set({[LISTED_AT_CACHE_KEY]:cache})}catch{}}
}
async function enrichListingDetails(listings){
  let stored={};try{stored=await chrome.storage.local.get([LISTING_DETAIL_CACHE_KEY])}catch{}
  const cache=(stored&&typeof stored[LISTING_DETAIL_CACHE_KEY]==="object"&&stored[LISTING_DETAIL_CACHE_KEY])||{};
  const eligible=[...listings.values()].filter(row=>row?.id&&["active","reserved","hidden","draft"].includes(String(row.status||"active")));
  const missing=[];let changed=false;
  for(const row of eligible){
    const cached=cache[row.id];
    if(cached&&typeof cached==="object"){
      row.metadata={...(cached.metadata||{}),...(row.metadata||{})};
      const images=Array.isArray(cached.image_urls)?cached.image_urls.filter(Boolean):[];
      if(images.length){row.image_urls=images;row.image_url=images[0]}
      if(!row.listed_at&&cached.listed_at){const exact=exactStamp(cached.listed_at);if(exact){row.listed_at=exact;row.listed_at_source="vinted_detail_cache"}}
      const cachedAge=cachedRelativeAge(cached);
      if(row.listed_age_seconds==null&&cachedAge!=null){row.listed_age_seconds=cachedAge;row.listed_age_source="vinted_relative_cache";row.listed_age_text=cached.listed_age_text||null}
      continue
    }
    missing.push(row);
  }
  let cursor=0,rateLimited=false;
  async function worker(){
    while(!rateLimited&&cursor<missing.length){
      const row=missing[cursor++];
      try{
        const payload=await fetchJson(`/api/v2/items/${row.id}`,{localize:"false"});
        const raw=payload?.item||payload?.data?.item||payload?.data||payload||{};
        const metadata=listingMetadata(raw);
        const images=imageUrls(raw);
        const created=exactStamp(first(raw,"created_at_ts","created_timestamp_ts","created_at","uploaded_at","uploaded_ts","posted_at","upload_date_dte"));
        const relative=relativeAgeFromRaw(raw);
        row.metadata={...metadata,...(row.metadata||{})};
        if(images.length){row.image_urls=images;row.image_url=images[0]}
        if(created&&!row.listed_at){row.listed_at=created;row.listed_at_source="vinted_detail"}
        if(relative&&row.listed_age_seconds==null){row.listed_age_seconds=relative.seconds;row.listed_age_source="vinted_relative";row.listed_age_text=relative.text}
        cache[row.id]={metadata,image_urls:images,listed_at:created||null,listed_age_seconds:relative?.seconds??null,listed_age_text:relative?.text??null,age_observed_at:Date.now()/1000};
        changed=true;
      }catch(error){
        if(String(error?.message||error).includes("HTTP 429"))rateLimited=true;
      }
    }
  }
  await Promise.all([worker(),worker()]);
  const eligibleIds=new Set(eligible.map(row=>String(row.id)));
  for(const key of Object.keys(cache)){if(!eligibleIds.has(String(key))){delete cache[key];changed=true}}
  if(changed){try{await chrome.storage.local.set({[LISTING_DETAIL_CACHE_KEY]:cache})}catch{}}
}

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
  await enrichListingDetails(listings);
  await enrichListingDates(listings);

  let notifications=[];
  for(const path of["/api/v2/notifications","/web/api/notifications/notifications"]){try{const payload=await fetchJson(path,{page:1,per_page:100});if(payload!==null){notifications=listFrom(payload,["notifications","items","entries"]).map(notificationRow).filter(row=>row.category==="favorite").map(row=>({id:row.id,category:row.category,item_id:row.item_id,item_title:row.item_title,actor:row.actor,occurred_at:row.occurred_at}));break}}catch{}}

  const orders=[];
  for(const[type,direction]of[["sold","sell"],["purchased","buy"]]){try{const rows=await paged("/api/v2/my_orders",["my_orders","orders","items"],{type,status:"all"},100);for(const raw of rows||[])orders.push(orderRow(raw,direction))}catch{}}

  return{collected_at:Date.now()/1000,current_user:currentUser,listings:[...listings.values()],notifications,orders,market_results:[]};
}


chrome.runtime.onMessage.addListener((message,_sender,sendResponse)=>{
  if(message?.type==="collect-vinted-data"){
    collectVintedData().then(snapshot=>sendResponse({ok:true,snapshot})).catch(error=>sendResponse({ok:false,error:error instanceof Error?error.message:String(error)}));
    return true;
  }
});
