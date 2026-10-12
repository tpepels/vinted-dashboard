const $ = selector => document.querySelector(selector);
const send = payload => chrome.runtime.sendMessage(payload);

function show(text, error = false) {
  $("#status").textContent = text;
  $("#status").className = "status" + (error ? " error" : "");
}

function pausedUntil(status) {
  const until = Number(status?.api_cooldown_until || 0);
  return until > Date.now() ? new Date(until).toLocaleString() : null;
}

function syncDetails(status) {
  const panel = $("#sync-details");
  const text = $("#detail-info");
  const pending = Number(status.detail_pending ?? status.detail_deferred ?? 0);
  const enriched = Number(status.detail_enriched || 0);
  const paused = pausedUntil(status);
  const notes = [];
  if (pending > 0) {
    notes.push(pending + " listings still need optional details checked on Vinted. "
      + "Your basic listing update has already been sent. "
      + "To reduce the risk of Vinted blocking access, the bridge checks up to three "
      + "additional listings per update.");
  }
  if (enriched > 0) notes.push("Extra details checked for " + enriched + " listings in the last update.");
  if (paused) notes.push("Vinted has limited requests. More checks are paused until " + paused + ".");
  const unread = Number(status.age_scan_failed || 0);
  if (unread) notes.push(unread + " older listing dates could not be checked.");
  panel.classList.toggle("hidden", notes.length === 0);
  text.textContent = notes.join(" ");
}

async function refresh() {
  const response = await send({type:"status"});
  $("#pair").classList.toggle("hidden", !!response.paired);
  $("#paired").classList.toggle("hidden", !response.paired);
  const version = chrome.runtime.getManifest().version;
  $("#bridge-version").textContent = "Chrome Bridge v" + version;
  if (!response.paired) {
    $("#sync-details").classList.add("hidden");
    return show("Not connected. Enter the pairing code shown in your dashboard.");
  }
  const status = response.status || {};
  const last = status.at ? new Date(status.at).toLocaleString() : null;
  const paused = pausedUntil(status);
  if (status.ok === false) {
    show("Last update failed. " + (status.error || "Check the Vinted tab and try later."), true);
  } else if (last) {
    const count = Number(status.listings || 0);
    const summary = count + " Vinted listing" + (count === 1 ? "" : "s") + " synced";
    show(summary + "\nLast checked: " + last
      + (paused ? "\nExtra checks paused until " + paused : ""));
  } else {
    show("Connected to " + (response.workspace || "your dashboard") + ". No update yet.");
  }
  syncDetails(status);
  if (response.error) {
    // Cached status may still be usable, but we could not check the server.
    show("Cannot reach the dashboard right now. Last saved update: "
      + (last || "none") + ".", true);
  }
}

$("#pair-btn").onclick = async () => {
  show("Connecting browser…");
  const response = await send({type:"pair", code:$("#code").value});
  if (response?.ok === false || response?.error) {
    return show(response.error || "Could not connect browser.", true);
  }
  await refresh();
};

$("#sync").onclick = async () => {
  const button = $("#sync");
  button.disabled = true;
  show("Checking Vinted for changes…");
  try {
    const result = await send({type:"sync-now"});
    if (result?.sync_skipped_for_api_cooldown) {
      return show("Vinted checks are paused until "
        + new Date(result.api_cooldown_until).toLocaleString() + ".");
    }
    if (result?.sync_skipped_for_interval) {
      return show("Vinted was checked recently. Next check after "
        + new Date(result.next_sync_at).toLocaleTimeString() + ".");
    }
    if (!result?.ok) return show(result?.error || "Could not check Vinted.", true);
    await refresh();
  } catch (error) {
    show("Could not check Vinted: " + String(error.message || error), true);
  } finally {
    button.disabled = false;
  }
};

$("#dashboard").onclick = async () => {
  const response = await send({type:"status"});
  chrome.tabs.create({url:response.apiOrigin});
};

$("#unpair").onclick = async () => {
  await send({type:"unpair"});
  await refresh();
};

refresh();
