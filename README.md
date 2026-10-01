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

## Data layer (SQLAlchemy + Alembic)

Alongside the original hand-rolled sqlite3 tables (`app/channels.py`, `app/intelligence.py`), the app now also has an ORM-backed schema (`app/models.py`) built around a `Workspace` and a channel-agnostic `InventoryItem`/`ChannelListing` - the foundation for multi-channel/multi-tenant features in later phases. The legacy tables are untouched and still power the current UI; nothing here changes existing behavior.

- **Default (zero-config):** a local sqlite file at `./data/app.sqlite3`, alongside the existing `./data/vinted-history.sqlite3`. No extra services required.
- **Optional Postgres:** run `docker compose --profile postgres up -d --build` to also start a bundled `db` service, and set `DATABASE_URL` in `.env` to point at it (see `.env.example`).

Migrations run automatically on container start (the entrypoint runs `alembic upgrade head`, retrying while a freshly-started Postgres is still coming up). To run them manually, e.g. outside Docker:

```bash
alembic upgrade head
```

### Backfilling existing data

The first time the app starts against an empty new-schema database, it also runs a one-time, best-effort, idempotent backfill (`python -m app.legacy_migration`) that copies everything from the existing `vinted-history.sqlite3` (Vinted/BIBLIO/eBay inventory, sync history, favourites, orders, market research) into the new schema under a single bootstrap workspace. It is safe to re-run on every start: already-migrated rows are matched by their original natural key and only updated in place, never duplicated. A fresh install with no legacy database simply skips it.

The bootstrap workspace/owner can be customized via `.env` (see `BOOTSTRAP_WORKSPACE_NAME`/`BOOTSTRAP_WORKSPACE_SLUG`/`BOOTSTRAP_OWNER_EMAIL`); the legacy file location defaults to `VINTED_HISTORY_DB` and can be overridden separately via `LEGACY_SQLITE_PATH` if needed.

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

## Multi-channel stock

The **Stock** view tracks active inventory across Vinted, BIBLIO and eBay.

### Vinted

Vinted stock is written automatically whenever the Chrome sync extension sends a fresh snapshot.

### BIBLIO

BIBLIO is integrated through its official FTP inventory workflow.

Configure the FTP account only in the server-side `.env`:

```env
BIBLIO_CURRENCY=EUR
BIBLIO_FTP_HOST=ftp.biblio.com
BIBLIO_FTP_USERNAME=
BIBLIO_FTP_PASSWORD=
BIBLIO_FTP_DIRECTORY=
BIBLIO_FTP_TIMEOUT_SECONDS=20
BIBLIO_FTP_FILENAME_PREFIX=vinted-dashboard
BIBLIO_FTP_AUTO_SYNC=false
```

Recommended first setup:

1. In BIBLIOdirect, request a complete active inventory download.
2. Import that tab-delimited file once in **Stock -> BIBLIO -> Initial import / reconciliation**. BIBLIO's own download includes Book ID/SKU, author, title, description, price, status, ISBN and quantity, so it gives the local database enough data to safely reproduce inventory files.
3. Click **Test FTP**.
4. Click **Upload now** once and verify the upload in BIBLIOdirect's Upload History.
5. After the format has been accepted by BIBLIO, set `BIBLIO_FTP_AUTO_SYNC=true` and rebuild/restart the container.

The generated active inventory file is tab-delimited and contains the required Book ID, Author, Title, Description and Price fields, plus Status, ISBN and Quantity.

Inactive/sold records are sent separately in a filename containing `deletes`, which is BIBLIO's documented FTP convention for delete-only uploads. Successfully sent deletes are not resent forever.

The connector deliberately refuses to upload active listings that are missing BIBLIO's required fields. If automatic sync is enabled and only deletes are pending, those deletes can still be sent safely while the active upload waits for a complete initial import.

The old file import remains available as a reconciliation/restore path.

### eBay

eBay stock uses the official Trading API `GetMyeBaySelling` call and retrieves the authenticated seller's active listings.

For a short-lived setup, provide a current user OAuth access token:

```env
EBAY_OAUTH_TOKEN=
```

For automatic access-token renewal, configure:

```env
EBAY_CLIENT_ID=
EBAY_CLIENT_SECRET=
EBAY_REFRESH_TOKEN=
EBAY_SITE_ID=0
EBAY_COMPATIBILITY_LEVEL=1477
```

Keep eBay client secrets and tokens only in the server-side `.env`; never commit them.

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
