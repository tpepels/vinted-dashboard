const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  me: null,
  view: "today",
  file: null,
  mapping: {},
  mappings: [],
  onboarding: null,
  inventoryItems: [],
  listings: [],
  reconciliation: [],
  crossChannelActions: [],
  unlinkedSales: [],
  salesRows: [],
  editItemId: null,
  quickAssistant: null,
  quickAnalysisUsed: false,
  quickPhotoUrls: [],
  quickRequiredValues: {},
  connectorChannel: null,
  todayQueue: [],
  todayShowAll: false,
  stockIntakeQueue: [],
  stockIntakeRestored: false,
  stockWorkspaceKey: null,
  stockEnrichmentQueue: [],
  stockEnrichmentQueued: new Set(),
  stockEnrichmentActive: 0,
  stockAudioContext: null,
  biblioPublish: null,
  crossList: null,
  connectors: [],
  barcodeStream: null,
  barcodeTimer: null,
  barcodeDetector: null,
  barcodeBusy: false,
  barcodeMisses: 0,
  barcodeCameraLatch: null,
  barcodeCameraClearFrames: 0,
};

const importFields = [
  "", "sku", "title", "category", "quantity", "condition", "cost", "price",
  "currency", "location", "notes", "author", "isbn", "publisher", "edition",
  "binding", "publication_year", "brand", "size", "colour", "material",
  "measurements",
];

const connectorSchemas = {
  biblio: {
    title: "BIBLIO",
    help: "Book connector. Inventory and Vinted source photos are sent by FTP. Photos are converted to JPG and named from the BIBLIO Book ID automatically. Multiple photos use BookID_1.jpg, BookID_2.jpg, etc.; BIBLIO may need that multi-photo convention enabled on your seller account.",
    fields: [
      ["host", "FTP host", "ftp.biblio.com", "text"],
      ["username", "FTP username", "", "text"],
      ["password", "FTP password", "", "password"],
      ["directory", "FTP directory", "", "text"],
      ["filename_prefix", "Upload filename prefix", "reseller-dashboard", "text"],
    ],
  },
  ebay: {
    title: "eBay",
    help: "Use either a current OAuth token, or refreshable OAuth credentials. Secrets are encrypted server-side.",
    fields: [
      ["oauth_token", "OAuth access token", "", "password"],
      ["client_id", "Client ID", "", "text"],
      ["client_secret", "Client secret", "", "password"],
      ["refresh_token", "Refresh token", "", "password"],
      ["site_id", "eBay site ID", "0", "text"],
      ["compatibility_level", "Trading API compatibility", "1477", "text"],
    ],
  },
  etsy: {
    title: "Etsy",
    help: "Official Open API v3. Save the app keystring, shared secret and Shop ID, then authorize with Etsy for read-only listings_r and transactions_r access. Manual tokens remain available as a fallback.",
    test: true,
    fields: [
      ["keystring", "App keystring", "", "text"],
      ["shared_secret", "App shared secret", "", "password"],
      ["shop_id", "Shop ID", "", "text"],
      ["oauth_token", "OAuth access token", "", "password"],
      ["refresh_token", "OAuth refresh token", "", "password"],
      ["order_days", "Order history days", "365", "number"],
      ["currency", "Fallback currency", "EUR", "text"],
    ],
  },
  woocommerce: {
    title: "WooCommerce",
    help: "WooCommerce REST API v3. For cross-listing, use a REST API key with read/write Products permission; orders remain read-only in the dashboard.",
    test: true,
    fields: [
      ["store_url", "Store URL", "https://shop.example.com", "url"],
      ["consumer_key", "Consumer key", "ck_…", "text"],
      ["consumer_secret", "Consumer secret", "cs_…", "password"],
      ["order_days", "Order history days", "365", "number"],
      ["currency", "Store currency", "EUR", "text"],
    ],
  },
  shopify: {
    title: "Shopify",
    help: "GraphQL Admin API. Cross-listing needs write_products plus inventory/location access; imports and orders use read_products, read_inventory, read_locations and read_orders.",
    test: true,
    fields: [
      ["store_domain", "Store domain", "your-store.myshopify.com", "text"],
      ["access_token", "Admin API access token", "shpat_…", "password"],
      ["api_version", "Admin API version", "2026-10", "text"],
      ["order_days", "Order history days", "60", "number"],
      ["currency", "Fallback currency", "EUR", "text"],
    ],
  },
  bigcommerce: {
    title: "BigCommerce",
    help: "REST Management API. Use a store hash and OAuth access token with read-only Products and Orders permissions.",
    test: true,
    fields: [
      ["store_hash", "Store hash", "abc123", "text"],
      ["access_token", "OAuth access token", "", "password"],
      ["order_days", "Order history days", "365", "number"],
      ["currency", "Fallback currency", "EUR", "text"],
    ],
  },
  squarespace: {
    title: "Squarespace",
    help: "Squarespace Commerce APIs. Generate an API key with read-only Products, Inventory and Orders permissions, or use an OAuth access token.",
    test: true,
    fields: [
      ["access_token", "API key or OAuth access token", "", "password"],
      ["order_days", "Order history days", "365", "number"],
      ["currency", "Fallback currency", "EUR", "text"],
    ],
  },
  wix: {
    title: "Wix",
    help: "Wix REST APIs. Cross-listing needs Product write and Inventory write for the target site; catalog, inventory and order imports also need their read permissions.",
    test: true,
    fields: [
      ["site_id", "Site ID", "00000000-0000-0000-0000-000000000000", "text"],
      ["api_key", "API key", "", "password"],
      ["order_days", "Order history days", "365", "number"],
      ["currency", "Fallback currency", "EUR", "text"],
    ],
  },
  depop: {
    title: "Depop",
    help: "Private Depop Selling API. Direct seller integrations require Depop partner approval and a per-shop API key. This connector is read-only.",
    test: true,
    fields: [
      ["api_key", "Partner API key", "pak_…", "password"],
      ["environment", "Environment", "production", "text"],
      ["order_days", "Order history days", "365", "number"],
      ["currency", "Fallback currency", "EUR", "text"],
    ],
  },
};

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function money(value, currency) {
  if (value == null) return "—";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: currency || "EUR",
  }).format(Number(value) / 100);
}

function when(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

const tableNumberFormat = new Intl.NumberFormat();
const tableNumberParts = tableNumberFormat.formatToParts(12345.6);
const tableGroup = tableNumberParts.find((part) => part.type === "group")?.value || ",";
const tableDecimal = tableNumberParts.find((part) => part.type === "decimal")?.value || ".";

function parseSortableNumber(text) {
  let candidate = String(text || "").trim();
  if (!candidate || candidate === "—") return null;
  candidate = candidate
    .replace(/[\p{Sc}%]/gu, "")
    .replace(/\b(?:days?|day|d)\b/gi, "")
    .replace(/\s+/g, "");
  if (!/^[+\-]?\d[\d.,]*$/.test(candidate)) return null;
  if (tableGroup) candidate = candidate.split(tableGroup).join("");
  if (tableDecimal && tableDecimal !== ".") {
    candidate = candidate.replace(tableDecimal, ".");
  }
  const value = Number(candidate);
  return Number.isFinite(value) ? value : null;
}

function parseSortableDate(text) {
  const raw = String(text || "").trim();
  if (!raw || raw === "—") return null;

  const numeric = raw.match(/^(\d{1,4})\D(\d{1,2})\D(\d{1,4})(?:\D+(\d{1,2})[:.](\d{2})(?::(\d{2}))?\s*(AM|PM)?)?/i);
  if (numeric) {
    const order = new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date(2001, 10, 22))
      .filter((part) => ["year", "month", "day"].includes(part.type))
      .map((part) => part.type);
    if (order.length === 3) {
      const pieces = {
        [order[0]]: Number(numeric[1]),
        [order[1]]: Number(numeric[2]),
        [order[2]]: Number(numeric[3]),
      };
      if (pieces.year < 100) pieces.year += 2000;
      let hour = Number(numeric[4] || 0);
      const marker = String(numeric[7] || "").toUpperCase();
      if (marker === "PM" && hour < 12) hour += 12;
      if (marker === "AM" && hour === 12) hour = 0;
      const date = new Date(
        pieces.year,
        pieces.month - 1,
        pieces.day,
        hour,
        Number(numeric[5] || 0),
        Number(numeric[6] || 0),
      );
      if (!Number.isNaN(date.getTime())) return date.getTime();
    }
  }

  const direct = Date.parse(raw);
  return Number.isFinite(direct) ? direct : null;
}

function sortableCellValue(cell) {
  const explicit = cell?.dataset?.sortValue;
  if (explicit != null && explicit !== "") {
    const numeric = Number(explicit);
    return Number.isFinite(numeric)
      ? { kind: "number", value: numeric }
      : { kind: "text", value: explicit };
  }
  const text = cell?.innerText?.replace(/\s+/g, " ").trim() || "";
  if (!text || text === "—") return { kind: "empty", value: "" };
  const number = parseSortableNumber(text);
  if (number != null) return { kind: "number", value: number };
  const date = parseSortableDate(text);
  if (date != null) return { kind: "number", value: date };
  return { kind: "text", value: text.toLocaleLowerCase() };
}

function compareSortableValues(left, right, direction) {
  if (left.kind === "empty" && right.kind === "empty") return 0;
  if (left.kind === "empty") return 1;
  if (right.kind === "empty") return -1;
  let result;
  if (left.kind === "number" && right.kind === "number") {
    result = left.value - right.value;
  } else {
    result = String(left.value).localeCompare(String(right.value), undefined, {
      numeric: true,
      sensitivity: "base",
    });
  }
  return direction === "asc" ? result : -result;
}

function sortTableByHeader(header) {
  const table = header.closest("table");
  const body = table?.tBodies?.[0];
  const headerRow = header.parentElement;
  if (!table || !body || !headerRow) return;
  const column = Array.from(headerRow.children).indexOf(header);
  if (column < 0) return;

  const direction = header.dataset.sortDirection === "asc" ? "desc" : "asc";
  Array.from(table.querySelectorAll("thead th")).forEach((cell) => {
    delete cell.dataset.sortDirection;
    cell.removeAttribute("aria-sort");
  });
  header.dataset.sortDirection = direction;
  header.setAttribute("aria-sort", direction === "asc" ? "ascending" : "descending");

  const rows = Array.from(body.rows);
  rows.sort((leftRow, rightRow) => compareSortableValues(
    sortableCellValue(leftRow.cells[column]),
    sortableCellValue(rightRow.cells[column]),
    direction,
  ));
  rows.forEach((row) => body.appendChild(row));
}

document.addEventListener("click", (event) => {
  if (event.target.closest("a,button,input,select,textarea")) return;
  const header = event.target.closest("table thead th");
  if (!header || !header.textContent.trim()) return;
  sortTableByHeader(header);
});

function csrf() {
  if (state.me?.csrf_token) return state.me.csrf_token;
  const hit = document.cookie.split("; ").find((value) => value.startsWith("reseller_csrf="));
  return hit ? decodeURIComponent(hit.split("=")[1]) : "";
}

function flash(message, error = false) {
  $("#flash").innerHTML = message
    ? '<div class="flash ' + (error ? "error" : "") + '">' + esc(message) + "</div>"
    : "";
  if (message) setTimeout(() => { $("#flash").innerHTML = ""; }, 4500);
}

async function api(url, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (options.method && !["GET", "HEAD"].includes(options.method)) {
    headers["X-CSRF-Token"] = csrf();
  }
  if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, Object.assign(
    { credentials: "same-origin" },
    options,
    { headers },
  ));
  const type = response.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(body?.detail || body || ("HTTP " + response.status));
  }
  return body;
}

function metric(label, value, sub) {
  return '<article class="card metric"><span>' + esc(label) + "</span><strong>"
    + esc(value) + "</strong>" + (sub ? '<div class="sub">' + esc(sub) + "</div>" : "")
    + "</article>";
}

function authScreen(which) {
  $("#auth").classList.remove("hidden");
  $("#shell").classList.add("hidden");
  $("#login").classList.toggle("hidden", which === "register");
  $("#register").classList.toggle("hidden", which !== "register");
}

function appScreen() {
  $("#auth").classList.add("hidden");
  $("#shell").classList.remove("hidden");
}

function renderBillingLock(info) {
  const banner = $("#billing-lock");
  const billing = info || {};
  if (!billing.enabled || !billing.read_only) {
    banner.classList.add("hidden");
    banner.innerHTML = "";
    return;
  }
  banner.innerHTML =
    '<div><strong>Workspace is read-only.</strong><span>'
    + esc(billing.reason || "Subscription action is required.")
    + " Status: " + esc(billing.status || "unknown") + '.</span></div>'
    + '<button id="billing-lock-settings" class="btn" type="button">Billing settings</button>';
  banner.classList.remove("hidden");
  $("#billing-lock-settings").onclick = () => selectView("settings");
}

async function init() {
  try {
    state.me = await api("/api/auth/me");
    const workspaceKey = state.me?.workspace?.id || state.me?.workspace?.slug || "default";
    if (state.stockWorkspaceKey !== workspaceKey) {
      resetStockIntakeRuntime();
      state.stockWorkspaceKey = workspaceKey;
    }
    appScreen();
    $("#app-name").textContent = state.me.app_name;
    $("#auth-name").textContent = state.me.app_name;
    $("#workspace-name").textContent = state.me.workspace.name;
    $("#bridge-version-page").textContent = "Bridge v" + (state.me.bridge_version || "unknown");
    renderBillingLock(state.me.billing);
    const params = new URLSearchParams(window.location.search);
    if (params.get("connector") === "etsy" && params.get("oauth")) {
      await selectView("connections");
      if (params.get("oauth") === "connected") flash("Etsy authorization completed.");
      else if (params.get("oauth") === "denied") flash("Etsy authorization was not granted.", true);
      window.history.replaceState({}, "", window.location.pathname);
    } else {
      await load("today");
    }
  } catch {
    authScreen("login");
  }
}

$("#show-register").onclick = () => authScreen("register");
$("#show-login").onclick = () => authScreen("login");

$("#login").onsubmit = async (event) => {
  event.preventDefault();
  $("#auth-error").textContent = "";
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))),
    });
    await init();
  } catch (error) {
    $("#auth-error").textContent = error.message;
  }
};

$("#register").onsubmit = async (event) => {
  event.preventDefault();
  $("#register-error").textContent = "";
  try {
    await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))),
    });
    await init();
  } catch (error) {
    $("#register-error").textContent = error.message;
  }
};

$("#logout").onclick = async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch {}
  resetStockIntakeRuntime();
  state.stockWorkspaceKey = null;
  state.me = null;
  authScreen("login");
};

$$(".nav").forEach((button) => {
  button.onclick = () => selectView(button.dataset.view);
});

async function selectView(view) {
  if (view !== "inventory" && state.barcodeStream) stopBarcodeCamera();
  state.view = view;
  $$(".nav").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === view));
  $("#page-title").textContent = $('.nav[data-view="' + view + '"]').textContent.trim();
  await load(view);
}

async function load(view) {
  try {
    if (view === "today") await today();
    else if (view === "inventory") await inventory();
    else if (view === "reconcile") await reconcile();
    else if (view === "listings") await listings();
    else if (view === "sales") await sales();
    else if (view === "analytics") await analytics();
    else if (view === "imports") await imports();
    else if (view === "connections") await connections();
    else if (view === "settings") await settings();
  } catch (error) {
    flash(error.message, true);
  }
}

async function today() {
  const [todayData, analyticsData, onboardingData, connectorData] = await Promise.all([
    api("/api/app/today"),
    api("/api/app/analytics"),
    api("/api/app/onboarding"),
    api("/api/app/connectors"),
  ]);
  state.onboarding = onboardingData;
  renderOnboarding(onboardingData);

  const activeInventory = Number(analyticsData.active_inventory || 0);
  const pricedCount = Number(analyticsData.priced_inventory_count || 0);
  const marginCount = Number(analyticsData.margin_inventory_count || 0);
  const queue = Array.isArray(todayData.work_queue) ? todayData.work_queue : [];
  state.todayQueue = queue;

  $("#today-metrics").innerHTML =
    metric("Active inventory", activeInventory)
    + metric(
      "Current asking value",
      pricedCount ? money(analyticsData.inventory_ask_cents, analyticsData.currency) : "—",
      pricedCount + " of " + activeInventory + " priced"
    )
    + metric(
      "Sales YTD",
      analyticsData.sales_ytd_count,
      money(analyticsData.sales_ytd_cents, analyticsData.currency)
    )
    + metric(
      "Potential margin",
      marginCount ? money(analyticsData.inventory_potential_margin_cents, analyticsData.currency) : "—",
      marginCount
        ? marginCount + " item" + (marginCount === 1 ? "" : "s") + " with cost + ask"
        : "Add acquisition costs to calculate margin"
    );

  renderTodayFocus(queue);
  renderTodaySourceStatus(connectorData.connectors || []);
  renderTodayWorkQueue(queue, Number(todayData.work_queue_count || queue.length));
}

function todayWorkControls(row) {
  if (row.kind === "stock_action" && row.stock_action) {
    return crossChannelActionControls(row.stock_action);
  }
  const controls = [];
  if (row.url) {
    controls.push('<a class="btn" target="_blank" rel="noreferrer" href="' + esc(row.url) + '">Open listing</a>');
  }
  if (row.view) {
    controls.push(
      '<button class="btn primary today-nav" type="button" data-view="' + esc(row.view)
      + '" data-kind="' + esc(row.kind || "") + '">' + esc(row.label || "Open") + "</button>"
    );
  }
  return controls.join("");
}

function todayPriorityBand(row) {
  const priority = Number(row?.priority || 0);
  if (priority >= 105) return "urgent";
  if (priority >= 90) return "attention";
  return "opportunity";
}

function renderTodayFocus(rows) {
  const queue = Array.isArray(rows) ? rows : [];
  const urgent = queue.filter((row) => todayPriorityBand(row) === "urgent").length;
  const attention = queue.filter((row) => todayPriorityBand(row) === "attention").length;
  const opportunity = queue.filter((row) => todayPriorityBand(row) === "opportunity").length;
  const first = queue[0];

  if (!first) {
    $("#today-focus-title").textContent = "Everything important is clear.";
    $("#today-focus-detail").textContent = "No reseller task needs attention right now.";
    return;
  }

  $("#today-focus-title").textContent = (urgent ? "Start with: " : "Next: ") + first.title;
  const parts = [];
  if (urgent) parts.push(urgent + " urgent");
  if (attention) parts.push(attention + " follow-up");
  if (opportunity) parts.push(opportunity + " optimization");
  $("#today-focus-detail").textContent = parts.join(" · ") + " · highest-priority work is shown first";
}

function relativeTimestamp(value) {
  if (!value) return null;
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return null;
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return minutes + "m ago";
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return hours + "h ago";
  const days = Math.floor(hours / 24);
  return days + "d ago";
}

function renderTodaySourceStatus(connectors) {
  const rows = (connectors || [])
    .filter((row) => row.group !== "files" && row.channel !== "manual")
    .filter((row) => row.configured || row.operational)
    .sort((a, b) => String(a.display_name || a.channel).localeCompare(String(b.display_name || b.channel)));

  $("#today-source-status").innerHTML = rows.length
    ? rows.map((row) => {
      const synced = relativeTimestamp(row.last_synced_at);
      const syncTime = row.last_synced_at ? new Date(row.last_synced_at).getTime() : null;
      const ageHours = Number.isFinite(syncTime) ? Math.max(0, (Date.now() - syncTime) / 3600000) : null;
      let status = "ready";
      let dot = "warn";
      if (!row.operational) {
        status = "attention";
        dot = "error";
      } else if (ageHours != null && ageHours <= 24) {
        status = "fresh";
        dot = "ok";
      } else if (ageHours != null && ageHours <= 168) {
        status = "stale";
        dot = "warn";
      } else if (ageHours != null) {
        status = "old";
        dot = "error";
      }
      const detail = row.operational
        ? (synced ? "Last sync " + synced : "Connected - no sync recorded yet")
        : (row.note || "Connection needs attention");
      return '<div class="source-row"><span class="source-dot ' + dot + '"></span>'
        + '<div class="source-copy"><strong>' + esc(row.display_name || row.channel)
        + '</strong><span>' + esc(detail) + '</span></div>'
        + '<span class="source-state">' + esc(status) + '</span></div>';
    }).join("")
    : '<div class="source-empty">No marketplace source is connected yet.</div>';
}

function todayWorkRow(row) {
  return '<div class="work-row work-' + esc(row.kind || "general") + '">'
    + '<div class="work-copy"><div class="work-heading"><span class="work-kind">'
    + esc((row.kind || "task").replaceAll("_", " ")) + '</span><strong>' + esc(row.title)
    + '</strong></div><p>' + esc(row.detail || "") + '</p></div>'
    + '<div class="actions compact">' + todayWorkControls(row) + "</div></div>";
}

function renderTodayWorkQueue(rows, total) {
  const queue = Array.isArray(rows) ? rows : [];
  const visible = state.todayShowAll ? queue : queue.slice(0, 12);
  $("#today-work-count").textContent = total
    ? total + " task" + (total === 1 ? "" : "s")
    : "Clear";

  if (!queue.length) {
    $("#today-actions").innerHTML =
      '<div class="today-clear"><strong>You are caught up.</strong><span>No reseller task needs attention right now.</span></div>';
  } else {
    const definitions = [
      ["urgent", "Urgent"],
      ["attention", "Needs attention"],
      ["opportunity", "Opportunities"],
    ];
    let html = definitions.map(([band, label]) => {
      const fullGroup = queue.filter((row) => todayPriorityBand(row) === band);
      const group = visible.filter((row) => todayPriorityBand(row) === band);
      if (!group.length) return "";
      return '<div class="work-group"><div class="work-group-title"><span>' + esc(label)
        + '</span><span>' + fullGroup.length + '</span></div>'
        + group.map(todayWorkRow).join("") + "</div>";
    }).join("");
    if (!state.todayShowAll && queue.length > visible.length) {
      html += '<div class="work-more"><button id="today-show-all" class="btn" type="button">Show all '
        + queue.length + " tasks</button></div>";
    } else if (state.todayShowAll && queue.length > 12) {
      html += '<div class="work-more"><button id="today-show-less" class="btn" type="button">Show top 12</button></div>';
    }
    $("#today-actions").innerHTML = html;
  }

  $$(".today-nav").forEach((button) => {
    button.onclick = async () => {
      const view = button.dataset.view;
      await selectView(view);
      if (button.dataset.kind === "purchase_cost" && view === "sales") {
        $("#sales-direction").value = "buy";
        renderSales();
        $("#purchase-cost-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    };
  });
  const showAll = $("#today-show-all");
  if (showAll) {
    showAll.onclick = () => {
      state.todayShowAll = true;
      renderTodayWorkQueue(state.todayQueue, state.todayQueue.length);
    };
  }
  const showLess = $("#today-show-less");
  if (showLess) {
    showLess.onclick = () => {
      state.todayShowAll = false;
      renderTodayWorkQueue(state.todayQueue, state.todayQueue.length);
      $(".today-work")?.scrollIntoView({ behavior: "smooth", block: "start" });
    };
  }
  bindCrossChannelButtons(today);
}

function onboardingStep(label, done, detail) {
  return '<div class="onboarding-step ' + (done ? "done" : "") + '"><strong>'
    + (done ? "✓ " : "○ ") + esc(label) + '</strong>'
    + (detail ? '<span>' + esc(detail) + '</span>' : "") + "</div>";
}

function renderOnboarding(data) {
  const card = $("#onboarding-card");
  card.classList.toggle("hidden", Boolean(data.completed));
  if (data.completed) return;
  $("#onboarding-category").value = data.steps?.choose_category ? data.primary_category : "";
  const connected = [
    ...(data.connected_channels || []),
    ...(data.vinted_bridge_paired && !(data.connected_channels || []).includes("vinted") ? ["vinted"] : []),
  ];
  $("#onboarding-steps").innerHTML =
    onboardingStep("Choose inventory category", Boolean(data.steps?.choose_category), data.primary_category)
    + onboardingStep("Load stock", Boolean(data.steps?.stock_loaded), data.inventory_count + " item" + (data.inventory_count === 1 ? "" : "s"))
    + onboardingStep("Connect marketplace", Boolean(data.steps?.marketplace_connected), connected.join(", ") || "not connected")
    + onboardingStep("Review duplicate stock matches", Boolean(data.steps?.matches_reviewed),
      data.reconciliation_count ? data.reconciliation_count + " suggestion" + (data.reconciliation_count === 1 ? "" : "s") : "clear");
}

$("#onboarding-category").onchange = async () => {
  const value = $("#onboarding-category").value;
  if (!value) return;
  try {
    await api("/api/app/onboarding", {
      method: "PUT",
      body: JSON.stringify({ primary_category: value }),
    });
    await today();
  } catch (error) {
    flash(error.message, true);
  }
};

$("#onboarding-finish").onclick = async () => {
  try {
    await api("/api/app/onboarding", {
      method: "PUT",
      body: JSON.stringify({ completed: true }),
    });
    await today();
  } catch (error) {
    flash(error.message, true);
  }
};

$("#onboarding-import").onclick = async () => {
  await selectView("inventory");
  openStockIntake();
};
$("#onboarding-connect").onclick = () => selectView("connections");
$("#onboarding-reconcile").onclick = () => selectView("reconcile");

$$(".today-shortcut").forEach((button) => {
  button.onclick = async () => {
    await selectView(button.dataset.view);
    if (button.dataset.action === "add-stock") {
      $("#add-stock")?.click();
    }
  };
});
$("#today-open-connections").onclick = () => selectView("connections");

function crossChannelActionControls(row) {
  const open = row.listing?.url
    ? '<a class="btn" target="_blank" rel="noreferrer" href="' + esc(row.listing.url) + '">Open listing</a>'
    : "";
  if (row.status === "attention") {
    return open + '<button class="btn stock-ack" data-id="' + esc(row.id) + '">Mark handled</button>';
  }
  if (row.status === "error") {
    return open + '<button class="btn stock-retry" data-id="' + esc(row.id) + '">Retry</button>';
  }
  return open;
}

function bindCrossChannelButtons(after) {
  $$(".stock-ack").forEach((button) => {
    button.onclick = async () => {
      try {
        await api("/api/app/cross-channel-actions/" + button.dataset.id + "/acknowledge", { method: "POST" });
        flash("Manual close marked handled.");
        await after();
      } catch (error) { flash(error.message, true); }
    };
  });
  $$(".stock-retry").forEach((button) => {
    button.onclick = async () => {
      try {
        await api("/api/app/cross-channel-actions/" + button.dataset.id + "/retry", { method: "POST" });
        flash("Remote close queued again.");
        await after();
      } catch (error) { flash(error.message, true); }
    };
  });
}

function selectedInventoryIds() {
  return $$(".inventory-select:checked").map((box) => box.dataset.id);
}

function updateInventorySelection() {
  const ids = selectedInventoryIds();
  $("#inventory-selected").textContent = ids.length + " selected";
  $("#bulk-edit").disabled = ids.length === 0;
  const all = $("#inventory-select-all");
  if (all) {
    const boxes = $$(".inventory-select");
    all.checked = boxes.length > 0 && ids.length === boxes.length;
    all.indeterminate = ids.length > 0 && ids.length < boxes.length;
  }
}

function inventoryCrossListAction(item) {
  if (!item?.id) return "";
  return '<button class="btn cross-list" data-item-id="' + esc(item.id) + '">Cross-list</button>';
}

function listingCrossListAction(row) {
  if (row.channel !== "vinted") return "";
  if (!row.inventory_item_id) {
    return '<button class="btn cross-list-link" data-listing-id="' + esc(row.id)
      + '">Link to inventory</button>';
  }
  return '<button class="btn cross-list" data-item-id="' + esc(row.inventory_item_id)
    + '" data-source-listing-id="' + esc(row.id) + '">Cross-list</button>';
}

function crossListStatusLabel(status) {
  return {
    ready: "Ready",
    listed: "Already listed",
    connect: "Needs connection",
    needs_fields: "Needs fields",
    review: "Needs review",
    not_writable: "Not writable yet",
  }[status] || status || "Unavailable";
}

function crossListGroup(destination) {
  if (destination.status === "ready") return "ready";
  if (destination.status === "listed") return "listed";
  if (["connect", "needs_fields", "review"].includes(destination.status)) return "attention";
  return "unavailable";
}

function crossListGroupLabel(group) {
  return {
    ready: "Ready to publish",
    attention: "Needs setup or review",
    listed: "Already listed",
    unavailable: "Not writable yet",
  }[group] || group;
}

function crossListDestinationAction(destination) {
  if (destination.action === "publish") {
    return '<button class="btn primary cross-destination-publish" data-channel="' + esc(destination.channel) + '">Publish</button>';
  }
  if (destination.action === "biblio") {
    return '<button class="btn primary cross-destination-biblio">Review / publish</button>';
  }
  if (destination.action === "biblio_classify") {
    return '<button class="btn primary cross-destination-biblio-classify">Mark as book & continue</button>';
  }
  if (destination.action === "connect") {
    return '<button class="btn cross-destination-connect" data-channel="' + esc(destination.channel) + '">Connect</button>';
  }
  if (destination.action === "edit" || destination.action === "review") {
    return '<button class="btn cross-destination-edit">Edit source data</button>';
  }
  if (destination.action === "open" && destination.url) {
    return '<a class="btn" href="' + esc(destination.url) + '" target="_blank" rel="noreferrer">Open listing</a>';
  }
  return "";
}

function renderCrossList(data) {
  if (!state.crossList) return;
  state.crossList.data = data;
  const fields = data.fields || {};
  const source = data.source || {};
  $("#cross-list-title").textContent = "Cross-list " + (data.item_title || "item");
  $("#cross-list-source").innerHTML = source.channel === "vinted"
    ? 'Using <strong>Vinted</strong> as the listing source'
      + (source.url ? ' · <a href="' + esc(source.url) + '" target="_blank" rel="noreferrer">open source</a>' : "")
    : "Using master inventory data; no linked Vinted source was selected.";
  $("#cross-list-summary").innerHTML = [
    "<span><strong>Title:</strong> " + esc(fields.title || "Missing") + "</span>",
    "<span><strong>Price:</strong> " + esc(fields.price_cents == null ? "Missing" : money(fields.price_cents, fields.currency)) + "</span>",
    "<span><strong>Stock:</strong> " + esc(fields.quantity == null ? "—" : fields.quantity) + "</span>",
    "<span><strong>Photos:</strong> " + esc(source.photo_count || 0) + "</span>",
  ].join("");

  const destinations = Array.isArray(data.destinations) ? data.destinations : [];
  const groupOrder = ["ready", "attention", "listed", "unavailable"];
  const groups = groupOrder.map((group) => ({
    group,
    rows: destinations.filter((destination) => crossListGroup(destination) === group),
  })).filter((entry) => entry.rows.length);

  $("#cross-list-destinations").innerHTML = groups.length
    ? groups.map(({ group, rows }) =>
      '<section class="cross-list-group" data-group="' + esc(group) + '">'
      + '<div class="cross-list-group-head"><strong>' + esc(crossListGroupLabel(group)) + '</strong>'
      + '<span>' + rows.length + '</span></div>'
      + rows.map((destination) =>
        '<div class="cross-list-destination ' + esc(destination.status) + '">'
        + '<div class="cross-list-destination-name"><span>' + esc(destination.display_name) + '</span>'
        + '<span class="cross-list-state">' + esc(crossListStatusLabel(destination.status)) + "</span></div>"
        + '<div class="cross-list-destination-copy">' + esc(destination.reason || "") + "</div>"
        + '<div class="actions">' + crossListDestinationAction(destination) + "</div></div>"
      ).join("")
      + "</section>"
    ).join("")
    : '<div class="empty">No cross-list destinations are available.</div>';

  document.querySelectorAll(".cross-destination-publish").forEach((button) => {
    button.onclick = () => publishCrossDestination(button.dataset.channel, button);
  });
  document.querySelectorAll(".cross-destination-connect").forEach((button) => {
    button.onclick = () => openCrossListConnection(button.dataset.channel);
  });
  document.querySelectorAll(".cross-destination-biblio").forEach((button) => {
    button.onclick = async () => {
      const current = state.crossList;
      if (!current?.itemId) return;
      $("#cross-list-panel").classList.add("hidden");
      await openBiblioPublish(current.itemId, current.sourceListingId);
    };
  });
  document.querySelectorAll(".cross-destination-biblio-classify").forEach((button) => {
    button.onclick = async () => {
      const current = state.crossList;
      if (!current?.itemId) return;
      button.disabled = true;
      button.textContent = "Updating…";
      try {
        await api("/api/app/inventory/" + encodeURIComponent(current.itemId), {
          method: "PATCH",
          body: JSON.stringify({ category: "book" }),
        });
        const itemId = current.itemId;
        const sourceListingId = current.sourceListingId || null;
        await inventory();
        $("#cross-list-panel").classList.add("hidden");
        flash("Marked as Book. Review the BIBLIO fields below.");
        await openBiblioPublish(itemId, sourceListingId);
      } catch (error) {
        flash(error.message, true);
        button.disabled = false;
        button.textContent = "Mark as book & continue";
      }
    };
  });
  document.querySelectorAll(".cross-destination-edit").forEach((button) => {
    button.onclick = () => {
      const current = state.crossList;
      const item = state.inventoryItems.find((row) => row.id === current?.itemId);
      if (!item) return flash("Inventory item could not be found.", true);
      $("#cross-list-panel").classList.add("hidden");
      openItemForm(item);
    };
  });
}

async function openCrossList(itemId, sourceListingId = null) {
  if (!itemId) return flash("Link this listing to a physical inventory item first.", true);
  if (state.view !== "inventory") await selectView("inventory");
  state.crossList = { itemId, sourceListingId, data: null };
  $("#cross-list-panel").classList.remove("hidden");
  $("#cross-list-title").textContent = "Checking destinations…";
  $("#cross-list-source").textContent = "";
  $("#cross-list-summary").innerHTML = "";
  $("#cross-list-destinations").innerHTML = '<div class="empty">Checking connected platforms and listing requirements…</div>';
  $("#cross-list-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const suffix = sourceListingId ? "?source_listing_id=" + encodeURIComponent(sourceListingId) : "";
    const data = await api("/api/app/inventory/" + encodeURIComponent(itemId) + "/cross-list" + suffix);
    renderCrossList(data);
  } catch (error) {
    $("#cross-list-destinations").innerHTML = '<div class="empty">' + esc(error.message) + "</div>";
    flash(error.message, true);
  }
}

async function publishCrossDestination(channel, button) {
  const current = state.crossList;
  if (!current?.itemId || !channel) return;
  button.disabled = true;
  const old = button.textContent;
  button.textContent = "Publishing…";
  try {
    const result = await api(
      "/api/app/inventory/" + encodeURIComponent(current.itemId) + "/cross-list/" + encodeURIComponent(channel),
      {
        method: "POST",
        body: JSON.stringify({ source_listing_id: current.sourceListingId || null }),
      },
    );
    flash("Published to " + (connectorSchemas[channel]?.title || channel) + ".");
    await inventory();
    await openCrossList(current.itemId, current.sourceListingId);
  } catch (error) {
    flash(error.message, true);
    button.disabled = false;
    button.textContent = old;
  }
}

async function openCrossListConnection(channel) {
  if (state.crossList) state.crossList.connectChannel = channel;
  await selectView("connections");
  const connector = (state.connectors || []).find((row) => row.channel === channel);
  if (!connector || !connectorSchemas[channel]) {
    return flash("This destination has no connection form yet.", true);
  }
  openConnectorConfig(channel, connector);
}

function bindCrossListButtons() {
  document.querySelectorAll(".cross-list").forEach((button) => {
    button.onclick = () => openCrossList(
      button.dataset.itemId,
      button.dataset.sourceListingId || null,
    );
  });
  document.querySelectorAll(".cross-list-link").forEach((button) => {
    button.onclick = () => linkListingToInventory(button.dataset.listingId);
  });
}

function biblioSourceBadge(source) {
  if (!source) return "";
  const label = source === "vinted" ? "Vinted" : source === "isbn" ? "ISBN lookup" : source === "master" ? "Master" : source;
  return '<span class="biblio-source">' + esc(label) + "</span>";
}

function renderBiblioPublish(data) {
  state.biblioPublish.data = data;
  $("#biblio-publish-title").textContent = (data.action === "update" ? "Update " : "Publish ") + (data.item_title || "book");
  const source = data.source || {};
  $("#biblio-publish-source").innerHTML = source.channel === "vinted"
    ? 'Using the linked <strong>Vinted listing</strong> as the source'
      + (source.url ? ' · <a href="' + esc(source.url) + '" target="_blank" rel="noreferrer">open Vinted</a>' : "")
    : "No linked Vinted source was found; using the master inventory record.";

  const photoUrls = Array.isArray(source.image_urls) ? source.image_urls.filter(Boolean).slice(0, 5) : [];
  const photoPreview = $("#biblio-photo-preview");
  if (photoUrls.length) {
    photoPreview.classList.remove("hidden");
    photoPreview.innerHTML = '<div class="biblio-photo-copy"><strong>'
      + photoUrls.length + ' Vinted photo' + (photoUrls.length === 1 ? "" : "s")
      + ' will be uploaded automatically to BIBLIO.</strong>'
      + '<span>No manual image upload is required.</span></div>'
      + '<div class="biblio-photo-strip">'
      + photoUrls.map((url, index) =>
        '<img src="' + esc(url) + '" alt="Vinted photo ' + (index + 1) + '" loading="lazy">'
      ).join("")
      + '</div>';
  } else {
    photoPreview.classList.add("hidden");
    photoPreview.innerHTML = "";
  }

  const fields = data.fields || {};
  const sources = data.field_sources || {};
  const enrichment = data.bibliographic_enrichment || {};
  const bookIdValue = fields.book_id || data.book_id_suggestion || "";
  const rows = [
    ["title", "Title", fields.title || "", sources.title, true, true],
    ["author", "Author", fields.author || "", sources.author, true, true],
    ["description", "Description", fields.description || "", sources.description, true, true],
    ["isbn", "ISBN", fields.isbn || "", sources.isbn, true, false],
    ["publisher", "Publisher", enrichment.publisher || "", enrichment.publisher ? "isbn" : null, true, false],
    ["edition", "Edition", enrichment.edition || "", enrichment.edition ? "isbn" : null, true, false],
    ["publish_date", "Publish date", enrichment.publish_date || "", enrichment.publish_date ? "isbn" : null, true, false],
    ["price_cents", "Price", fields.price_cents == null ? "" : (Number(fields.price_cents) / 100).toFixed(2), sources.price_cents, true, true],
    ["book_id", "Book ID", bookIdValue, sources.book_id, true, true],
    ["quantity", "Quantity", fields.quantity, sources.quantity, false, true],
    ["photos", "Photos", source.photo_count ? source.photo_count + " Vinted photo" + (source.photo_count === 1 ? "" : "s") + " - automatic BIBLIO upload" : "No Vinted photos available", source.photo_count ? "vinted" : null, false, false],
  ];
  $("#biblio-publish-fields").innerHTML = rows.map(([key, label, value, sourceName, editable, required]) => {
    const missing = required && (value == null || String(value).trim() === "");
    let valueHtml;
    if (editable) {
      if (key === "description") {
        valueHtml = '<textarea class="biblio-review-input" data-field="description" data-required="' + (required ? "true" : "false")
          + '" rows="4" placeholder="Description required by BIBLIO">' + esc(value || "") + '</textarea>';
      } else {
        const type = key === "price_cents" ? "number" : "text";
        const extra = key === "price_cents" ? ' min="0" step="0.01" inputmode="decimal"' : "";
        const placeholder = key === "book_id" ? "Unique BIBLIO Book ID" : (required ? label : label + " (optional)");
        valueHtml = '<input class="biblio-review-input" data-field="' + esc(key) + '" data-required="' + (required ? "true" : "false")
          + '" type="' + type + '"' + extra + ' value="' + esc(value || "") + '" placeholder="' + esc(placeholder) + '">';
      }
    } else {
      valueHtml = '<strong>' + esc(value == null || value === "" ? "—" : value) + '</strong>';
    }
    return '<div class="biblio-field ' + (missing ? "missing" : "") + '">'
      + '<span class="biblio-field-label">' + esc(label) + '</span>'
      + valueHtml
      + (sourceName ? biblioSourceBadge(sourceName) : "")
      + '</div>';
  }).join("");

  const missing = data.missing || [];
  let warning = "";
  if (!data.configured) warning = "BIBLIO is not connected yet.";
  else if (missing.length) warning = "Before publishing: " + missing.join(", ") + ".";
  else if (data.enrichment_warning) warning = "ISBN lookup warning: " + data.enrichment_warning;
  else warning = data.already_listed
    ? "This physical book already has a BIBLIO listing. Publishing will update it from the current source data."
    : "Ready. Publishing creates a BIBLIO listing linked to this same physical book and queues the FTP sync.";
  $("#biblio-publish-warning").textContent = warning;

  $("#biblio-open-connections").classList.toggle("hidden", Boolean(data.configured));
  $("#biblio-edit-stock").classList.toggle("hidden", !missing.includes("available stock"));
  const publish = $("#biblio-publish-submit");
  const hardMissing = missing.filter((value) => ![
    "author", "title", "description", "price", "Book ID", "unique BIBLIO Book ID",
  ].includes(value));
  const refreshPublishState = () => {
    const unresolvedEditable = Array.from(document.querySelectorAll(".biblio-review-input[data-required='true']"))
      .some((field) => !String(field.value || "").trim());
    publish.disabled = !data.configured || hardMissing.length > 0 || unresolvedEditable;
  };
  document.querySelectorAll(".biblio-review-input").forEach((field) => {
    field.oninput = refreshPublishState;
  });
  refreshPublishState();
  publish.textContent = data.action === "update" ? "Update on BIBLIO" : "Publish to BIBLIO";
}

async function openBiblioPublish(itemId, sourceListingId = null) {
  if (!itemId) return flash("This listing is not linked to master inventory yet.", true);
  state.biblioPublish = { itemId, sourceListingId, data: null };
  $("#biblio-publish-panel").classList.remove("hidden");
  $("#biblio-publish-title").textContent = "Checking BIBLIO…";
  $("#biblio-publish-source").textContent = "";
  $("#biblio-photo-preview").classList.add("hidden");
  $("#biblio-photo-preview").innerHTML = "";
  $("#biblio-publish-fields").innerHTML = '<div class="empty">Preparing listing from Vinted, ISBN metadata and master data…</div>';
  $("#biblio-publish-warning").textContent = "";
  $("#biblio-publish-submit").disabled = true;
  $("#biblio-open-connections").classList.add("hidden");
  $("#biblio-edit-stock").classList.add("hidden");
  $("#biblio-publish-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const suffix = sourceListingId ? "?source_listing_id=" + encodeURIComponent(sourceListingId) : "";
    const data = await api("/api/app/inventory/" + encodeURIComponent(itemId) + "/publish/biblio" + suffix);
    renderBiblioPublish(data);
  } catch (error) {
    $("#biblio-publish-fields").innerHTML = "";
    $("#biblio-publish-warning").textContent = error.message;
    flash(error.message, true);
  }
}

async function linkListingToInventory(listingId) {
  await selectView("reconcile");
  const select = $("#reconcile-listing");
  if (!select || !Array.from(select.options).some((option) => option.value === listingId)) {
    return flash("Listing could not be found in reconciliation.", true);
  }
  select.value = listingId;
  select.scrollIntoView({ behavior: "smooth", block: "center" });
  flash("Choose the physical inventory item, then click Link to master item. Cross-listing will become available after linking.");
}

async function inventory() {
  const q = encodeURIComponent($("#inventory-q").value.trim());
  const status = encodeURIComponent($("#inventory-status").value);
  const data = await api("/api/app/inventory?q=" + q + "&status=" + status);
  state.inventoryItems = data.items || [];
  const costFilter = $("#inventory-cost").value;
  const visibleItems = state.inventoryItems.filter((item) => {
    if (costFilter === "missing") return item.cost_cents == null;
    if (costFilter === "recorded") return item.cost_cents != null;
    return true;
  });
  $("#inventory-count").textContent = visibleItems.length
    + " shown" + (visibleItems.length !== state.inventoryItems.length ? " of " + state.inventoryItems.length : "");

  $("#inventory-table").innerHTML = visibleItems.length
    ? '<table><thead><tr><th><input id="inventory-select-all" type="checkbox" aria-label="Select all"></th><th>Item</th><th>SKU</th><th>Category</th><th>Qty</th><th>Location</th><th>Cost</th><th>Ask</th><th>Margin</th><th>Channels</th><th>Status</th><th>Actions</th></tr></thead><tbody>'
      + visibleItems.map((item) =>
        '<tr><td><input class="inventory-select" type="checkbox" data-id="' + esc(item.id) + '" aria-label="Select ' + esc(item.title) + '"></td>'
        + '<td><div class="title">' + esc(item.title) + '</div><div class="sub">'
        + [item.condition, item.attributes?.author, item.attributes?.brand, item.attributes?.size, item.attributes?.vinted_category]
          .filter(Boolean).map(esc).join(" · ")
        + '</div></td>'
        + '<td>' + esc(item.sku) + "</td><td>" + esc(item.category)
        + "</td><td>" + item.quantity + "</td><td>" + esc(item.location || "—")
        + "</td><td>" + money(item.cost_cents, item.currency)
        + (item.attributes?.cost_source
          ? '<div class="sub">' + esc(
            item.attributes.cost_source === "vinted_purchase"
              ? "Vinted purchase" + (item.attributes.cost_source_adjusted ? " · adjusted" : "")
              : item.attributes.cost_source
          ) + "</div>"
          : "")
        + "</td><td>" + money(item.effective_ask_cents, item.currency)
        + (item.effective_ask_source === "marketplace" ? '<div class="sub">marketplace</div>' : "")
        + "</td><td>" + money(item.potential_margin_cents, item.currency)
        + "</td><td>"
        + ((item.listings || []).map((listing) =>
          '<span class="pill ' + esc(listing.channel) + '">' + esc(listing.channel) + "</span>"
        ).join(" ") || "—")
        + "</td><td>" + esc(item.status) + '</td><td class="row-actions">'
        + inventoryCrossListAction(item)
        + '<button class="btn edit-item" data-id="' + item.id + '">Edit</button></td></tr>'
      ).join("")
      + "</tbody></table>"
    : '<div class="empty">No inventory yet. Add an item or import a file.</div>';

  document.querySelectorAll(".edit-item").forEach((button) => {
    button.onclick = () => openItemForm(state.inventoryItems.find((item) => item.id === button.dataset.id));
  });
  bindCrossListButtons();
  $$(".inventory-select").forEach((box) => { box.onchange = updateInventorySelection; });
  const selectAll = $("#inventory-select-all");
  if (selectAll) {
    selectAll.onchange = () => {
      $$(".inventory-select").forEach((box) => { box.checked = selectAll.checked; });
      updateInventorySelection();
    };
  }
  updateInventorySelection();
}

$("#close-cross-list").onclick = () => {
  state.crossList = null;
  $("#cross-list-panel").classList.add("hidden");
};

$("#close-biblio-publish").onclick = () => {
  state.biblioPublish = null;
  $("#biblio-publish-panel").classList.add("hidden");
};
$("#biblio-open-connections").onclick = () => selectView("connections");
$("#biblio-edit-stock").onclick = () => {
  const itemId = state.biblioPublish?.itemId;
  const item = state.inventoryItems.find((row) => row.id === itemId);
  if (!item) return flash("Inventory item could not be found.", true);
  $("#biblio-publish-panel").classList.add("hidden");
  openItemForm(item);
  flash("Set the physical stock quantity, save, then publish to BIBLIO.");
};
$("#biblio-publish-submit").onclick = async () => {
  const current = state.biblioPublish;
  if (!current?.itemId) return;
  const button = $("#biblio-publish-submit");
  if (button.disabled) return;
  const payload = { source_listing_id: current.sourceListingId || null };
  document.querySelectorAll(".biblio-review-input").forEach((field) => {
    const value = String(field.value || "").trim();
    const required = field.dataset.required === "true";
    if (!value && required) return;
    if (field.dataset.field === "price_cents") {
      if (value) payload.price_cents = Math.round(Number(value) * 100);
    } else {
      payload[field.dataset.field] = value;
    }
  });
  button.disabled = true;
  button.textContent = "Queueing BIBLIO…";
  try {
    const result = await api("/api/app/inventory/" + encodeURIComponent(current.itemId) + "/publish/biblio", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    flash("BIBLIO listing and available Vinted photos queued for FTP publication.");
    $("#biblio-publish-panel").classList.add("hidden");
    state.biblioPublish = null;
    await inventory();
  } catch (error) {
    flash(error.message, true);
    button.disabled = false;
    button.textContent = current.data.action === "update" ? "Update on BIBLIO" : "Publish to BIBLIO";
  }
};

$("#bulk-edit").onclick = () => {
  if (!selectedInventoryIds().length) return;
  $("#bulk-form").reset();
  $("#bulk-form").classList.remove("hidden");
  $("#bulk-form").scrollIntoView({ behavior: "smooth", block: "start" });
};

$("#cancel-bulk").onclick = () => $("#bulk-form").classList.add("hidden");

$("#bulk-form").onsubmit = async (event) => {
  event.preventDefault();
  const itemIds = selectedInventoryIds();
  if (!itemIds.length) return flash("Select at least one inventory item.", true);
  const raw = Object.fromEntries(new FormData(event.currentTarget));
  const payload = { item_ids: itemIds };
  if (raw.category) payload.category = raw.category;
  if (raw.condition) payload.condition = raw.condition;
  if (raw.location) payload.location = raw.location;
  if (raw.currency) payload.currency = raw.currency.trim().toUpperCase();
  if (raw.status) payload.status = raw.status;
  if (raw.cost !== "") payload.cost_cents = Math.round(Number(raw.cost) * 100);
  if (raw.price !== "") payload.default_price_cents = Math.round(Number(raw.price) * 100);
  if (Object.keys(payload).length === 1) return flash("Choose at least one field to update.", true);
  try {
    const result = await api("/api/app/inventory/bulk", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    flash("Updated " + result.updated + " inventory item" + (result.updated === 1 ? "." : "s."));
    $("#bulk-form").classList.add("hidden");
    await inventory();
  } catch (error) {
    flash(error.message, true);
  }
};

$("#inventory-q").oninput = () => inventory().catch((error) => flash(error.message, true));
$("#inventory-status").onchange = () => inventory().catch((error) => flash(error.message, true));
$("#inventory-cost").onchange = () => inventory().catch((error) => flash(error.message, true));
$("#inventory-reset").onclick = () => {
  $("#inventory-q").value = "";
  $("#inventory-status").value = "";
  $("#inventory-cost").value = "";
  inventory().catch((error) => flash(error.message, true));
};
function resetStockIntakeRuntime() {
  stopBarcodeCamera();
  state.stockIntakeQueue = [];
  state.stockIntakeRestored = false;
  state.stockEnrichmentQueue = [];
  state.stockEnrichmentQueued.clear();
}

function stockIntakeDefaults() {
  return {
    location: String($("#stock-default-location")?.value || "").trim(),
    condition: String($("#stock-default-condition")?.value || "").trim(),
    cost: String($("#stock-default-cost")?.value || "").trim(),
    price: String($("#stock-default-price")?.value || "").trim(),
    currency: String($("#stock-default-currency")?.value || "EUR").trim().toUpperCase() || "EUR",
  };
}

function stockLocalId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return "scan-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

function stockSessionKey() {
  const workspace = state.me?.workspace?.id || state.me?.workspace?.slug || "default";
  return "reseller-dashboard:stock-intake:" + workspace;
}

function stockModeKey() {
  const workspace = state.me?.workspace?.id || state.me?.workspace?.slug || "default";
  return "reseller-dashboard:stock-intake-mode:" + workspace;
}

function persistStockIntakeSession() {
  try {
    localStorage.setItem(stockSessionKey(), JSON.stringify({
      queue: state.stockIntakeQueue,
      defaults: stockIntakeDefaults(),
      saved_at: new Date().toISOString(),
    }));
  } catch {}
}

function persistStockIntakeMode(mode) {
  try {
    localStorage.setItem(stockModeKey(), mode);
  } catch {}
}

function lastStockIntakeMode() {
  try {
    return localStorage.getItem(stockModeKey()) || "";
  } catch {
    return "";
  }
}

function restoreStockIntakeSession() {
  if (state.stockIntakeRestored) return;
  state.stockIntakeRestored = true;
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(stockSessionKey()) || "null");
  } catch {}
  if (!saved || typeof saved !== "object") return;

  const defaults = saved.defaults || {};
  if ($("#stock-default-location")) $("#stock-default-location").value = defaults.location || "";
  if ($("#stock-default-condition")) $("#stock-default-condition").value = defaults.condition || "";
  if ($("#stock-default-cost")) $("#stock-default-cost").value = defaults.cost || "";
  if ($("#stock-default-price")) $("#stock-default-price").value = defaults.price || "";
  if ($("#stock-default-currency")) $("#stock-default-currency").value = defaults.currency || "EUR";

  const rows = Array.isArray(saved.queue) ? saved.queue.slice(0, 100) : [];
  state.stockIntakeQueue = rows.map((row) => ({
    ...row,
    local_id: row.local_id || stockLocalId(),
    enrichment_state: row.enrichment_state === "ready" || row.enrichment_state === "needs_review"
      ? row.enrichment_state
      : "pending",
  }));
  state.stockIntakeQueue
    .filter((row) => row.enrichment_state === "pending")
    .forEach((row) => queueStockEnrichment(row.local_id));
}

function stockRowReady(row) {
  return Boolean(String(row?.title || "").trim())
    && row?.enrichment_state !== "pending";
}

function stockQueueStats() {
  const total = state.stockIntakeQueue.length;
  const pending = state.stockIntakeQueue.filter((row) => row.enrichment_state === "pending").length;
  const ready = state.stockIntakeQueue.filter(stockRowReady).length;
  const review = total - pending - ready;
  return { total, pending, ready, review };
}

function updateStockQueueButtons() {
  const stats = stockQueueStats();
  const parts = [stats.total + " scanned"];
  if (stats.ready) parts.push(stats.ready + " ready");
  if (stats.pending) parts.push(stats.pending + " identifying");
  if (stats.review) parts.push(stats.review + " need review");
  $("#stock-queue-count").textContent = parts.join(" · ");
  $("#stock-clear-batch").disabled = stats.total === 0;
  $("#stock-undo-last").disabled = stats.total === 0;
  $("#stock-create-batch").disabled = stats.ready === 0;
  $("#stock-create-batch").textContent = stats.ready
    ? "Create " + stats.ready + " ready item" + (stats.ready === 1 ? "" : "s")
    : "Create ready items";
}

function bindStockQueueInputs() {
  $$(".stock-row-input").forEach((field) => {
    field.oninput = () => {
      const index = Number(field.dataset.index);
      const key = field.dataset.field;
      if (!Number.isInteger(index) || !state.stockIntakeQueue[index] || !key) return;
      state.stockIntakeQueue[index][key] = field.value;
      if (key === "title") {
        state.stockIntakeQueue[index].enrichment_state = String(field.value || "").trim()
          ? "ready"
          : (state.stockIntakeQueue[index].enrichment_state === "pending" ? "pending" : "needs_review");
      }
      persistStockIntakeSession();
      updateStockQueueButtons();
    };
  });
  $$(".stock-row-remove").forEach((button) => {
    button.onclick = () => {
      const index = Number(button.dataset.index);
      if (!Number.isInteger(index)) return;
      state.stockIntakeQueue.splice(index, 1);
      persistStockIntakeSession();
      renderStockIntakeQueue();
      $("#stock-barcode-input")?.focus();
    };
  });
}

function stockRowStatus(row) {
  if (row.enrichment_state === "pending") {
    return '<span class="stock-row-state pending">Identifying…</span>';
  }
  if (row.enrichment_state === "needs_review") {
    return '<span class="stock-row-state review">Needs review</span>';
  }
  return '<span class="stock-row-state ready">Ready</span>';
}

function renderStockIntakeQueue() {
  const rows = state.stockIntakeQueue;
  updateStockQueueButtons();
  if (!rows.length) {
    $("#stock-intake-queue").innerHTML = '<div class="empty">Scan a barcode to start the batch.</div>';
    return;
  }
  $("#stock-intake-queue").innerHTML = '<div class="table-wrap"><table class="stock-scan-table"><thead><tr>'
    + '<th>Item</th><th>Condition</th><th>Cost</th><th>Ask</th><th>Location</th><th></th>'
    + '</tr></thead><tbody>'
    + rows.map((row, index) => {
      const bookBits = [
        row.isbn ? "ISBN " + row.isbn : row.barcode,
        row.author,
        row.publisher,
      ].filter(Boolean);
      const duplicate = Number(row.existing_copy_count || 0)
        ? '<span class="stock-copy-note">' + Number(row.existing_copy_count)
          + ' existing cop' + (Number(row.existing_copy_count) === 1 ? "y" : "ies") + '</span>'
        : "";
      const cover = row.cover_url
        ? '<img class="stock-cover" src="' + esc(row.cover_url) + '" alt="">'
        : '<div class="stock-cover placeholder"></div>';
      const titlePlaceholder = row.enrichment_state === "pending"
        ? "Identifying…"
        : "Item title required";
      return '<tr><td><div class="stock-item-cell">' + cover + '<div>'
        + '<div class="stock-item-status">' + stockRowStatus(row) + '</div>'
        + '<input class="stock-row-input stock-title-input" data-index="' + index
        + '" data-field="title" value="' + esc(row.title || "") + '" placeholder="' + titlePlaceholder + '">'
        + '<div class="sub">' + esc(bookBits.join(" · ")) + " " + duplicate + '</div></div></div></td>'
        + '<td><input class="stock-row-input stock-small-input" data-index="' + index
        + '" data-field="condition" value="' + esc(row.condition || "") + '"></td>'
        + '<td><input class="stock-row-input stock-money-input" data-index="' + index
        + '" data-field="cost" type="number" min="0" step="0.01" value="' + esc(row.cost || "") + '"></td>'
        + '<td><input class="stock-row-input stock-money-input" data-index="' + index
        + '" data-field="price" type="number" min="0" step="0.01" value="' + esc(row.price || "") + '"></td>'
        + '<td><input class="stock-row-input stock-small-input" data-index="' + index
        + '" data-field="location" value="' + esc(row.location || "") + '"></td>'
        + '<td><button class="btn stock-row-remove" data-index="' + index + '" type="button">Remove</button></td>'
        + '</tr>';
    }).join("")
    + "</tbody></table></div>";
  bindStockQueueInputs();
}

function playStockScanTone() {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    if (!state.stockAudioContext) state.stockAudioContext = new AudioContext();
    const context = state.stockAudioContext;
    if (context.state === "suspended") context.resume().catch(() => {});
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = 880;
    gain.gain.setValueAtTime(0.04, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.07);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.07);
  } catch {}
}

function acknowledgeStockScan() {
  try {
    if (navigator.vibrate) navigator.vibrate(45);
  } catch {}
  playStockScanTone();
}

function fastStockBarcodeKind(raw) {
  const code = String(raw || "").trim();
  const upper = code.toUpperCase();
  if (upper.startsWith("RDLOC:")) return "location";
  const compact = upper.replace(/[\s-]+/g, "");
  if (/^\d{13}$/.test(compact) && (compact.startsWith("978") || compact.startsWith("979"))) return "isbn";
  if (/^\d{9}[\dX]$/.test(compact)) return "isbn";
  return "barcode";
}

function queueStockEnrichment(localId) {
  if (!localId || state.stockEnrichmentQueued.has(localId)) return;
  state.stockEnrichmentQueued.add(localId);
  state.stockEnrichmentQueue.push(localId);
  pumpStockEnrichment();
}

function pumpStockEnrichment() {
  while (state.stockEnrichmentActive < 2 && state.stockEnrichmentQueue.length) {
    const localId = state.stockEnrichmentQueue.shift();
    state.stockEnrichmentActive += 1;
    enrichStockRow(localId)
      .catch(() => {})
      .finally(() => {
        state.stockEnrichmentQueued.delete(localId);
        state.stockEnrichmentActive -= 1;
        pumpStockEnrichment();
      });
  }
}

async function enrichStockRow(localId) {
  const initial = state.stockIntakeQueue.find((row) => row.local_id === localId);
  if (!initial) return;
  try {
    const data = await api("/api/app/stock-intake/barcode/lookup", {
      method: "POST",
      body: JSON.stringify({ code: initial.barcode }),
    });
    const row = state.stockIntakeQueue.find((candidate) => candidate.local_id === localId);
    if (!row) return;

    const metadata = data.metadata || {};
    const titleParts = [metadata.title, metadata.subtitle].filter(Boolean);
    row.category = data.kind === "isbn" ? "book" : "general";
    row.isbn = data.isbn || null;
    if (!String(row.title || "").trim() && titleParts.length) row.title = titleParts.join(": ");
    row.author = metadata.author || row.author || null;
    row.publisher = metadata.publisher || row.publisher || null;
    row.edition = metadata.edition || row.edition || null;
    row.publication_year = metadata.publication_year || row.publication_year || null;
    row.cover_url = metadata.cover_url || row.cover_url || null;
    row.source_url = metadata.source_url || row.source_url || null;
    row.existing_copy_count = Number(data.existing_copy_count || 0);
    row.enrichment_warning = data.metadata_warning || null;
    row.enrichment_state = String(row.title || "").trim() ? "ready" : "needs_review";
  } catch (error) {
    const row = state.stockIntakeQueue.find((candidate) => candidate.local_id === localId);
    if (!row) return;
    row.enrichment_warning = error.message;
    row.enrichment_state = String(row.title || "").trim() ? "ready" : "needs_review";
  }
  persistStockIntakeSession();
  renderStockIntakeQueue();
}

function undoLastStockScan() {
  if (!state.stockIntakeQueue.length) return;
  const removed = state.stockIntakeQueue.pop();
  if (removed?.local_id) {
    state.stockEnrichmentQueued.delete(removed.local_id);
    state.stockEnrichmentQueue = state.stockEnrichmentQueue.filter((id) => id !== removed.local_id);
  }
  persistStockIntakeSession();
  renderStockIntakeQueue();
  $("#stock-scan-status").textContent = "Removed last scan.";
  $("#stock-barcode-input")?.focus();
}

function addScannedBarcode(code, format = "manual") {
  const raw = String(code || "").trim();
  if (!raw) return false;

  if (fastStockBarcodeKind(raw) === "location") {
    const location = raw.split(":", 2)[1]?.trim() || "";
    $("#stock-default-location").value = location;
    persistStockIntakeSession();
    $("#stock-scan-status").textContent = "Current location set to " + (location || "—") + ".";
    $("#stock-barcode-input").value = "";
    $("#stock-barcode-input").focus();
    acknowledgeStockScan();
    return true;
  }

  if (state.stockIntakeQueue.length >= 100) {
    $("#stock-scan-status").textContent = "This batch already has 100 items. Create ready stock or clear the batch before scanning more.";
    flash("The scan batch is full at 100 items.", true);
    return false;
  }

  const defaults = stockIntakeDefaults();
  const kind = fastStockBarcodeKind(raw);
  const row = {
    local_id: stockLocalId(),
    barcode: raw.replace(/[\s-]+/g, ""),
    barcode_format: format || "unknown",
    category: kind === "isbn" ? "book" : "general",
    isbn: kind === "isbn" ? raw.replace(/[\s-]+/g, "").toUpperCase() : null,
    title: "",
    author: null,
    publisher: null,
    edition: null,
    publication_year: null,
    cover_url: null,
    source_url: null,
    condition: defaults.condition,
    cost: defaults.cost,
    price: defaults.price,
    currency: defaults.currency,
    location: defaults.location,
    existing_copy_count: 0,
    enrichment_state: "pending",
  };

  state.stockIntakeQueue.push(row);
  persistStockIntakeSession();
  renderStockIntakeQueue();
  acknowledgeStockScan();
  $("#stock-scan-status").textContent = "Scanned " + row.barcode + ". Ready for the next item.";
  $("#stock-barcode-input").value = "";
  $("#stock-barcode-input").focus();
  queueStockEnrichment(row.local_id);
  return true;
}

async function decodeBarcodeImage(file) {
  if (!file) return [];
  const body = new FormData();
  body.append("image", file, file.name || "barcode.jpg");
  const result = await api("/api/app/stock-intake/barcode/decode", {
    method: "POST",
    body,
  });
  return Array.isArray(result.barcodes) ? result.barcodes : [];
}

async function captureBarcodeFrame() {
  const video = $("#stock-barcode-video");
  if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return [];
  const canvas = $("#stock-barcode-canvas");
  const maxWidth = 960;
  const scale = Math.min(1, maxWidth / video.videoWidth);
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
  canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
  const context = canvas.getContext("2d", { alpha: false });
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.72));
  if (!blob) return [];
  const file = new File([blob], "camera-barcode.jpg", { type: "image/jpeg" });
  return decodeBarcodeImage(file);
}

function acceptCameraBarcode(code, format) {
  const raw = String(code || "").trim();
  if (!raw) return false;
  if (state.barcodeCameraLatch === raw) return false;
  state.barcodeCameraLatch = raw;
  state.barcodeCameraClearFrames = 0;
  return addScannedBarcode(raw, format || "camera");
}

function noteCameraBarcodeMiss() {
  state.barcodeCameraClearFrames += 1;
  if (state.barcodeCameraClearFrames >= 2) {
    state.barcodeCameraLatch = null;
    state.barcodeCameraClearFrames = 0;
  }
}

async function barcodeCameraTick() {
  if (state.barcodeBusy) return;
  const video = $("#stock-barcode-video");
  if (!video || video.readyState < 2) return;
  state.barcodeBusy = true;
  try {
    if (state.barcodeDetector) {
      let found = [];
      try {
        found = await state.barcodeDetector.detect(video);
      } catch {
        state.barcodeDetector = null;
      }
      if (found.length) {
        state.barcodeMisses = 0;
        const first = found[0];
        acceptCameraBarcode(first.rawValue, first.format || "camera");
        $("#stock-camera-status").textContent = "Captured. Move to the next barcode.";
        return;
      }
      noteCameraBarcodeMiss();
      state.barcodeMisses += 1;
      if (state.barcodeMisses < 3) return;
    } else {
      state.barcodeMisses += 1;
      if (state.barcodeMisses < 2) return;
    }

    state.barcodeMisses = 0;
    const decoded = await captureBarcodeFrame();
    if (decoded.length) {
      acceptCameraBarcode(decoded[0].code, decoded[0].format || "camera");
      $("#stock-camera-status").textContent = "Captured. Move to the next barcode.";
    } else if (!state.barcodeDetector) {
      noteCameraBarcodeMiss();
    }
  } catch (error) {
    $("#stock-camera-status").textContent = error.message;
  } finally {
    state.barcodeBusy = false;
  }
}

function stopBarcodeCamera() {
  if (state.barcodeTimer) {
    window.clearInterval(state.barcodeTimer);
    state.barcodeTimer = null;
  }
  if (state.barcodeStream) {
    state.barcodeStream.getTracks().forEach((track) => track.stop());
    state.barcodeStream = null;
  }
  state.barcodeDetector = null;
  state.barcodeBusy = false;
  state.barcodeMisses = 0;
  state.barcodeCameraLatch = null;
  state.barcodeCameraClearFrames = 0;
  const video = $("#stock-barcode-video");
  if (video) video.srcObject = null;
  $("#stock-camera-box")?.classList.add("hidden");
  $("#stock-stop-camera")?.classList.add("hidden");
  $("#stock-start-camera")?.classList.remove("hidden");
}

function showStockChoices() {
  stopBarcodeCamera();
  $("#stock-intake-choices").classList.remove("hidden");
  $("#stock-scan-workspace").classList.add("hidden");
}

function showStockScanner() {
  persistStockIntakeMode("scan");
  restoreStockIntakeSession();
  $("#stock-intake-choices").classList.add("hidden");
  $("#stock-scan-workspace").classList.remove("hidden");
  renderStockIntakeQueue();
  window.setTimeout(() => $("#stock-barcode-input")?.focus(), 20);
}

function openStockIntake(mode = null) {
  $("#bulk-form")?.classList.add("hidden");
  $("#quick-listing-form")?.classList.add("hidden");
  $("#item-form")?.classList.add("hidden");
  $("#stock-intake-panel").classList.remove("hidden");
  const preferred = mode || (lastStockIntakeMode() === "scan" ? "scan" : "choices");
  if (preferred === "scan") showStockScanner();
  else showStockChoices();
  $("#stock-intake-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeStockIntake() {
  stopBarcodeCamera();
  persistStockIntakeSession();
  $("#stock-intake-panel").classList.add("hidden");
}

async function startBarcodeCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    $("#stock-camera-status").textContent = "This browser cannot open a camera. Use Scan photo instead.";
    $("#stock-camera-box").classList.remove("hidden");
    return;
  }
  stopBarcodeCamera();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: 1280 },
        height: { ideal: 720 },
      },
      audio: false,
    });
    state.barcodeStream = stream;
    const video = $("#stock-barcode-video");
    video.srcObject = stream;
    await video.play();

    const Detector = window.BarcodeDetector;
    if (Detector) {
      try {
        const supported = typeof Detector.getSupportedFormats === "function"
          ? await Detector.getSupportedFormats()
          : [];
        const desired = [
          "ean_13", "ean_8", "upc_a", "upc_e", "code_128", "qr_code",
        ].filter((format) => !supported.length || supported.includes(format));
        state.barcodeDetector = desired.length
          ? new Detector({ formats: desired })
          : new Detector();
      } catch {
        state.barcodeDetector = null;
      }
    }
    $("#stock-camera-box").classList.remove("hidden");
    $("#stock-start-camera").classList.add("hidden");
    $("#stock-stop-camera").classList.remove("hidden");
    $("#stock-camera-status").textContent = state.barcodeDetector
      ? "Camera ready. Scan continuously."
      : "Camera ready. Server decode fallback is active.";
    state.barcodeTimer = window.setInterval(barcodeCameraTick, 400);
  } catch (error) {
    $("#stock-camera-box").classList.remove("hidden");
    $("#stock-camera-status").textContent = "Could not open the camera: " + error.message;
  }
}

async function createScannedStockBatch() {
  const readyRows = state.stockIntakeQueue.filter(stockRowReady);
  if (!readyRows.length) {
    flash("No ready items yet. Keep scanning or review unidentified rows.", true);
    return;
  }
  const button = $("#stock-create-batch");
  button.disabled = true;
  const createdIds = new Set(readyRows.map((row) => row.local_id));
  const items = readyRows.map((row) => ({
    barcode: row.barcode || null,
    barcode_format: row.barcode_format || null,
    title: String(row.title || "").trim(),
    category: row.category || "general",
    condition: String(row.condition || "").trim() || null,
    cost_cents: String(row.cost || "").trim() === ""
      ? null
      : Math.round(Number(row.cost) * 100),
    price_cents: String(row.price || "").trim() === ""
      ? null
      : Math.round(Number(row.price) * 100),
    currency: String(row.currency || "EUR").trim().toUpperCase(),
    location: String(row.location || "").trim() || null,
    author: row.author || null,
    isbn: row.isbn || null,
    publisher: row.publisher || null,
    edition: row.edition || null,
    publication_year: row.publication_year || null,
    cover_url: row.cover_url || null,
    source_url: row.source_url || null,
  }));
  try {
    const result = await api("/api/app/stock-intake/items", {
      method: "POST",
      body: JSON.stringify({ items }),
    });
    const count = Number(result.count || 0);
    state.stockIntakeQueue = state.stockIntakeQueue.filter((row) => !createdIds.has(row.local_id));
    persistStockIntakeSession();
    renderStockIntakeQueue();
    await inventory();
    flash(
      "Created " + count + " stock item" + (count === 1 ? "." : "s.")
      + (state.stockIntakeQueue.length ? " Unresolved scans remain in the batch." : "")
    );
    $("#stock-barcode-input")?.focus();
  } catch (error) {
    flash(error.message, true);
    updateStockQueueButtons();
  }
}

function clearQuickPhotoUrls() {
  state.quickPhotoUrls.forEach((url) => URL.revokeObjectURL(url));
  state.quickPhotoUrls = [];
}

function renderQuickPhotos() {
  clearQuickPhotoUrls();
  const files = Array.from($("#quick-photos").files || []);
  $("#quick-photo-preview").innerHTML = files.map((file) => {
    const url = URL.createObjectURL(file);
    state.quickPhotoUrls.push(url);
    return '<figure><img src="' + esc(url) + '" alt=""><figcaption>'
      + esc(file.name) + "</figcaption></figure>";
  }).join("");
}

function quickType() {
  return String($("#quick-item-type").value || "").trim().toLowerCase();
}

function captureQuickRequiredValues() {
  $$("#quick-required input, #quick-required textarea").forEach((field) => {
    const targetName = field.dataset.target || field.name;
    state.quickRequiredValues[targetName] = field.value;
    const main = $("#quick-listing-form").elements.namedItem(targetName);
    if (main && main !== field && "value" in main) main.value = field.value;
  });
}

function quickRequirements() {
  const form = $("#quick-listing-form");
  const category = $("#quick-category").value;
  const itemType = quickType();
  const result = [];

  if (category === "book" && !String(form.elements.namedItem("isbn")?.value || "").trim()) {
    result.push(["required_isbn", "ISBN", "text", "Required for the book listing", "isbn"]);
  }
  if (category === "clothing") {
    const shoeTypes = new Set(["shoes", "boots", "trainers", "sneakers"]);
    if (shoeTypes.has(itemType) && !String(form.elements.namedItem("size")?.value || "").trim()) {
      result.push(["required_size", "Size", "text", "Enter the marked size", "size"]);
    }
    if (new Set(["jeans", "trousers", "pants", "shorts"]).has(itemType)) {
      result.push(["waist_cm", "Waist (cm)", "number", "Measure flat/according to your normal workflow"]);
      result.push(["inside_leg_cm", "Inside leg (cm)", "number", "Crotch seam to hem"]);
    } else if (new Set([
      "shirt", "blouse", "top", "t-shirt", "tee", "sweater", "jumper",
      "jacket", "coat", "dress",
    ]).has(itemType)) {
      result.push(["pit_to_pit_cm", "Pit to pit (cm)", "number", "Flat across the chest"]);
      result.push(["length_cm", "Length (cm)", "number", "Top shoulder to hem"]);
    } else if (!shoeTypes.has(itemType)) {
      result.push(["measurements", "Measurements", "text", "Only the useful measurements for this item"]);
    }
  }
  result.push(["price", "Asking price", "number", "Required before creating master stock"]);
  return result;
}

function updateQuickCategoryFields() {
  const category = $("#quick-category").value;
  $$(".quick-clothing-field").forEach((field) => {
    field.classList.toggle("hidden", category !== "clothing");
  });
  $$(".quick-book-field").forEach((field) => {
    field.classList.toggle("hidden", category !== "book");
  });
}

function renderQuickRequired() {
  captureQuickRequiredValues();
  updateQuickCategoryFields();
  const form = $("#quick-listing-form");
  const existing = (name) => {
    const main = form.elements.namedItem(name);
    if (main && String(main.value || "").trim()) return main.value;
    return state.quickRequiredValues[name] || "";
  };
  $("#quick-required").innerHTML = quickRequirements().map(([name, label, type, help, target]) => {
    const step = type === "number" ? ' step="0.01" min="0"' : "";
    const targetName = target || name;
    return '<label>' + esc(label)
      + '<input name="' + esc(name) + '" data-target="' + esc(targetName)
      + '" type="' + esc(type) + '"' + step
      + ' value="' + esc(existing(targetName)) + '"><span class="field-help">'
      + esc(help) + "</span></label>";
  }).join("");
  $$("#quick-required input").forEach((field) => {
    field.oninput = () => {
      const targetName = field.dataset.target || field.name;
      state.quickRequiredValues[targetName] = field.value;
      const main = form.elements.namedItem(targetName);
      if (main && main !== field && "value" in main) main.value = field.value;
    };
  });
}

function applyQuickAnalysis(data) {
  const result = data.analysis || {};
  const form = $("#quick-listing-form");
  const mapping = {
    category: "quick_category",
    item_type: "item_type",
    brand: "brand",
    size: "size",
    colour: "colour",
    material: "material",
    condition: "condition",
    author: "author",
    isbn: "isbn",
    publisher: "publisher",
    edition: "edition",
    suggested_title: "title",
    suggested_description: "description",
  };
  Object.entries(mapping).forEach(([source, target]) => {
    if (result[source]) setFormValue(form, target, result[source]);
  });
  state.quickAnalysisUsed = true;
  state.quickRequiredValues = {};
  const notes = result.confidence_notes || [];
  $("#quick-confidence").innerHTML = notes.length
    ? "<strong>Check:</strong> " + notes.map(esc).join(" · ")
    : "Photo analysis returned no uncertainty notes.";
  renderQuickRequired();
}

async function openQuickListing() {
  const form = $("#quick-listing-form");
  form.reset();
  setFormValue(form, "currency", "EUR");
  setFormValue(form, "quick_category", $("#quick-category-hint").value || "general");
  state.quickAnalysisUsed = false;
  state.quickRequiredValues = {};
  clearQuickPhotoUrls();
  $("#quick-photo-preview").innerHTML = "";
  $("#quick-confidence").textContent = "";
  $("#quick-listing-result").classList.add("hidden");
  form.classList.remove("hidden");
  try {
    state.quickAssistant = await api("/api/app/listing-assistant/status");
    const configured = Boolean(state.quickAssistant.configured);
    $("#quick-analyze").disabled = !configured;
    $("#quick-ai-status").textContent = configured
      ? "Photo analysis available · photos are not stored"
      : "Photo analysis is not configured · manual quick listing still works";
  } catch (error) {
    state.quickAssistant = null;
    $("#quick-analyze").disabled = true;
    $("#quick-ai-status").textContent = error.message;
  }
  renderQuickRequired();
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closeQuickListing() {
  $("#quick-listing-form").classList.add("hidden");
  clearQuickPhotoUrls();
}

$("#add-stock").onclick = () => openStockIntake();
$("#close-stock-intake").onclick = closeStockIntake;
$("#stock-choice-scan").onclick = showStockScanner;
$("#stock-back-choices").onclick = showStockChoices;
$("#stock-choice-photo").onclick = () => {
  persistStockIntakeMode("photo");
  closeStockIntake();
  openQuickListing();
};
$("#stock-choice-import").onclick = async () => {
  persistStockIntakeMode("import");
  closeStockIntake();
  await selectView("imports");
};
$("#stock-choice-connect").onclick = async () => {
  persistStockIntakeMode("connect");
  closeStockIntake();
  await selectView("connections");
};
$("#stock-choice-manual").onclick = () => {
  persistStockIntakeMode("manual");
  closeStockIntake();
  openItemForm(null);
};
$("#stock-barcode-submit").onclick = () => addScannedBarcode($("#stock-barcode-input").value);
$("#stock-barcode-input").onkeydown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    undoLastStockScan();
    return;
  }
  if (event.key !== "Enter") return;
  event.preventDefault();
  addScannedBarcode(event.currentTarget.value);
};
$("#stock-start-camera").onclick = startBarcodeCamera;
$("#stock-stop-camera").onclick = stopBarcodeCamera;
$("#stock-barcode-image").onchange = async (event) => {
  const file = event.currentTarget.files?.[0];
  if (!file) return;
  $("#stock-scan-status").textContent = "Reading barcode photo…";
  try {
    const decoded = await decodeBarcodeImage(file);
    if (!decoded.length) {
      $("#stock-scan-status").textContent = "No barcode found in that photo.";
      return;
    }
    for (const row of decoded) {
      addScannedBarcode(row.code, row.format || "photo");
    }
  } catch (error) {
    $("#stock-scan-status").textContent = error.message;
    flash(error.message, true);
  } finally {
    event.currentTarget.value = "";
  }
};
$("#stock-undo-last").onclick = undoLastStockScan;
$("#stock-clear-batch").onclick = () => {
  if (state.stockIntakeQueue.length && !window.confirm("Clear the scanned batch?")) return;
  state.stockIntakeQueue = [];
  state.stockEnrichmentQueue = [];
  state.stockEnrichmentQueued.clear();
  persistStockIntakeSession();
  renderStockIntakeQueue();
  $("#stock-barcode-input").focus();
};
["stock-default-location", "stock-default-condition", "stock-default-cost", "stock-default-price", "stock-default-currency"]
  .forEach((id) => {
    $("#" + id).oninput = persistStockIntakeSession;
  });
$("#stock-create-batch").onclick = createScannedStockBatch;

$("#cancel-quick-listing").onclick = closeQuickListing;
$("#quick-photos").onchange = renderQuickPhotos;
$("#quick-category").onchange = renderQuickRequired;
$("#quick-category-hint").onchange = () => {
  const hint = $("#quick-category-hint").value;
  if (hint) $("#quick-category").value = hint;
  renderQuickRequired();
};
$("#quick-item-type").oninput = renderQuickRequired;

$("#quick-analyze").onclick = async () => {
  const files = Array.from($("#quick-photos").files || []);
  if (!files.length) return flash("Choose at least one photo.", true);
  const button = $("#quick-analyze");
  button.disabled = true;
  $("#quick-ai-status").textContent = "Analyzing selected photos…";
  const body = new FormData();
  files.forEach((file) => body.append("photos", file));
  body.append("hints_json", JSON.stringify({
    category: $("#quick-category-hint").value || "",
  }));
  try {
    const result = await api("/api/app/listing-assistant/analyze", {
      method: "POST",
      body,
    });
    applyQuickAnalysis(result);
    $("#quick-ai-status").textContent = "Analysis applied · review every detected field";
  } catch (error) {
    $("#quick-ai-status").textContent = error.message;
    flash(error.message, true);
  } finally {
    button.disabled = !state.quickAssistant?.configured;
  }
};

$("#quick-listing-form").onsubmit = async (event) => {
  event.preventDefault();
  captureQuickRequiredValues();
  const form = event.currentTarget;
  const raw = Object.fromEntries(new FormData(form));
  const price = Number(raw.price || 0);
  if (!(price > 0)) return flash("Enter an asking price.", true);

  const payload = {
    sku: String(raw.sku || "").trim() || null,
    title: String(raw.title || "").trim(),
    description: String(raw.description || "").trim(),
    category: String(raw.quick_category || "general"),
    item_type: String(raw.item_type || "").trim() || null,
    brand: String(raw.brand || "").trim() || null,
    size: String(raw.size || "").trim() || null,
    colour: String(raw.colour || "").trim() || null,
    material: String(raw.material || "").trim() || null,
    condition: String(raw.condition || "").trim() || null,
    author: String(raw.author || "").trim() || null,
    isbn: String(raw.isbn || "").trim() || null,
    publisher: String(raw.publisher || "").trim() || null,
    edition: String(raw.edition || "").trim() || null,
    measurements: String(raw.measurements || "").trim() || null,
    waist_cm: String(raw.waist_cm || "").trim() || null,
    inside_leg_cm: String(raw.inside_leg_cm || "").trim() || null,
    pit_to_pit_cm: String(raw.pit_to_pit_cm || "").trim() || null,
    length_cm: String(raw.length_cm || "").trim() || null,
    price_cents: Math.round(price * 100),
    cost_cents: raw.cost ? Math.round(Number(raw.cost) * 100) : null,
    currency: String(raw.currency || "EUR").trim().toUpperCase(),
    location: String(raw.location || "").trim() || null,
    notes: String(raw.notes || "").trim() || null,
    photo_count: Array.from($("#quick-photos").files || []).length,
    analysis_used: state.quickAnalysisUsed,
  };
  if (!payload.title) return flash("Review or enter a title.", true);

  const button = $("#quick-create");
  button.disabled = true;
  try {
    const result = await api("/api/app/listing-assistant/create", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const listing = result.listing_package;
    $("#quick-result-sku").value = listing.sku || "";
    $("#quick-result-price").value = money(listing.price_cents, listing.currency);
    $("#quick-result-title").value = listing.title || "";
    $("#quick-result-description").value = listing.description || "";
    closeQuickListing();
    $("#quick-listing-result").classList.remove("hidden");
    $("#quick-listing-result").scrollIntoView({ behavior: "smooth", block: "start" });
    await inventory();
    flash("Master item created. Listing package is ready for manual upload.");
  } catch (error) {
    flash(error.message, true);
  } finally {
    button.disabled = false;
  }
};

async function copyQuickField(selector, label) {
  const value = $(selector).value || "";
  try {
    await navigator.clipboard.writeText(value);
    flash(label + " copied.");
  } catch {
    flash("Could not copy automatically. Select the text and copy it manually.", true);
  }
}

$("#copy-quick-title").onclick = () => copyQuickField("#quick-result-title", "Title");
$("#copy-quick-description").onclick = () => copyQuickField("#quick-result-description", "Description");
$("#close-quick-result").onclick = () => $("#quick-listing-result").classList.add("hidden");
$("#quick-open-inventory").onclick = () => {
  $("#quick-listing-result").classList.add("hidden");
  $("#inventory-table").scrollIntoView({ behavior: "smooth", block: "start" });
};

$("#cancel-item").onclick = closeItemForm;

function categoryFields() {
  const category = $("#item-category").value;
  $("#book-fields").classList.toggle("hidden", category !== "book");
  $("#clothing-fields").classList.toggle("hidden", category !== "clothing");
}

$("#item-category").onchange = categoryFields;

function closeItemForm() {
  state.editItemId = null;
  $("#item-form").classList.add("hidden");
}

function setFormValue(form, name, value) {
  const field = form.elements.namedItem(name);
  if (field) field.value = value == null ? "" : value;
}

function openItemForm(item) {
  const form = $("#item-form");
  form.reset();
  state.editItemId = item?.id || null;
  $("#item-form-title").textContent = item ? "Edit item" : "Add item";
  $("#save-item").textContent = item ? "Save changes" : "Add item";
  const skuField = form.elements.namedItem("sku");
  skuField.disabled = Boolean(item);
  $("#item-form-channels").innerHTML = item?.listings?.length
    ? item.listings.map((listing) =>
      '<span class="pill ' + esc(listing.channel) + '">' + esc(listing.channel) + "</span>"
    ).join(" ")
    : "";

  if (item) {
    setFormValue(form, "sku", item.sku);
    setFormValue(form, "title", item.title);
    setFormValue(form, "category", item.category);
    setFormValue(form, "quantity", item.quantity);
    setFormValue(form, "condition", item.condition);
    setFormValue(form, "location", item.location);
    setFormValue(form, "cost", item.cost_cents == null ? "" : (Number(item.cost_cents) / 100).toFixed(2));
    setFormValue(form, "price", item.attributes?.default_price_cents == null ? "" : (Number(item.attributes.default_price_cents) / 100).toFixed(2));
    setFormValue(form, "currency", item.currency || "EUR");
    setFormValue(form, "notes", item.notes);
    [
      "author", "isbn", "publisher", "edition", "binding", "publication_year",
      "brand", "size", "colour", "material", "measurements",
    ].forEach((key) => setFormValue(form, key, item.attributes?.[key]));
  } else {
    setFormValue(form, "quantity", 1);
    setFormValue(form, "currency", "EUR");
  }
  categoryFields();
  form.classList.remove("hidden");
  form.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#item-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const raw = Object.fromEntries(new FormData(form));
  const existing = state.inventoryItems.find((item) => item.id === state.editItemId);
  const attributes = Object.assign({}, existing?.attributes || {});
  const attributeKeys = [
    "author", "isbn", "publisher", "edition", "binding", "publication_year",
    "brand", "size", "colour", "material", "measurements",
  ];
  attributeKeys.forEach((key) => {
    if (raw[key]) attributes[key] = raw[key];
    else delete attributes[key];
    delete raw[key];
  });
  if (raw.price) attributes.default_price_cents = Math.round(Number(raw.price) * 100);
  else delete attributes.default_price_cents;
  delete raw.price;

  raw.attributes = attributes;
  raw.quantity = Number(raw.quantity || 0);
  raw.cost_cents = raw.cost ? Math.round(Number(raw.cost) * 100) : null;
  delete raw.cost;
  raw.currency = String(raw.currency || "EUR").toUpperCase();

  try {
    if (state.editItemId) {
      delete raw.sku;
      await api("/api/app/inventory/" + state.editItemId, {
        method: "PATCH",
        body: JSON.stringify(raw),
      });
      flash("Item updated.");
    } else {
      await api("/api/app/inventory", {
        method: "POST",
        body: JSON.stringify(raw),
      });
      flash("Item added.");
    }
    closeItemForm();
    await inventory();
  } catch (error) {
    flash(error.message, true);
  }
};

function reconciliationItemLabel(item) {
  const channels = (item.channels || []).join(", ") || "master only";
  return item.sku + " · " + item.title + " · " + channels;
}

function renderReconciliation(data, crossData) {
  const suggestions = data.suggestions || [];
  state.crossChannelActions = crossData.actions || [];
  state.unlinkedSales = crossData.unlinked_sales || [];
  state.reconciliation = suggestions;
  $("#reconcile-summary").textContent = suggestions.length
    ? suggestions.length + " suggestion" + (suggestions.length === 1 ? "" : "s")
      + " · " + (data.high_confidence || 0) + " high confidence"
    : "No candidate matches";

  $("#reconcile-suggestions").innerHTML = suggestions.length
    ? '<table><thead><tr><th></th><th>Candidate A</th><th>Candidate B</th><th>Evidence</th><th>Confidence</th><th>Keep as master</th></tr></thead><tbody>'
      + suggestions.map((row) => {
        const a = row.item_a;
        const b = row.item_b;
        const optionA = '<option value="' + esc(a.id) + '"'
          + (row.recommended_target_id === a.id ? " selected" : "") + ">"
          + esc(a.sku + " · " + a.title) + "</option>";
        const optionB = '<option value="' + esc(b.id) + '"'
          + (row.recommended_target_id === b.id ? " selected" : "") + ">"
          + esc(b.sku + " · " + b.title) + "</option>";
        const itemCell = (item) =>
          '<div class="title">' + esc(item.title) + '</div><div class="sub">'
          + esc(item.sku) + " · "
          + esc((item.channels || []).join(", ") || "master only")
          + (item.synthetic_sku ? " · generated SKU" : "") + "</div>";
        return '<tr data-reconcile-id="' + esc(row.id) + '">'
          + '<td><input type="checkbox" class="reconcile-check" data-id="' + esc(row.id) + '"></td>'
          + "<td>" + itemCell(a) + "</td>"
          + "<td>" + itemCell(b) + "</td>"
          + "<td>" + row.reasons.map(esc).join("<br>") + "</td>"
          + '<td><span class="confidence ' + esc(row.confidence) + '">' + esc(row.confidence) + "</span></td>"
          + '<td><select class="reconcile-target" data-id="' + esc(row.id) + '">' + optionA + optionB + "</select></td>"
          + "</tr>";
      }).join("")
      + "</tbody></table>"
    : '<div class="empty">No safe cross-channel matches found. You can still reconcile a listing manually below.</div>';

  $("#reconcile-listing").innerHTML = state.listings.length
    ? state.listings.map((listing) =>
      '<option value="' + esc(listing.id) + '">' + esc(
        listing.channel + " · " + listing.title + " · " + (listing.external_sku || listing.external_id)
      ) + "</option>"
    ).join("")
    : '<option value="">No listings</option>';

  $("#reconcile-item").innerHTML = state.inventoryItems.length
    ? state.inventoryItems.map((item) =>
      '<option value="' + esc(item.id) + '">' + esc(
        item.sku + " · " + item.title + " · "
        + ((item.listings || []).map((row) => row.channel).join(", ") || "master only")
      ) + "</option>"
    ).join("")
    : '<option value="">No inventory</option>';

  const historicalUnmatched = Number(crossData.historical_unmatched_sell_count || 0);
  $("#unlinked-sale-count").textContent = state.unlinkedSales.length
    ? state.unlinkedSales.length + " ambiguous"
      + (historicalUnmatched ? " · " + historicalUnmatched + " historical unmatched" : "")
    : (historicalUnmatched
      ? "No ambiguous · " + historicalUnmatched + " historical without retained stock"
      : "None");
  $("#reconcile-sale").innerHTML = state.unlinkedSales.length
    ? state.unlinkedSales.map((sale) =>
      '<option value="' + esc(sale.id) + '">' + esc(
        sale.channel + " · " + (sale.title || sale.external_order_id) + " · " + dateOnly(sale.occurred_at)
      ) + "</option>"
    ).join("")
    : '<option value="">No ambiguous sold orders</option>';

  const renderSaleCandidates = () => {
    const selectedSale = state.unlinkedSales.find((sale) => sale.id === $("#reconcile-sale").value);
    const candidateIds = new Set(selectedSale?.candidate_item_ids || []);
    const candidates = candidateIds.size
      ? state.inventoryItems.filter((item) => candidateIds.has(item.id))
      : [];
    $("#reconcile-sale-item").innerHTML = candidates.length
      ? candidates.map((item) =>
        '<option value="' + esc(item.id) + '">' + esc(item.sku + " · " + item.title) + "</option>"
      ).join("")
      : '<option value="">No candidate stock items</option>';
  };
  renderSaleCandidates();
  $("#reconcile-sale").onchange = renderSaleCandidates;

  $("#cross-channel-log").innerHTML = state.crossChannelActions.length
    ? '<table><thead><tr><th>Item</th><th>Triggered by</th><th>Target</th><th>Status</th><th>Attempts</th><th></th></tr></thead><tbody>'
      + state.crossChannelActions.map((row) =>
        '<tr><td><div class="title">' + esc(row.item?.title || row.listing?.title || "Sold item")
        + '</div><div class="sub">' + esc(row.item?.sku || "") + '</div></td>'
        + '<td>' + esc(row.sale?.channel || "") + '<div class="sub">' + esc(row.sale?.external_order_id || "") + '</div></td>'
        + '<td><span class="pill ' + esc(row.channel) + '">' + esc(row.channel) + '</span><div class="sub">' + esc(row.listing?.title || "") + '</div></td>'
        + '<td>' + esc(row.status) + (row.last_error ? '<div class="sub error">' + esc(row.last_error) + '</div>' : "") + '</td>'
        + '<td>' + esc(row.attempts) + '</td><td class="row-actions">' + crossChannelActionControls(row) + '</td></tr>'
      ).join("")
      + "</tbody></table>"
    : '<div class="empty">No cross-channel sold-stock actions yet.</div>';
  bindCrossChannelButtons(reconcile);
}

async function reconcile() {
  const [data, inventoryData, listingData, crossData] = await Promise.all([
    api("/api/app/reconciliation"),
    api("/api/app/inventory"),
    api("/api/app/listings"),
    api("/api/app/cross-channel-actions"),
  ]);
  state.inventoryItems = inventoryData.items || [];
  state.listings = listingData.listings || [];
  renderReconciliation(data, crossData);
}

$("#reconcile-refresh").onclick = () => reconcile().catch((error) => flash(error.message, true));

$("#reconcile-select-high").onclick = () => {
  const high = new Set(
    state.reconciliation.filter((row) => row.confidence === "high").map((row) => row.id),
  );
  $$(".reconcile-check").forEach((box) => { box.checked = high.has(box.dataset.id); });
};

$("#reconcile-apply").onclick = async () => {
  const selected = $$(".reconcile-check").filter((box) => box.checked);
  if (!selected.length) return flash("Select at least one reconciliation.", true);

  const merges = selected.map((box) => {
    const suggestion = state.reconciliation.find((row) => row.id === box.dataset.id);
    const target = $('.reconcile-target[data-id="' + box.dataset.id + '"]').value;
    const source = suggestion.item_a.id === target ? suggestion.item_b.id : suggestion.item_a.id;
    return { target_item_id: target, source_item_id: source };
  });

  try {
    const result = await api("/api/app/reconciliation/apply", {
      method: "POST",
      body: JSON.stringify({ merges }),
    });
    flash("Merged " + result.count + " stock record" + (result.count === 1 ? "." : "s."));
    await reconcile();
  } catch (error) {
    flash(error.message, true);
  }
};

$("#reconcile-sale-link").onclick = async () => {
  const saleId = $("#reconcile-sale").value;
  const itemId = $("#reconcile-sale-item").value;
  if (!saleId || !itemId) return flash("Choose a sold order and master item.", true);
  try {
    const result = await api("/api/app/sales/" + saleId + "/link", {
      method: "POST",
      body: JSON.stringify({ inventory_item_id: itemId }),
    });
    flash(
      "Sale linked. " + result.actions_created + " cross-channel close action"
      + (result.actions_created === 1 ? " created." : "s created.")
    );
    await reconcile();
  } catch (error) {
    flash(error.message, true);
  }
};

$("#reconcile-manual").onclick = async () => {
  const listingId = $("#reconcile-listing").value;
  const targetItemId = $("#reconcile-item").value;
  if (!listingId || !targetItemId) return flash("Choose a listing and master item.", true);

  const listing = state.listings.find((row) => row.id === listingId);
  if (!listing) return flash("Listing is no longer available.", true);
  if (listing.inventory_item_id === targetItemId) {
    return flash("That listing is already linked to this master item.", true);
  }

  try {
    if (listing.inventory_item_id) {
      await api("/api/app/reconciliation/apply", {
        method: "POST",
        body: JSON.stringify({
          merges: [{
            target_item_id: targetItemId,
            source_item_id: listing.inventory_item_id,
          }],
        }),
      });
    } else {
      await api("/api/app/inventory/" + targetItemId + "/link", {
        method: "POST",
        body: JSON.stringify({ listing_id: listingId }),
      });
    }
    flash("Listing linked to master inventory.");
    await reconcile();
  } catch (error) {
    flash(error.message, true);
  }
};

function dateOnly(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
}

function ageDays(value) {
  if (!value) return null;
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return null;
  return Math.max(0, Math.floor((Date.now() - timestamp) / 86400000));
}

function age(value) {
  const days = ageDays(value);
  if (days == null) return "—";
  return days + " day" + (days === 1 ? "" : "s");
}

function timeValue(value) {
  const number = new Date(value || 0).getTime();
  return Number.isNaN(number) ? 0 : number;
}

function median(values) {
  const numbers = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!numbers.length) return null;
  const middle = Math.floor(numbers.length / 2);
  return numbers.length % 2
    ? numbers[middle]
    : Math.round((numbers[middle - 1] + numbers[middle]) / 2);
}

function listingFingerprint(title) {
  return String(title || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function duplicateInfo(rows) {
  const groups = new Map();
  for (const row of rows) {
    const fingerprint = listingFingerprint(row.title);
    if (!fingerprint) continue;
    const key = row.channel + "::" + fingerprint;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  const duplicateGroups = Array.from(groups.values()).filter((group) => group.length > 1);
  const counts = new Map();
  for (const group of duplicateGroups) {
    for (const row of group) counts.set(row._index, group.length);
  }
  return { duplicateGroups, counts };
}

function listingDisplayDate(row) {
  return row.listed_at || null;
}

function listingAgeSeconds(row) {
  const exact = timeValue(listingDisplayDate(row));
  if (exact > 0) return Math.max(0, Math.floor((Date.now() - exact) / 1000));
  if (
    row?.channel === "vinted"
    && !String(row?.listed_age_source || "").startsWith("vinted_page")
  ) return null;
  const relative = Number(row?.listed_age_seconds);
  return Number.isFinite(relative) && relative >= 0 ? relative : null;
}

function listingAgeLabel(row) {
  const seconds = listingAgeSeconds(row);
  if (seconds == null) return "—";
  if (!row.listed_at && row.listed_age_text && String(row.listed_age_source || "").startsWith("vinted_page")) {
    return String(row.listed_age_text);
  }
  const approximate = !row.listed_at && row.listed_age_seconds != null;
  if (seconds < 3600) {
    const minutes = Math.max(0, Math.floor(seconds / 60));
    return (approximate ? "≈ " : "") + (minutes < 1 ? "<1 min" : minutes + " min");
  }
  if (seconds < 86400) {
    const hours = Math.max(1, Math.floor(seconds / 3600));
    return (approximate ? "≈ " : "") + hours + " h";
  }
  const days = Math.max(0, Math.floor(seconds / 86400));
  return (approximate ? "≈ " : "") + days + " day" + (days === 1 ? "" : "s");
}

function listingShownDate(row) {
  if (row.listed_at) return { text: dateOnly(row.listed_at), approximate: false };
  const seconds = listingAgeSeconds(row);
  if (seconds == null) return { text: "—", approximate: false };
  return {
    text: "≈ " + dateOnly(new Date(Date.now() - seconds * 1000).toISOString()),
    approximate: true,
  };
}

function listingComparator(sort) {
  const number = (value, fallback = -1) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  if (sort === "oldest") {
    return (a, b) => {
      const aa = listingAgeSeconds(a);
      const ba = listingAgeSeconds(b);
      if (aa != null && ba != null) return ba - aa;
      if (aa != null) return -1;
      if (ba != null) return 1;
      return b._index - a._index;
    };
  }
  if (sort === "price-asc") return (a, b) => number(a.price_cents, Infinity) - number(b.price_cents, Infinity);
  if (sort === "price-desc") return (a, b) => number(b.price_cents, -1) - number(a.price_cents, -1);
  if (sort === "likes-desc") return (a, b) => number(b.favourites, 0) - number(a.favourites, 0) || a._index - b._index;
  if (sort === "views-desc") return (a, b) => number(b.views, 0) - number(a.views, 0) || a._index - b._index;
  if (sort === "title-asc") {
    return (a, b) => String(a.title || "").localeCompare(String(b.title || ""), undefined, { sensitivity: "base" });
  }
  return (a, b) => {
    const aa = listingAgeSeconds(a);
    const ba = listingAgeSeconds(b);
    if (aa != null && ba != null) return aa - ba;
    if (aa != null) return -1;
    if (ba != null) return 1;
    return a._index - b._index;
  };
}

async function listings() {
  const data = await api("/api/app/listings");
  state.listings = data.listings || [];
  renderListings();
}

function renderListingStats(rows, duplicates) {
  const prices = rows.map((row) => Number(row.price_cents)).filter(Number.isFinite);
  const total = prices.reduce((sum, value) => sum + value, 0);
  const average = prices.length ? Math.round(total / prices.length) : null;
  const middle = median(prices);
  const currency = rows.find((row) => row.currency)?.currency || "EUR";
  const favourites = rows.reduce((sum, row) => sum + (Number(row.favourites) || 0), 0);
  const zeroFavourites = rows.filter((row) => Number(row.favourites || 0) === 0).length;

  const vinted = rows.filter((row) => row.channel === "vinted");
  const agedVinted = vinted
    .map((row) => ({ row, ageSeconds: listingAgeSeconds(row) }))
    .filter((entry) => entry.ageSeconds != null)
    .sort((a, b) => a.ageSeconds - b.ageSeconds);
  const youngest = agedVinted[0] || null;
  const oldest = agedVinted.length ? agedVinted[agedVinted.length - 1] : null;
  const unknownVinted = vinted.length - agedVinted.length;

  $("#listing-stat-count").textContent = String(rows.length);
  $("#listing-stat-value").textContent = money(total, currency);
  $("#listing-stat-average").textContent = money(average, currency);
  $("#listing-stat-median").textContent = money(middle, currency);
  $("#listing-stat-favourites").textContent = String(favourites);
  $("#listing-stat-zero-favourites").textContent = String(zeroFavourites);
  $("#listing-stat-duplicate-groups").textContent = String(duplicates.duplicateGroups.length);
  $("#listing-stat-duplicates").classList.toggle("has-duplicates", duplicates.duplicateGroups.length > 0);

  $("#listing-stat-age-known").textContent = agedVinted.length + "/" + vinted.length;
  $("#listing-stat-age-unknown").textContent = unknownVinted
    ? unknownVinted + " pending Vinted Uploaded scan"
    : (vinted.length ? "Exact or Vinted Uploaded age" : "No Vinted rows in this view");

  const renderEdge = (entry, ageSelector, detailSelector) => {
    if (!entry) {
      $(ageSelector).textContent = "—";
      $(detailSelector).textContent = "No Vinted age available";
      return;
    }
    const shown = listingShownDate(entry.row);
    $(ageSelector).textContent = listingAgeLabel(entry.row);
    $(detailSelector).textContent = shown.text + " · " + entry.row.title;
  };
  renderEdge(youngest, "#listing-stat-youngest-age", "#listing-stat-youngest-detail");
  renderEdge(oldest, "#listing-stat-oldest-age", "#listing-stat-oldest-detail");
}

function renderListings() {
  const rows = (state.listings || []).map((row, _index) => ({ ...row, _index }));
  const query = $("#listing-search").value.trim().toLowerCase();
  const channel = $("#listing-channel").value;
  const status = $("#listing-status").value;
  const interest = $("#listing-interest").value;
  const duplicateMode = $("#listing-duplicate").value;
  const minimum = parseFloat($("#listing-price-min").value);
  const maximum = parseFloat($("#listing-price-max").value);
  const minCents = Number.isFinite(minimum) ? Math.round(minimum * 100) : null;
  const maxCents = Number.isFinite(maximum) ? Math.round(maximum * 100) : null;
  const sort = $("#listing-sort").value;

  const baseFiltered = rows
    .filter((row) => !channel || row.channel === channel)
    .filter((row) => !status || String(row.status || "").toLowerCase() === status)
    .filter((row) => !query || (
      String(row.title || "").toLowerCase().includes(query)
      || String(row.external_sku || "").toLowerCase().includes(query)
      || String(row.external_id || "").toLowerCase().includes(query)
    ))
    .filter((row) => interest !== "liked" || Number(row.favourites || 0) > 0)
    .filter((row) => interest !== "unliked" || Number(row.favourites || 0) === 0)
    .filter((row) => minCents === null || row.price_cents == null || Number(row.price_cents) >= minCents)
    .filter((row) => maxCents === null || row.price_cents == null || Number(row.price_cents) <= maxCents);

  const duplicates = duplicateInfo(baseFiltered);
  const filtered = baseFiltered
    .filter((row) => duplicateMode !== "duplicates" || duplicates.counts.has(row._index))
    .filter((row) => duplicateMode !== "unique" || !duplicates.counts.has(row._index))
    .sort(listingComparator(sort));

  const statsDuplicates = duplicateInfo(filtered);
  renderListingStats(filtered, duplicateMode === "unique" ? statsDuplicates : duplicates);
  $("#listing-count").textContent = filtered.length + " of " + rows.length;

  const showFavourites = filtered.some((row) => row.favourites != null);
  const showViews = filtered.some((row) => row.views != null);
  const showDate = filtered.some((row) => row.channel === "vinted")
    || filtered.some((row) => timeValue(listingDisplayDate(row)) > 0);

  $("#listings-table").innerHTML = filtered.length
    ? '<table><thead><tr><th>Listing</th><th>Marketplace</th><th>Status</th>'
      + (showDate ? "<th>Listed</th><th>Age</th>" : "")
      + (showFavourites ? "<th>Favourites</th>" : "")
      + (showViews ? "<th>Views</th>" : "")
      + '<th>Price</th><th>Actions</th></tr></thead><tbody>'
      + filtered.map((row) => {
        const duplicateCount = duplicates.counts.get(row._index);
        const title = row.url
          ? '<a href="' + esc(row.url) + '" target="_blank" rel="noreferrer">' + esc(row.title) + "</a>"
          : esc(row.title);
        return '<tr class="' + (duplicateCount ? "duplicate-row" : "") + '">'
          + '<td><div class="title">' + title
          + (duplicateCount ? '<span class="duplicate-pill">' + duplicateCount + " copies</span>" : "")
          + '</div><div class="sub">' + esc(row.external_sku || row.external_id || "") + "</div></td>"
          + '<td><span class="pill ' + esc(row.channel) + '">' + esc(row.channel) + "</span></td>"
          + "<td>" + esc(row.status) + "</td>"
          + (showDate ? (() => {
            const shown = listingShownDate(row);
            const approximate = shown.approximate
              ? '<div class="sub">'
                + (String(row.listed_age_source || "").startsWith("vinted_page")
                  ? "from Vinted Uploaded field"
                  : "from Vinted relative age")
                + "</div>"
              : "";
            return '<td>' + shown.text + approximate
              + '</td><td>' + listingAgeLabel(row) + approximate + '</td>';
          })() : "")
          + (showFavourites ? "<td>" + esc(row.favourites == null ? "—" : row.favourites) + "</td>" : "")
          + (showViews ? "<td>" + esc(row.views == null ? "—" : row.views) + "</td>" : "")
          + "<td>" + money(row.price_cents, row.currency) + "</td>"
          + '<td class="row-actions">'
          + listingCrossListAction(row)
          + "</td></tr>";
      }).join("")
      + "</tbody></table>"
    : '<div class="empty">No matching listings.</div>';
  bindCrossListButtons();
}

[
  "#listing-search",
  "#listing-price-min",
  "#listing-price-max",
].forEach((selector) => {
  $(selector).addEventListener("input", renderListings);
});
[
  "#listing-channel",
  "#listing-status",
  "#listing-interest",
  "#listing-duplicate",
  "#listing-sort",
].forEach((selector) => {
  $(selector).addEventListener("change", renderListings);
});

$("#listing-stat-duplicates").onclick = () => {
  $("#listing-duplicate").value = "duplicates";
  renderListings();
};

$("#listing-stat-youngest").onclick = () => {
  $("#listing-channel").value = "vinted";
  $("#listing-sort").value = "newest";
  renderListings();
};

$("#listing-stat-oldest").onclick = () => {
  $("#listing-channel").value = "vinted";
  $("#listing-sort").value = "oldest";
  renderListings();
};

$("#listing-reset").onclick = () => {
  $("#listing-search").value = "";
  $("#listing-channel").value = "";
  $("#listing-status").value = "active";
  $("#listing-interest").value = "";
  $("#listing-duplicate").value = "";
  $("#listing-price-min").value = "";
  $("#listing-price-max").value = "";
  $("#listing-sort").value = "newest";
  renderListings();
};

async function sales() {
  const [data, purchaseCosts] = await Promise.all([
    api("/api/app/sales"),
    api("/api/app/purchase-cost-suggestions"),
  ]);
  state.salesRows = data.sales || [];
  state.purchaseCostSuggestions = purchaseCosts || {
    suggestions: [],
    count: 0,
    ambiguous_count: 0,
    unmatched_count: 0,
  };
  renderSales();
}

function renderPurchaseCostSuggestions() {
  const data = state.purchaseCostSuggestions || {};
  const rows = data.suggestions || [];
  const card = $("#purchase-cost-card");
  const visible = $("#sales-direction").value === "buy" || $("#sales-direction").value === "";
  card.classList.toggle("hidden", !visible);

  const parts = [];
  if (rows.length) parts.push(rows.length + " suggestion" + (rows.length === 1 ? "" : "s"));
  if (data.ambiguous_count) parts.push(data.ambiguous_count + " ambiguous");
  if (data.unmatched_count) parts.push(data.unmatched_count + " unmatched history");
  $("#purchase-cost-summary").textContent = parts.join(" · ") || "No cost suggestions";

  $("#purchase-cost-suggestions").innerHTML = rows.length
    ? '<table><thead><tr><th>Purchase</th><th>Matched stock</th><th>Match</th><th>Order amount</th><th>Cost to record</th><th></th></tr></thead><tbody>'
      + rows.map((row) => {
        const purchase = row.purchase;
        const item = row.item;
        const euros = purchase.total_cents == null ? "" : (Number(purchase.total_cents) / 100).toFixed(2);
        return '<tr data-purchase-cost-id="' + esc(row.id) + '">'
          + '<td><div class="title">' + esc(purchase.title) + '</div><div class="sub">'
          + esc(dateOnly(purchase.occurred_at)) + " · " + esc(purchase.external_order_id || "") + "</div></td>"
          + '<td><div class="title">' + esc(item.title) + '</div><div class="sub">' + esc(item.sku) + " · " + esc(item.status) + "</div></td>"
          + '<td>' + esc(row.match_reason) + '</td>'
          + '<td>' + money(purchase.total_cents, purchase.currency) + '</td>'
          + '<td><input class="purchase-cost-input" type="number" min="0" step="0.01" inputmode="decimal" value="' + esc(euros) + '" aria-label="Acquisition cost"></td>'
          + '<td><button class="btn primary purchase-cost-apply" type="button" data-purchase-id="' + esc(purchase.id)
          + '" data-item-id="' + esc(item.id) + '">Apply cost</button></td></tr>';
      }).join("")
      + "</tbody></table>"
    : '<div class="empty">No unambiguous purchase-to-stock cost matches need review.</div>';

  $$(".purchase-cost-apply").forEach((button) => {
    button.onclick = async () => {
      const row = button.closest("tr");
      const input = row?.querySelector(".purchase-cost-input");
      const value = Number(input?.value);
      if (!Number.isFinite(value) || value < 0) {
        return flash("Enter a valid acquisition cost.", true);
      }
      try {
        await api("/api/app/purchases/" + button.dataset.purchaseId + "/apply-cost", {
          method: "POST",
          body: JSON.stringify({
            inventory_item_id: button.dataset.itemId,
            cost_cents: Math.round(value * 100),
          }),
        });
        flash("Acquisition cost recorded.");
        await sales();
      } catch (error) {
        flash(error.message, true);
      }
    };
  });
}

function renderSales() {
  const direction = $("#sales-direction").value;
  const stateFilter = $("#sales-state").value;
  const channel = $("#sales-channel").value;
  const query = $("#sales-search").value.trim().toLowerCase();

  const rows = (state.salesRows || []).filter((sale) => {
    if (direction && sale.direction !== direction) return false;
    if (stateFilter === "open" && sale.is_closed) return false;
    if (stateFilter === "closed" && !sale.is_closed) return false;
    if (channel && sale.channel !== channel) return false;
    if (query) {
      const haystack = [sale.title, sale.counterparty, sale.external_order_id, sale.status]
        .map((value) => String(value || "").toLowerCase())
        .join(" ");
      if (!haystack.includes(query)) return false;
    }
    return true;
  });

  $("#sales-count").textContent = rows.length + " of " + state.salesRows.length;
  renderPurchaseCostSuggestions();
  $("#sales-table").innerHTML = rows.length
    ? '<table><thead><tr><th>Date</th><th>Channel</th><th>Item</th><th>Person</th><th>Direction</th><th>Status</th><th>Amount</th></tr></thead><tbody>'
      + rows.map((sale) =>
        "<tr><td>" + esc(when(sale.occurred_at)) + '</td><td><span class="pill '
        + esc(sale.channel) + '">' + esc(sale.channel) + "</span></td><td>"
        + esc(sale.title || "—") + "</td><td>" + esc(sale.counterparty || "—")
        + "</td><td>" + esc(sale.direction) + "</td><td>"
        + esc(sale.status || "—") + "</td><td>" + money(sale.total_cents, sale.currency) + "</td></tr>"
      ).join("")
      + "</tbody></table>"
    : '<div class="empty">No matching orders.</div>';
}

["#sales-direction", "#sales-state", "#sales-channel"].forEach((selector) => {
  $(selector).addEventListener("change", renderSales);
});
$("#sales-search").addEventListener("input", renderSales);
$("#sales-reset").onclick = () => {
  $("#sales-direction").value = "sell";
  $("#sales-state").value = "";
  $("#sales-channel").value = "";
  $("#sales-search").value = "";
  renderSales();
};

function seriesChart(target, points, valueKey, { moneyValues = false } = {}) {
  const element = $(target);
  const clean = (points || [])
    .map((point) => ({ x: point.date || point.captured_at, y: point[valueKey] }))
    .filter((point) => point.x && point.y != null && Number.isFinite(Number(point.y)));
  if (!clean.length) {
    element.innerHTML = '<div class="chart-empty">Not enough history yet.</div>';
    return;
  }
  const width = 720;
  const height = 190;
  const left = 42;
  const right = 12;
  const top = 14;
  const bottom = 28;
  const values = clean.map((point) => Number(point.y));
  const min = Math.min(0, ...values);
  const max = Math.max(...values, 1);
  const span = Math.max(1, max - min);
  const x = (index) => left + (clean.length === 1 ? 0 : index * (width - left - right) / (clean.length - 1));
  const y = (value) => top + (max - value) * (height - top - bottom) / span;
  const coords = clean.map((point, index) => x(index).toFixed(1) + "," + y(Number(point.y)).toFixed(1)).join(" ");
  const first = clean[0];
  const last = clean[clean.length - 1];
  const formatValue = (value) => moneyValues ? money(value, "EUR") : String(Math.round(value));
  element.innerHTML =
    '<svg viewBox="0 0 ' + width + " " + height + '" role="img">'
    + '<line class="chart-grid" x1="' + left + '" y1="' + y(max) + '" x2="' + (width - right) + '" y2="' + y(max) + '"></line>'
    + '<line class="chart-grid" x1="' + left + '" y1="' + y(min) + '" x2="' + (width - right) + '" y2="' + y(min) + '"></line>'
    + '<polyline class="chart-line" points="' + coords + '"></polyline>'
    + '<text class="chart-label" x="2" y="' + (y(max) + 3) + '">' + esc(formatValue(max)) + "</text>"
    + '<text class="chart-label" x="2" y="' + (y(min) + 3) + '">' + esc(formatValue(min)) + "</text>"
    + '<text class="chart-label" x="' + left + '" y="' + (height - 6) + '">' + esc(String(first.x).slice(0, 10)) + "</text>"
    + '<text class="chart-label" text-anchor="end" x="' + (width - right) + '" y="' + (height - 6) + '">' + esc(String(last.x).slice(0, 10)) + "</text>"
    + "</svg>";
}

function analyticsBars(target, rows, labelKey, countKey, labelMap = {}) {
  const element = $(target);
  const max = Math.max(1, ...rows.map((row) => Number(row[countKey] || 0)));
  element.innerHTML = rows.length
    ? rows.map((row) => {
      const value = Number(row[countKey] || 0);
      const label = labelMap[row[labelKey]] || row[labelKey];
      const width = Math.round(value * 100 / max);
      return '<div class="analytics-bar-row"><div class="analytics-bar-label"><span>'
        + esc(label) + '</span><strong>' + esc(value) + '</strong></div>'
        + '<div class="analytics-bar-track"><span style="width:' + width + '%"></span></div></div>';
    }).join("")
    : '<div class="chart-empty">No data yet.</div>';
}

function renderVintedBehavior(data) {
  const summary = data.summary || {};
  const strategy = data.strategy || {};
  const analyticsCurrency = data.currency || "EUR";
  $("#vinted-coverage").textContent =
    (summary.tracked_active_listings || 0) + " of " + (summary.active_listings || 0)
    + " active listings have snapshot history";

  $("#vinted-metrics").innerHTML =
    metric("Views gained", summary.views_gained || 0, data.days + " days")
    + metric("Favourites gained", summary.favourites_gained || 0, data.days + " days")
    + metric("Favourites / 100 views",
      summary.favourites_per_100_views == null ? "—" : summary.favourites_per_100_views)
    + metric("Median active age",
      summary.median_active_age_days == null ? "—" : summary.median_active_age_days + " d",
      (summary.actual_age_count || 0) + " of " + (summary.active_listings || 0) + " exact Vinted dates")
    + metric("Linked Vinted sales", summary.linked_sales || 0,
      summary.median_days_to_sale == null ? "" : "median " + summary.median_days_to_sale + " d to sale")
    + metric(
      "Gross profit",
      summary.costed_linked_sales ? money(summary.gross_profit_cents, analyticsCurrency) : "—",
      summary.costed_linked_sales + " of " + (summary.linked_sales || 0) + " sales costed"
    )
    + metric(
      "Gross margin",
      summary.gross_margin_pct == null ? "—" : summary.gross_margin_pct + "%",
      summary.roi_pct == null ? "" : "ROI " + summary.roi_pct + "%"
    )
    + metric("Price changes", summary.price_changes_30d || 0, "last 30 days");

  $("#vinted-thresholds").textContent =
    "Segments use workspace thresholds: momentum = at least "
    + (strategy.momentum_favourites_7d ?? "—") + " new favourites in 7 days; "
    + "high-interest stale = at least " + (strategy.stale_days ?? "—") + " days old and "
    + (strategy.high_favourites ?? "—") + "+ favourites; low-interest stale = at least "
    + (strategy.very_stale_days ?? "—") + " days old and at most "
    + (strategy.low_favourites ?? "—") + " favourite(s).";

  analyticsBars("#vinted-age-bars", data.age_buckets || [], "label", "count");
  analyticsBars(
    "#vinted-segment-bars",
    data.segments || [],
    "segment",
    "count",
    {
      momentum: "Momentum",
      high_interest_stale: "High-interest stale",
      low_interest_stale: "Low-interest stale",
      steady: "Steady",
    },
  );

  const categories = data.categories || [];
  $("#vinted-categories").innerHTML = categories.length
    ? '<table><thead><tr><th>Category</th><th>Active</th><th>Views</th><th>Views gained</th><th>Favourites</th><th>Favourites gained</th><th>Fav / 100 views</th><th>Linked sales</th><th>Costed</th><th>Revenue</th><th>Cost</th><th>Profit</th><th>Margin</th><th>ROI</th></tr></thead><tbody>'
      + categories.map((row) =>
        "<tr><td>" + esc(row.category) + "</td><td>" + esc(row.active_listings)
        + "</td><td>" + esc(row.views) + "</td><td>+" + esc(row.views_gain)
        + "</td><td>" + esc(row.favourites) + "</td><td>+" + esc(row.favourites_gain)
        + "</td><td>" + esc(row.favourites_per_100_views == null ? "—" : row.favourites_per_100_views)
        + "</td><td>" + esc(row.linked_sales)
        + '</td><td data-sort-value="' + esc(row.cost_coverage_pct == null ? "" : row.cost_coverage_pct) + '">'
        + (row.linked_sales ? esc(row.costed_sales + "/" + row.linked_sales) : "—")
        + '</td><td data-sort-value="' + esc(row.costed_sales ? row.costed_revenue_cents : "") + '">'
        + (row.costed_sales ? money(row.costed_revenue_cents, analyticsCurrency) : "—")
        + '</td><td data-sort-value="' + esc(row.costed_sales ? row.cost_cents : "") + '">'
        + (row.costed_sales ? money(row.cost_cents, analyticsCurrency) : "—")
        + '</td><td data-sort-value="' + esc(row.costed_sales ? row.gross_profit_cents : "") + '">'
        + (row.costed_sales ? money(row.gross_profit_cents, analyticsCurrency) : "—")
        + '</td><td data-sort-value="' + esc(row.gross_margin_pct == null ? "" : row.gross_margin_pct) + '">'
        + (row.gross_margin_pct == null ? "—" : esc(row.gross_margin_pct) + "%")
        + '</td><td data-sort-value="' + esc(row.roi_pct == null ? "" : row.roi_pct) + '">'
        + (row.roi_pct == null ? "—" : esc(row.roi_pct) + "%") + "</td></tr>"
      ).join("")
      + "</tbody></table>"
    : '<div class="empty">No Vinted category history yet.</div>';

  const soldRows = data.sold_stock || [];
  $("#vinted-sold-stock").innerHTML = soldRows.length
    ? '<table><thead><tr><th>Item</th><th>Category</th><th>Listed</th><th>Sold</th><th>Days online</th><th>Sale price</th><th>Cost</th><th>Profit</th><th>Margin</th><th>ROI</th><th>Order</th></tr></thead><tbody>'
      + soldRows.map((row) => {
        const title = row.url
          ? '<a href="' + esc(row.url) + '" target="_blank" rel="noreferrer">' + esc(row.title) + "</a>"
          : esc(row.title);
        return '<tr><td><div class="title">' + title + '</div></td>'
          + '<td>' + esc(row.category) + '</td>'
          + '<td data-sort-value="' + esc(row.listed_at ? new Date(row.listed_at).getTime() : "") + '">'
          + (row.listed_at ? esc(when(row.listed_at)) : "—") + '</td>'
          + '<td data-sort-value="' + esc(new Date(row.sold_at).getTime()) + '">' + esc(when(row.sold_at))
          + (row.sold_at_source === "first_seen" ? '<div class="sub">first observed</div>' : "")
          + '</td>'
          + '<td data-sort-value="' + esc(row.days_online == null ? "" : row.days_online) + '">'
          + (row.days_online == null ? "—" : esc(row.days_online) + " d") + '</td>'
          + '<td data-sort-value="' + esc(row.sale_total_cents == null ? "" : row.sale_total_cents) + '">'
          + money(row.sale_total_cents, row.currency) + '</td>'
          + '<td data-sort-value="' + esc(row.cost_cents == null ? "" : row.cost_cents) + '">'
          + money(row.cost_cents, row.currency)
          + (row.cost_source ? '<div class="sub">' + esc(
            row.cost_source === "vinted_purchase"
              ? "Vinted purchase" + (row.cost_source_adjusted ? " · adjusted" : "")
              : row.cost_source
          ) + "</div>" : "")
          + '</td>'
          + '<td data-sort-value="' + esc(row.gross_profit_cents == null ? "" : row.gross_profit_cents) + '">'
          + money(row.gross_profit_cents, row.currency) + '</td>'
          + '<td data-sort-value="' + esc(row.gross_margin_pct == null ? "" : row.gross_margin_pct) + '">'
          + (row.gross_margin_pct == null ? "—" : esc(row.gross_margin_pct) + "%") + '</td>'
          + '<td data-sort-value="' + esc(row.roi_pct == null ? "" : row.roi_pct) + '">'
          + (row.roi_pct == null ? "—" : esc(row.roi_pct) + "%") + '</td>'
          + '<td>' + esc(row.external_order_id || "—") + '</td></tr>';
      }).join("")
      + "</tbody></table>"
    : '<div class="empty">No linked Vinted sales in this period.</div>';

  const rows = data.listings || [];
  const segmentLabel = {
    momentum: "Momentum",
    high_interest_stale: "High-interest stale",
    low_interest_stale: "Low-interest stale",
    steady: "Steady",
  };
  $("#vinted-efficiency").innerHTML = rows.length
    ? '<table><thead><tr><th>Listing</th><th>Age</th><th>Views</th><th>+7d</th><th>Favs</th><th>+7d</th><th>Fav / 100 views</th><th>Views/day</th><th>Price changes</th><th>Signal</th></tr></thead><tbody>'
      + rows.map((row) => {
        const title = row.url
          ? '<a href="' + esc(row.url) + '" target="_blank" rel="noreferrer">' + esc(row.title) + "</a>"
          : esc(row.title);
        return '<tr class="analytics-listing" data-id="' + esc(row.listing_id) + '"><td><div class="title">'
          + title + '</div><div class="sub">' + esc(row.category) + " · "
          + money(row.price_cents, row.currency) + "</div></td><td>"
          + (row.age_days == null ? "—" : esc(row.age_days) + " d")
          + "</td><td>"
          + esc(row.views) + '</td><td class="gain">+' + esc(row.views_gain_7d)
          + "</td><td>" + esc(row.favourites) + '</td><td class="gain">+' + esc(row.favourites_gain_7d)
          + "</td><td>" + esc(row.favourites_per_100_views == null ? "—" : row.favourites_per_100_views)
          + "</td><td>" + esc(row.views_per_day) + "</td><td>" + esc(row.price_changes_30d)
          + '</td><td><span class="signal ' + esc(row.segment) + '">'
          + esc(segmentLabel[row.segment] || row.segment) + "</span></td></tr>";
      }).join("")
      + "</tbody></table>"
    : '<div class="empty">No active Vinted listing history yet.</div>';
}

async function analytics() {
  const days = Number($("#analytics-days").value || 90);
  const [summary, history, vinted] = await Promise.all([
    api("/api/app/analytics"),
    api("/api/app/analytics/history?days=" + days),
    api("/api/app/analytics/vinted?days=" + days),
  ]);
  const activeInventory = Number(summary.active_inventory || 0);
  const pricedInventory = Number(summary.priced_inventory_count || 0);
  const costedInventory = Number(summary.costed_inventory_count || 0);
  const marginInventory = Number(summary.margin_inventory_count || 0);
  $("#analytics-metrics").innerHTML =
    metric("Active inventory", activeInventory)
    + metric(
      "Inventory cost",
      costedInventory ? money(summary.inventory_cost_cents, summary.currency) : "—",
      costedInventory + " of " + activeInventory + " costs recorded"
    )
    + metric(
      "Inventory ask",
      pricedInventory ? money(summary.inventory_ask_cents, summary.currency) : "—",
      pricedInventory + " of " + activeInventory + " priced"
    )
    + metric(
      "Potential margin",
      marginInventory ? money(summary.inventory_potential_margin_cents, summary.currency) : "—",
      marginInventory + " of " + activeInventory + " have cost + ask"
    )
    + metric(
      "Sales YTD",
      summary.sales_ytd_count,
      money(summary.sales_ytd_cents, summary.currency)
        + " · " + summary.sales_ytd_revenue_known_count + " of " + summary.sales_ytd_count + " amounts known"
    )
    + metric(
      "Gross profit YTD",
      summary.sales_ytd_costed_count
        ? money(summary.sales_ytd_gross_profit_cents, summary.currency)
        : "—",
      summary.sales_ytd_costed_count + " of " + summary.sales_ytd_revenue_known_count + " revenue-known sales costed"
    )
    + metric(
      "Gross margin YTD",
      summary.sales_ytd_gross_margin_pct == null ? "—" : summary.sales_ytd_gross_margin_pct + "%",
      summary.sales_ytd_roi_pct == null ? "" : "ROI " + summary.sales_ytd_roi_pct + "%"
    )
    + metric("Followers", summary.followers == null ? "—" : summary.followers, summary.following == null ? "" : summary.following + " following");

  renderVintedBehavior(vinted);

  seriesChart("#views-chart", history.daily, "views_gained");
  seriesChart("#favourites-chart", history.daily, "favourites_gained");
  seriesChart("#followers-chart", history.followers, "followers");
  seriesChart("#sales-chart", history.daily, "revenue_cents", { moneyValues: true });

  $("#analytics-listings").innerHTML = history.top_listings.length
    ? '<table><thead><tr><th>Listing</th><th>Views</th><th>+7d</th><th>Favourites</th><th>+7d</th><th>Status</th></tr></thead><tbody>'
      + history.top_listings.map((row) =>
        '<tr class="analytics-listing" data-id="' + row.listing_id + '"><td><div class="title">'
        + esc(row.title) + '</div></td><td>' + esc(row.views == null ? "—" : row.views)
        + '</td><td class="gain">+' + esc(row.views_gain_7d || 0)
        + '</td><td>' + esc(row.favourites == null ? "—" : row.favourites)
        + '</td><td class="gain">+' + esc(row.favourites_gain_7d || 0)
        + "</td><td>" + esc(row.status) + "</td></tr>"
      ).join("")
      + "</tbody></table>"
    : '<div class="empty">No Vinted listing history yet.</div>';

  $$(".analytics-listing").forEach((row) => {
    row.onclick = () => loadListingHistory(row.dataset.id);
  });
}

$("#analytics-days").onchange = () => analytics().catch((error) => flash(error.message, true));

async function loadListingHistory(listingId) {
  try {
    const data = await api("/api/app/listings/" + listingId + "/history?days=365");
    $("#listing-history-title").textContent = data.listing.title;
    const link = $("#listing-history-link");
    if (data.listing.url) {
      link.href = data.listing.url;
      link.classList.remove("hidden");
    } else {
      link.classList.add("hidden");
    }
    const points = data.history.map((row) => ({
      captured_at: row.captured_at,
      views: row.views,
      favourites: row.favourites,
    }));
    seriesChart("#listing-views-chart", points, "views");
    seriesChart("#listing-favourites-chart", points, "favourites");
    $("#listing-history-card").classList.remove("hidden");
    $("#listing-history-card").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    flash(error.message, true);
  }
}

async function imports() {
  const data = await api("/api/app/mappings?direction=import");
  state.mappings = data.presets || [];
  $("#mapping-preset").innerHTML = '<option value="">Automatic mapping</option>'
    + state.mappings.map((row) =>
      '<option value="' + row.id + '">' + esc(row.name) + " (" + esc(row.file_type) + ")</option>"
    ).join("");
}

$("#mapping-preset").onchange = () => {
  const preset = state.mappings.find((row) => row.id === $("#mapping-preset").value);
  state.mapping = preset ? Object.assign({}, preset.mapping || {}) : {};
  if (preset?.options?.full_snapshot != null) {
    $("#full-snapshot").checked = Boolean(preset.options.full_snapshot);
  }
  if (preset?.options?.default_category) {
    $("#import-default-category").value = preset.options.default_category;
  }
};

$("#preview-import").onclick = async () => {
  const file = $("#import-file").files[0];
  if (!file) return flash("Choose a file first.", true);
  const same = state.file && state.file.name === file.name && state.file.size === file.size;
  state.file = file;
  const form = new FormData();
  form.append("file", file);
  form.append("mapping_json", same && Object.keys(state.mapping).length ? JSON.stringify(state.mapping) : JSON.stringify(state.mapping || {}));
  form.append("full_snapshot", $("#full-snapshot").checked ? "true" : "false");
  form.append("default_category", $("#import-default-category").value);
  try {
    const data = await api("/api/app/import/preview", { method: "POST", body: form });
    if (!Object.keys(state.mapping).length) state.mapping = Object.assign({}, data.suggested_mapping);
    renderMapping(data);
    renderPreview(data);
    $("#mapping").classList.remove("hidden");
    $("#preview").classList.remove("hidden");
    $("#import-status").textContent = data.mapping_required
      ? "Map SKU and title, then click Preview again."
      : "Previewed " + data.rows + " rows from " + file.name;
  } catch (error) {
    flash(error.message, true);
  }
};

function renderMapping(data) {
  $("#mapping-grid").innerHTML = data.headers.map((header) =>
    '<div class="map-row"><strong>' + esc(header) + '</strong><span>→</span><select data-h="'
    + esc(header) + '">' + importFields.map((field) =>
      '<option value="' + field + '" ' + (state.mapping[header] === field ? "selected" : "") + ">"
      + (field || "Ignore") + "</option>"
    ).join("") + "</select></div>"
  ).join("");
  $$("#mapping-grid select").forEach((select) => {
    select.onchange = () => {
      if (select.value) state.mapping[select.dataset.h] = select.value;
      else delete state.mapping[select.dataset.h];
      $("#apply-import").disabled = true;
      $("#import-status").textContent = "Mapping changed - click Preview again before applying.";
    };
  });
}

function renderPreview(data) {
  const counts = data.counts || {};
  const reviewCount = (data.preview || []).filter((row) => (row.potential_duplicates || []).length).length;
  const rows = data.preview || [];
  const rowTable = rows.length
    ? '<div class="table-wrap"><table><thead><tr><th>Row</th><th>Action</th><th>SKU</th><th>Title</th><th>Review</th></tr></thead><tbody>'
      + rows.map((row) => {
        const duplicateText = (row.potential_duplicates || []).map((candidate) =>
          "Possible existing item: " + candidate.sku + " · " + candidate.title
        );
        const notes = [...(row.errors || []), ...duplicateText];
        return "<tr><td>" + esc(row.row) + "</td><td>" + esc(row.action)
          + "</td><td>" + esc(row.sku || "—") + "</td><td>" + esc(row.title || "—")
          + "</td><td>" + (notes.length ? notes.map(esc).join("<br>") : "—") + "</td></tr>";
      }).join("")
      + "</tbody></table></div>"
    : "";
  const missing = (data.missing_existing || []).length
    ? '<div class="import-warning"><strong>Would archive:</strong> '
      + data.missing_existing.map((row) => esc(row.sku + " · " + row.title)).join(", ")
      + ((data.missing_existing_count || 0) > data.missing_existing.length ? " …" : "")
      + "</div>"
    : "";
  $("#preview").innerHTML =
    '<div class="card-head"><h2>Preview - nothing written yet</h2></div><div class="preview-stats">'
    + "<span>" + (counts.new || 0) + " new</span>"
    + "<span>" + (counts.update || 0) + " updates</span>"
    + "<span>" + (counts.unchanged || 0) + " unchanged</span>"
    + "<span>" + (counts.conflict || 0) + " conflicts</span>"
    + "<span>" + reviewCount + " need duplicate review</span>"
    + "<span>" + (data.missing_existing_count || 0) + " would archive</span></div>"
    + (reviewCount ? '<p class="muted">Potential duplicates are not auto-merged. Importing creates separate master records; reconcile them explicitly afterwards.</p>' : "")
    + missing + rowTable;
  $("#apply-import").disabled = !data.can_apply;
}

$("#apply-import").onclick = async () => {
  if (!state.file) return;
  const form = new FormData();
  form.append("file", state.file);
  form.append("mapping_json", JSON.stringify(state.mapping));
  form.append("full_snapshot", $("#full-snapshot").checked ? "true" : "false");
  form.append("default_category", $("#import-default-category").value);
  form.append("preset_name", $("#preset-name").value.trim());
  try {
    const result = await api("/api/app/import/apply", { method: "POST", body: form });
    flash("Import applied: " + result.created + " new, " + result.updated + " updated, " + result.archived + " archived.");
    $("#mapping").classList.add("hidden");
    $("#preview").classList.add("hidden");
    await imports();
  } catch (error) {
    flash(error.message, true);
  }
};

function exportInventory(format) {
  const params = new URLSearchParams({ format });
  const status = $("#export-status").value;
  const channel = $("#export-channel").value;
  if (status) params.set("status", status);
  if (channel) params.set("channel", channel);
  window.location.href = "/api/app/export?" + params.toString();
}

$("#export-csv").onclick = () => exportInventory("csv");
$("#export-xlsx").onclick = () => exportInventory("xlsx");

async function connections() {
  const [data, devices] = await Promise.all([
    api("/api/app/connectors"),
    api("/api/app/extension/devices"),
  ]);
  state.connectors = data.connectors || [];
  $("#connector-grid").innerHTML = data.connectors.map((connector) => {
    const connected = connector.status === "connected";
    const statusClass = connected ? "status-ok" : (connector.configured ? "status-warn" : "");
    const statusText = connector.authorization_required
      ? "Authorization required"
      : (connected
        ? "Connected"
        : (connector.configured ? "Configured - not synced yet" : "Not configured"));
    return '<div class="connector"><h2>' + esc(connector.display_name) + "</h2><p>"
      + esc(connector.description) + '</p><div class="meta ' + statusClass + '">'
      + statusText
      + (connector.last_synced_at ? " · " + esc(when(connector.last_synced_at)) : "")
      + '</div><div class="actions">'
      + (connector.channel === "vinted"
        ? '<button class="btn primary pair">Pair Chrome</button><a class="btn" href="'
          + esc(devices.download_url || "/downloads/reseller-chrome-bridge.zip")
          + '">Download bridge v' + esc(devices.latest_version || state.me?.bridge_version || "unknown") + '</a>'
          + '<div class="pairing-inline hidden"><span class="eyebrow">PAIR CODE</span>'
          + '<strong class="pair-code pair-code-inline"></strong>'
          + '<small>Enter this in Chrome Bridge. Expires in 10 minutes.</small></div>'
        : "")
      + (connectorSchemas[connector.channel]
        ? '<button class="btn configure" data-c="' + esc(connector.channel) + '">Configure</button>'
        : "")
      + (connector.sync_available
        ? '<button class="btn sync" data-c="' + esc(connector.channel) + '">Queue sync</button>'
        : "")
      + "</div>"
      + (connector.note ? '<div class="connector-note">' + esc(connector.note) + "</div>" : "")
      + "</div>";
  }).join("");

  document.querySelectorAll(".pair").forEach((button) => { button.onclick = () => pair(button); });
  $$(".configure").forEach((button) => {
    button.onclick = () => openConnectorConfig(button.dataset.c, data.connectors.find((row) => row.channel === button.dataset.c));
  });
  $$(".sync").forEach((button) => {
    button.onclick = async () => {
      try {
        await api("/api/app/connectors/" + button.dataset.c + "/sync", { method: "POST" });
        flash("Sync queued.");
      } catch (error) {
        flash(error.message, true);
      }
    };
  });

  $("#devices").innerHTML = devices.devices.length
    ? devices.devices.map((device) =>
      '<div class="device-row"><div><strong>' + esc(device.name) + '</strong><div class="sub">Version '
      + esc(device.extension_version || "unknown") + " · "
      + (device.last_seen_at ? esc(when(device.last_seen_at)) : "not seen yet")
      + (device.revoked ? " · revoked" : "") + "</div></div>"
      + (device.revoked ? "" : '<button class="btn revoke" data-id="' + device.id + '">Revoke</button>')
      + "</div>"
    ).join("")
    : '<div class="empty">No Chrome browsers paired yet.</div>';

  $$(".revoke").forEach((button) => {
    button.onclick = async () => {
      try {
        await api("/api/app/extension/devices/" + button.dataset.id, { method: "DELETE" });
        await connections();
      } catch (error) {
        flash(error.message, true);
      }
    };
  });
  return data;
}

function renderEtsyOAuthTools(connector) {
  const active = state.connectorChannel === "etsy";
  $("#etsy-tools").classList.toggle("hidden", !active);
  if (!active) return;
  const callback = connector?.oauth_redirect_uri || "";
  $("#etsy-oauth-help").textContent = callback
    ? "Register this exact redirect URI in the Etsy app: " + callback
    : "Set PUBLIC_APP_URL to the public HTTPS origin before using Etsy OAuth.";
  $("#authorize-etsy").classList.toggle("hidden", !connector?.configured || !callback);
  $("#authorize-etsy").textContent = connector?.operational
    ? "Reauthorize with Etsy"
    : "Authorize with Etsy";
}

function openConnectorConfig(channel, connector) {
  const schema = connectorSchemas[channel];
  if (!schema) return;
  state.connectorChannel = channel;
  $("#connector-config-title").textContent = schema.title;
  $("#connector-config-help").textContent = schema.help;
  $("#connector-fields").innerHTML = schema.fields.map(([name, label, placeholder, type]) =>
    '<label>' + esc(label) + '<input name="' + esc(name) + '" type="' + esc(type)
    + '" placeholder="' + esc(placeholder) + '"></label>'
  ).join("");
  $("#biblio-tools").classList.toggle("hidden", channel !== "biblio");
  renderEtsyOAuthTools(connector);
  $("#test-connector").classList.toggle("hidden", !schema.test || !connector?.operational);
  $("#remove-connector").classList.toggle("hidden", !connector?.configured);
  $("#connector-config-status").textContent = connector?.configured
    ? "Credentials are stored. Leave an existing secret field blank to keep its current value."
    : "";
  $("#connector-config").classList.remove("hidden");
  $("#connector-config").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#close-connector-config").onclick = () => {
  state.connectorChannel = null;
  $("#connector-config").classList.add("hidden");
};

$("#connector-config").onsubmit = async (event) => {
  event.preventDefault();
  const channel = state.connectorChannel;
  if (!channel) return;
  const values = {};
  new FormData(event.currentTarget).forEach((value, key) => {
    if (String(value).trim()) values[key] = String(value).trim();
  });
  try {
    const saved = await api("/api/app/connectors/" + channel + "/credentials", {
      method: "PUT",
      body: JSON.stringify({ values }),
    });
    $("#connector-config-status").textContent = channel === "etsy" && !saved.operational
      ? "App details saved. Authorize with Etsy next."
      : "Connection settings saved.";
    $("#test-connector").classList.toggle(
      "hidden",
      !connectorSchemas[channel]?.test || !saved.operational,
    );
    flash(channel === "etsy" && !saved.operational
      ? "Etsy app details saved."
      : connectorSchemas[channel].title + " configured.");
    const data = await connections();
    if (state.connectorChannel === channel) {
      renderEtsyOAuthTools(data.connectors.find((row) => row.channel === channel));
    }
    if (state.crossList?.connectChannel === channel && state.crossList?.itemId) {
      const { itemId, sourceListingId } = state.crossList;
      state.crossList.connectChannel = null;
      await selectView("inventory");
      await openCrossList(itemId, sourceListingId || null);
    }
  } catch (error) {
    $("#connector-config-status").textContent = error.message;
  }
};

$("#authorize-etsy").onclick = async () => {
  $("#connector-config-status").textContent = "Opening Etsy authorization…";
  try {
    const result = await api("/api/app/connectors/etsy/oauth/start", { method: "POST" });
    window.location.assign(result.authorization_url);
  } catch (error) {
    $("#connector-config-status").textContent = error.message;
  }
};

$("#remove-connector").onclick = async () => {
  if (!state.connectorChannel) return;
  try {
    await api("/api/app/connectors/" + state.connectorChannel + "/credentials", { method: "DELETE" });
    flash("Connector credentials removed.");
    $("#connector-config").classList.add("hidden");
    state.connectorChannel = null;
    await connections();
  } catch (error) {
    flash(error.message, true);
  }
};

$("#test-connector").onclick = async () => {
  const channel = state.connectorChannel;
  if (!channel || !connectorSchemas[channel]?.test) return;
  $("#connector-config-status").textContent = "Testing connection…";
  try {
    const result = await api("/api/app/connectors/" + channel + "/test-connection", { method: "POST" });
    $("#connector-config-status").textContent = result.detail || "Connection succeeded.";
  } catch (error) {
    $("#connector-config-status").textContent = error.message;
  }
};

$("#test-biblio").onclick = async () => {
  $("#connector-config-status").textContent = "Testing FTP…";
  try {
    const result = await api("/api/app/connectors/biblio/test", { method: "POST" });
    $("#connector-config-status").textContent = result.detail || "BIBLIO FTP connection succeeded.";
  } catch (error) {
    $("#connector-config-status").textContent = error.message;
  }
};

$("#import-biblio").onclick = async () => {
  const file = $("#biblio-import-file").files[0];
  if (!file) return flash("Choose a BIBLIO inventory file first.", true);
  const form = new FormData();
  form.append("file", file);
  $("#connector-config-status").textContent = "Importing BIBLIO inventory…";
  try {
    const result = await api("/api/app/connectors/biblio/import", { method: "POST", body: form });
    $("#connector-config-status").textContent = "Imported " + result.items + " BIBLIO listings · " + result.active + " active.";
    flash("BIBLIO inventory imported.");
  } catch (error) {
    $("#connector-config-status").textContent = error.message;
  }
};

async function pair(button) {
  try {
    const result = await api("/api/app/extension/pairings", { method: "POST" });
    const connector = button?.closest(".connector");
    const panel = connector?.querySelector(".pairing-inline");
    const code = connector?.querySelector(".pair-code-inline");
    if (!panel || !code) return flash("Pair code could not be displayed.", true);
    code.textContent = result.code;
    panel.classList.remove("hidden");
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    flash(error.message, true);
  }
}

async function settings() {
  const data = await api("/api/app/settings");
  $("#settings-name").value = data.workspace.name;
  renderBillingLock(data.billing);
  $("#delete-workspace-slug").textContent = data.workspace.slug;
  $("#delete-workspace-confirm").value = "";
  $("#billing").innerHTML = data.billing.enabled
    ? '<p>Status: <strong>' + esc(data.billing.status) + '</strong></p><button id="billing-action" class="btn primary">'
      + (data.billing.customer_configured ? "Manage subscription" : "Choose plan") + "</button>"
    : '<p class="muted">Billing is disabled for this installation. This is the normal local/beta configuration.</p>'
      + (data.security.derived_encryption_key
        ? '<p class="muted">Configure APP_ENCRYPTION_KEY before hosting for real customers.</p>'
        : "");
  const button = $("#billing-action");
  if (button) {
    button.onclick = async () => {
      try {
        const result = await api(
          data.billing.customer_configured ? "/api/app/billing/portal" : "/api/app/billing/checkout",
          { method: "POST" },
        );
        location.href = result.url;
      } catch (error) {
        flash(error.message, true);
      }
    };
  }
}

$("#delete-workspace").onclick = async () => {
  const expected = state.me?.workspace?.slug || "";
  const confirm = $("#delete-workspace-confirm").value.trim();
  if (!expected || confirm !== expected) {
    return flash("Type the workspace slug exactly before deleting it.", true);
  }
  if (!window.confirm("Permanently delete this workspace and all of its marketplace data?")) return;
  try {
    await api("/api/app/account", {
      method: "DELETE",
      body: JSON.stringify({ confirm }),
    });
    state.me = null;
    authScreen("login");
    flash("");
  } catch (error) {
    flash(error.message, true);
  }
};

$("#settings-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const result = await api("/api/app/settings", {
      method: "PATCH",
      body: JSON.stringify({ name: $("#settings-name").value.trim() }),
    });
    state.me.workspace = result.workspace;
    $("#workspace-name").textContent = result.workspace.name;
    flash("Settings saved.");
  } catch (error) {
    flash(error.message, true);
  }
};

init();
