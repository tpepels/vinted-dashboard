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

async function sendContentMessage(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch (error) {
    const detail = String(error?.message || error || "");
    if (!detail.includes("Receiving end does not exist")) throw error;

    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"]
    });
    await sleep(300);
    return await chrome.tabs.sendMessage(tabId, message);
  }
}

async function collectFromTab(tabId) {
  let lastError = null;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      const response = await sendContentMessage(tabId, { type: "collect-vinted-data" });
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

async function getResearchQueue() {
  try {
    const response = await fetch(`${DASHBOARD_URL}/api/market-research/queue?limit=3`);
    if (!response.ok) return [];
    const body = await response.json();
    return Array.isArray(body?.jobs) ? body.jobs : [];
  } catch {
    return [];
  }
}

function marketSearchUrl(query) {
  const url = new URL("/catalog", VINTED_URL);
  url.searchParams.set("search_text", String(query || "").trim());
  url.searchParams.set("order", "relevance");
  return url.href;
}

async function researchMarketJob(job) {
  const jobId = Number(job?.id || 0);
  const listingId = String(job?.listing_id || "");
  const query = String(job?.query || "").trim();
  if (!jobId || !query) {
    return { job_id: jobId || null, error: "Invalid market research job", results: [] };
  }

  let tabId = null;
  try {
    const tab = await chrome.tabs.create({
      url: marketSearchUrl(query),
      active: false
    });
    tabId = tab.id;
    if (!tabId) throw new Error("Could not open Vinted search page.");

    await waitForTab(tabId, 25000);
    await sleep(1200);

    let response = null;
    let lastError = null;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        response = await sendContentMessage(tabId, { type: "scrape-market-page" });
        if (response?.ok) break;
        if (response?.error) throw new Error(response.error);
      } catch (error) {
        lastError = error;
      }
      await sleep(700);
    }
    if (!response?.ok) throw lastError || new Error("Could not read Vinted search results.");

    const results = (response.results || []).filter(row => String(row.id || "") !== listingId);
    if (!results.length) {
      const detail = Number(response.anchors_seen || 0) === 0
        ? "Vinted search page rendered no listing cards."
        : `Vinted page had ${response.anchors_seen} item links but no priced cards could be parsed.`;
      return {
        job_id: jobId,
        error: detail,
        results: [],
        raw_count: Number(response.anchors_seen || 0),
        parsed_count: Number(response.cards_with_price || 0)
      };
    }

    return {
      job_id: jobId,
      results,
      raw_count: Number(response.anchors_seen || 0),
      parsed_count: results.length
    };
  } catch (error) {
    return {
      job_id: jobId,
      error: error instanceof Error ? error.message : String(error),
      results: []
    };
  } finally {
    if (tabId !== null) {
      try {
        await chrome.tabs.remove(tabId);
      } catch {}
    }
  }
}

async function collectMarketResearch(jobs) {
  const results = [];
  for (const job of (Array.isArray(jobs) ? jobs.slice(0, 3) : [])) {
    results.push(await researchMarketJob(job));
    await sleep(350);
  }
  return results;
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

      const researchJobs = await getResearchQueue();
      const snapshot = await collectFromTab(tab.id);
      snapshot.market_results = await collectMarketResearch(researchJobs);
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

function installAlarms() {
  chrome.alarms.create("vinted-dashboard-sync", { periodInMinutes: 10 });
  chrome.alarms.create("vinted-market-research", { periodInMinutes: 2 });
}

chrome.runtime.onInstalled.addListener(() => {
  installAlarms();
});

chrome.runtime.onStartup.addListener(() => {
  installAlarms();
});

chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "vinted-dashboard-sync") {
    runSync("periodic");
    return;
  }
  if (alarm.name === "vinted-market-research") {
    getResearchQueue().then(jobs => {
      if (jobs.length) runSync("market-research");
    });
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
