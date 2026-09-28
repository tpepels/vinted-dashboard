async function collectCurrentVintedUser() {
  const response = await fetch("/api/v2/users/current", {
    headers: { "Accept": "application/json" }
  });
  if (!response.ok) {
    throw new Error(`Vinted returned HTTP ${response.status}`);
  }
  const payload = await response.json();
  const user = payload?.user || payload || {};
  return {
    id: String(user.id || ""),
    username: String(user.login || user.username || "")
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "collect-vinted-user") return;
  collectCurrentVintedUser()
    .then(user => sendResponse({ ok: true, user }))
    .catch(error => sendResponse({
      ok: false,
      error: error instanceof Error ? error.message : String(error)
    }));
  return true;
});
