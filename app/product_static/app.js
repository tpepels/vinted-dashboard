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
  itemMarketplaceId: null,
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
  biblioActivity: null,
  biblioActivityExpanded: false,
  biblioPhotoExpanded: false,
  biblioRecoveryExpanded: false,
  biblioBookChoices: null,
  biblioCompareCheckedFile: null,
  biblioCompareResult: null,
  biblioPhotoTarget: "",
  biblioPhotoInspection: null,
  biblioPhotoError: "",
  biblioActivityTimer: null,
  biblioInventoryTimer: null,
  storeStockAuditTimer: null,
  biblioListingsTimer: null,
  crossList: null,
  connectors: [],
  marketplaceDevelopment: null,
  marketplaceSelected: "biblio",
  barcodeStream: null,
  barcodeTimer: null,
  barcodeDetector: null,
  barcodeBusy: false,
  barcodeMisses: 0,
  barcodeCameraLatch: null,
  barcodeCameraClearFrames: 0,
  browserLogs: [],
  diagnosticsTimer: null,
  diagnosticsDevConsole: false,
  diagnosticsRows: [],
  diagnosticsLevelFilter: new Set(["error"]),
};

const importFields = [
  "", "sku", "title", "category", "quantity", "condition", "cost", "price",
  "currency", "location", "notes", "barcode", "author", "isbn", "subtitle",
  "publisher", "edition", "binding", "language", "publish_date",
  "publication_year", "pages", "publication_place", "first_edition", "signed",
  "dust_jacket_present", "dust_jacket_condition", "dust_jacket_description",
  "illustrator", "keywords", "catalog_1", "catalog_2", "catalog_3", "catalog_4",
  "catalog_5", "catalog_6", "catalog_7", "catalog_8",
  "brand", "size", "colour", "material", "measurements",
];

const connectorSchemas = {
  biblio: {
    title: "BIBLIO",
    help: "Enter the FTP credentials provided for your BIBLIO seller account. These settings let the dashboard send book information and photos. Saving them does not upload any listings. Technical connection and photo-format details are available below.",
    fields: [
      ["username", "BIBLIO FTP username", "", "text"],
      ["password", "BIBLIO FTP password", "", "password"],
      ["filename_prefix", "File name prefix (optional)", "reseller-dashboard", "text"],
      ["allow_plain_ftp", "Allow unencrypted FTP only if BIBLIO does not support encrypted FTPS (less secure)", "", "checkbox"],
      ["auto_sync", "Automatically send changed BIBLIO listings after new Vinted updates", "", "checkbox"],
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

// Tables retain natural column sizes until a user resizes them. Resized widths
// are scoped to the view/table and ignored if the header structure changes.
const tableResizeSeen = new WeakSet();
const tableResizeWidths = new WeakMap();
const TABLE_MIN_COLUMN_WIDTH = 72;
const TABLE_MAX_COLUMN_WIDTH = 640;

function tableResizeKey(table) {
  const host = table.closest(".table-wrap");
  const scope = table.closest("[id]");
  if (!host || !scope) return null;
  const index = Array.from(scope.querySelectorAll("table")).indexOf(table);
  if (index < 0) return null;
  return "reseller:table-widths:v1:" + scope.id + ":" + index;
}

function tableStoredWidths(key, labels) {
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "null");
    return saved && Array.isArray(saved.widths)
      && Array.isArray(saved.labels) && saved.labels.join("|") === labels.join("|")
      && saved.widths.length === labels.length
      && saved.widths.every(value => Number.isInteger(value)
        && value >= TABLE_MIN_COLUMN_WIDTH && value <= TABLE_MAX_COLUMN_WIDTH)
      ? saved.widths : null;
  } catch (_) {
    return null;
  }
}

function applyTableColumnWidths(table, widths) {
  if (!widths?.length) return;
  let group = table.querySelector(":scope > colgroup.table-resize-columns");
  if (!group) {
    group = document.createElement("colgroup");
    group.className = "table-resize-columns";
    table.insertBefore(group, table.firstChild);
  }
  group.replaceChildren(...widths.map(width => {
    const col = document.createElement("col");
    col.style.width = width + "px";
    return col;
  }));
  table.style.tableLayout = "fixed";
  table.style.width = "max(100%, " + widths.reduce((sum, width) => sum + width, 0) + "px)";
  tableResizeWidths.set(table, widths.slice());
  Array.from(table.tHead?.rows[0]?.cells || []).forEach((cell, i) => {
    const handle = cell.querySelector(".table-resize-handle");
    if (handle) handle.setAttribute("aria-valuenow", String(widths[i]));
  });
}

function resetTableColumnWidths(table, key) {
  table.querySelector(":scope > colgroup.table-resize-columns")?.remove();
  table.style.removeProperty("table-layout");
  table.style.removeProperty("width");
  tableResizeWidths.delete(table);
  try { localStorage.removeItem(key); } catch (_) { /* storage disabled */ }
  Array.from(table.tHead?.rows[0]?.cells || []).forEach(cell => {
    cell.querySelector(".table-resize-handle")?.removeAttribute("aria-valuenow");
  });
}

function activateResizableTable(table) {
  if (tableResizeSeen.has(table)) return;
  const row = table.tHead?.rows?.[0];
  const key = tableResizeKey(table);
  if (!row || !key || row.cells.length < 3 || row.cells.length > 25
      || Array.from(row.cells).some(cell => cell.colSpan !== 1)
      || table.classList.contains("stock-scan-table")) return;
  tableResizeSeen.add(table);
  const headers = Array.from(row.cells);
  const labels = headers.map(cell => cell.textContent.replace(/\s+/g, " ").trim());
  table.classList.add("table-resizable");
  const container = table.closest(".table-wrap");
  if (container && !container.hasAttribute("tabindex")) {
    container.tabIndex = 0;
    container.setAttribute("aria-label", "Scrollable data table");
  }
  const saved = tableStoredWidths(key, labels);
  if (saved) applyTableColumnWidths(table, saved);

  headers.forEach((header, column) => {
    if (labels[column] && !header.querySelector("a,button,input,select")) {
      header.classList.add("table-sortable");
      header.tabIndex = 0;
      header.setAttribute("aria-keyshortcuts", "Enter Space");
    }
    const handle = document.createElement("button");
    handle.type = "button";
    handle.className = "table-resize-handle";
    handle.tabIndex = 0;
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-label", "Resize " + (labels[column] || "column " + (column + 1)));
    handle.setAttribute("aria-valuemin", String(TABLE_MIN_COLUMN_WIDTH));
    handle.setAttribute("aria-valuemax", String(TABLE_MAX_COLUMN_WIDTH));
    if (saved) handle.setAttribute("aria-valuenow", String(saved[column]));
    handle.title = "Drag or use arrow keys to resize; double-click to reset";
    header.appendChild(handle);
    let drag = null;

    function currentWidths() {
      return tableResizeWidths.get(table)?.slice()
        || headers.map(cell => Math.min(TABLE_MAX_COLUMN_WIDTH,
          Math.max(TABLE_MIN_COLUMN_WIDTH, Math.round(cell.getBoundingClientRect().width))));
    }
    function changeWidth(widths, width) {
      widths[column] = Math.max(TABLE_MIN_COLUMN_WIDTH,
        Math.min(TABLE_MAX_COLUMN_WIDTH, Math.round(width)));
      applyTableColumnWidths(table, widths);
    }
    function saveWidths() {
      try {
        const widths = tableResizeWidths.get(table);
        if (widths) localStorage.setItem(key, JSON.stringify({labels, widths}));
      } catch (_) { /* storage disabled */ }
    }
    handle.addEventListener("pointerdown", event => {
      if (event.button !== 0 || window.matchMedia("(max-width: 900px), (pointer: coarse)").matches) return;
      event.stopPropagation();
      event.preventDefault();
      const widths = currentWidths();
      drag = {startX: event.clientX, startWidth: widths[column], widths,
        pointerId: event.pointerId};
      handle.setPointerCapture(event.pointerId);
      document.body.classList.add("is-resizing-columns");
    });
    handle.addEventListener("pointermove", event => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      changeWidth(drag.widths, drag.startWidth + event.clientX - drag.startX);
    });
    function finish(event) {
      if (!drag || event.pointerId !== drag.pointerId) return;
      drag = null;
      document.body.classList.remove("is-resizing-columns");
      saveWidths();
    }
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
    handle.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      event.stopPropagation();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const widths = currentWidths();
      changeWidth(widths, widths[column] + direction * (event.shiftKey ? 40 : 12));
      saveWidths();
    });
    handle.addEventListener("dblclick", event => {
      event.preventDefault();
      event.stopPropagation();
      resetTableColumnWidths(table, key);
    });
  });
}

function startResponsiveTables() {
  const root = document.querySelector("main");
  if (!root) return;
  const scan = () => root.querySelectorAll(".table-wrap table")
    .forEach(activateResizableTable);
  scan();
  const observer = new MutationObserver(records => {
    if (records.some(record => record.addedNodes.length)) scan();
  });
  observer.observe(root, {childList: true, subtree: true});
}

document.addEventListener("keydown", event => {
  if (event.target.tagName !== "TH" || !event.target.classList.contains("table-sortable")
      || !["Enter", " "].includes(event.key)) return;
  event.preventDefault();
  sortTableByHeader(event.target);
});
startResponsiveTables();

function redactClientText(value) {
  return String(value ?? "")
    .replace(/(authorization:\s*(?:bearer|basic)\s+)\S+/gi, "$1[REDACTED]")
    .replace(/(bearer\s+)[A-Za-z0-9._~+/=-]+/gi, "$1[REDACTED]")
    .replace(/\b(password|passwd|secret|token|api[_-]?key|client[_-]?secret|refresh[_-]?token|access[_-]?token)(\s*[=:]\s*)([^\s,;]+)/gi, "$1$2[REDACTED]");
}

function diagnosticLog(level, event, detail = "") {
  const row = {
    at: new Date().toISOString(),
    level: String(level || "info").toLowerCase(),
    event: redactClientText(event),
    detail: redactClientText(detail),
  };
  state.browserLogs.push(row);
  if (state.browserLogs.length > 1000) state.browserLogs.splice(0, state.browserLogs.length - 1000);
}

window.addEventListener("error", (event) => {
  diagnosticLog("error", "browser.error", event.message || "Unhandled browser error");
});
window.addEventListener("unhandledrejection", (event) => {
  diagnosticLog("error", "browser.unhandledrejection", event.reason?.message || event.reason || "Unhandled promise rejection");
});

const originalConsoleError = console.error.bind(console);
const originalConsoleWarn = console.warn.bind(console);
console.error = (...args) => {
  diagnosticLog("error", "console.error", args.map(redactClientText).join(" "));
  originalConsoleError(...args);
};
console.warn = (...args) => {
  diagnosticLog("warn", "console.warn", args.map(redactClientText).join(" "));
  originalConsoleWarn(...args);
};

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
  const method = String(options.method || "GET").toUpperCase();
  const path = (() => {
    try { return new URL(url, window.location.origin).pathname; } catch { return String(url); }
  })();
  if (options.method && !["GET", "HEAD"].includes(options.method)) {
    headers["X-CSRF-Token"] = csrf();
  }
  if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const started = performance.now();
  if (!path.startsWith("/api/app/diagnostics/logs")) diagnosticLog("info", "api.request", method + " " + path);
  let response;
  try {
    response = await fetch(url, Object.assign(
      { credentials: "same-origin" },
      options,
      { headers },
    ));
  } catch (error) {
    diagnosticLog("error", "api.network_error", method + " " + path + " · " + (error?.message || error));
    throw error;
  }
  const type = response.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await response.json() : await response.text();
  const duration = Math.round(performance.now() - started);
  if (!path.startsWith("/api/app/diagnostics/logs")) {
    diagnosticLog(response.ok ? "info" : "error", "api.response", method + " " + path + " · HTTP " + response.status + " · " + duration + "ms");
  }
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
  if (state.diagnosticsTimer) {
    clearInterval(state.diagnosticsTimer);
    state.diagnosticsTimer = null;
  }
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
  if (view !== "inventory" && state.storeStockAuditTimer) {
    clearTimeout(state.storeStockAuditTimer);
    state.storeStockAuditTimer = null;
  }
  if (view !== "inventory" && state.barcodeStream) stopBarcodeCamera();
  if (view !== "settings" && state.diagnosticsTimer) {
    clearInterval(state.diagnosticsTimer);
    state.diagnosticsTimer = null;
  }
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
  if (row.needs_reopen) {
    // Stock was restored after the listing was closed. Never claim that
    // reopening happened automatically: it requires an explicit remote check.
    return open + '<span class="error">Stock available: reopen this listing on the marketplace and verify its status.</span>';
  }
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

function itemMarketplaceStatusLabel(listing) {
  if (listing.channel === "biblio") {
    return {
      matches: "Matches last BIBLIO inventory download",
      differs: "Details differ from the BIBLIO download",
      stale: "Changed since last BIBLIO check",
      compared: "Found in BIBLIO; details not compared",
      not_verified: "Not yet verified on BIBLIO",
    }[listing.verification] || "Not independently checked";
  }
  if (["woocommerce", "shopify", "wix"].includes(listing.channel)) {
    return {
      stock_checked: "Stock matched at the last check",
      stock_mismatch: "Stock differs - review before updating",
      stock_stale: "Dashboard stock changed - check again",
      not_checked: "Stock not yet checked",
    }[listing.verification] || "Marketplace stock has not been checked";
  }
  return "Imported or linked; current remote publication not independently verified";
}

function itemMarketplaceOperationText(op) {
  if (!op) return "No tracked operation yet";
  const status = {
    queued:"Waiting", running:"In progress",
    succeeded:"Completed locally", needs_verification:"Sent; remote check needed",
    failed:"Failed", attention:"Needs your attention", cancelled:"Cancelled",
  };
  return (op.verification === "remote_verified" && op.status === "succeeded"
    ? "Verified on marketplace" : (status[op.status] || op.status))
    + (op.completed_at ? " · " + when(op.completed_at) : "");
}

function renderItemMarketplacePanel(data) {
  const item = data.item || {};
  const listings = data.listings || [];
  const operations = data.operations || [];
  const closing = data.closure_actions || [];
  $("#item-marketplaces-title").textContent = "Where is " + (item.title || "this item") + " listed?";
  const rows = listings.map(listing => {
    const errors = [
      listing.photo_error,
      listing.last_operation?.error,
    ].filter(Boolean);
    const caution = listing.attention || ["differs", "stale", "stock_mismatch", "stock_stale"].includes(listing.verification);
    const verified = !caution && ["matches", "stock_checked"].includes(listing.verification);
    return '<div class="item-marketplace-row">'
      + '<div class="item-marketplace-main">'
      + '<div class="item-marketplace-head"><strong>' + esc(listing.channel.toUpperCase())
      + '</strong><span class="item-marketplace-state ' + (caution ? 'attention' : verified ? 'verified' : '') + '">'
      + esc(listing.status) + (caution ? " · review needed" : "") + '</span></div>'
      + '<p>' + esc(itemMarketplaceStatusLabel(listing)) + '</p>'
      + '<p class="muted">Marketplace reference: ' + esc(listing.external_id)
      + ' · Advertised quantity: ' + Number(listing.quantity ?? 0)
      + (listing.verified_at ? ' · Last checked: ' + esc(when(listing.verified_at)) : '')
      + '</p>'
      + '<p class="muted">Latest action: ' + esc(itemMarketplaceOperationText(listing.last_operation)) + '</p>'
      + (listing.channel === "biblio" ? '<p class="muted">Dashboard photos: '
        + Number(listing.photo_count || 0) + ' · Transfer state: '
        + esc(listing.photo_state || "not recorded") + '</p>' : '')
      + errors.map(error => '<p class="error">' + esc(error) + '</p>').join("")
      + '</div><div class="item-marketplace-actions">'
      + (listing.url ? '<a class="btn" href="' + esc(listing.url)
        + '" target="_blank" rel="noreferrer">Open marketplace listing</a>' : "")
      + (listing.can_inspect_photos ? '<button class="btn item-marketplace-photos" type="button" data-book="'
        + esc(listing.external_id) + '">Check / repair photos</button>' : "")
      + (listing.channel === "biblio" ? '<button class="btn item-marketplace-verify" type="button">Compare BIBLIO inventory</button>' : "")
      + ((listing.can_sync_woocommerce_stock || listing.can_sync_shopify_stock || listing.can_sync_wix_stock)
        ? '<button class="btn item-stock-check" data-channel="' + esc(listing.channel)
          + '" type="button">'
          + (listing.verification === "stock_checked" ? "Check again" : "Check stock")
          + '</button>'
          + (listing.verification === "stock_mismatch"
            ? '<button class="btn primary item-stock-update" data-channel="' + esc(listing.channel)
              + '" type="button">Set stock to ' + Number(item.quantity || 0) + '…</button>'
            : '')
        : '')

      + (listing.can_sync_woocommerce_price || listing.can_sync_shopify_price
        ? '<details class="item-price-tools"'
          + (listing.price_verification === "price_mismatch" ? ' open' : '')
          + '><summary>' + (listing.channel === "shopify" ? 'Base price' : 'Regular price') + ' · '
          + esc(money(item.default_price_cents, item.currency)) + '</summary>'
          + '<p class="muted">Compare the store price first. Promotions and regional prices are not changed.</p>'
          + (listing.price_verification !== "not_checked"
            ? '<p class="item-price-state">'
              + (listing.price_verification === "price_checked" ? 'Price matches'
                : listing.price_verification === "price_mismatch" ? 'Price differs'
                : 'Previous check is outdated')
              + (Number.isInteger(listing.remote_price_cents)
                ? ' · Store ' + esc(money(listing.remote_price_cents, item.currency)) : '')
              + '</p>' : '')
          + '<div class="actions"><button class="btn item-price-check" type="button" data-channel="' + esc(listing.channel) + '">'
          + (listing.price_verification === "not_checked" ? 'Check price' : 'Check again')
          + '</button>'
          + (listing.price_verification === "price_mismatch"
              && listing.price_verified_at
              && Number.isFinite(Date.parse(listing.price_verified_at))
              && Date.now() - Date.parse(listing.price_verified_at) < 300000
              && Date.now() >= Date.parse(listing.price_verified_at)
            ? '<button class="btn primary item-price-update" type="button" data-channel="' + esc(listing.channel) + '">Set price to '
              + esc(money(item.default_price_cents, item.currency)) + '…</button>'
            : '')
          + '</div></details>' : '')
      + '</div></div>';
  });
  const operationsMarkup = operations.map(op => {
    const title = {
      publish:"Publish",update:op.target?.endsWith(":price") ? "Price update" : "Stock update",photos:"Send photos",
      close:"Close after sale",sync:"Synchronize",verify:"Verify",
    }[op.type] || op.type;
    return '<div class="item-marketplace-history-row"><div><strong>' + esc(op.channel.toUpperCase())
      + ' · ' + esc(title) + '</strong><span>' + esc(itemMarketplaceOperationText(op))
      + (op.error ? ' · ' + esc(op.error) : '') + '</span></div>'
      + (op.can_retry
        ? '<button class="btn item-marketplace-retry" data-id="' + esc(op.id)
          + '" type="button">Retry this safe operation…</button>'
        : '') + '</div>';
  }).join("");
  $("#item-marketplaces-content").innerHTML =
    '<div class="item-marketplace-summary"><strong>' + Number(item.quantity ?? 0)
    + '</strong> physical units in the dashboard · ' + listings.length + ' marketplace links'
    + '<div class="actions"><button class="btn primary item-marketplace-publish" type="button">Publish to another marketplace</button>'
    + '<button class="btn item-marketplace-edit" type="button">Edit this item</button>'
    + '</div></div>'
    + (rows.length ? '<div class="item-marketplace-list">' + rows.join("") + '</div>'
      : '<p class="muted">No marketplace listings are linked to this item yet. Use Publish to review available channels.</p>')
    + (closing.length ? '<div class="item-marketplace-closure"><strong>Sold-out follow-up</strong>'
      + closing.map(action => '<p>' + esc(action.channel.toUpperCase())
        + ': ' + esc(action.status) + ' (' + esc(action.type) + ')</p>').join("") + '</div>' : '')
    + '<details class="item-marketplace-history"><summary>Recent operations (' + operations.length + ')</summary>'
    + '<p>Transfers may need marketplace confirmation.</p>'
    + (operationsMarkup || '<p>No operations recorded for this item yet.</p>') + '</details>'
    + '<p class="item-marketplace-footnote">Stock and price changes are separate, explicit actions. '
    + 'WooCommerce regular prices and Shopify base prices change only when explicitly confirmed. Send BIBLIO changes from Connections.</p>';
  const itemId = item.id;
  $(".item-marketplace-publish").onclick = () => openCrossList(itemId);
  $(".item-marketplace-edit").onclick = () => {
    const original = state.inventoryItems.find(row => row.id === itemId);
    if (original) openItemForm(original);
  };
  $$(".item-marketplace-photos").forEach(button => {
    button.onclick = async () => {
      state.biblioPhotoTarget = button.dataset.book;
      state.biblioPhotoExpanded = true;
      await selectView("connections");
      const target = $(".biblio-photos-panel");
      if (target) {
        target.open = true;
        target.scrollIntoView({behavior:"smooth",block:"center"});
        const field = $("#biblio-photo-book-id");
        if (field) field.value = button.dataset.book;
        await inspectBiblioPhotoTarget();
      }
    };
  });
  $$(".item-marketplace-verify").forEach(button => {
    button.onclick = async () => {
      await selectView("connections");
      const target = $("#biblio-compare-panel");
      if (target) {
        target.open = true;
        target.scrollIntoView({behavior:"smooth",block:"center"});
      }
    };
  });
  // One workflow for every supported store. Read-only check comes first.
  $$(".item-stock-check").forEach(button => {
    button.onclick = async () => {
      const channel = button.dataset.channel;
      const marketplace = {woocommerce:"WooCommerce", shopify:"Shopify", wix:"Wix"}[channel];
      if (!marketplace) return;
      button.disabled = true;
      try {
        const result = await api(
          "/api/app/inventory/" + encodeURIComponent(itemId)
            + "/marketplaces/" + channel + "/check-stock",
          {method:"POST"},
        );
        flash(result.matches
          ? marketplace + ": stock matches the dashboard (" + result.local_quantity + ")."
          : marketplace + ": " + result.remote_quantity + " listed, "
            + result.local_quantity + " in the dashboard. Check the difference before updating.");
        await openItemMarketplaces(itemId);
      } catch (error) {
        flash("Could not check " + marketplace + " stock: " + error.message, true);
        button.disabled = false;
      }
    };
  });
  $$(".item-stock-update").forEach(button => {
    button.onclick = async () => {
      const channel = button.dataset.channel;
      const marketplace = {woocommerce:"WooCommerce", shopify:"Shopify", wix:"Wix"}[channel];
      if (!marketplace) return;
      const desired = Number(item.quantity || 0);
      if (!window.confirm(
        "Set " + marketplace + " stock to " + desired + " unit(s)? "
        + "This changes the marketplace only. The dashboard checks the remote identity "
        + "and current stock first, then checks the result. If anything differs, "
        + "the update stops for manual review."
      )) return;
      button.disabled = true;
      try {
        const result = await api(
          "/api/app/inventory/" + encodeURIComponent(itemId)
            + "/marketplaces/" + channel + "/stock",
          {method:"POST"},
        );
        flash(result.remote_verified
          ? marketplace + ": stock verified (" + result.quantity + ")."
          : marketplace + ": remote result needs checking.");
        await openItemMarketplaces(itemId);
      } catch (error) {
        flash(marketplace + " stock is not verified. Check it before another update. "
          + error.message, true);
        button.disabled = false;
      }
    };
  });

  $$(".item-price-check").forEach(button => {
    button.onclick = async () => {
      const channel = button.dataset.channel;
      const store = {woocommerce:"WooCommerce regular price",shopify:"Shopify base price"}[channel];
      if (!store) return;
      button.disabled = true;
      try {
        const result = await api("/api/app/inventory/" + encodeURIComponent(itemId)
          + "/marketplaces/" + channel + "/check-price", {method:"POST"});
        flash(result.matches ? store + " matches the dashboard."
          : store + " differs from the default asking price.");
        await openItemMarketplaces(itemId);
      } catch (error) {
        flash(store + " check: " + error.message, true);
        button.disabled = false;
      }
    };
  });
  $$(".item-price-update").forEach(button => {
    button.onclick = async () => {
      const channel = button.dataset.channel;
      const store = {woocommerce:"WooCommerce regular price",shopify:"Shopify base price"}[channel];
      if (!store) return;
      const price = money(item.default_price_cents, item.currency);
      if (!window.confirm("Set the " + store + " to " + price + "? "
        + "Only this linked product or variant price is changed. "
        + "Stock, promotions and regional prices will not be modified.")) return;
      button.disabled = true;
      try {
        const result = await api("/api/app/inventory/" + encodeURIComponent(itemId)
          + "/marketplaces/" + channel + "/price", {method:"POST"});
        flash(result.remote_verified ? store + " verified at " + price + "."
          : "Price update needs remote verification.");
        await openItemMarketplaces(itemId);
      } catch (error) {
        flash(store + " not confirmed. Check the store before retrying. "
          + error.message, true);
        button.disabled = false;
      }
    };
  });
  $(".item-marketplace-retry").forEach(button => {
    button.onclick = async () => {
      if (!window.confirm("Retry this supported operation? Check any uncertain remote result before resending.")) return;
      button.disabled = true;
      try {
        await api("/api/app/marketplace-operations/" + encodeURIComponent(button.dataset.id) + "/retry",
          {method:"POST"});
        flash("Safe operation queued. It will appear in this item's history.");
        await openItemMarketplaces(itemId);
      } catch (error) {
        flash(error.message, true);
        button.disabled = false;
      }
    };
  });
}

async function openItemMarketplaces(itemId) {
  state.itemMarketplaceId = itemId;
  const panel = $("#item-marketplaces-panel");
  panel.classList.remove("hidden");
  $("#item-marketplaces-content").textContent = "Loading linked listings and operation history…";
  panel.scrollIntoView({behavior:"smooth",block:"start"});
  try {
    const data = await api("/api/app/inventory/" + encodeURIComponent(itemId) + "/marketplace-status");
    if (state.itemMarketplaceId === itemId) renderItemMarketplacePanel(data);
  } catch (error) {
    $("#item-marketplaces-content").textContent = "Could not load marketplace status: " + error.message;
  }
}

$("#close-item-marketplaces").onclick = () => {
  state.itemMarketplaceId = null;
  $("#item-marketplaces-panel").classList.add("hidden");
};

function inventoryCrossListAction(item) {
  if (!item?.id) return "";
  return '<button class="btn item-marketplaces" data-item-id="' + esc(item.id)
    + '" type="button">Marketplaces</button>'
    + '<button class="btn cross-list" data-item-id="' + esc(item.id)
    + '" type="button">Publish</button>';
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
    return '<button class="btn primary cross-destination-biblio-classify">Mark as book & review</button>';
  }
  if (destination.action === "connect") {
    return '<button class="btn cross-destination-connect" data-channel="' + esc(destination.channel) + '">Connect</button>';
  }
  if (destination.action === "edit" || destination.action === "review") {
    return '<button class="btn cross-destination-review" data-channel="' + esc(destination.channel) + '">Review fields</button>';
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
  const prefilled = [
    ["ISBN", fields.isbn],
    ["Barcode", fields.isbn ? null : fields.barcode],
    ["Author", fields.author],
    ["Publisher", fields.publisher],
    ["Edition", fields.edition],
    ["Published", fields.publish_date || fields.publication_year],
    ["Language", fields.language],
    ["Binding", fields.binding],
    ["Pages", fields.pages],
    ["Condition", fields.condition],
    ["Brand", fields.brand],
    ["Size", fields.size],
    ["Colour", fields.colour],
    ["Material", fields.material],
  ].filter(([, value]) => value != null && String(value).trim() !== "");
  $("#cross-list-summary").innerHTML = [
    "<span><strong>Title:</strong> " + esc(fields.title || "Missing") + "</span>",
    "<span><strong>Price:</strong> " + esc(fields.price_cents == null ? "Missing" : money(fields.price_cents, fields.currency)) + "</span>",
    "<span><strong>Stock:</strong> " + esc(fields.quantity == null ? "—" : fields.quantity) + "</span>",
    "<span><strong>Photos:</strong> " + esc(source.photo_count || 0) + "</span>",
    ...(prefilled.length
      ? ['<span class="cross-list-prefilled"><strong>Prefilled:</strong> '
        + prefilled.map(([label, value]) => esc(label) + " " + esc(value)).join(" · ")
        + "</span>"]
      : []),
    ...(data.enrichment_warning
      ? ['<span class="cross-list-warning"><strong>ISBN lookup:</strong> ' + esc(data.enrichment_warning) + "</span>"]
      : []),
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
        button.textContent = "Mark as book & review";
      }
    };
  });
  document.querySelectorAll(".cross-destination-review").forEach((button) => {
    button.onclick = () => openCrossListReview(button.dataset.channel);
  });
}

async function openCrossList(itemId, sourceListingId = null) {
  if (!itemId) return flash("Link this listing to a physical inventory item first.", true);
  if (state.view !== "inventory") await selectView("inventory");
  state.crossList = { itemId, sourceListingId, data: null, reviewChannel: null };
  $("#cross-list-panel").classList.remove("hidden");
  $("#cross-list-review").classList.add("hidden");
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

async function publishCrossDestination(channel, button, overrides = null) {
  const current = state.crossList;
  if (!current?.itemId || !channel) return;
  button.disabled = true;
  const old = button.textContent;
  button.textContent = "Publishing…";
  try {
    const payload = {
      source_listing_id: current.sourceListingId || null,
      ...(overrides || {}),
    };
    const result = await api(
      "/api/app/inventory/" + encodeURIComponent(current.itemId) + "/cross-list/" + encodeURIComponent(channel),
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    );
    flash("Published to " + (connectorSchemas[channel]?.title || channel) + ".");
    $("#cross-list-review").classList.add("hidden");
    await inventory();
    await openCrossList(current.itemId, current.sourceListingId);
  } catch (error) {
    flash(error.message, true);
    button.disabled = false;
    button.textContent = old;
  }
}

function openCrossListReview(channel) {
  const current = state.crossList;
  const data = current?.data;
  const fields = data?.fields || {};
  if (!current?.itemId || !channel) return;
  current.reviewChannel = channel;
  $("#cross-list-review-title").textContent = "Review for " + (connectorSchemas[channel]?.title || channel);
  $("#cross-list-review-name").value = fields.title || "";
  $("#cross-list-review-description").value = fields.description || "";
  $("#cross-list-review-price").value = fields.price_cents == null ? "" : (Number(fields.price_cents) / 100).toFixed(2);
  $("#cross-list-review-stock").value = fields.quantity == null ? "0" : String(fields.quantity);
  const destination = (data.destinations || []).find((row) => row.channel === channel);
  $("#cross-list-review-warning").textContent = destination?.reason || "";
  $("#cross-list-review-publish").textContent = "Publish to " + (connectorSchemas[channel]?.title || channel);
  $("#cross-list-review").classList.remove("hidden");
  $("#cross-list-review").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function publishReviewedCrossList() {
  const current = state.crossList;
  const channel = current?.reviewChannel;
  if (!current?.itemId || !channel) return;
  const title = $("#cross-list-review-name").value.trim();
  const description = $("#cross-list-review-description").value.trim();
  const priceValue = Number($("#cross-list-review-price").value);
  const quantityValue = Number($("#cross-list-review-stock").value);
  if (!title) return flash("Title is required.", true);
  if (!Number.isFinite(priceValue) || priceValue <= 0) return flash("Price must be greater than zero.", true);
  if (!Number.isInteger(quantityValue) || quantityValue < 0) return flash("Stock must be a non-negative whole number.", true);

  const originalQuantity = Number(current.data?.fields?.quantity ?? 0);
  const button = $("#cross-list-review-publish");
  if (quantityValue !== originalQuantity) {
    try {
      button.disabled = true;
      button.textContent = "Updating stock…";
      await api("/api/app/inventory/" + encodeURIComponent(current.itemId), {
        method: "PATCH",
        body: JSON.stringify({ quantity: quantityValue }),
      });
      current.data.fields.quantity = quantityValue;
      button.disabled = false;
      button.textContent = "Publish to " + (connectorSchemas[channel]?.title || channel);
    } catch (error) {
      button.disabled = false;
      button.textContent = "Publish to " + (connectorSchemas[channel]?.title || channel);
      return flash(error.message, true);
    }
  }
  await publishCrossDestination(channel, button, {
    title,
    description,
    price_cents: Math.round(priceValue * 100),
  });
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

$("#cross-list-review-close").onclick = () => {
  if (state.crossList) state.crossList.reviewChannel = null;
  $("#cross-list-review").classList.add("hidden");
};
$("#cross-list-review-publish").onclick = publishReviewedCrossList;

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
  const labels = {
    vinted: "Vinted",
    isbn: "ISBN lookup",
    master: "Master",
    vinted_barcode: "Vinted barcode",
    master_barcode: "Master barcode",
    review: "Reviewed",
    biblio: "BIBLIO",
    default: "Default",
  };
  return '<span class="biblio-source">' + esc(labels[source] || source) + "</span>";
}

function renderBiblioPublish(data) {
  state.biblioPublish.data = data;
  $("#biblio-publish-title").textContent = (data.action === "update" ? "Update " : "Publish ") + (data.item_title || "book");
  const source = data.source || {};
  $("#biblio-publish-source").innerHTML = source.channel === "vinted"
    ? 'Using the linked <strong>Vinted listing</strong> as the source'
      + (source.url ? ' · <a href="' + esc(source.url) + '" target="_blank" rel="noreferrer">open Vinted</a>' : "")
    : "No linked Vinted source was found; using the master inventory record.";

  const photoUrls = Array.isArray(source.image_urls) ? source.image_urls.filter(Boolean).slice(0, 12) : [];
  const photoPreview = $("#biblio-photo-preview");
  if (photoUrls.length) {
    photoPreview.classList.remove("hidden");
    photoPreview.innerHTML = '<div class="biblio-photo-copy"><strong>'
      + photoUrls.length + ' Vinted photo' + (photoUrls.length === 1 ? "" : "s")
      + ' will be uploaded automatically to BIBLIO.</strong>'
      + '<span>' + (photoUrls.length > 1
        ? 'Multiple photos require BIBLIO to map the BookID.jpg, BookID_1.jpg, BookID_2.jpg… filename convention for your seller account.'
        : 'No manual image upload is required.') + '</span></div>'
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
  const bibliographicSources = data.bibliographic_sources || {};
  const bookIdValue = fields.book_id || data.book_id_suggestion || "";
  const bookIdEditable = !data.book_id_locked;
  const rows = [
    ["title", "Title", fields.title || "", sources.title, true, true],
    ["author", "Author", fields.author || "", sources.author, true, true],
    ["description", "Description", fields.description || "", sources.description, true, true],
    ["isbn", "ISBN", fields.isbn || "", sources.isbn, true, false],
    ["subtitle", "Subtitle", enrichment.subtitle || "", bibliographicSources.subtitle || null, true, false],
    ["publisher", "Publisher", enrichment.publisher || "", bibliographicSources.publisher || null, true, false],
    ["edition", "Edition", enrichment.edition || "", bibliographicSources.edition || null, true, false],
    ["binding", "Binding", enrichment.binding || "", bibliographicSources.binding || null, true, false],
    ["language", "Language", enrichment.language || "", bibliographicSources.language || null, true, false],
    ["publish_date", "Publish date", enrichment.publish_date || "", bibliographicSources.publish_date || null, true, false],
    ["pages", "Pages", enrichment.pages || "", bibliographicSources.pages || null, true, false],
    ["condition", "Condition", enrichment.condition || "", bibliographicSources.condition || null, true, false],
    ["publication_place", "Publication place", enrichment.publication_place || "", bibliographicSources.publication_place || null, true, false],
    ["first_edition", "First edition", enrichment.first_edition ?? "", bibliographicSources.first_edition || null, true, false],
    ["signed", "Signed", enrichment.signed ?? "", bibliographicSources.signed || null, true, false],
    ["dust_jacket_present", "Dust jacket present", enrichment.dust_jacket_present ?? "", bibliographicSources.dust_jacket_present || null, true, false],
    ["dust_jacket_condition", "Dust jacket condition", enrichment.dust_jacket_condition || "", bibliographicSources.dust_jacket_condition || null, true, false],
    ["dust_jacket_description", "Dust jacket description", enrichment.dust_jacket_description || "", bibliographicSources.dust_jacket_description || null, true, false],
    ["illustrator", "Illustrator", enrichment.illustrator || "", bibliographicSources.illustrator || null, true, false],
    ["keywords", "Keywords", enrichment.keywords || "", bibliographicSources.keywords || null, true, false],
    ["catalog_1", "Catalog 1", enrichment.catalog_1 || "", bibliographicSources.catalog_1 || null, true, false],
    ["catalog_2", "Catalog 2", enrichment.catalog_2 || "", bibliographicSources.catalog_2 || null, true, false],
    ["catalog_3", "Catalog 3", enrichment.catalog_3 || "", bibliographicSources.catalog_3 || null, true, false],
    ["catalog_4", "Catalog 4", enrichment.catalog_4 || "", bibliographicSources.catalog_4 || null, true, false],
    ["catalog_5", "Catalog 5", enrichment.catalog_5 || "", bibliographicSources.catalog_5 || null, true, false],
    ["catalog_6", "Catalog 6", enrichment.catalog_6 || "", bibliographicSources.catalog_6 || null, true, false],
    ["catalog_7", "Catalog 7", enrichment.catalog_7 || "", bibliographicSources.catalog_7 || null, true, false],
    ["catalog_8", "Catalog 8", enrichment.catalog_8 || "", bibliographicSources.catalog_8 || null, true, false],
    ["price_cents", "Price", fields.price_cents == null ? "" : (Number(fields.price_cents) / 100).toFixed(2), sources.price_cents, true, true],
    ["book_id", data.book_id_locked ? "Book ID (locked after first upload)" : "Book ID", bookIdValue, sources.book_id, bookIdEditable, true],
    ["quantity", "Quantity", fields.quantity, sources.quantity, false, true],
    ["photos", "Photos", source.photo_count ? source.photo_count + " Vinted photo" + (source.photo_count === 1 ? "" : "s") + " - automatic BIBLIO upload" : "No Vinted photos available", source.photo_count ? "vinted" : null, false, false],
  ];
  $("#biblio-publish-fields").innerHTML = rows.map(([key, label, value, sourceName, editable, required]) => {
    const missing = required && (value == null || String(value).trim() === "");
    let valueHtml;
    if (editable) {
      const booleanField = ["first_edition", "signed", "dust_jacket_present"].includes(key);
      if (booleanField) {
        const selected = value === true ? "true" : (value === false ? "false" : "");
        valueHtml = '<select class="biblio-review-input" data-field="' + esc(key)
          + '" data-type="boolean" data-required="' + (required ? "true" : "false") + '">'
          + '<option value=""' + (selected === "" ? " selected" : "") + '>Not set</option>'
          + '<option value="true"' + (selected === "true" ? " selected" : "") + '>Yes</option>'
          + '<option value="false"' + (selected === "false" ? " selected" : "") + '>No</option>'
          + '</select>';
      } else if (key === "description" || key === "dust_jacket_description") {
        valueHtml = '<textarea class="biblio-review-input" data-field="' + esc(key) + '" data-required="' + (required ? "true" : "false")
          + '" rows="4" placeholder="' + esc(key === "description" ? "Description required by BIBLIO" : "Dust jacket description (optional)") + '">'
          + esc(value || "") + '</textarea>';
      } else {
        const type = key === "price_cents" || key === "pages" ? "number" : "text";
        const extra = key === "price_cents"
          ? ' min="0" step="0.01" inputmode="decimal"'
          : (key === "pages" ? ' min="0" step="1" inputmode="numeric"' : "");
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
  else if (data.photo_warning) warning = "Photo warning: " + data.photo_warning;
  else warning = data.already_listed
    ? "Ready to update using the extended BIBLIO format, including the optional bibliographic fields shown above."
      + (data.book_id_locked ? " Book ID is locked because changing it after upload could leave a duplicate remote listing." : "")
    : "Ready. The extended BIBLIO format will send the optional bibliographic fields shown above.";
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

function biblioListingState(sync) {
  if (!sync) return null;
  const stateValue = String(sync.state || "").toLowerCase();
  if (stateValue === "queued") return { label: "queued", cls: "queued", detail: "Waiting for worker" };
  if (stateValue === "uploading") {
    const photo = sync.photo_state === "uploading" ? " · photos uploading" : "";
    return { label: "uploading" + photo, cls: "running", detail: "Sending to BIBLIO FTP" };
  }
  if (stateValue === "error") return { label: "error", cls: "error", detail: sync.error || "FTP publication failed" };
  if (stateValue === "ftp_uploaded") {
    if (sync.photo_state === "error") {
      return { label: "FTP uploaded · photo error", cls: "warn", detail: sync.photo_error || "Inventory reached FTP; one or more photos failed" };
    }
    if (sync.photo_state === "queued" || sync.photo_state === "uploading") {
      return { label: "inventory uploaded · photos pending", cls: "running", detail: "Inventory reached FTP; photos are still being sent" };
    }
    if (sync.photo_state === "retry_scheduled") {
      return {
        label: "FTP uploaded · photo retry scheduled",
        cls: "warn",
        detail: sync.photo_error || "BIBLIO may ignore photos until the listing is active; one delayed retry is scheduled",
      };
    }
    return { label: "FTP uploaded", cls: "success", detail: "Transfer finished; BIBLIO processing is separate" };
  }
  return null;
}

function marketplaceListingBadge(listing) {
  const pill = '<span class="pill ' + esc(listing.channel) + '">' + esc(listing.channel) + "</span>";
  if (listing.channel !== "biblio") return pill;
  const stateInfo = biblioListingState(listing.biblio_sync);
  if (!stateInfo) return pill;
  const title = stateInfo.detail ? ' title="' + esc(stateInfo.detail) + '"' : "";
  return '<span class="channel-sync-stack">' + pill
    + '<span class="biblio-sync-mini ' + esc(stateInfo.cls) + '"' + title + '>'
    + esc(stateInfo.label) + "</span></span>";
}

function biblioListingDetails(row) {
  if (row.channel !== "biblio") return "";
  const details = row.biblio_details || {};
  const parts = [];
  if (details.author) parts.push("Author: " + details.author);
  if (details.condition) parts.push("Condition: " + details.condition);
  if (details.publisher) parts.push("Publisher: " + details.publisher);
  if (details.edition) parts.push("Edition: " + details.edition);
  if (details.isbn) parts.push("ISBN: " + details.isbn);
  let verification = "Submitted locally by FTP; not yet verified from a BIBLIO inventory download.";
  if (details.remote_verification_stale) {
    verification = "Remote verification is stale because this listing changed locally after the last BIBLIO download.";
  } else if (details.remote_verified) {
    if (details.remote_matches_local === true) {
      verification = "Verified in BIBLIO inventory" + (details.remote_verified_at ? " · " + when(details.remote_verified_at) : "");
    } else {
      const fields = (details.remote_mismatch_fields || []).join(", ");
      verification = "Verified in BIBLIO, but remote data differs" + (fields ? ": " + fields : "");
    }
  } else if (details.remote_missing_at) {
    verification = "Missing from the last complete BIBLIO active-inventory snapshot.";
  }
  const metadata = parts.length
    ? '<div class="sub">' + esc(parts.join(" · ")) + '</div>'
    : '<div class="sub">BIBLIO FTP listing</div>';
  return metadata + '<div class="sub">' + esc(verification) + '</div>';
}

function biblioActivityStatus(row) {
  if (!row) return { label: "Nothing sent yet", cls: "idle" };
  if (row.status === "queued") return { label: "Waiting to send", cls: "queued" };
  if (row.status === "running") {
    const stages = {
      preparing: "Preparing files",
      connecting: "Connecting to BIBLIO",
      connected: "Sending files",
      inventory_uploaded: "Book details sent",
      deletes_uploaded: "Sold-out updates sent",
      photos_uploading: "Sending photos",
    };
    return { label: stages[row.stage] || "Sending to BIBLIO", cls: "running" };
  }
  if (row.status === "error") return { label: "Could not send files", cls: "error" };
  if (row.status === "success") {
    return {
      label: (row.photo_errors || []).length ? "Some photos could not be sent" : "Files sent to BIBLIO",
      cls: (row.photo_errors || []).length ? "warn" : "success",
    };
  }
  return { label: String(row.status || "Unknown"), cls: "idle" };
}

function biblioActivityDetail(row) {
  if (!row) return "No BIBLIO transfer has been recorded yet.";
  const parts = [];
  if (row.listing_title) parts.push(row.listing_title);
  if (row.photos_only || row.mode === "photos") parts.push("Photos only");
  else if (row.full_sync || row.mode === "full") parts.push("Entire catalogue");
  else if (row.mode === "incremental") parts.push("Only changed records");
  if (row.message) parts.push(row.message);
  const inventoryDone = row.inventory_uploaded ?? (row.status === "success" ? row.active_count : null);
  if (row.inventory_total != null || inventoryDone != null) {
    parts.push("book records sent " + Number(inventoryDone || 0) + "/" + Number(row.inventory_total ?? row.active_count ?? 0));
  }
  if (row.photos_total != null) {
    parts.push("photos sent " + Number(row.photos_uploaded || 0) + "/" + Number(row.photos_total || 0));
  }
  if (Number(row.photos_skipped || 0) > 0) {
    parts.push(Number(row.photos_skipped) + " previously accepted photos skipped");
  }
  if (Number(row.photo_retry_scheduled || 0) > 0) {
    parts.push(Number(row.photo_retry_scheduled) + " automatic photo follow-up");
  }
  if (row.error) parts.push(row.error);
  return parts.join(" · ") || "BIBLIO transfer recorded.";
}

function biblioPhotoInspectionHtml() {
  const info = state.biblioPhotoInspection;
  if (state.biblioPhotoError) return '<p class="error">' + esc(state.biblioPhotoError) + '</p>';
  if (!info) return '<p class="muted">Choose a book, or enter its BIBLIO Book ID, to see what photos the dashboard has and what was last sent.</p>';
  const known = Number(info.biblio_source_photos || 0);
  const vinted = info.vinted_source_photos;
  const difference = vinted != null && Number(vinted) !== known
    ? '<p class="biblio-photo-alert">Vinted and staged BIBLIO photo counts differ. Resending this book will refresh the source first.</p>'
    : "";
  const job = info.job
    ? '<p class="muted">Most recent targeted job: ' + esc(info.job.status)
      + (info.job.available_at ? ' · ' + esc(when(info.job.available_at)) : '')
      + (info.job.error ? ' · ' + esc(info.job.error) : '') + '</p>'
    : "";
  return '<div class="biblio-photo-inspection-details">'
    + '<strong>' + esc(info.title || info.book_id) + '</strong>'
    + '<div class="diagnostics-pills">'
    + '<span>Saved in dashboard <strong>' + known + '</strong></span>'
    + '<span>Available on Vinted <strong>' + esc(vinted == null ? "unknown" : vinted) + '</strong></span>'
    + '<span>Last sent count <strong>' + esc(info.last_ftp_photo_count ?? "unrecorded") + '</strong></span>'
    + '</div>'
    + difference
    + '<details class="biblio-photo-files"><summary>Technical file names</summary><p>' + esc((info.filenames || []).join(", ") || "None") + '</p></details>'
    + '<p class="muted">Dashboard transfer status: ' + esc(info.photo_state || "not sent")
    + (info.last_ftp_photo_at ? ' · last FTP ' + esc(when(info.last_ftp_photo_at)) : '') + '</p>'
    + (info.photo_error ? '<p class="error">' + esc(info.photo_error) + '</p>' : '')
    + ((info.file_progress || []).length
      ? '<details class="biblio-photo-files"><summary>File transfer evidence (' + Number(info.successful_file_transfers || 0)
        + ' previously accepted by FTP; ' + Number(info.unconfirmed_file_transfers || 0)
        + ' without a recorded receipt)</summary>'
        + (info.file_progress || []).map(file =>
          '<div class="' + (file.sent_to_ftp ? 'muted' : 'error') + '">' + esc(file.filename)
          + ' · ' + (file.sent_to_ftp ? 'Previously accepted by FTP' : 'No matching successful FTP receipt')
          + '</div>').join("") + '</details>'
      : '')
    + job
    + '<p class="muted">These counts describe the dashboard and its file transfers, not pictures visible to buyers. Check the book on BIBLIO to confirm the result.</p>'
    + '</div>';
}

async function loadBiblioPhotoChoices() {
  const select = $("#biblio-photo-book-select");
  if (!select) return;
  if (state.biblioBookChoices === null) {
    select.innerHTML = '<option value="">Loading your BIBLIO books…</option>';
    try {
      const response = await api("/api/app/listings?channel=biblio");
      state.biblioBookChoices = (response.listings || [])
        .filter(book => book.status === "active" && Number(book.quantity || 0) > 0)
        .sort((a,b) => String(a.title || "").localeCompare(String(b.title || "")));
    } catch (error) {
      select.innerHTML = '<option value="">Could not load books. Enter a Book ID below.</option>';
      return;
    }
  }
  select.innerHTML = '<option value="">Choose a book by title…</option>'
    + state.biblioBookChoices.map(book =>
      '<option value="' + esc(book.external_id) + '"'
      + (state.biblioPhotoTarget === book.external_id ? ' selected' : '') + '>'
      + esc(book.title || "Untitled") + ' (' + esc(book.external_id) + ')</option>'
    ).join("");
}

function resetBiblioPhotoInspection(value) {
  state.biblioPhotoTarget = value;
  if (value !== state.biblioPhotoInspection?.book_id) {
    state.biblioPhotoInspection = null;
    state.biblioPhotoError = "";
    const retry = $("#biblio-retry-listing-photos");
    if (retry) retry.disabled = true;
    const selective = $("#biblio-retry-failed-photos");
    if (selective) selective.disabled = true;
    const report = $("#biblio-photo-inspection");
    if (report) report.innerHTML = biblioPhotoInspectionHtml();
  }
}

async function inspectBiblioPhotoTarget() {
  const field = $("#biblio-photo-book-id");
  const target = (field?.value || "").trim();
  state.biblioPhotoTarget = target;
  state.biblioPhotoError = "";
  state.biblioPhotoInspection = null;
  if (!target) {
    state.biblioPhotoError = "Enter a BIBLIO Book ID.";
  } else {
    try {
      state.biblioPhotoInspection = await api(
        "/api/app/connectors/biblio/photo-status?book_id=" + encodeURIComponent(target)
      );
    } catch (error) {
      state.biblioPhotoError = error.message;
    }
  }
  $("#biblio-photo-inspection").innerHTML = biblioPhotoInspectionHtml();
  const retry = $("#biblio-retry-listing-photos");
  if (retry) retry.disabled = !state.biblioPhotoInspection?.active
    || !(state.biblioPhotoInspection.biblio_source_photos || state.biblioPhotoInspection.vinted_source_photos);
  const selective = $("#biblio-retry-failed-photos");
  if (selective) selective.disabled = !state.biblioPhotoInspection?.active
    || !state.biblioPhotoInspection?.photo_error
    || !(state.biblioPhotoInspection?.successful_file_transfers > 0)
    || !(state.biblioPhotoInspection?.unconfirmed_file_transfers > 0);
}

function renderBiblioActivity(activity, operational) {
  const health = activity?.health || {};
  const dataAvailable = health.active_listings != null;
  const count = (key) => Number(health[key] || 0);
  const waiting = count("inventory_changes_pending") + count("deletes_pending");
  const last = activity?.current || null;
  const status = biblioActivityStatus(last);
  const runs = activity?.runs || [];
  const lastSentAt = last?.completed_at || last?.started_at;
  const verified = count("remote_verified_matching") + count("remote_verified_mismatching");
  const overview = !dataAvailable
    ? '<p class="biblio-user-warning">BIBLIO counts are temporarily unavailable.</p>'
    : '<div class="biblio-summary">'
      + '<p><strong>' + count("active_listings") + '</strong> books prepared'
      + ' · <strong>' + waiting + '</strong> changes waiting'
      + (count("photo_attention")
        ? ' · <strong>' + count("photo_attention") + '</strong> books need photo review'
        : '') + '</p>'
      + (count("remote_verified_mismatching") || count("remote_verification_stale")
        ? '<p class="biblio-user-warning">'
          + count("remote_verified_mismatching") + ' differ from last comparison; '
          + count("remote_verification_stale") + ' changed since then.</p>'
        : '')
      + '<p class="biblio-user-note">Prepared does not mean published.'
      + (verified ? ' ' + verified + ' listings checked against a BIBLIO export.' : '')
      + '</p></div>';
  const latest = '<div class="biblio-latest">'
    + '<span class="biblio-activity-dot ' + esc(status.cls) + '"></span>'
    + '<div><strong>' + esc(last ? status.label : "Nothing sent yet") + '</strong>'
    + '<p>' + (last ? esc(biblioActivityDetail(last)) : "Send changed books when you are ready.")
    + (lastSentAt ? ' · ' + esc(when(lastSentAt)) : '') + '</p></div></div>';
  const photoHelp = operational
    ? '<details class="biblio-task-panel biblio-photos-panel"'
      + (state.biblioPhotoExpanded ? ' open' : '') + '>'
      + '<summary><span><strong>Fix photos for one book</strong><small>Check the pictures available locally, then resend that book’s photos if needed</small></span></summary>'
      + '<div class="biblio-task-inner">'
      + '<label class="biblio-field-label" for="biblio-photo-book-select">Choose a BIBLIO book</label>'
      + '<select id="biblio-photo-book-select"><option value="">Loading books when opened…</option></select>'
      + '<details class="biblio-photo-manual-id"><summary>Enter a book ID instead</summary>'
      + '<input id="biblio-photo-book-id" aria-label="BIBLIO Book ID" placeholder="BIBLIO Book ID" value="' + esc(state.biblioPhotoTarget) + '"></details>'
      + '<div class="actions biblio-photo-recovery-controls">'
      + '<button id="biblio-inspect-photos" class="btn" type="button">Check this book’s photos</button>'
      + '<button id="biblio-retry-failed-photos" class="btn" type="button" disabled>Retry failed photo files…</button>'
      + '<button id="biblio-retry-listing-photos" class="btn" type="button" disabled>Resend all photos for this book…</button>'
      + '</div><p class="biblio-user-note">Retry failed files only when some individual transfers succeeded and others failed. To repair photos missing from BIBLIO even though FTP accepted them, resend the whole book’s photos.</p>'
      + '<div id="biblio-photo-inspection" role="status" aria-live="polite">' + biblioPhotoInspectionHtml() + '</div>'
      + '</div></details>'
    : '';
  const fileHistory = runs.map((run) => {
    const state = biblioActivityStatus(run);
    const photos = (run.photo_results || []).length
      ? '<details class="biblio-photo-files"><summary>See individual photo transfer results (' + run.photo_results.length + ')</summary>'
        + run.photo_results.map(photo => '<div class="' + (photo.status === "error" ? "error" : "muted") + '">'
          + esc(photo.filename) + ' · ' + esc(photo.status === "error" ? "Transfer failed" : "Sent to BIBLIO") + '</div>').join("") + '</details>'
      : "";
    return '<article class="biblio-activity-run"><div><strong>' + esc(state.label) + '</strong><time>' + esc(when(run.started_at)) + '</time></div>'
      + '<p>' + esc(biblioActivityDetail(run)) + '</p>'
      + ((run.photo_errors || []).length ? '<p class="error">' + esc(run.photo_errors.join(" · ")) + '</p>' : '')
      + photos + '</article>';
  }).join("");
  const history = '<details class="biblio-task-panel biblio-history-panel"'
    + (state.biblioActivityExpanded ? ' open' : '') + '><summary>'
    + '<span><strong>What has the dashboard sent?</strong><small>Recent transfers and individual photo results</small></span>'
    + '</summary><div class="biblio-task-inner">'
    + '<p>“Sent” means that BIBLIO received the files. BIBLIO may need additional time to process and display the books and photos.</p>'
    + '<p>For a newly added book, BIBLIO may not recognize its photos immediately. The dashboard automatically attempts one later photo follow-up when needed; you normally do not need to resend all photos.</p>'
    + '<div class="biblio-activity-history">' + (fileHistory || '<p>No transfers have been recorded yet.</p>') + '</div>'
    + '</div></details>';
  const advanced = operational
    ? '<details class="biblio-task-panel biblio-recovery-panel"'
      + (state.biblioRecoveryExpanded ? ' open' : '') + '><summary>'
      + '<span><strong>Advanced recovery</strong><small>Resend everything only when normal uploads have not worked</small></span></summary>'
      + '<div class="biblio-task-inner">'
      + '<div class="biblio-recovery-row"><div><strong>Resend all photos</strong><p>Reuploads pictures for every active BIBLIO book. Use the one-book tool above for a single missing image.</p></div>'
      + '<button class="btn biblio-retry-photos" type="button">Resend all photos…</button></div>'
      + '<div class="biblio-recovery-row"><div><strong>Resend the entire catalogue</strong><p>Reuploads every active listing, updates and photos, even if nothing has changed. Usually unnecessary.</p></div>'
      + '<button class="btn biblio-full-sync" type="button">Resend all listings…</button></div>'
      + '<p class="biblio-user-note">This does not confirm publication. BIBLIO orders are not imported automatically because the separate Bulk Order Management interface is not connected.</p>'
      + '</div></details>'
    : '';
  return '<div class="biblio-human-workflow">'
    + overview + latest
    + '<div class="biblio-next-actions">'
    + '<button class="btn biblio-open-compare" type="button">Compare BIBLIO inventory</button>'
    + '<span>Download a listing file from your BIBLIO seller account; checking it here does not alter your books.</span>'
    + '</div>'
    + photoHelp + history + advanced
    + '</div>';
}

async function inspectInventoryRelationships() {
  const button = $("#inventory-check-relations");
  const target = $("#inventory-relation-report");
  button.disabled = true;
  target.classList.remove("hidden");
  target.textContent = "Inspecting relationships…";
  try {
    const result = await api("/api/app/inventory/relationship-audit");
    const conflicts = result.shared_marketplace_skus || [];
    const orphan = result.unlinked_listings || [];
    const multiple = result.multi_active_same_market || [];
    const candidates = result.duplicate_candidates || [];
    const provisional = result.provisional || [];
    const issues = [
      ...conflicts.map(row => 'SKU ' + row.sku + ' occurs across ' + row.item_ids.length + ' master records'),
      ...multiple.map(row => row.channel + ': ' + row.external_ids.length + ' active listings linked to one item'),
      ...orphan.map(row => row.channel + ' / ' + row.external_id + ' has no master stock reference'),
    ];
    target.innerHTML = '<div class="inventory-relation-counts">'
      + '<span><strong>' + Number(result.physical || 0) + '</strong> confirmed stock records</span>'
      + '<span><strong>' + provisional.length + '</strong> provisional imports</span>'
      + '<span><strong>' + Number(result.legacy_unclassified || 0) + '</strong> legacy unclassified</span>'
      + '<span><strong>' + candidates.length + '</strong> potential duplicate pairs</span>'
      + '<span><strong>' + orphan.length + '</strong> unlinked listings</span>'
      + '</div>'
      + '<p class="muted">Read-only. Matching SKUs, ISBNs and titles do not prove that listings describe one physical copy.</p>'
      + (issues.length ? '<ul class="inventory-relation-issues">' + issues.slice(0, 12).map(issue => '<li>' + esc(issue) + '</li>').join("")
        + (issues.length > 12 ? '<li>…and ' + (issues.length - 12) + ' more</li>' : '') + '</ul>' : '')
      + (provisional.length || orphan.length || candidates.length || issues.length
        ? '<button class="btn inventory-open-reconcile" type="button">Review possible matches</button>' : '');
    const reconcile = $(".inventory-open-reconcile");
    if (reconcile) reconcile.onclick = () => selectView("reconcile");
  } catch (error) {
    target.textContent = "Relationship audit failed: " + error.message;
  } finally {
    button.disabled = false;
  }
}

function renderStoreStockAudit(data) {
  const root = $("#store-stock-audit-results");
  const progress = $("#store-stock-audit-progress");
  const button = $("#store-stock-audit-start");
  const job = data.job;
  const results = Array.isArray(data.results) ? data.results : [];
  const counts = data.counts || {};
  const checked = results.length;
  const running = job && ["queued", "running"].includes(job.status);
  button.disabled = Boolean(running) || Number(data.eligible || 0) === 0
    || Number(data.eligible || 0) > Number(data.limit || 100);
  progress.textContent = !job
    ? Number(data.eligible || 0) + " eligible store listing(s)"
    : running
      ? "Checking " + checked + " of " + Number(data.eligible || 0) + "…"
      : job.status === "error"
        ? "Check stopped. Review completed results and retry."
        : "Last check completed " + when(job.completed_at);
  const issues = results.filter(row => row.state !== "matched");
  const matches = results.filter(row => row.state === "matched");
  const heading = '<p class="store-stock-audit-summary"><strong>'
    + Number(counts.mismatch || 0) + ' different</strong> · '
    + Number(counts.error || 0) + ' could not be checked · '
    + Number(counts.matched || 0) + ' matched'
    + ((counts.skipped || 0) + (counts.changed || 0)
      ? ' · ' + Number((counts.skipped || 0) + (counts.changed || 0)) + ' needs review'
      : "") + '</p>';
  const labels = {
    mismatch: "Stock differs", error: "Check failed",
    changed: "Changed during check", skipped: "Needs linking review",
    matched: "Matches",
  };
  const issueRows = issues.map(row => {
    const known = Number.isInteger(row.remote_quantity);
    const quantities = known
      ? "Store " + row.remote_quantity + " · Dashboard " + row.local_quantity
      : "Dashboard " + row.local_quantity;
    return '<div class="store-stock-audit-row"><div class="store-stock-audit-copy">'
      + '<strong>' + esc(row.title) + '</strong><span>'
      + esc(row.channel.toUpperCase()) + ' · ' + esc(quantities)
      + '</span><span class="store-stock-audit-' + esc(row.state) + '">'
      + esc(labels[row.state] || "Review") + '</span>'
      + (row.message ? '<small>' + esc(row.message) + '</small>' : "")
      + '</div><button class="btn store-stock-audit-open" type="button" data-item="'
      + esc(row.item_id) + '">Review item</button></div>';
  }).join("");
  root.innerHTML = !job
    ? '<p class="muted">No store stock check has been run yet.</p>'
    : heading
      + (issues.length
        ? '<div class="store-stock-audit-issues">' + issueRows + '</div>'
        : (running ? '<p class="muted">No discrepancies found so far.</p>'
          : '<p class="muted">No discrepancies recorded in this check.</p>'))
      + (matches.length
        ? '<details class="store-stock-audit-matches"><summary>'
          + matches.length + ' matching listing(s)</summary><p class="muted">'
          + matches.map(row => esc(row.title) + ' (' + esc(row.channel) + ')').join(" · ")
          + '</p></details>'
        : "");
  $$(".store-stock-audit-open").forEach(button => {
    button.onclick = () => openItemMarketplaces(button.dataset.item);
  });
}

async function loadStoreStockAudit() {
  if (state.storeStockAuditTimer) {
    clearTimeout(state.storeStockAuditTimer);
    state.storeStockAuditTimer = null;
  }
  const panel = $("#store-stock-audit-panel");
  if (!panel?.open || state.view !== "inventory") return;
  try {
    const data = await api("/api/app/inventory/store-stock-audit");
    if (!panel.open || state.view !== "inventory") return;
    renderStoreStockAudit(data);
    if (data.job && ["queued", "running"].includes(data.job.status)) {
      state.storeStockAuditTimer = setTimeout(loadStoreStockAudit, 2500);
    }
  } catch (error) {
    $("#store-stock-audit-progress").textContent = "Unable to load results";
    $("#store-stock-audit-results").textContent = error.message;
  }
}

$("#store-stock-audit-panel").addEventListener("toggle", () => {
  if ($("#store-stock-audit-panel").open) {
    loadStoreStockAudit();
  } else if (state.storeStockAuditTimer) {
    clearTimeout(state.storeStockAuditTimer);
    state.storeStockAuditTimer = null;
  }
});
$("#store-stock-audit-start").onclick = async () => {
  const button = $("#store-stock-audit-start");
  button.disabled = true;
  try {
    const queued = await api("/api/app/inventory/store-stock-audit", {method:"POST"});
    flash("Checking " + Number(queued.eligible || 0)
      + " linked listings. Store quantities will not be changed.");
    await loadStoreStockAudit();
  } catch (error) {
    flash(error.message, true);
    button.disabled = false;
  }
};

async function inventory() {
  if (state.biblioInventoryTimer) {
    clearTimeout(state.biblioInventoryTimer);
    state.biblioInventoryTimer = null;
  }
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
        + '<td>' + esc(item.sku)
        + '<div class="sub">' + esc(item.stock_authority === "physical" ? "Confirmed stock"
          : item.stock_authority === "provisional" ? "Provisional import" : "Legacy · unclassified") + '</div>'
        + "</td><td>" + esc(item.category)
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
          marketplaceListingBadge(listing)
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
  $$(".item-marketplaces").forEach(button => {
    button.onclick = () => openItemMarketplaces(button.dataset.itemId);
  });
  $$(".inventory-select").forEach((box) => { box.onchange = updateInventorySelection; });
  const selectAll = $("#inventory-select-all");
  if (selectAll) {
    selectAll.onchange = () => {
      $$(".inventory-select").forEach((box) => { box.checked = selectAll.checked; });
      updateInventorySelection();
    };
  }
  updateInventorySelection();
  if ($("#store-stock-audit-panel").open && !state.storeStockAuditTimer) loadStoreStockAudit();
  const biblioPending = state.inventoryItems.some((item) =>
    (item.listings || []).some((listing) =>
      listing.channel === "biblio"
      && ["queued", "uploading"].includes(String(listing.biblio_sync?.state || ""))
    )
  );
  if (biblioPending && state.view === "inventory") {
    state.biblioInventoryTimer = setTimeout(() => {
      if (state.view === "inventory") inventory();
    }, 2500);
  }
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
    } else if (field.dataset.field === "pages") {
      payload.pages = value === "" ? null : Number(value);
    } else if (field.dataset.type === "boolean") {
      payload[field.dataset.field] = value === "" ? null : value === "true";
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
    row.subtitle = metadata.subtitle || row.subtitle || null;
    row.binding = metadata.physical_format || row.binding || null;
    row.language = metadata.language || row.language || null;
    row.publish_date = metadata.publish_date || row.publish_date || null;
    row.publication_year = metadata.publication_year || row.publication_year || null;
    row.pages = metadata.number_of_pages || row.pages || null;
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
    subtitle: null,
    binding: null,
    language: null,
    publish_date: null,
    publication_year: null,
    pages: null,
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
    subtitle: row.subtitle || null,
    binding: row.binding || null,
    language: row.language || null,
    publish_date: row.publish_date || null,
    publication_year: row.publication_year || null,
    pages: row.pages || null,
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

async function enrichQuickBookFromIsbn() {
  const form = $("#quick-listing-form");
  const isbnField = form.elements.namedItem("isbn");
  const isbn = String(isbnField?.value || "").trim();
  if (!isbn) return;
  try {
    const data = await api("/api/app/stock-intake/barcode/lookup", {
      method: "POST",
      body: JSON.stringify({ code: isbn }),
    });
    if (data.kind !== "isbn" || !data.metadata) return;
    const metadata = data.metadata || {};
    const fillBlank = (name, value) => {
      const field = form.elements.namedItem(name);
      if (!field || value == null || String(value).trim() === "") return;
      if (!String(field.value || "").trim()) field.value = value;
    };
    fillBlank("quick_category", "book");
    fillBlank("barcode", data.isbn || isbn);
    fillBlank("author", metadata.author);
    fillBlank("subtitle", metadata.subtitle);
    fillBlank("publisher", metadata.publisher);
    fillBlank("edition", metadata.edition);
    fillBlank("binding", metadata.physical_format);
    fillBlank("publish_date", metadata.publish_date);
    fillBlank("publication_year", metadata.publication_year);
    fillBlank("pages", metadata.number_of_pages);
    const title = [metadata.title, metadata.subtitle].filter(Boolean).join(": ");
    fillBlank("title", title);
    if (data.metadata_warning) {
      $("#quick-confidence").textContent = data.metadata_warning;
    }
    renderQuickRequired();
  } catch (_error) {
    // ISBN enrichment is opportunistic; the detected/entered ISBN remains
    // available even when the metadata provider cannot be reached.
  }
}

async function applyQuickAnalysis(data) {
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
    barcode: "barcode",
    author: "author",
    isbn: "isbn",
    subtitle: "subtitle",
    publisher: "publisher",
    edition: "edition",
    binding: "binding",
    language: "language",
    publish_date: "publish_date",
    publication_year: "publication_year",
    pages: "pages",
    suggested_title: "title",
    suggested_description: "description",
  };
  Object.entries(mapping).forEach(([source, target]) => {
    if (result[source]) setFormValue(form, target, result[source]);
  });
  if (result.isbn) await enrichQuickBookFromIsbn();
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

const quickIsbnField = $("#quick-listing-form").elements.namedItem("isbn");
if (quickIsbnField) {
  quickIsbnField.onchange = () => enrichQuickBookFromIsbn();
}

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
    await applyQuickAnalysis(result);
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
    barcode: String(raw.barcode || "").trim() || null,
    author: String(raw.author || "").trim() || null,
    isbn: String(raw.isbn || "").trim() || null,
    subtitle: String(raw.subtitle || "").trim() || null,
    publisher: String(raw.publisher || "").trim() || null,
    edition: String(raw.edition || "").trim() || null,
    binding: String(raw.binding || "").trim() || null,
    language: String(raw.language || "").trim() || null,
    publish_date: String(raw.publish_date || "").trim() || null,
    publication_year: raw.publication_year ? Number(raw.publication_year) : null,
    pages: raw.pages ? Number(raw.pages) : null,
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
      "barcode", "author", "isbn", "subtitle", "publisher", "edition",
      "binding", "language", "publish_date", "publication_year", "pages",
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
    "barcode", "author", "isbn", "subtitle", "publisher", "edition",
    "binding", "language", "publish_date", "publication_year", "pages",
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
        + '<td>' + esc(row.status)
        + (row.needs_reopen ? '<div class="sub error">Manual reopening required: stock is available again.</div>' : "")
        + (row.last_error ? '<div class="sub error">' + esc(row.last_error) + '</div>' : "") + '</td>'
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
  if (state.biblioListingsTimer) {
    clearTimeout(state.biblioListingsTimer);
    state.biblioListingsTimer = null;
  }
  const data = await api("/api/app/listings");
  state.listings = data.listings || [];
  renderListings();
  const biblioPending = state.listings.some((row) =>
    row.channel === "biblio"
    && ["queued", "uploading"].includes(String(row.biblio_sync?.state || ""))
  );
  if (biblioPending && state.view === "listings") {
    state.biblioListingsTimer = setTimeout(() => {
      if (state.view === "listings") listings();
    }, 2500);
  }
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
          + '</div><div class="sub">' + esc(row.external_sku || row.external_id || "") + "</div>"
          + biblioListingDetails(row) + "</td>"
          + "<td>" + marketplaceListingBadge(row) + "</td>"
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

$("#inventory-check-relations").onclick = inspectInventoryRelationships;
$("#export-csv").onclick = () => exportInventory("csv");
$("#export-xlsx").onclick = () => exportInventory("xlsx");


function marketplaceRuntimeDetails(market, definitions) {
  if (!market) return "";
  const runtime = market.runtime || {};
  const operations = definitions.operations || [];
  const statusText = runtime.credential_ready ? "Credentials present" : "Not ready to connect";
  const recent = runtime.last_run;
  const recentText = recent
    ? (recent.status || "unknown") + " (" + esc(recent.type || "sync") + ") · " + esc(when(recent.started_at))
    : "No recorded runs";
  const rows = operations.map((operation) => {
    const value = market.operations?.[operation.key] || {status:"missing"};
    const info = value.note ? '<div class="sub">' + esc(value.note) + '</div>' : "";
    const evidence = value.evidence
      ? '<div class="sub">Code: ' + esc(value.evidence) + '</div>'
      : "";
    return '<tr><th scope="row">' + esc(operation.label) + '</th>'
      + '<td><span class="market-status market-status-' + esc(value.status) + '">'
      + esc(value.status.replaceAll("_", " ")) + '</span></td>'
      + '<td>' + esc(operation.acceptance) + info + evidence + '</td></tr>';
  }).join("");
  return '<div class="market-development-detail-heading"><h3>' + esc(market.display_name) + ' · technical implementation</h3>'
    + '<button class="btn marketplace-open-connection" data-c="' + esc(market.channel) + '" type="button">Go to connection controls</button></div>'
    + '<div class="market-runtime">'
    + '<span><strong>Account:</strong> ' + esc(statusText) + '</span>'
    + '<span><strong>Last successful sync:</strong> ' + esc(runtime.last_successful_sync_at ? when(runtime.last_successful_sync_at) : "none") + '</span>'
    + '<span><strong>Latest run:</strong> ' + recentText + '</span>'
    + '<span><strong>Listings:</strong> ' + Number(runtime.listing_count || 0) + '</span>'
    + '<span><strong>Master references:</strong> ' + Number(runtime.master_references || 0) + '</span>'
    + '<span><strong>Unlinked listings:</strong> ' + Number(runtime.unlinked_listings || 0) + '</span>'
    + '<span><strong>Provisional stock references:</strong> ' + Number(runtime.import_placeholders || 0) + '</span>'
    + '<span><strong>Seller sales:</strong> ' + Number(runtime.seller_sales || 0) + '</span>'
    + '<span><strong>Sales without master:</strong> ' + Number(runtime.sales_unlinked_to_master || 0) + '</span>'
    + '</div>'
    + '<p class="muted">References do not prove two marketplace listings describe the same physical copy. Provisional counts cover newly imported placeholders; older imports may lack this marker.</p>'
    + '<div class="market-matrix-scroll"><table class="market-matrix market-details-table"><thead>'
    + '<tr><th>Operation</th><th>Code status</th><th>Acceptance criteria, caveats and evidence</th></tr>'
    + '</thead><tbody>' + rows + '</tbody></table></div>';
}

function renderMarketplaceDevelopment(definitions) {
  const root = $("#marketplace-development");
  if (!root) return;
  const markets = definitions?.channels || [];
  if (!markets.length) {
    root.textContent = "No marketplace audit is available.";
    return;
  }
  const ops = definitions.operations || [];
  const legend = Object.entries(definitions.statuses || {}).map(([status, label]) =>
    '<span class="market-status market-status-' + esc(status) + '" title="' + esc(label) + '">'
    + esc(status.replaceAll("_", " ")) + '</span>'
  ).join("");
  const rows = markets.map((market) => {
    const configured = market.runtime?.credential_ready ? "Connected/configured" : "Not ready";
    const statuses = ops.map((operation) => {
      const value = market.operations?.[operation.key] || { status: "missing" };
      return '<td><span class="market-status market-status-' + esc(value.status)
        + '" title="' + esc(operation.label + ": " + value.status + (value.note ? " - " + value.note : "")) + '">'
        + esc(value.status) + '</span></td>';
    }).join("");
    return '<tr><th scope="row"><button type="button" class="market-select'
      + (market.channel === state.marketplaceSelected ? ' active' : '')
      + '" data-market-channel="' + esc(market.channel) + '">' + esc(market.display_name)
      + '</button><span class="sub">' + esc(configured) + '</span></th>' + statuses + '</tr>';
  }).join("");
  const headers = ops.map(op => '<th title="' + esc(op.acceptance) + '">' + esc(op.label) + '</th>').join("");
  root.innerHTML = '<p class="muted">Code coverage only. A successful job does not establish that a marketplace published the result.</p>'
    + '<div class="market-status-legend">' + legend + '</div>'
    + '<div class="market-matrix-scroll"><table class="market-matrix">'
    + '<thead><tr><th>Marketplace</th>' + headers + '</tr></thead>'
    + '<tbody>' + rows + '</tbody></table></div>'
    + '<div id="marketplace-detail" class="marketplace-detail"></div>'
    + '<details class="market-relations"><summary>Shared relationship rules</summary><ul>'
    + (definitions.relations || []).map(rel => '<li><strong>' + esc(rel.key) + ':</strong> ' + esc(rel.rule) + '</li>').join("")
    + '</ul></details>';
  if (!markets.some(row => row.channel === state.marketplaceSelected)) {
    state.marketplaceSelected = markets[0].channel;
  }
  function showSelected() {
    const selected = markets.find(row => row.channel === state.marketplaceSelected);
    $("#marketplace-detail").innerHTML = marketplaceRuntimeDetails(selected, definitions);
    const jump = $(".marketplace-open-connection");
    if (jump) jump.onclick = () => {
      const target = document.querySelector('[data-connector-channel="' + selected.channel + '"]');
      if (!target) return;
      if ($("#connector-other-grid").contains(target)) $("#other-marketplaces").open = true;
      target.scrollIntoView({behavior: "smooth", block: "center"});
      target.classList.add("connector-focused");
    };
    $$(".market-select").forEach(button => {
      button.classList.toggle("active", button.dataset.marketChannel === state.marketplaceSelected);
      button.setAttribute("aria-pressed", String(button.dataset.marketChannel === state.marketplaceSelected));
    });
  }
  $$(".market-select").forEach(button => {
    button.onclick = () => {
      state.marketplaceSelected = button.dataset.marketChannel;
      showSelected();
    };
  });
  showSelected();
}

function renderMarketplaceOperations(data) {
  const root = $("#marketplace-operations-list");
  if (!root) return;
  const operations = data?.operations || [];
  if (!operations.length) {
    root.innerHTML = '<p class="muted">No audited marketplace operations yet. New syncs, uploads and cross-market close jobs will appear here.</p>';
    return;
  }
  const statuses = {
    queued: "Waiting to start", running: "In progress",
    succeeded: "Finished", needs_verification: "Sent; check the marketplace",
    failed: "Did not finish", attention: "Needs your attention", cancelled: "Cancelled",
  };
  const verificationLabels = {
    manual_required: "Check the result on the marketplace",
    snapshot_imported: "Information imported into the dashboard",
    no_remote_changes: "No new changes sent",
    not_checked: "Not independently checked",
    remote_verified: "Checked against the marketplace",
  };
  root.innerHTML = '<div class="table-wrap"><table><thead><tr>'
    + '<th>Marketplace / action</th><th>Target</th><th>Status</th><th>Created</th><th>Next step</th>'
    + '</tr></thead><tbody>'
    + operations.map(op => {
      const requiresCheck = op.status === "needs_verification" || op.status === "attention";
      const statusTone = op.status === "failed" ? "danger"
        : requiresCheck ? "warning"
        : ["queued", "running"].includes(op.status) ? "info"
        : op.status === "succeeded" && op.verification === "remote_verified" ? "success"
        : "neutral";
      const result = Object.entries(op.result || {}).slice(0, 6)
        .map(([key, value]) => esc(key.replaceAll("_", " ") + ": " + String(value))).join(" · ");
      const next = op.can_retry
        ? '<button class="btn marketplace-operation-retry" type="button" data-id="' + esc(op.id) + '">'
          + (op.type === "photos" ? "Try sending photos again…" : "Try again…") + '</button>'
        : requiresCheck ? '<span class="muted">Check the result on the marketplace first</span>'
        : "";
      const actionLabel = op.type === "sync"
        ? (op.channel === "biblio" ? "Send listing changes" : "Import marketplace data")
        : ({publish:"Add listing", update:"Update listing", photos:"Send photos",
          close:"Close sold listing", verify:"Check listing"}[op.type] || op.type);
      const targetLabel = op.target === "all" ? "All relevant listings"
        : /^[0-9a-f-]{36}$/i.test(op.target || "") ? "One listing"
        : op.target || "Single listing";
      return '<tr><td><strong>' + esc(op.channel.toUpperCase()) + ' · ' + esc(actionLabel)
        + '</strong><div class="sub">' + esc(verificationLabels[op.verification] || op.verification) + '</div></td>'
        + '<td>' + esc(targetLabel) + '</td>'
        + '<td><span class="operation-status operation-status-' + statusTone + '">'
        + esc(statuses[op.status] || op.status) + '</span>'
        + (op.error ? '<div class="error">' + esc(op.error) + '</div>' : "")
        + (result ? '<details class="operation-technical"><summary>Transfer details</summary><p>' + result + '</p></details>' : "")
        + '</td><td>' + esc(when(op.created_at)) + '</td><td>' + next + '</td></tr>';
    }).join("")
    + '</tbody></table></div>';
  $$(".marketplace-operation-retry").forEach(button => {
    button.onclick = async () => {
      if (!window.confirm("Try this operation again? The dashboard only allows automatic retries when repeating it is considered safe. If a marketplace may have accepted an earlier upload, check it first.")) return;
      button.disabled = true;
      try {
        await api("/api/app/marketplace-operations/" + button.dataset.id + "/retry", {method:"POST"});
        flash("Marketplace operation queued for retry.");
        await refreshMarketplaceOperations();
      } catch (error) {
        flash(error.message, true);
        button.disabled = false;
      }
    };
  });
}

async function refreshMarketplaceOperations() {
  const data = await api("/api/app/marketplace-operations?limit=60");
  renderMarketplaceOperations(data);
}

$("#marketplace-operations-refresh").onclick = async () => {
  const button = $("#marketplace-operations-refresh");
  button.disabled = true;
  try { await refreshMarketplaceOperations(); }
  catch (error) { flash(error.message, true); }
  finally { button.disabled = false; }
};

async function connections() {
  if (state.biblioActivityTimer) {
    clearTimeout(state.biblioActivityTimer);
    state.biblioActivityTimer = null;
  }
  const [data, devices, biblioActivity, development] = await Promise.all([
    api("/api/app/connectors"),
    api("/api/app/extension/devices"),
    api("/api/app/connectors/biblio/activity").catch(() => null),
    api("/api/app/connectors/development").catch(() => null),
  ]);
  state.connectors = data.connectors || [];
  state.biblioActivity = biblioActivity;
  state.marketplaceDevelopment = development;
  renderMarketplaceDevelopment(development);
  try {
    await refreshMarketplaceOperations();
  } catch (error) {
    $("#marketplace-operations-list").textContent = "Operation history unavailable: " + error.message;
  }
  const featured = state.connectors.filter(connector =>
    connector.configured || connector.channel === "vinted" || connector.channel === "biblio"
  ).sort((a, b) => {
    const priority = {vinted: 0, biblio: 1};
    return (priority[a.channel] ?? 5) - (priority[b.channel] ?? 5);
  });
  const other = state.connectors.filter(connector => !featured.includes(connector));
  const connectorHtml = (connector) => {
    const channel = connector.channel;
    const ready = Boolean(connector.operational);
    const display = esc(connector.display_name);
    const name = esc(channel);
    const lastSeen = connector.last_synced_at ? " · last import " + esc(when(connector.last_synced_at)) : "";
    const configure = connectorSchemas[channel]
      ? '<button class="btn configure" data-c="' + name + '" type="button">'
        + (connector.configured ? 'Connection settings' : 'Set up connection') + '</button>' : '';
    if (channel === "biblio") {
      const health = biblioActivity?.health || {};
      const pending = Number(health.inventory_changes_pending || 0)
        + Number(health.deletes_pending || 0);
      return '<section class="connector connector--biblio" data-connector-channel="biblio" aria-label="BIBLIO">'
        + '<div class="connector-header"><div><h2>BIBLIO</h2><p>Send and maintain book listings in your BIBLIO seller account.</p></div>'
        + '<span class="connection-state ' + (ready ? 'ready' : 'not-ready') + '">'
        + (ready ? 'Account set up' : 'Setup required') + '</span></div>'
        + (ready
          ? '<div class="biblio-primary-task"><div><strong>' + (pending > 0
              ? pending + ' listing change' + (pending === 1 ? '' : 's') + ' waiting to be sent'
              : 'Send new changes when ready') + '</strong>'
            + '<p>Sends changed listings and photos. ' 
            + 'It does not resend unchanged listings or confirm they are visible to buyers.</p></div>'
            + '<button class="btn primary sync" data-c="biblio" type="button">Send changes to BIBLIO</button></div>'
          : '<p class="biblio-setup-help">First enter your BIBLIO seller FTP credentials. After setup, you can send changed books and review what was transferred.</p>')
        + renderBiblioActivity(biblioActivity, ready)
        + '<div class="connector-settings-row">' + configure + '</div>'
        + '</section>';
    }
    if (channel === "vinted") {
      const paired = (devices.devices || []).filter(device => !device.revoked).length;
      return '<section class="connector connector--vinted" data-connector-channel="vinted" aria-label="Vinted">'
        + '<div class="connector-header"><div><h2>Vinted</h2><p>Your Vinted data comes from the Chrome browser extension while you are signed in to Vinted.</p></div>'
        + '<span class="connection-state ' + (paired ? 'ready' : 'not-ready') + '">'
        + (paired ? paired + ' browser' + (paired === 1 ? '' : 's') + ' paired' : 'Browser not paired') + '</span></div>'
        + '<div class="actions"><button class="btn pair" type="button">' + (paired ? 'Pair another browser' : 'Pair a Chrome browser') + '</button>'
        + '<a class="btn" href="' + esc(devices.download_url || "/downloads/reseller-chrome-bridge.zip")
        + '">Download Chrome extension</a></div>'
        + '<p class="connector-version">Chrome extension version ' + esc(devices.latest_version || state.me?.bridge_version || "unknown") + '</p>'
        + '<div class="pairing-inline hidden"><p>Enter this code in the Chrome extension within 10 minutes:</p>'
        + '<strong class="pair-code pair-code-inline"></strong></div>'
        + '<p class="connector-workflow-hint">Pairing allows uploads; it does not start one. Open Vinted with the extension active to collect new listings and sales. If Vinted has not updated recently, check the extension.</p>'
        + '<p class="connector-last-sync">Latest Vinted data received: <strong>'
        + (connector.last_synced_at ? esc(when(connector.last_synced_at)) : 'not recorded yet')
        + '</strong></p>'
        + '</section>';
    }
    const statusText = connector.authorization_required
      ? "Authorization needed" : ready ? "Account set up" : connector.configured
        ? "Needs attention" : "Not connected";
    return '<section class="connector connector--other" data-connector-channel="' + name + '">'
      + '<div class="connector-header"><div><h2>' + display + '</h2></div>'
      + '<span class="connection-state ' + (ready ? 'ready' : 'not-ready') + '">' + statusText + '</span></div>'
      + '<div class="actions">' + configure
      + (connector.sync_available
        ? '<button class="btn sync" data-c="' + name + '" type="button">Import latest data</button>'
        : '') + '</div>'
      + (lastSeen ? '<p class="connector-last-sync">' + lastSeen.slice(3) + '</p>' : '')
      + '<details class="connection-technical-help"><summary>About this connection</summary>'
      + '<p class="connector-workflow-hint">' + esc(connector.description) + '</p>'
      + (connector.note ? '<p class="connector-workflow-hint">' + esc(connector.note) + '</p>' : "")
      + '<p class="muted">Import reads marketplace data; it does not edit remote listings.</p></details>'
      + '</section>';
  };
  $("#connector-grid").innerHTML = featured.map(connectorHtml).join("");
  $("#connector-other-grid").innerHTML = other.map(connectorHtml).join("");
  $("#other-marketplaces-count").textContent = String(other.length);
  $("#other-marketplaces").classList.toggle("hidden", other.length === 0);
  $("#biblio-compare-panel").classList.toggle("hidden",
    !state.connectors.some(connector => connector.channel === "biblio" && connector.operational)
  );
  const openCompare = $(".biblio-open-compare");
  if (openCompare) openCompare.onclick = () => {
    const panel = $("#biblio-compare-panel");
    panel.open = true;
    panel.scrollIntoView({behavior:"smooth", block:"start"});
  };
  const trackedPanels = [
    [".biblio-photos-panel", "biblioPhotoExpanded"],
    [".biblio-history-panel", "biblioActivityExpanded"],
    [".biblio-recovery-panel", "biblioRecoveryExpanded"],
  ];
  trackedPanels.forEach(([selector, key]) => {
    const panel = $(selector);
    if (!panel) return;
    panel.addEventListener("toggle", () => {
      state[key] = panel.open;
      if (key === "biblioPhotoExpanded" && panel.open) loadBiblioPhotoChoices();
    });
  });
  if (state.biblioPhotoExpanded) loadBiblioPhotoChoices();

  document.querySelectorAll(".pair").forEach((button) => { button.onclick = () => pair(button); });
  $$(".configure").forEach((button) => {
    button.onclick = () => openConnectorConfig(button.dataset.c, data.connectors.find((row) => row.channel === button.dataset.c));
  });
  $$(".sync").forEach((button) => {
    button.onclick = async () => {
      button.disabled = true;
      const channel = button.dataset.c;
      try {
        const result = await api("/api/app/connectors/" + channel + "/sync", { method: "POST" });
        flash(channel === "biblio"
          ? (result.already_queued
              ? "Your BIBLIO upload is already in the queue. No second upload was started."
              : "Changes queued for BIBLIO. You can follow the transfer below.")
          : (result.already_queued ? "This import is already queued." : "Import queued."));
        await connections();
      } catch (error) {
        flash("Could not start the " + (channel === "biblio" ? "BIBLIO upload" : "import")
          + ": " + error.message, true);
      } finally {
        button.disabled = false;
      }
    };
  });
  $$(".biblio-retry-photos").forEach((button) => {
    button.onclick = async () => {
      if (!window.confirm("This resends the pictures for EVERY active BIBLIO listing. It is much larger than repairing one book and cannot confirm BIBLIO has displayed them. Do you want to resend all photos?")) return;
      button.disabled = true;
      try {
        await api("/api/app/connectors/biblio/retry-photos", { method: "POST" });
        flash("All BIBLIO photos queued for re-upload. Unchanged listing details will not be resent.");
        await connections();
      } catch (error) {
        flash(error.message, true);
        button.disabled = false;
      }
    };
  });
  $$(".biblio-full-sync").forEach((button) => {
    button.onclick = async () => {
      if (!window.confirm("This resends ALL active BIBLIO listings and photos, including unchanged ones. It may take longer and is usually unnecessary. Do you want to continue?")) return;
      button.disabled = true;
      try {
        await api("/api/app/connectors/biblio/full-sync", { method: "POST" });
        flash("Complete BIBLIO catalogue re-upload queued.");
        await connections();
      } catch (error) {
        flash(error.message, true);
        button.disabled = false;
      }
    };
  });

  const inspectPhotosButton = $("#biblio-inspect-photos");
  if (inspectPhotosButton) {
    inspectPhotosButton.onclick = () => inspectBiblioPhotoTarget();
    const field = $("#biblio-photo-book-id");
    field.oninput = () => resetBiblioPhotoInspection(field.value.trim());
    const chooser = $("#biblio-photo-book-select");
    if (chooser) chooser.onchange = () => {
      field.value = chooser.value;
      resetBiblioPhotoInspection(chooser.value);
    };
    field.onkeydown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        inspectBiblioPhotoTarget();
      }
    };
  }
  const retryPhotosButton = $("#biblio-retry-listing-photos");
  if (retryPhotosButton) {
    retryPhotosButton.disabled = !state.biblioPhotoInspection?.active
      || !(state.biblioPhotoInspection.biblio_source_photos || state.biblioPhotoInspection.vinted_source_photos);
    retryPhotosButton.onclick = async () => {
      const info = state.biblioPhotoInspection;
      if (!info || $("#biblio-photo-book-id").value.trim() !== info.book_id) return;
      if (!window.confirm("Resend the photos for " + info.title
        + " (" + info.book_id + ")? The book details will not be reuploaded, and you will still need to check the photos on BIBLIO.")) return;
      retryPhotosButton.disabled = true;
      try {
        const result = await api("/api/app/connectors/biblio/retry-listing-photos", {
          method: "POST",
          body: JSON.stringify({ book_id: info.book_id }),
        });
        flash("Photos for " + result.book_id + " queued for re-upload. The book details will not be resent.");
        await inspectBiblioPhotoTarget();
        await connections();
      } catch (error) {
        state.biblioPhotoError = error.message;
        $("#biblio-photo-inspection").innerHTML = biblioPhotoInspectionHtml();
        flash(error.message, true);
      }
    };
  }

  const failedPhotosButton = $("#biblio-retry-failed-photos");
  if (failedPhotosButton) {
    const info = state.biblioPhotoInspection;
    failedPhotosButton.disabled = !info?.active || !info?.photo_error
      || !(info.successful_file_transfers > 0)
      || !(info.unconfirmed_file_transfers > 0);
    failedPhotosButton.onclick = async () => {
      const selected = state.biblioPhotoInspection;
      if (!selected || $("#biblio-photo-book-id").value.trim() !== selected.book_id) return;
      if (!window.confirm("Retry only the " + selected.unconfirmed_file_transfers
        + " photo file(s) without a matching successful FTP receipt? Previously accepted files will be skipped. This does not prove BIBLIO displays any photo.")) return;
      failedPhotosButton.disabled = true;
      try {
        const result = await api("/api/app/connectors/biblio/retry-listing-photos", {
          method: "POST",
          body: JSON.stringify({book_id: selected.book_id, failed_only: true}),
        });
        flash("Unconfirmed photo files queued for " + result.book_id + ". Other previously transferred files will be skipped.");
        await inspectBiblioPhotoTarget();
        await connections();
      } catch (error) {
        state.biblioPhotoError = error.message;
        $("#biblio-photo-inspection").innerHTML = biblioPhotoInspectionHtml();
        flash(error.message, true);
        failedPhotosButton.disabled = false;
      }
    };
  }

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

  const currentStatus = String(biblioActivity?.current?.status || "");
  if (["queued", "running"].includes(currentStatus) && state.view === "connections") {
    if (state.biblioPhotoInspection) inspectBiblioPhotoTarget();
    state.biblioActivityTimer = setTimeout(() => {
      if (state.view === "connections") connections();
    }, 2500);
  }
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

$("#connections-refresh").onclick = async () => {
  const button = $("#connections-refresh");
  button.disabled = true;
  try {
    await connections();
    flash("Marketplace status refreshed.");
  } catch (error) {
    flash(error.message, true);
  } finally {
    button.disabled = false;
  }
};
$("#connections-inventory").onclick = () => selectView("inventory");
$("#connections-reconcile").onclick = () => selectView("reconcile");

function openConnectorConfig(channel, connector) {
  const schema = connectorSchemas[channel];
  if (!schema) return;
  state.connectorChannel = channel;
  $("#connector-config-title").textContent = schema.title;
  $("#connector-config-help").textContent = schema.help;
  const savedValues = connector?.saved_values || {};
  $("#connector-fields").innerHTML = schema.fields.map(([name, label, placeholder, type]) => {
    const value = type === "password" ? "" : (savedValues[name] ?? "");
    if (type === "checkbox") {
      const checked = ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
      return '<label class="checkline"><input name="' + esc(name) + '" type="checkbox" value="true"'
        + (checked ? " checked" : "") + '> ' + esc(label) + '</label>';
    }
    return '<label>' + esc(label) + '<input name="' + esc(name) + '" type="' + esc(type)
      + '" placeholder="' + esc(placeholder) + '" value="' + esc(value) + '"></label>';
  }).join("");
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
  (connectorSchemas[channel]?.fields || []).forEach(([name, _label, _placeholder, type]) => {
    if (type === "checkbox" && !(name in values)) values[name] = "false";
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
  $("#connector-config-status").textContent = "Testing BIBLIO transfer security…";
  try {
    const result = await api("/api/app/connectors/biblio/test", { method: "POST" });
    $("#connector-config-status").textContent = result.detail || "BIBLIO connection succeeded.";
  } catch (error) {
    $("#connector-config-status").textContent = error.message;
  }
};

function biblioInventoryForm(file, authoritative = false) {
  const form = new FormData();
  form.append("file", file);
  if (authoritative) form.append("authoritative", "true");
  return form;
}

function biblioReconciliationText(result) {
  return (Number(result.matched_clean || 0)) + " the same in both"
    + " · " + Number(result.mismatched || 0) + " with different details"
    + " · " + Number(result.remote_only || 0) + " on BIBLIO only"
    + " · " + Number(result.missing_local || 0) + " in dashboard only";
}

function biblioComparisonHtml(result) {
  const different = (result.mismatch_samples || []).slice(0, 12)
    .map(row => '<li>' + esc(row.book_id) + ': ' + esc((row.fields || []).join(", ")) + '</li>')
    .join("");
  const warnings = Number(result.mismatched || 0) + Number(result.remote_only || 0)
    + Number(result.missing_local || 0);
  return '<div class="biblio-comparison-results">'
    + '<strong>Comparison finished · ' + Number(result.remote_active || 0)
    + ' active books in the BIBLIO file</strong>'
    + '<p>' + esc(biblioReconciliationText(result)) + '</p>'
    + (warnings
      ? '<p>These are differences to review, not automatic errors or proof a photo is missing.</p>'
      : '<p>Book identifiers and compared details match. Photos still need checking on BIBLIO.</p>')
    + (different ? '<details><summary>See books with different details</summary><ul>' + different + '</ul></details>' : "")
    + (result.remote_only_ids?.length
      ? '<details><summary>Books only in the BIBLIO file</summary><p>'
        + esc(result.remote_only_ids.join(", ")) + '</p></details>' : '')
    + (result.missing_local_ids?.length
      ? '<details><summary>Books only in the dashboard</summary><p>'
        + esc(result.missing_local_ids.join(", ")) + '</p></details>' : '')
    + '<p class="muted">No book descriptions, prices or stock quantities were changed by this comparison.</p>'
    + '</div>';
}

const biblioImportInput = $("#biblio-import-file");
if (biblioImportInput) biblioImportInput.onchange = () => {
  state.biblioCompareCheckedFile = null;
  state.biblioCompareResult = null;
  $("#import-biblio").disabled = true;
  $("#biblio-compare-result").textContent = "File selected. Compare it first to see what differs.";
};

$("#verify-biblio").onclick = async () => {
  const file = $("#biblio-import-file").files[0];
  if (!file) return flash("Choose an inventory file downloaded from BIBLIO first.", true);
  const button = $("#verify-biblio");
  const message = $("#biblio-compare-result");
  button.disabled = true;
  $("#import-biblio").disabled = true;
  state.biblioCompareCheckedFile = null;
  state.biblioCompareResult = null;
  message.textContent = "Comparing the BIBLIO file with your dashboard…";
  try {
    const result = await api("/api/app/connectors/biblio/verify", {
      method: "POST",
      body: biblioInventoryForm(file),
    });
    state.biblioCompareCheckedFile = file;
    state.biblioCompareResult = result;
    $("#import-biblio").disabled = false;
    message.innerHTML = biblioComparisonHtml(result);
    await connections();
  } catch (error) {
    message.textContent = "Comparison failed: " + error.message;
    flash(error.message, true);
  } finally {
    button.disabled = false;
  }
};

$("#import-biblio").onclick = async () => {
  const file = $("#biblio-import-file").files[0];
  if (!file || state.biblioCompareCheckedFile !== file) {
    return flash("Compare the selected BIBLIO file before applying it.", true);
  }
  const authoritative = Boolean($("#biblio-import-authoritative")?.checked);
  const message = authoritative
    ? "This changes dashboard listing records using the BIBLIO file. Because you selected COMPLETE inventory, books missing from that file will also be marked inactive locally. This does NOT edit BIBLIO. Continue?"
    : "This imports BIBLIO listing information into your dashboard. Books missing from the file remain unchanged. This does NOT edit BIBLIO. Continue?";
  if (!window.confirm(message)) return;
  const button = $("#import-biblio");
  const resultBox = $("#biblio-compare-result");
  button.disabled = true;
  resultBox.textContent = "Updating dashboard listing records from the selected BIBLIO file…";
  try {
    const result = await api("/api/app/connectors/biblio/import", {
      method: "POST",
      body: biblioInventoryForm(file, authoritative),
    });
    resultBox.innerHTML = '<strong>BIBLIO file applied to the dashboard.</strong>'
      + '<p>' + esc(biblioReconciliationText(result)) + '</p>'
      + '<p>This changed local BIBLIO listing records, not your live BIBLIO account.</p>';
    state.biblioCompareCheckedFile = null;
    state.biblioCompareResult = null;
    flash("Dashboard listing records updated. No files were sent to BIBLIO.");
    await connections();
  } catch (error) {
    resultBox.textContent = "Could not apply file: " + error.message;
    button.disabled = false;
    flash(error.message, true);
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

function diagnosticsSummaryHtml(data) {
  const worker = data?.worker || {};
  const jobs = data?.jobs || [];
  const failedJobs = jobs.filter((row) => row.status === "failed" || row.error).length;
  const connectorRuns = data?.connector_runs || [];
  const failedRuns = connectorRuns.filter((row) => row.status === "error" || row.status === "failed" || row.error).length;
  return '<div class="diagnostics-pills">'
    + '<span>Environment <strong>' + esc(data?.runtime?.environment || "unknown") + '</strong></span>'
    + '<span>Worker <strong>' + esc(worker.healthy ? "healthy" : "unhealthy") + '</strong></span>'
    + '<span>Recent job errors <strong>' + esc(failedJobs) + '</strong></span>'
    + '<span>Connector errors <strong>' + esc(failedRuns) + '</strong></span>'
    + '</div>';
}

function diagnosticsSeverity(value, fallback = "info") {
  const normalized = String(value || "").toLowerCase();
  if (/\b(error|critical|fatal|exception|traceback|failed|failure)\b/.test(normalized)) return "error";
  if (/\b(warn|warning|degraded|retry|retrying)\b/.test(normalized)) return "warning";
  return fallback;
}

function formatDiagnostics(data) {
  const rows = [];
  (data?.logs || []).forEach((row) => {
    rows.push({
      severity: diagnosticsSeverity(row.line),
      text: "[" + (row.source || "server") + "] " + (row.line || ""),
    });
  });
  (data?.jobs || []).slice().reverse().forEach((row) => {
    rows.push({
      severity: row.error ? "error" : diagnosticsSeverity(row.status),
      text: "[job] " + (row.created_at || "") + " " + row.type + " " + row.status
        + (row.error ? " · " + row.error : ""),
    });
  });
  (data?.connector_runs || []).slice().reverse().forEach((row) => {
    rows.push({
      severity: row.error ? "error" : diagnosticsSeverity(row.status),
      text: "[connector] " + (row.started_at || "") + " " + row.channel + "/" + row.type + " " + row.status
        + (row.error ? " · " + row.error : ""),
    });
  });
  state.browserLogs.forEach((row) => {
    rows.push({
      severity: diagnosticsSeverity(row.level),
      text: "[browser] " + row.at + " " + row.level.toUpperCase() + " " + row.event
        + (row.detail ? " · " + row.detail : ""),
    });
  });
  return rows.slice(-700);
}

function renderDiagnosticsRows() {
  const logWindow = $("#diagnostics-log-window");
  if (!logWindow) return;
  const visible = state.diagnosticsRows.filter((row) => state.diagnosticsLevelFilter.has(row.severity));
  if (!visible.length) {
    logWindow.innerHTML = '<div class="diagnostics-log-empty">No matching log entries.</div>';
    return;
  }
  logWindow.innerHTML = visible.map((row) =>
    '<div class="diagnostics-log-line diagnostics-log-' + row.severity + '">'
      + '<span class="diagnostics-log-level">' + esc(row.severity.toUpperCase()) + '</span>'
      + '<span class="diagnostics-log-text">' + esc(row.text) + '</span>'
    + '</div>'
  ).join("");
}

async function refreshDiagnostics() {
  if (!state.diagnosticsDevConsole || state.view !== "settings") return;
  try {
    const data = await api("/api/app/diagnostics/logs?limit=350");
    $("#diagnostics-summary").innerHTML = diagnosticsSummaryHtml(data);
    state.diagnosticsRows = formatDiagnostics(data);
    renderDiagnosticsRows();
    $("#diagnostics-log-window").scrollTop = $("#diagnostics-log-window").scrollHeight;
    $("#diagnostics-live-status").textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (error) {
    $("#diagnostics-live-status").textContent = error.message;
  }
}

async function loadDiagnostics() {
  const data = await api("/api/app/diagnostics/status");
  $("#diagnostics-summary").innerHTML = diagnosticsSummaryHtml(data);
  state.diagnosticsDevConsole = Boolean(data.dev_console);
  $("#diagnostics-live").classList.toggle("hidden", !state.diagnosticsDevConsole);
  $("#diagnostics-refresh").classList.toggle("hidden", !state.diagnosticsDevConsole);
  if (state.diagnosticsDevConsole) {
    await refreshDiagnostics();
    if (state.diagnosticsTimer) clearInterval(state.diagnosticsTimer);
    state.diagnosticsTimer = setInterval(refreshDiagnostics, 2500);
  }
}

async function downloadDiagnostics() {
  diagnosticLog("info", "diagnostics.download", "Preparing diagnostics bundle");
  const response = await fetch("/api/app/diagnostics/download", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf(),
    },
    body: JSON.stringify({ browser_logs: state.browserLogs.slice(-1000) }),
  });
  if (!response.ok) {
    let detail = "Could not download diagnostics";
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {}
    diagnosticLog("error", "diagnostics.download_failed", "HTTP " + response.status);
    throw new Error(detail);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="([^"]+)"/i);
  const filename = match?.[1] || "reseller-dashboard-diagnostics.zip";
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(href);
  diagnosticLog("info", "diagnostics.download_complete", filename);
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
  try {
    await loadDiagnostics();
  } catch (error) {
    $("#diagnostics-summary").innerHTML = '<p class="error">' + esc(error.message) + '</p>';
    diagnosticLog("error", "diagnostics.status_failed", error.message);
  }
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

$("#diagnostics-refresh").onclick = () => refreshDiagnostics();
$$("[data-diagnostics-level]").forEach((button) => {
  button.onclick = () => {
    const level = button.dataset.diagnosticsLevel;
    if (!["error", "warning", "info"].includes(level)) return;
    if (state.diagnosticsLevelFilter.has(level)) {
      state.diagnosticsLevelFilter.delete(level);
    } else {
      state.diagnosticsLevelFilter.add(level);
    }
    button.classList.toggle("active", state.diagnosticsLevelFilter.has(level));
    button.setAttribute("aria-pressed", state.diagnosticsLevelFilter.has(level) ? "true" : "false");
    renderDiagnosticsRows();
    $("#diagnostics-log-window").scrollTop = $("#diagnostics-log-window").scrollHeight;
  };
});
$("#diagnostics-download").onclick = async () => {
  try {
    await downloadDiagnostics();
    flash("Diagnostics bundle downloaded.");
  } catch (error) {
    flash(error.message, true);
  }
};

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
