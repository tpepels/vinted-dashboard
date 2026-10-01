const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  me: null,
  view: "today",
  file: null,
  mapping: {},
  mappings: [],
  inventoryItems: [],
  editItemId: null,
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

async function init() {
  try {
    state.me = await api("/api/auth/me");
    appScreen();
    $("#app-name").textContent = state.me.app_name;
    $("#auth-name").textContent = state.me.app_name;
    $("#workspace-name").textContent = state.me.workspace.name;
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
  const [todayData, analyticsData] = await Promise.all([
    api("/api/app/today"),
    api("/api/app/analytics"),
  ]);
  $("#today-metrics").innerHTML =
    metric("Active inventory", analyticsData.active_inventory)
    + metric("Active listings", analyticsData.active_listings)
    + metric("Sales YTD", analyticsData.sales_ytd_count, money(analyticsData.sales_ytd_cents, analyticsData.currency))
    + metric("Followers", analyticsData.followers == null ? "—" : analyticsData.followers);

  $("#today-actions").innerHTML = todayData.actions.length
    ? todayData.actions.map((row) =>
      '<div class="action-row"><div><strong>' + esc(row.title) + "</strong><p>"
      + esc(row.channel) + " · " + row.age_days + " days old"
      + (row.favourites == null ? "" : " · " + row.favourites + " favourites")
      + (row.favourites_gain == null ? "" : " · +" + row.favourites_gain + " this week")
      + '</p></div><div class="action-tag">' + esc(row.action) + "</div></div>"
    ).join("")
    : '<div class="empty">Nothing needs attention right now.</div>';
}

async function inventory() {
  const q = encodeURIComponent($("#inventory-q").value.trim());
  const status = encodeURIComponent($("#inventory-status").value);
  const data = await api("/api/app/inventory?q=" + q + "&status=" + status);
  state.inventoryItems = data.items;

  $("#inventory-table").innerHTML = data.items.length
    ? '<table><thead><tr><th>Item</th><th>SKU</th><th>Category</th><th>Qty</th><th>Channels</th><th>Status</th><th></th></tr></thead><tbody>'
      + data.items.map((item) =>
        '<tr><td><div class="title">' + esc(item.title) + '</div><div class="sub">'
        + esc(item.condition || "") + (item.location ? " · " + esc(item.location) : "")
        + '</div></td><td>' + esc(item.sku) + "</td><td>" + esc(item.category)
        + "</td><td>" + item.quantity + "</td><td>"
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
}

$("#inventory-q").oninput = () => inventory().catch((error) => flash(error.message, true));
$("#inventory-status").onchange = () => inventory().catch((error) => flash(error.message, true));
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

async function sales() {
  const data = await api("/api/app/sales");
  $("#sales-table").innerHTML = data.sales.length
    ? '<table><thead><tr><th>Date</th><th>Channel</th><th>Item</th><th>Direction</th><th>Status</th><th>Amount</th></tr></thead><tbody>'
      + data.sales.map((sale) =>
        "<tr><td>" + esc(when(sale.occurred_at)) + '</td><td><span class="pill '
        + esc(sale.channel) + '">' + esc(sale.channel) + "</span></td><td>"
        + esc(sale.title || "—") + "</td><td>" + esc(sale.direction) + "</td><td>"
        + esc(sale.status || "—") + "</td><td>" + money(sale.total_cents, sale.currency) + "</td></tr>"
      ).join("")
      + "</tbody></table>"
    : '<div class="empty">No sales recorded yet.</div>';
}

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

async function analytics() {
  const days = Number($("#analytics-days").value || 90);
  const [summary, history] = await Promise.all([
    api("/api/app/analytics"),
    api("/api/app/analytics/history?days=" + days),
  ]);
  $("#analytics-metrics").innerHTML =
    metric("Active inventory", summary.active_inventory)
    + metric("Active listings", summary.active_listings)
    + metric("Sales YTD", summary.sales_ytd_count, money(summary.sales_ytd_cents, summary.currency))
    + metric("Followers", summary.followers == null ? "—" : summary.followers, summary.following == null ? "" : summary.following + " following");

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
  $("#mapping-grid select").forEach((select) => {
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
  $("#preview").innerHTML =
    '<div class="card-head"><h2>Preview - nothing written yet</h2></div><div class="preview-stats">'
    + "<span>" + (counts.new || 0) + " new</span>"
    + "<span>" + (counts.update || 0) + " updates</span>"
    + "<span>" + (counts.unchanged || 0) + " unchanged</span>"
    + "<span>" + (counts.conflict || 0) + " conflicts</span>"
    + "<span>" + (data.missing_existing_count || 0) + " would archive</span></div>";
  $("#apply-import").disabled = !data.can_apply;
}

$("#apply-import").onclick = async () => {
  if (!state.file) return;
  const form = new FormData();
  form.append("file", state.file);
  form.append("mapping_json", JSON.stringify(state.mapping));
  form.append("full_snapshot", $("#full-snapshot").checked ? "true" : "false");
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
