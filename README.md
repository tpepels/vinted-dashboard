# Vinted Dashboard

A local Docker dashboard that reads directly from Vinted.

Configured for:

- Vinted profile: https://www.vinted.pt/member/58344842
- User ID: `58344842`

There is no Gmail/IMAP integration, CSV import, or locally reconstructed order history. Listings, notifications and transaction/order state come from Vinted.

## Data sources

The dashboard uses the same Vinted web endpoints that the Vinted site uses:

- public profile + listings: `/api/v2/users/{id}` and `/api/v2/wardrobe/{id}/items`
- authenticated account: `/api/v2/users/current`
- notifications: `/api/v2/notifications` (with a web-notifications fallback)
- buy/sell orders: `/api/v2/my_orders?type=sold|purchased&status=all`
- conversation/inbox data is used only as a compatibility fallback if `my_orders` is unavailable

Vinted does not document these web endpoints as a stable public API, so they can change. All endpoint-specific code is isolated in `app/vinted.py`.

## Run

```bash
git clone https://github.com/tpepels/vinted-dashboard.git
cd vinted-dashboard
cp .env.example .env
mkdir -p data
docker compose up -d --build
```

Open:

```
http://SERVER_IP:5050
```

The public Listings view can work without an authenticated session. Notifications and buy/sell orders are read directly from Vinted's authenticated web API and require your Vinted web session.

## Chrome session sync (recommended)

The easiest setup is the bundled Chrome extension. Open the dashboard, click **Chrome sync / session**, then **Download Chrome extension ZIP**.

Install it once:

1. Extract the ZIP somewhere permanent.
2. Open `chrome://extensions`.
3. Enable **Developer mode**.
4. Click **Load unpacked** and select the extracted folder.
5. Click the **Vinted Dashboard Sync** extension and choose **Sync now**.

The extension is deliberately narrow: it runs only on `www.vinted.pt` and can talk only to `http://media-server:5050`. It does **not** export Vinted cookie values. Instead it reads the same Vinted JSON data that the signed-in page can access and sends the resulting listings, orders and notifications to the local dashboard.

It syncs every 10 minutes. If no Vinted tab is open, it briefly opens an inactive Vinted tab, collects the data, and closes it again. The server uses a fresh Chrome snapshot in preference to its own Vinted session and falls back to the server-side session when Chrome sync is stale or unavailable.

Direct download from the running dashboard:

```
http://media-server:5050/downloads/vinted-session-sync.zip
```

## Manual Vinted session fallback

This dashboard is read-only. It does not need your Vinted password.

From a browser where you are already signed in to `vinted.pt`:

1. Open DevTools -> Network.
2. Reload Vinted.
3. Select a request to `www.vinted.pt`.
4. Copy the request's complete `Cookie` header.
5. Open the dashboard and click **Vinted session**.
6. Paste the Cookie header and save it.

The cookie is stored only in `./data/vinted-session.cookie` on your server. That file is ignored by git and the container sets restrictive permissions.

Alternatively set the complete cookie header in `.env`:

```env
VINTED_COOKIE=access_token_web=...; refresh_token_web=...; ...
```

You can also configure the two main token cookies separately:

```env
VINTED_ACCESS_TOKEN_WEB=
VINTED_REFRESH_TOKEN_WEB=
```

Do not commit any of these values.

## Vinted / Cloudflare blocking

The app deliberately refreshes slowly and caches Vinted responses for 60 seconds. If Vinted returns 403 or 429, the dashboard reports that state instead of retrying aggressively.

Running it from your home server is preferable to a datacenter/VPS IP.

## Configuration

```env
VINTED_BASE_URL=https://www.vinted.pt
VINTED_USER_ID=58344842
VINTED_PROFILE_URL=https://www.vinted.pt/member/58344842
VINTED_USERNAME=tom_waits
VINTED_CACHE_SECONDS=60
VINTED_TIMEOUT_SECONDS=20
```

Optional:

```env
VINTED_COOKIE=
VINTED_ACCESS_TOKEN_WEB=
VINTED_REFRESH_TOKEN_WEB=
VINTED_ANON_ID=
VINTED_CSRF_TOKEN=
VINTED_USER_AGENT=
```

## Health and diagnostics

```bash
curl http://localhost:5050/api/health
curl http://localhost:5050/api/diagnostics
```

Diagnostics reports which Vinted endpoints respond, without returning your authentication cookie.

## Tests

```bash
python -m pytest -q
```

## Security

This is intended to run on your LAN. Your Vinted session cookie grants access to your account. Do not expose this dashboard directly to the public internet.
