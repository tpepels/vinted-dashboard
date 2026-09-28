const DASHBOARD_URL = "http://media-server:5050";
const VINTED_URL = "https://www.vinted.pt/";
let syncInFlight = null;

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function setStatus(value) {
  await chrome.storage.local.set({ syncStatus: value });
}

async function getStatus() {
  const stored = await chrome.storage.local.get(["syncStatus"]);
  return stored.syncStatus || null;
}

async function waitForTab(tabId, timeoutMs = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return tab;
    await sleep(300);
  }
  throw new Error("Vinted tab did not finish loading.");
}

async function ensureContentScript(tabId) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "collect-vinted-data" });
    return response;
  } catch (error) {
    const message = String(error?.message || error || "");
    if (!message.includes("Receiving end does not exist")) throw error;

    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"]
    });
    await sleep(250);
    return await chrome.tabs.sendMessage(tabId, { type: "collect-vinted-data" });
  }
}

async function collectFromTab(tabId) {
  let lastError = null;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      const response = await ensureContentScript(tabId);
      if (response?.ok) return response.snapshot;
      if (response?.error) throw new Error(response.error);
    } catch (error) {
      lastError = error;
    }
    await sleep(500);
  }
  throw lastError || new Error("Could not connect to the Vinted page.");
}

async function findOrOpenVintedTab() {
  const tabs = await chrome.tabs.query({ url: "https://www.vinted.pt/*" });
  if (tabs.length) return { tab: tabs[0], temporary: false };

  const tab = await chrome.tabs.create({ url: VINTED_URL, active: false });
  await waitForTab(tab.id);
  return { tab, temporary: true };
}

async function pushSnapshot(snapshot, reason) {
  const response = await fetch(`${DASHBOARD_URL}/api/browser-sync`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(snapshot)
  });

  let body = {};
  try {
    body = await response.json();
  } catch {}

  if (!response.ok) {
    throw new Error(body.detail || `Dashboard returned HTTP ${response.status}`);
  }

  const status = {
    ok: true,
    at: new Date().toISOString(),
    reason,
    listings: body.listings ?? snapshot.listings.length,
    notifications: body.notifications ?? snapshot.notifications.length,
    orders: body.orders ?? snapshot.orders.length
  };
  await setStatus(status);
  return status;
}

async function runSync(reason = "manual") {
  if (syncInFlight) return syncInFlight;

  syncInFlight = (async () => {
    let temporaryTab = null;
    try {
      const { tab, temporary } = await findOrOpenVintedTab();
      if (!tab?.id) throw new Error("No Vinted tab available.");
      if (temporary) temporaryTab = tab.id;

      const ready = await waitForTab(tab.id);
      if (!String(ready.url || "").startsWith("https://www.vinted.pt/")) {
        throw new Error("Chrome is not signed in to vinted.pt.");
      }

      const snapshot = await collectFromTab(tab.id);
      return await pushSnapshot(snapshot, reason);
    } catch (error) {
      const status = {
        ok: false,
        at: new Date().toISOString(),
        reason,
        error: error instanceof Error ? error.message : String(error)
      };
      await setStatus(status);
      return status;
    } finally {
      if (temporaryTab !== null) {
        try {
          await chrome.tabs.remove(temporaryTab);
        } catch {}
      }
      syncInFlight = null;
    }
  })();

  return syncInFlight;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("vinted-dashboard-sync", { periodInMinutes: 10 });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("vinted-dashboard-sync", { periodInMinutes: 10 });
});

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "vinted-dashboard-sync") {
    runSync("periodic");
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (
    changeInfo.status === "complete" &&
    String(tab.url || "").startsWith("https://www.vinted.pt/")
  ) {
    getStatus().then(status => {
      const last = status?.at ? new Date(status.at).getTime() : 0;
      if (Date.now() - last > 60000) runSync("vinted-tab");
    });
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message?.type === "sync-now") {
      sendResponse(await runSync("manual"));
      return;
    }
    if (message?.type === "status") {
      sendResponse({ ok: true, status: await getStatus(), dashboardUrl: DASHBOARD_URL });
      return;
    }
    sendResponse({ ok: false, error: "Unknown message" });
  })();
  return true;
});
