# Reseller Dashboard

A self-hostable reseller inventory and analytics platform with a Vinted-first
Chrome bridge, generic master inventory, CSV/TSV/XLSX import/export and
optional marketplace connectors.

The commercial architecture is category-neutral. Clothing is a normal/default
reseller use case, while book metadata and the existing BIBLIO workflow remain
fully supported for book sellers.

## Quick start

\`\`\`bash
git clone https://github.com/tpepels/vinted-dashboard.git
cd vinted-dashboard
cp .env.example .env
docker compose up -d --build
\`\`\`

Open \`http://localhost:5050\` or your server's LAN address.

The container automatically runs Alembic migrations. The default database is
SQLite, so a fresh local install needs no separate database service. A small
worker starts after the web health check and processes queued connector jobs.

Useful checks:

\`\`\`bash
curl http://localhost:5050/api/health
curl http://localhost:5050/api/ready
docker compose ps
docker compose logs -f vinted-dashboard worker
\`\`\`

## Product model

The core object is a physical inventory item, not a marketplace listing:

\`\`\`text
Inventory item / physical copy
  SKU
  title
  quantity
  condition
  cost/location
  category-specific metadata

  Channel listings
    Vinted
    eBay
    BIBLIO
    Etsy / WooCommerce
    Shopify / BigCommerce
    future connectors
\`\`\`

Books can carry ISBN, author, publisher, edition, binding and publication year.
Clothing can carry brand, size, colour, material and measurements. Marketplace
records are linked underneath the same physical item.

The **Add stock** workflow is the primary intake entry point. It routes sellers
to barcode scanning, photo-assisted intake, file import, marketplace sync or a
manual single-item form.

Barcode intake supports keyboard-style USB/Bluetooth scanners, a live phone or
laptop camera, and a captured barcode photo. Browsers with BarcodeDetector use
it first; camera frames fall back to authenticated server-side ZXing-C++ decode
so scanning does not depend on BarcodeDetector support. Scanning itself is
non-blocking: the barcode is queued, acknowledged and the input is ready for the
next item before ISBN enrichment starts. Two background enrichment workers fill
in metadata while scanning continues.

The unfinished scan batch and its defaults are persisted in browser local
storage, so an accidental reload does not discard the session. After barcode
mode has been used it reopens directly on the scanner. Ready rows can be committed
without waiting for unidentified rows, which remain in the batch for review.
Keyboard intake supports Ctrl/Cmd+Z or the Undo last button. Camera intake uses a
frame-aware latch so one stationary barcode is added only once, while a second
physical copy with the same barcode can be scanned after the first leaves the
frame.

A scan creates one physical-copy row in a batch - scanning the same ISBN twice
deliberately creates two separate master items with separate generated SKUs,
condition, cost and location.

Valid ISBN-10/13 scans are enriched from Open Library's low-volume ISBN API with
title, author, publisher, edition/format, year and cover metadata when available.
The scanned ISBN remains usable when enrichment is unavailable. Scanning a QR
payload such as `RDLOC:BOX-17` changes the current storage location for subsequent
items instead of creating inventory.

The **Photograph item** path reuses the Quick listing workflow. With the optional
photo assistant enabled, the user explicitly selects product photos and clicks
Analyze; the server suggests visible facts, title and description, then the UI
asks for the remaining category-specific measurements and asking price. Selected
photo bytes are not persisted by the dashboard. The final Vinted step is still a
manual handoff for ordinary accounts: the product does not publish, relist, like
or message on the user's behalf.

All commercial data is workspace-scoped. Users may belong to multiple
workspaces; API access checks membership before exposing or mutating inventory.

## Existing installations and migration

Older Vinted/BIBLIO/eBay SQLite files are supported only as **read-only migration
input**. They are not part of the runtime application.

`python -m app.legacy_migration` performs an additive, idempotent backfill into
the workspace/master-inventory schema. Existing legacy files are never modified.
Runtime startup does not run this by default; `scripts/upgrade.sh` performs an
explicit idempotent backfill check for upgraded self-hosted installations.
`RUN_LEGACY_BACKFILL=true` is available only when an installation explicitly
needs the import to happen during startup.

On an upgraded personal installation, the first real account registration
claims the unclaimed bootstrap owner, so migrated history stays attached to
that account.

There is no classic UI, legacy API, legacy browser-sync store or legacy Chrome
extension in the runtime. The workspace database is the single source of truth.

## Chrome bridge

`app/extension` is the single Manifest V3 Chrome bridge. It:

- pairs with a workspace using a short-lived one-time code;
- stores only a revocable dashboard bridge token;
- reads the inventory/order/analytics data needed by the product from the
  user's signed-in Vinted tab;
- sends snapshots directly to the paired workspace;
- supports manual and periodic sync;
- does not export the user's Vinted password or raw cookie values;
- contains no unattended destructive Vinted actions.

For local beta testing, download the bridge from the running dashboard. The
stable route is:

```text
/downloads/reseller-chrome-bridge.zip
```

The downloaded artifact itself is always versioned, for example:

```text
reseller-dashboard-chrome-bridge-v3.2.0.zip
```

Extract it, open `chrome://extensions`, enable Developer mode, choose **Load
unpacked**, then use **Connections -> Vinted -> Pair Chrome** in the web app.

The bridge supports the principal European Vinted web origins. On its first
sync it uses an already-open signed-in Vinted tab and remembers that origin for
later periodic syncs.

For active/reserved/hidden/draft listings, bridge 3.2.0 keeps a local detail
cache for richer Vinted item metadata. Core inventory pagination is paced and
retries HTTP 429 responses with backoff. Rich `/api/v2/items/{id}` enrichment
is deliberately budgeted separately - up to 12 records on a manual sync and 4
on a periodic sync, one paced request stream with at least 1.5 seconds between
detail calls rather than hundreds of concurrent requests. If rich-detail enrichment is rate-limited, the complete core
inventory snapshot still syncs and optional notification/order calls are skipped
for that pass. If the core inventory endpoint itself remains rate-limited after
backoff, the bridge aborts instead of sending a partial inventory snapshot.

When Vinted does not expose a trustworthy absolute posting timestamp, the bridge
reads the visible `Uploaded` value from the **rendered** Vinted item page, for
example `5 weeks ago`. Missing ages form one finite persisted job. A single
minimized worker window uses 4 reusable tabs. Tab navigation is globally spaced
by at least 1.8 seconds, the queue is checkpointed after every wave, and each
service-worker event handles a bounded number of waves so Chrome can
terminate/restart the Manifest V3 worker without losing progress. The same
window is reused until the finite queue finishes, then it is closed. If a
rendered page shows a Vinted rate-limit or anti-bot challenge, the window closes
and the job pauses for a persisted cooldown (starting at 30 minutes and backing
off up to 6 hours) rather than continuing to hit the site. Successful item IDs
are cached and are not rescanned. Ordinary unread pages are cooldown-marked for
24 hours on periodic syncs; an explicit manual sync can retry them after the
current finite job completes. Periodic inventory sync is skipped while an age
job is active, so the bridge does not add API traffic while the rendered-page
job is still running.

The dashboard and server accept Vinted relative age only when it came from the
rendered page collector (`vinted_page_*`). Generic API-relative ages,
`first_seen_at`, and old `Today` fallbacks are never treated as posting age.
Bridge 3.2.0 also performs a content-script protocol handshake and reloads an
already-open Vinted tab once when it is still running code from an older bridge.
The content script itself is idempotent: if Chrome or the service worker injects
it again into the same Vinted tab, a protocol guard exits before redeclaring
cache constants or registering a second message listener. Bridge 3.2.0 uses
content protocol 7, so tabs still running an older bridge script are forcibly
reloaded once.


Age parsing is isolated in `app/extension/vinted_age.js`; the finite rendered
page burst stays in `background.js`; normal Vinted snapshot collection stays
in `content.js`.

Bridge releases follow a visible-version invariant: the downloaded ZIP filename
contains the manifest version, the extension popup displays
`Chrome Bridge vX.Y.Z`, and the dashboard displays the current bridge version.
The manifest is authoritative for these user-visible values.


## Import / export

The product UI supports CSV, TSV and XLSX inventory imports.

Flow:

1. upload a file;
2. detect headers/delimiter/sheet;
3. map incoming columns;
4. preview new/updated/unchanged/conflicting records;
5. optionally preview items that would be archived by a full snapshot;
6. confirm and apply transactionally.

Mappings can be saved as reusable presets. Malformed or duplicate-SKU imports
are blocked instead of partially applied.

Inventory exports are available as CSV or XLSX. They can export the master
inventory or a channel-specific view with marketplace listing IDs, prices,
statuses and URLs. Workspace account-data export is also available under
Settings.

Vinted can act as the source listing for cross-listing. Inventory and linked
Vinted rows expose a single **Cross-list** action - there is never one table
column per marketplace. The destination panel is generated from connector
capabilities and grouped into **Ready to publish**, **Needs setup or review**,
**Already listed** and **Not writable yet**. New connectors therefore appear in
the same panel without changing the inventory/listing table shape. The panel
shows BIBLIO, eBay, Etsy, WooCommerce, Shopify, BigCommerce, Squarespace, Wix
and Depop rather than hiding destinations behind eligibility checks.

The shared cross-list candidate takes title, description, price and photos from
Vinted when available, while SKU and physical stock remain authoritative on the
master InventoryItem. WooCommerce, Shopify and Wix can create remote products
directly from this preflight, including Vinted source images. The created
ChannelListing is then linked back to the same physical InventoryItem so later
sales and stock reconciliation operate on one copy of the item.

BIBLIO keeps its book-specific preflight inside the same destination panel.
When an ISBN is available, ISBN metadata is preferred for bibliographic title
and author; the Vinted description, price and photos remain the commercial
source. Title, author, description, ISBN, publisher, edition, publish date,
price and Book ID stay editable in the preflight even when they were prefilled.
The POST carries those reviewed values explicitly, so a second ISBN lookup cannot
silently replace what the user reviewed. Publisher/edition/publish-date
enrichment is persisted onto the master item when published. Older Vinted books
still classified as `general` qualify from linked ISBN/author/book-category
evidence. If a sparse legacy Vinted book has no such evidence, Cross-list shows
**Mark as book & continue**; that explicit action updates the master category and
opens the real BIBLIO preflight immediately instead of dumping the user into
generic Edit.
Zero stock exposes **Edit stock**, and a conflicting BIBLIO Book ID can be
replaced inline with a server-validated unique ID. Unlinked Vinted listings show
**Link to inventory** and jump directly to reconciliation before any destination
can be published.

When the Vinted source carries photos, up to five are copied automatically to
BIBLIO during the FTP sync. The BIBLIO preflight shows the actual Vinted image
thumbnails and explicitly states that they will upload automatically. The server
downloads only trusted Vinted HTTPS image URLs, converts them to JPG, enforces
BIBLIO's basic image requirements, and uploads them as `BookID.jpg`,
`BookID_1.jpg`, etc. Successful photo sets are fingerprinted so unchanged
photos are not re-uploaded on every sync; partial failures remain pending for
retry.

Vinted taxonomy is used to set the broad master category automatically when an
item is first synced or still classified as `general`. This covers books,
clothing, electronics, home, collectibles, toys/games, media, sports, beauty,
and art/crafts while retaining the original Vinted category separately. A book
with a Vinted book category therefore does not require a manual “mark as book”
confirmation.

For BIBLIO books with an ISBN, the preflight performs the existing Open Library
ISBN lookup even when Vinted already supplied an author. ISBN metadata is the
preferred source for the bibliographic title and author, so marketing-heavy
Vinted titles are not copied blindly. The Vinted listing description remains the
preferred BIBLIO description, and Vinted price/photos remain the preferred
commercial source data.



The product Analytics page reads Vinted listing snapshots directly from the
workspace schema, including view/favourite gains, follower history, sales
revenue and per-listing history. Listings, purchases/sales, inventory,
connections and normal analytics all use the workspace schema directly.

## Connections

Connector behavior is capability-driven rather than inferred from marketplace
names.

Current scope:

- **Vinted** - paired Chrome bridge, listings/orders/history/analytics.
- **CSV / TSV** - generic import/export.
- **Excel** - generic import/export.
- **BIBLIO** - optional book connector with workspace-scoped inventory import,
  FTP connection testing, inventory/delete synchronization, and one-click
  cross-listing from a linked Vinted/master book.
- **eBay** - workspace-scoped active seller inventory synchronization through
  the official Trading API; direct-create remains visible in Cross-list but is
  blocked until category and seller-policy preflight is implemented.
- **WooCommerce / Shopify / Wix** - inventory and order import plus direct
  product creation from the shared Cross-list panel.

BIBLIO and eBay can be configured per workspace in **Connections**. The
existing environment-variable configuration remains supported as a fallback
for the migrated personal/bootstrap workspace.

### BIBLIO personal setup

\`\`\`env
BIBLIO_CURRENCY=EUR
BIBLIO_FTP_HOST=ftp.biblio.com
BIBLIO_FTP_USERNAME=
BIBLIO_FTP_PASSWORD=
BIBLIO_FTP_DIRECTORY=
BIBLIO_FTP_AUTO_SYNC=false
\`\`\`

Keep the first upload manual and verify it in BIBLIOdirect before enabling
automatic FTP sync.

### eBay personal setup

Either set \`EBAY_OAUTH_TOKEN\`, or configure refreshable OAuth with
\`EBAY_CLIENT_ID\`, \`EBAY_CLIENT_SECRET\` and \`EBAY_REFRESH_TOKEN\`.

## Authentication, security and billing

The hosted app uses:

- PBKDF2-SHA256 password hashes;
- opaque revocable server-side web sessions;
- CSRF protection for mutating browser requests;
- workspace membership checks;
- separate revocable Chrome bridge credentials;
- encrypted connector credential payloads;
- rate limiting on login/registration/pairing;
- secure-cookie mode for production;
- account/workspace data export and deletion endpoints.

Local/beta billing is disabled by default:

\`\`\`env
BILLING_ENABLED=false
\`\`\`

Stripe checkout, webhooks and customer-portal plumbing can be enabled later
without changing the product domain model. Chrome Web Store is distribution,
not the payment processor.

See \`docs/security.md\` before public launch.

## Chrome Web Store build

Development package:

\`\`\`bash
python scripts/build_extension.py \
  --mode dev \
  --api-origin http://localhost:5050 \
  --output dist/reseller-chrome-bridge-dev.zip
\`\`\`

Store package:

\`\`\`bash
python scripts/build_extension.py \
  --mode store \
  --api-origin https://YOUR-DOMAIN \
  --version 2.2.0 \
  --output dist/reseller-chrome-bridge.zip
\`\`\`

Store builds require HTTPS, forbid \`<all_urls>\`, reject unresolved build
placeholders and exclude development secrets/source maps.

Tag builds create a Store-ready GitHub Actions artifact. Publishing is a
separate manual workflow action. See \`docs/chrome-web-store.md\`.

## Hosted deployment

The production shape is intentionally small:

\`\`\`text
HTTPS web container
PostgreSQL
small worker
Chrome companion
\`\`\`

A Render reference deployment is provided in \`render.yaml\`. Nothing in the
application depends on Render; any container host with PostgreSQL works.

See \`docs/deployment.md\` for local, Postgres and hosted instructions.

## Self-hosted operations

For the central `~/media-stack` layout, add the worker service once as
documented in `docs/media-stack.md`.

After that, normal upgrades are one command:

```bash
cd ~/media-stack/vinted-dashboard
bash scripts/upgrade.sh --compose-dir ~/media-stack
```

The upgrade command:

1. pulls with `--ff-only`;
2. validates that web and worker services exist;
3. builds the new images;
4. creates a consistent pre-migration SQLite backup;
5. starts the upgraded web + worker;
6. waits for readiness;
7. re-runs the legacy backfill idempotently and proves the worker consumes a
   queued probe job.

To include safe live connector checks:

```bash
bash scripts/upgrade.sh --compose-dir ~/media-stack --live-connectors
```

This tests BIBLIO with an FTP login/PWD only and reads eBay active inventory;
it does not upload to BIBLIO or mutate an eBay listing.

Useful operations:

```bash
python -m app.ops status
python -m app.ops smoke --backfill
python -m app.ops smoke --backfill --live-connectors
python -m app.ops backup
```

See:

- `docs/media-stack.md` for the central Compose worker;
- `docs/backup-restore.md` for backup/restore procedures;
- `docs/deployment.md` for local and hosted deployment.

## Development and tests

\`\`\`bash
python -m pytest -q
node --check app/product_static/app.js
python scripts/build_extension.py \
  --mode store \
  --api-origin https://example.invalid \
  --output /tmp/chrome-bridge.zip
\`\`\`

CI additionally runs Alembic to head and builds the Docker image.

## Vinted platform boundary

The commercial extension is intentionally not designed as an automation or
anti-bot product. Do not add bot-evasion, automated likes/messages, mass
relisting or unattended destructive Vinted actions. Browser-side Vinted logic
is isolated behind the connector/bridge so it can be changed or disabled
without affecting master inventory, import/export, eBay/BIBLIO or account data.


## General marketplace integrations

The workspace connector catalog includes Etsy, WooCommerce, Shopify,
BigCommerce, Squarespace, Wix and Depop in addition to Vinted, eBay and BIBLIO.

- Etsy uses Open API v3 and reads active listings plus seller receipts/order
  lines. Save the app keystring, shared secret and Shop ID, then use the
  dashboard OAuth/PKCE flow for `listings_r` and `transactions_r`; manual
  access/refresh tokens remain available as a fallback.
- WooCommerce uses the WC REST API v3 over HTTPS. Cross-listing requires a
  read/write REST API key; Vinted title, description, price, stock, SKU and up
  to five image URLs are carried into the created simple product.
- Shopify uses the GraphQL Admin API. Cross-listing uses `productSet` with
  `write_products`, the store's active inventory location, Vinted image files,
  SKU, price and physical quantity. Import/order access still uses the
  corresponding read scopes.
- BigCommerce uses the REST Management API through
  `api.bigcommerce.com/stores/{store_hash}`. Configure a store hash and OAuth
  access token with read-only Products and Orders permissions.
- Squarespace uses Products API v2 plus the Inventory and Orders APIs. Configure
  an API key with read-only Products, Inventory and Orders permissions, or an
  OAuth access token. Variant IDs remain the remote listing identity while SKU
  drives deterministic master-inventory matching.
- Wix uses Catalog V3, Inventory V3 and eCommerce Orders. Cross-listing calls
  Create Product With Inventory with Product/Inventory write permissions and
  carries Vinted images as external product media. Product ID + variant ID is
  the remote listing identity.
- Depop uses the private Selling API for approved partners. Direct seller
  integrations use a static per-shop API key; production and staging use
  separate keys and fixed API origins. The connector imports all products and
  orders read-only, uses Depop product ID as the stable listing identity, and
  never stores buyer address details. OAuth is intentionally not exposed until
  a Depop OAuth client has actually been issued.
- BigCommerce and Squarespace remain import-only until their create adapters
  are implemented. Etsy remains import/order-only until the product collects
  its required taxonomy/maker/creation-era fields and requests `listings_w`.
  Depop remains read-only under the current approved-partner contract. These
  destinations still appear in Cross-list with the blocker shown explicitly.
