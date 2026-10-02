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
    help: "Optional book connector. Import your current BIBLIO inventory once, then use FTP for updates and deletes.",
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
    appScreen();
    $("#app-name").textContent = state.me.app_name;
    $("#auth-name").textContent = state.me.app_name;
    $("#workspace-name").textContent = state.me.workspace.name;
    renderBillingLock(state.me.billing);
    await load("today");
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
  state.me = null;
  authScreen("login");
};

$$(".nav").forEach((button) => {
  button.onclick = () => selectView(button.dataset.view);
});

async function selectView(view) {
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
  const [todayData, analyticsData, onboardingData] = await Promise.all([
    api("/api/app/today"),
    api("/api/app/analytics"),
    api("/api/app/onboarding"),
  ]);
  state.onboarding = onboardingData;
  renderOnboarding(onboardingData);

  const activeInventory = Number(analyticsData.active_inventory || 0);
  const pricedCount = Number(analyticsData.priced_inventory_count || 0);
  const marginCount = Number(analyticsData.margin_inventory_count || 0);
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

  const todayActions = Array.isArray(todayData.actions) ? todayData.actions : [];
  $("#today-actions").innerHTML = todayActions.length
    ? todayActions.map((row) =>
      '<div class="action-row"><div><strong>' + esc(row.title) + "</strong><p>"
      + esc(row.channel) + " · " + row.age_days + " days old"
      + (row.favourites == null ? "" : " · " + row.favourites + " favourites")
      + (row.favourites_gain == null ? "" : " · +" + row.favourites_gain + " favourites in 7d")
      + (row.views_gain == null ? "" : " · +" + row.views_gain + " views in 7d")
      + '</p></div><div class="action-tag">' + esc(row.action) + "</div></div>"
    ).join("")
    : '<div class="empty">No listing crosses your action thresholds today.</div>';

  renderStockActions(
    Array.isArray(todayData.cross_channel_actions) ? todayData.cross_channel_actions : []
  );
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

$("#onboarding-import").onclick = () => selectView("imports");
$("#onboarding-connect").onclick = () => selectView("connections");
$("#onboarding-reconcile").onclick = () => selectView("reconcile");

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

function renderStockActions(rows) {
  const actionRows = Array.isArray(rows) ? rows : [];
  const card = $("#stock-actions").closest(".stock-alerts");
  card.classList.toggle("hidden", actionRows.length === 0);
  if (!actionRows.length) {
    $("#stock-alert-count").textContent = "";
    $("#stock-actions").innerHTML = "";
    return;
  }
  $("#stock-alert-count").textContent =
    actionRows.length + " active action" + (actionRows.length === 1 ? "" : "s");
  $("#stock-actions").innerHTML = actionRows.map((row) =>
    '<div class="action-row"><div><strong>' + esc(row.item?.title || row.listing?.title || "Sold item")
    + '</strong><p>' + esc(row.channel) + " · " + esc(row.status)
    + (row.last_error ? " · " + esc(row.last_error) : "")
    + '</p></div><div class="actions compact">' + crossChannelActionControls(row) + "</div></div>"
  ).join("");
  bindCrossChannelButtons(today);
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

  $("#inventory-table").innerHTML = visibleItems.length
    ? '<table><thead><tr><th><input id="inventory-select-all" type="checkbox" aria-label="Select all"></th><th>Item</th><th>SKU</th><th>Category</th><th>Qty</th><th>Location</th><th>Cost</th><th>Ask</th><th>Margin</th><th>Channels</th><th>Status</th><th></th></tr></thead><tbody>'
      + visibleItems.map((item) =>
        '<tr><td><input class="inventory-select" type="checkbox" data-id="' + esc(item.id) + '" aria-label="Select ' + esc(item.title) + '"></td>'
        + '<td><div class="title">' + esc(item.title) + '</div><div class="sub">' + esc(item.condition || "") + '</div></td>'
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
        + "</td><td>" + esc(item.status) + '</td><td class="row-actions"><button class="btn edit-item" data-id="'
        + item.id + '">Edit</button></td></tr>'
      ).join("")
      + "</tbody></table>"
    : '<div class="empty">No inventory yet. Add an item or import a file.</div>';

  $$(".edit-item").forEach((button) => {
    button.onclick = () => openItemForm(state.inventoryItems.find((item) => item.id === button.dataset.id));
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
}

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

$("#quick-listing").onclick = openQuickListing;
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

$("#add-item").onclick = () => openItemForm(null);
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

function age(value) {
  if (!value) return "—";
  const date = new Date(value);
  const timestamp = date.getTime();
  if (Number.isNaN(timestamp)) return "—";
  const days = Math.max(0, Math.floor((Date.now() - timestamp) / 86400000));
  if (days === 0) return "Today";
  if (days === 1) return "1 day";
  if (days < 30) return days + " days";
  if (days < 365) {
    const months = Math.max(1, Math.floor(days / 30.44));
    return months + " month" + (months === 1 ? "" : "s");
  }
  const years = Math.max(1, Math.floor(days / 365.25));
  return years + " year" + (years === 1 ? "" : "s");
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

function listingComparator(sort) {
  const number = (value, fallback = -1) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  if (sort === "oldest") {
    return (a, b) => {
      const ad = timeValue(a.listed_at);
      const bd = timeValue(b.listed_at);
      if (ad && bd) return ad - bd;
      if (ad) return -1;
      if (bd) return 1;
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
    const ad = timeValue(a.listed_at);
    const bd = timeValue(b.listed_at);
    if (ad && bd) return bd - ad;
    if (ad) return -1;
    if (bd) return 1;
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

  $("#listing-stat-count").textContent = String(rows.length);
  $("#listing-stat-value").textContent = money(total, currency);
  $("#listing-stat-average").textContent = money(average, currency);
  $("#listing-stat-median").textContent = money(middle, currency);
  $("#listing-stat-favourites").textContent = String(favourites);
  $("#listing-stat-zero-favourites").textContent = String(zeroFavourites);
  $("#listing-stat-duplicate-groups").textContent = String(duplicates.duplicateGroups.length);
  $("#listing-stat-duplicates").classList.toggle("has-duplicates", duplicates.duplicateGroups.length > 0);
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
  const showDate = filtered.some((row) => timeValue(row.listed_at) > 0);

  $("#listings-table").innerHTML = filtered.length
    ? '<table><thead><tr><th>Listing</th><th>Marketplace</th><th>Status</th>'
      + (showDate ? "<th>Listed</th><th>Age</th>" : "")
      + (showFavourites ? "<th>Favourites</th>" : "")
      + (showViews ? "<th>Views</th>" : "")
      + '<th>Price</th></tr></thead><tbody>'
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
          + (showDate ? "<td>" + dateOnly(row.listed_at) + "</td><td>" + age(row.listed_at) + "</td>" : "")
          + (showFavourites ? "<td>" + esc(row.favourites == null ? "—" : row.favourites) + "</td>" : "")
          + (showViews ? "<td>" + esc(row.views == null ? "—" : row.views) + "</td>" : "")
          + "<td>" + money(row.price_cents, row.currency) + "</td></tr>";
      }).join("")
      + "</tbody></table>"
    : '<div class="empty">No matching listings.</div>';
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
      summary.median_active_age_days == null ? "—" : summary.median_active_age_days + " d")
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
          + '<td data-sort-value="' + esc(new Date(row.listed_at).getTime()) + '">' + esc(when(row.listed_at))
          + (row.listed_at_source === "first_seen" ? '<div class="sub">first observed</div>' : "")
          + '</td>'
          + '<td data-sort-value="' + esc(new Date(row.sold_at).getTime()) + '">' + esc(when(row.sold_at))
          + (row.sold_at_source === "first_seen" ? '<div class="sub">first observed</div>' : "")
          + '</td>'
          + '<td data-sort-value="' + esc(row.days_online) + '">' + esc(row.days_online) + ' d</td>'
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
          + money(row.price_cents, row.currency) + "</div></td><td>" + esc(row.age_days) + " d</td><td>"
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
  $("#connector-grid").innerHTML = data.connectors.map((connector) => {
    const connected = connector.status === "connected";
    const statusClass = connected ? "status-ok" : (connector.configured ? "status-warn" : "");
    const statusText = connected
      ? "Connected"
      : (connector.configured ? "Configured - not synced yet" : "Not configured");
    return '<div class="connector"><h2>' + esc(connector.display_name) + "</h2><p>"
      + esc(connector.description) + '</p><div class="meta ' + statusClass + '">'
      + statusText
      + (connector.last_synced_at ? " · " + esc(when(connector.last_synced_at)) : "")
      + '</div><div class="actions">'
      + (connector.channel === "vinted"
        ? '<button class="btn primary pair">Pair Chrome</button><a class="btn" href="/downloads/reseller-chrome-bridge.zip">Download dev bridge</a>'
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

  $$(".pair").forEach((button) => { button.onclick = pair; });
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
    await api("/api/app/connectors/" + channel + "/credentials", {
      method: "PUT",
      body: JSON.stringify({ values }),
    });
    $("#connector-config-status").textContent = "Connection settings saved.";
    flash(connectorSchemas[channel].title + " configured.");
    await connections();
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

async function pair() {
  try {
    const result = await api("/api/app/extension/pairings", { method: "POST" });
    $("#pair-code").textContent = result.code;
    $("#pairing").classList.remove("hidden");
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
