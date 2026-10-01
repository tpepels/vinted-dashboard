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
    future connectors
\`\`\`

Books can carry ISBN, author, publisher, edition, binding and publication year.
Clothing can carry brand, size, colour, material and measurements. Marketplace
records are linked underneath the same physical item.

All commercial data is workspace-scoped. Users may belong to multiple
workspaces; API access checks membership before exposing or mutating inventory.

## Existing installations and migration

The legacy Vinted/BIBLIO/eBay SQLite data is not discarded.

At startup, \`python -m app.legacy_migration\` performs an additive,
idempotent backfill into the workspace/master-inventory schema. Existing
legacy files remain untouched.

On an upgraded personal installation, the first real account registration
claims the unclaimed bootstrap owner, so migrated history stays attached to
that account.

The original personal UI remains available at:

\`\`\`text
/classic
\`\`\`

Public hosted deployments should disable the classic UI/API with:

\`\`\`env
LEGACY_UI_ENABLED=false
LEGACY_API_ENABLED=false
LEGACY_COMPAT_SYNC=false
\`\`\`

## Chrome bridge

There are deliberately two extension variants.

### Commercial / paired bridge

\`app/extension\` is the thin Manifest V3 companion intended for Chrome Web
Store distribution. It:

- pairs with a workspace using a short-lived one-time code;
- stores only a revocable dashboard bridge token;
- reads the inventory/order/analytics data needed by the product from the
  user's signed-in Vinted tab;
- sends that snapshot to the paired workspace;
- supports manual and periodic sync;
- does not export the user's Vinted password or raw cookie values;
- contains no dashboard business logic;
- contains no rendered-market-research module or unattended destructive Vinted
  actions.

For local beta testing, download the development build directly from the
running dashboard:

\`\`\`text
/downloads/reseller-chrome-bridge.zip
\`\`\`

Extract it, open \`chrome://extensions\`, enable Developer mode, choose **Load
unpacked**, then use **Connections -> Vinted -> Pair Chrome** in the web app.

The bridge supports explicit Vinted web origins for the principal European
markets rather than being tied to the original Portuguese account. On its
first sync it uses an already-open signed-in Vinted tab and remembers that
origin for later periodic syncs.

### Personal legacy extension

\`app/legacy_extension\` preserves the previous self-hosted extension,
including the opt-in rendered market-research workflow used by the classic
dashboard.

The classic download remains:

\`\`\`text
/downloads/vinted-session-sync.zip
\`\`\`

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

Inventory exports are available as CSV or XLSX. Workspace account-data export
is also available under Settings.

## Connections

Connector behavior is capability-driven rather than inferred from marketplace
names.

Current scope:

- **Vinted** - paired Chrome bridge, listings/orders/history/analytics.
- **CSV / TSV** - generic import/export.
- **Excel** - generic import/export.
- **BIBLIO** - existing import and FTP inventory/delete workflow for the
  bootstrap/self-hosted workspace.
- **eBay** - existing official seller inventory connector for the
  bootstrap/self-hosted workspace.

BIBLIO and eBay remain fully usable for the existing personal deployment, but
are not falsely advertised as hosted multi-tenant integrations. Hosted
workspace credential records can be stored encrypted, while their
per-workspace sync adapters remain marked non-operational until implemented.

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
  --version 2.0.0 \
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
