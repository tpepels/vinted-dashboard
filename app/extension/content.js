function listFrom(payload, keys) {
  if (Array.isArray(payload)) return payload;
  if (!payload || typeof payload !== "object") return [];
  for (const key of keys) {
    if (Array.isArray(payload[key])) return payload[key];
  }
  const data = payload.data;
  if (Array.isArray(data)) return data;
  if (data && typeof data === "object") {
    for (const key of keys) {
      if (Array.isArray(data[key])) return data[key];
    }
  }
  return [];
}

async function fetchJson(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, String(value));
  }
  const response = await fetch(url, {
    headers: {
      "Accept": "application/json, text/plain, */*",
      "X-Platform": "web"
    }
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`Vinted returned HTTP ${response.status} for ${url.pathname}`);
  }
  return await response.json();
}

async function paged(path, keys, params = {}, perPage = 96) {
  const rows = [];
  for (let page = 1; page <= 10; page += 1) {
    const payload = await fetchJson(path, { ...params, page, per_page: perPage });
    if (payload === null) return null;
    const chunk = listFrom(payload, keys);
    rows.push(...chunk);
    if (!chunk.length) break;

    const pagination = payload?.pagination;
    if (pagination && typeof pagination === "object") {
      if (Number.isInteger(pagination.total_pages) && page >= pagination.total_pages) break;
      if (pagination.next_page == null && chunk.length < perPage) break;
    } else if (chunk.length < perPage) {
      break;
    }
  }
  return rows;
}

async function collectVintedData() {
  const currentPayload = await fetchJson("/api/v2/users/current");
  const currentUser = currentPayload?.user || currentPayload || {};
  const userId = currentUser.id || currentUser.user_id;
  if (!userId) {
    throw new Error("This Vinted tab is not signed in.");
  }

  const listingEntries = [];
  for (const status of ["active", "sold", "reserved", "draft", "closed"]) {
    try {
      const rows = await paged(
        `/api/v2/users/${userId}/items`,
        ["items", "user_items"],
        { status, order: "newest_first" }
      );
      for (const raw of rows || []) {
        listingEntries.push({ forced_status: status, raw });
      }
    } catch {}
  }

  try {
    const wardrobe = await paged(
      `/api/v2/wardrobe/${userId}/items`,
      ["items", "user_items"],
      { order: "newest_first" }
    );
    for (const raw of wardrobe || []) {
      listingEntries.push({ forced_status: null, raw });
    }
  } catch {}

  let notifications = [];
  for (const path of ["/api/v2/notifications", "/web/api/notifications/notifications"]) {
    try {
      const payload = await fetchJson(path, { page: 1, per_page: 100 });
      if (payload !== null) {
        notifications = listFrom(payload, ["notifications", "items", "entries"]);
        break;
      }
    } catch {}
  }

  const orderEntries = [];
  for (const [type, direction] of [["sold", "sell"], ["purchased", "buy"]]) {
    try {
      const rows = await paged(
        "/api/v2/my_orders",
        ["my_orders", "orders", "items"],
        { type, status: "all" },
        100
      );
      for (const raw of rows || []) {
        orderEntries.push({ direction, raw });
      }
    } catch {}
  }

  return {
    collected_at: Date.now() / 1000,
    current_user: currentUser,
    listing_entries: listingEntries,
    notifications,
    order_entries: orderEntries
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "collect-vinted-data") return;
  collectVintedData()
    .then(snapshot => sendResponse({ ok: true, snapshot }))
    .catch(error => sendResponse({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    }));
  return true;
});
